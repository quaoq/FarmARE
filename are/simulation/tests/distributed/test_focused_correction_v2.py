from __future__ import annotations

from copy import deepcopy

import pytest

from are.simulation.distributed.evaluation_adapters import (
    DiagnosticPacket,
    DiagnosticWitness,
    run_adapters,
)
from are.simulation.distributed.evaluator_v5 import evaluate_farm_dcore_v5
from are.simulation.distributed.experiments import aggregate_rows
from are.simulation.distributed.journal import DurableRunJournal, interruption_status
from are.simulation.distributed.models import (
    AgentIntent,
    DistributedRunnerConfig,
    IntentKind,
    stable_digest,
)
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.paper_report import SUPPLEMENT_TABLES, _table_rows
from are.simulation.distributed.prefix_replay import (
    execute_unchanged_replay,
    semantic_trace_digest,
)
from are.simulation.distributed.repair_study import enumerate_repairs
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


class _IntentController:
    def __init__(self, intents):
        self.intents = iter(intents)
        self.complete = False

    def initialize(self, actor, local_view):
        return None

    def decide(self, local_view):
        intent = next(self.intents)
        self.complete = intent.kind == IntentKind.FINISH
        return intent

    def observe(self, result):
        return None

    def is_complete(self):
        return self.complete


class _FinishThenWaitController:
    def __init__(self):
        self.first = True
        self.complete = False

    def initialize(self, actor, local_view):
        return None

    def decide(self, local_view):
        if self.first:
            self.first = False
            self.complete = True
            return AgentIntent(kind=IntentKind.FINISH)
        self.complete = False
        return AgentIntent(kind=IntentKind.WAIT)

    def observe(self, result):
        if result.get("deferred"):
            self.complete = False

    def is_complete(self):
        return self.complete


def _packet(
    *,
    value=True,
    scope=(22, 32),
    acquired=True,
    prompted=True,
    valid_until=200.0,
    visible_to=("operations",),
    second_guard=False,
) -> DiagnosticPacket:
    contract = "frozen public contract"
    decision_id = "decision-1"
    fact = {
        "version_id": "fact-v1",
        "fact_key": "disease:present",
        "value": value,
        "scope": scope,
        "source_event_id": "observation-1",
        "world_time": 80.0,
        "valid_until": valid_until,
        "visible_to": visible_to,
        "authoritative": False,
    }
    guards = [
        {
            "guard_id": "disease-current",
            "fact_key": "disease:present",
            "operator": "eq",
            "expected": True,
            "scope": (22, 32),
            "max_age": 30.0,
            "source": "actor_evidence",
        }
    ]
    if second_guard:
        guards.append(
            {
                "guard_id": "weather-current",
                "fact_key": "weather:spray_window_open",
                "operator": "eq",
                "expected": True,
                "scope": (22, 32),
                "source": "actor_evidence",
            }
        )
    decision = {
        "decision_id": decision_id,
        "actor_id": "operations",
        "logical_time": 3.0,
        "proposed_intent": {
            "kind": "act",
            "action": "FarmWorldApp__apply_fungicide",
            "args": {"start_ridge": 22, "end_ridge": 32},
        },
        "knowledge_snapshot": {"item_ids": ["fact-v1"] if acquired else []},
        "prompt_item_ids": ["fact-v1"] if prompted else [],
        "prompt_message_ids": [],
    }
    raw = {
        "schema_version": "diagnostic_packet_v2",
        "run_id": "run-1",
        "scenario_id": "farm_wetjune_recheck",
        "specification_digest": "a" * 64,
        "public_task_contract": contract,
        "public_task_contract_digest": stable_digest(contract),
        "actors": ("field_intelligence", "operations"),
        "events": (
            {
                "event_id": decision_id,
                "kind": "decision",
                "actor_id": "operations",
                "logical_time": 3.0,
                "world_time": 100.0,
            },
        ),
        "local_contexts": (),
        "messages": (),
        "receipts": (),
        "decisions": (decision,),
        "fact_versions": (fact,),
        "requirements": (),
        "reference_transitions": (
            {
                "transition_id": "spray",
                "actor_id": "operations",
                "action": "FarmWorldApp__apply_fungicide",
                "guards": guards,
            },
        ),
        "target_decision": decision,
        "prefix_decision_id": decision_id,
        "cutoff_event_index": 0,
        "cutoff_logical_time": 3.0,
        "cutoff_world_time": 100.0,
        "outcome": None,
        "existing_metrics": {},
    }
    return DiagnosticPacket(**raw, packet_digest=stable_digest(raw))


@pytest.mark.parametrize(
    ("overrides", "mechanism"),
    [
        ({"value": False}, "failure_to_use_available_evidence"),
        ({"scope": (0, 10)}, "incorrect_scope"),
        ({"valid_until": 90.0}, "expired_evidence"),
        (
            {"acquired": False, "prompted": False, "visible_to": ("field_intelligence",)},
            "failed_delivery",
        ),
        ({"prompted": False}, "context_omission"),
    ],
)
def test_prefix_diagnosis_hand_labelled_mechanisms(overrides, mechanism):
    result = run_adapters(_packet(**overrides), ["dcore"])[0]
    assert result.status == "ok"
    assert [item.mechanism for item in result.witnesses] == [mechanism]
    witness = result.witnesses[0]
    assert witness.schema_version == "diagnostic_witness_v2"
    assert witness.prerequisite["guard_id"] == "disease-current"


def test_clean_prompted_evidence_requires_no_repair_and_multiple_failures_split():
    clean = run_adapters(_packet(), ["dcore"])[0]
    assert clean.witnesses == ()
    multiple = run_adapters(
        _packet(value=False, second_guard=True), ["dcore"]
    )[0]
    assert len(multiple.witnesses) == 2
    assert len({item.prerequisite_id for item in multiple.witnesses}) == 2


def test_repair_past_deadline_is_infeasible():
    witness = DiagnosticWitness(
        witness_id="w",
        decision_id="d",
        obligation_id="o",
        prerequisite_id="p",
        actor_id="operations",
        mechanism="missing_observation",
        fact_key="disease:present",
        root_support_group="o",
        determination="supported",
        target_scope=(22, 32),
        decision_time=100.0,
        deadline=101.0,
    )
    candidates = enumerate_repairs(
        witness,
        observer_by_fact={"disease:present": "field_intelligence"},
        native_action_by_fact={"disease:present": "Mavic3M__fly_survey"},
        native_cost_by_primitive={"acquire_observation": 1.0, "route_evidence": 0.0},
        duration_by_primitive={"acquire_observation": 2.0, "route_evidence": 1.0},
    )
    assert candidates[0].feasibility == "infeasible"
    assert candidates[0].timing_slack_seconds == -2.0


def test_graph_canonicalization_accepts_renaming_but_rejects_semantic_changes():
    trace = {
        "events": [
            {
                "event_id": "e1",
                "kind": "message_send",
                "actor_id": "field_intelligence",
                "logical_time": 1.0,
                "world_time": 10.0,
                "message_id": "m1",
                "payload": {"recipient": "operations", "fact_versions": ["v1"]},
            },
            {
                "event_id": "e2",
                "kind": "message_receive",
                "actor_id": "operations",
                "logical_time": 2.0,
                "world_time": 11.0,
                "message_id": "m1",
                "causal_parents": ["e1"],
                "fact_version": "v1",
                "payload": {"recipient": "operations"},
            },
            {
                "event_id": "target",
                "kind": "decision",
                "actor_id": "operations",
                "logical_time": 3.0,
                "world_time": 12.0,
                "causal_parents": ["e2"],
            },
            {
                "event_id": "future",
                "kind": "action",
                "actor_id": "operations",
                "logical_time": 4.0,
                "world_time": 13.0,
                "status": "ok",
            },
        ]
    }
    renamed = deepcopy(trace)
    replacements = {"e1": "x1", "e2": "x2", "target": "x3", "m1": "mx", "v1": "vx"}
    for event in renamed["events"]:
        for key in ("event_id", "message_id", "fact_version"):
            if event.get(key) in replacements:
                event[key] = replacements[event[key]]
        event["causal_parents"] = [replacements.get(item, item) for item in event.get("causal_parents", ())]
        if "fact_versions" in event.get("payload", {}):
            event["payload"]["fact_versions"] = [replacements[item] for item in event["payload"]["fact_versions"]]
    assert semantic_trace_digest(trace) == semantic_trace_digest(renamed)

    changed_owner = deepcopy(trace)
    changed_owner["events"][1]["actor_id"] = "field_intelligence"
    assert semantic_trace_digest(trace) != semantic_trace_digest(changed_owner)
    changed_recipient = deepcopy(trace)
    changed_recipient["events"][0]["payload"]["recipient"] = "field_intelligence"
    assert semantic_trace_digest(trace) != semantic_trace_digest(changed_recipient)
    changed_edge = deepcopy(trace)
    changed_edge["events"][2]["causal_parents"] = ["e1"]
    assert semantic_trace_digest(trace) != semantic_trace_digest(changed_edge)
    changed_version = deepcopy(trace)
    changed_version["events"][1]["fact_version"] = "different-version"
    assert semantic_trace_digest(trace) != semantic_trace_digest(changed_version)
    changed_prompt = deepcopy(trace)
    changed_prompt["events"][2]["payload"] = {"prompt_item_ids": ["v2"]}
    assert semantic_trace_digest(trace) != semantic_trace_digest(changed_prompt)

    changed_future = deepcopy(trace)
    changed_future["events"][3]["status"] = "error"
    assert semantic_trace_digest(trace, before_event_id="target") == semantic_trace_digest(
        changed_future, before_event_id="target"
    )


def test_uncertain_provider_request_is_not_safe_to_replay(tmp_path):
    path = tmp_path / "progress.jsonl"
    journal = DurableRunJournal(path)
    journal.append(
        "provider_request_intent",
        {"provider_request_id": "p1", "reserved_tokens": 100},
    )
    status = interruption_status(path)
    assert status["status"] == "uncertain_provider_request"
    assert status["safe_to_replay_in_place"] is False
    assert status["uncertain_provider_requests"][0]["reserved_tokens"] == 100


def test_always_verify_uses_metered_actor_local_prefix(tmp_path):
    output = tmp_path / "always"
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=8,
            live_verification_policy="always_verify",
            model_by_actor={
                "field_intelligence": "offline-mock",
                "operations": "offline-mock",
            },
            provider_by_actor={
                "field_intelligence": "mock",
                "operations": "mock",
            },
            output_dir=str(output),
        )
    )
    outcome = result.trace.outcome
    assert outcome["high_impact_proposal_count"] >= 1
    assert outcome["live_verification_count"] == outcome["high_impact_proposal_count"]
    assert outcome["provider_use_by_purpose"]["verifier"] >= 1
    journal = (output / "progress.dcore.jsonl").read_text()
    assert '"kind": "live_verifier_request"' in journal
    assert '"schema_version": "live_verifier_prefix_v1"' in journal
    assert '"outcome"' not in next(
        line for line in journal.splitlines() if '"kind": "live_verifier_request"' in line
    )


@pytest.mark.parametrize(("extra_seconds", "expected_status"), [(0.0, "ok"), (1.0, "horizon_overrun_rejected")])
def test_horizon_boundary_allowed_and_overrun_rejected(extra_seconds, expected_status):
    scenario = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    advance = AgentIntent(
        kind=IntentKind.ACT,
        action="SystemApp__advance_time",
        args={"seconds": int(scenario.duration - 1 + extra_seconds)},
    )
    runner = NativeDistributedSeasonRunner(
        controllers={
            "field_intelligence": _IntentController(
                [AgentIntent(kind=IntentKind.FINISH)]
            ),
            "operations": _IntentController(
                [advance, AgentIntent(kind=IntentKind.FINISH)]
            ),
        }
    )
    result = runner.run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            controller_mode="scripted",
            enforcement_mode="off",
            max_logical_steps=4,
        )
    )
    action = next(
        item
        for item in result.trace.events
        if item.action == "SystemApp__advance_time" and item.kind.value == "action"
    )
    assert action.status == expected_status
    assert result.trace.outcome["measurement_at_horizon"] is True


def test_failed_assignment_and_partial_harvest_reach_final_tables():
    common = {
        "scenario": "farm_wetjune_recheck",
        "condition": "miniature",
        "fault": "none",
        "repeat_index": 0,
    }
    rows = [
        {
            **common,
            "assignment_id": "partial",
            "run_id": "partial-run",
            "world_seed": 70,
            "world_cluster_id": "farm_wetjune_recheck:w70",
            "success": False,
            "safety_success": True,
            "infrastructure_failure": False,
            "recovered_harvest_kg": 42.0,
            "harvest_complete": False,
            "storage_complete": False,
            "postharvest_compliant": False,
            "provider_accounted_usd": 0.25,
        },
        {
            **common,
            "assignment_id": "failed",
            "run_id": "failed-run",
            "world_seed": 71,
            "world_cluster_id": "farm_wetjune_recheck:w71",
            "success": False,
            "safety_success": False,
            "infrastructure_failure": True,
            "recovered_harvest_kg": None,
            "provider_accounted_usd": 0.5,
        },
    ]
    tables = _table_rows(rows, aggregate_rows(rows), [])
    completion = tables[SUPPLEMENT_TABLES[0]][0]
    missingness = tables[SUPPLEMENT_TABLES[1]][0]
    costs = tables[SUPPLEMENT_TABLES[2]]
    assert completion["harvest_complete"] == 0.0
    assert missingness["assigned"] == 2
    assert missingness["infrastructure_failure_rate"] == 0.5
    assert missingness["recovered_harvest_kg_assigned_n_missing"] == 1
    assert missingness["recovered_harvest_kg_assigned_availability_rate"] == 0.5
    assert [item["assignment_id"] for item in costs] == ["partial", "failed"]
    assert [item["provider_accounted_usd"] for item in costs] == [0.25, 0.5]


def test_deferred_finish_replays_with_the_same_termination_boundary(tmp_path):
    source = tmp_path / "deferred-finish-source"
    config = DistributedRunnerConfig(
        scenario_id="farm_wetjune_recheck",
        controller_mode="scripted",
        scientific_contract="v5",
        live_verification_policy="dcore_selective",
        max_logical_steps=4,
        output_dir=str(source),
    )
    execution = NativeDistributedSeasonRunner(
        controllers={
            "field_intelligence": _FinishThenWaitController(),
            "operations": _FinishThenWaitController(),
        }
    ).run(config)
    assert all(
        item["kind"] == "finish_deferral"
        for item in execution.trace.outcome["live_interventions"]
    )
    metrics = evaluate_farm_dcore_v5(execution.process_spec, execution.trace)
    DistributedScenarioRunner()._write_native_artifacts(
        config, execution, metrics, runtime_seconds=0.0
    )
    replay = execute_unchanged_replay(source, tmp_path / "deferred-finish-replay")
    assert replay["verified"] is True
    assert replay["semantic_trace_match"] is True
    assert replay["outcome_match"] is True
