from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from are.simulation.distributed.diagnostic_metrics import diagnostic_metric_rows
from are.simulation.distributed.evaluation_adapters import (
    DiagnosticPacket,
    DiagnosticWitness,
    run_adapters,
)
from are.simulation.distributed.evaluator_v5 import evaluate_farm_dcore_v5
from are.simulation.distributed.experiments import aggregate_rows
from are.simulation.distributed.journal import (
    DurableRunJournal,
    interruption_status,
    load_journal,
)
from are.simulation.distributed.models import (
    AgentIntent,
    DistributedRunnerConfig,
    EpistemicStatus,
    FactRequirement,
    GuardResult,
    GuardVerdict,
    IntentKind,
    KnowledgeItem,
    RequirementVerdict,
    stable_digest,
)
from are.simulation.distributed.native_season import (
    NativeDistributedSeasonRunner,
    live_repair_attempt_key,
    selective_repair_trigger_reason,
)
from are.simulation.distributed.paper_report import (
    SUPPLEMENT_TABLES,
    _read_auxiliary_records,
    _read_frozen_labels,
    _table_rows,
)
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
    scope_match="covers",
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
            "scope_match": scope_match,
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
            {
                "acquired": False,
                "prompted": False,
                "visible_to": ("field_intelligence",),
            },
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
    multiple = run_adapters(_packet(value=False, second_guard=True), ["dcore"])[0]
    assert len(multiple.witnesses) == 2
    assert len({item.prerequisite_id for item in multiple.witnesses}) == 2


def test_exact_scope_is_shared_by_dcore_and_independent_checker():
    packet = _packet(scope=(0, 63), scope_match="exact")
    dcore, checker = run_adapters(packet, ["dcore", "full_information_checker"])
    assert [item.mechanism for item in dcore.witnesses] == ["incorrect_scope"]
    assert [item.mechanism for item in checker.witnesses] == ["incorrect_scope"]


def test_live_repair_retry_key_changes_for_staleness_and_authored_occurrence():
    requirement = FactRequirement(
        requirement_id="harvest-maturity",
        action="TractorApp__harvest",
        actor_id="operations",
        fact_key="crop:mature",
        scope=(0, 20),
        scope_match="exact",
        max_age=100.0,
    )
    evidence = KnowledgeItem(
        item_id="maturity-v1",
        fact_key="crop:mature",
        value=True,
        scope=(0, 20),
        status=EpistemicStatus.OBSERVED,
        source_actor="field_intelligence",
        observed_at=100.0,
        learned_at=100.0,
    )
    common = {
        "actor_id": "operations",
        "requirement": requirement,
        "knowledge_items": (evidence,),
        "prompt_item_ids": {"maturity-v1"},
    }
    fresh = live_repair_attempt_key(
        operation_resolution=SimpleNamespace(transition_id="harvest-a"),
        world_time=150.0,
        **common,
    )
    unchanged = live_repair_attempt_key(
        operation_resolution=SimpleNamespace(transition_id="harvest-a"),
        world_time=151.0,
        **common,
    )
    stale = live_repair_attempt_key(
        operation_resolution=SimpleNamespace(transition_id="harvest-a"),
        world_time=201.0,
        **common,
    )
    later_occurrence = live_repair_attempt_key(
        operation_resolution=SimpleNamespace(transition_id="harvest-b"),
        world_time=150.0,
        **common,
    )
    assert unchanged == fresh
    assert stale != fresh
    assert later_occurrence != fresh


def test_saved_packet_ablations_remove_only_the_declared_information_component():
    temporal = run_adapters(
        _packet(valid_until=90.0),
        ["dcore_full", "dcore_no_temporal_validity"],
    )
    assert [item.mechanism for item in temporal[0].witnesses] == ["expired_evidence"]
    assert temporal[1].witnesses == ()
    assert temporal[1].adapter_metadata["removed_component"] == "temporal_validity"

    prompt = run_adapters(
        _packet(prompted=False),
        ["dcore_full", "dcore_no_prompt_inclusion"],
    )
    assert [item.mechanism for item in prompt[0].witnesses] == ["context_omission"]
    assert prompt[1].witnesses == ()

    delivery = run_adapters(
        _packet(
            acquired=False,
            prompted=False,
            visible_to=("field_intelligence",),
        ),
        ["dcore_full", "dcore_no_actor_delivery"],
    )
    assert [item.mechanism for item in delivery[0].witnesses] == ["failed_delivery"]
    assert [item.mechanism for item in delivery[1].witnesses] == ["missing_observation"]
    assert delivery[0].repairs != delivery[1].repairs

    targeted = run_adapters(
        _packet(prompted=False),
        ["dcore_full", "dcore_no_targeted_selection", "dcore_diagnosis_only"],
    )
    assert targeted[0].repairs
    assert targeted[1].repairs[0].primitives[0].primitive == ("request_reconsideration")
    assert targeted[2].repairs == ()


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
        event["causal_parents"] = [
            replacements.get(item, item) for item in event.get("causal_parents", ())
        ]
        if "fact_versions" in event.get("payload", {}):
            event["payload"]["fact_versions"] = [
                replacements[item] for item in event["payload"]["fact_versions"]
            ]
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
    assert semantic_trace_digest(
        trace, before_event_id="target"
    ) == semantic_trace_digest(changed_future, before_event_id="target")


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


def test_llm_always_verify_uses_metered_actor_local_prefix(tmp_path):
    output = tmp_path / "always"
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=8,
            live_verification_policy="llm_always_verify",
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
        line
        for line in journal.splitlines()
        if '"kind": "live_verifier_request"' in line
    )


def test_dcore_all_eligible_uses_the_same_legal_prefix_repair_path(tmp_path):
    output = tmp_path / "dcore-all-eligible"
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=8,
            live_verification_policy="dcore_always",
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
    diagnoses = [
        item
        for item in load_journal(output / "progress.dcore.jsonl")
        if item["kind"] == "dcore_live_diagnosis"
    ]
    assert diagnoses
    assert all(item["payload"]["invoked"] is True for item in diagnoses)
    assert {item["payload"]["evidence_interface"] for item in diagnoses} == {
        "legal_team_prefix_with_actor_prompt_inclusion_v1"
    }
    assert result.trace.outcome["live_verification_count"] == len(diagnoses)
    assert not any(
        item["kind"] == "live_verifier_request"
        for item in load_journal(output / "progress.dcore.jsonl")
    )


def test_every_parsed_proposal_has_a_durable_structured_decision(tmp_path):
    output = tmp_path / "structured-decisions"
    DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=8,
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
    journal = [
        json.loads(line)
        for line in (output / "progress.dcore.jsonl").read_text().splitlines()
    ]
    proposals = [item for item in journal if item["kind"] == "parsed_proposal"]
    decisions = [item for item in journal if item["kind"] == "structured_decision"]
    exchanges = [item for item in journal if item["kind"] == "model_exchange"]
    assert len(decisions) == len(proposals)
    assert {item["payload"]["intent_id"] for item in decisions} == {
        item["payload"]["intent_id"] for item in proposals
    }
    assert all("result_world_time" in item["payload"]["result"] for item in decisions)
    assert all("guard" in item["payload"] for item in decisions)
    assert exchanges
    assert all("prompt" not in item["payload"] for item in exchanges)
    assert all(item["payload"].get("prompt_digest") for item in exchanges)


def test_selective_dcore_triggers_when_guard_allows_but_prompt_omits_evidence():
    requirement = FactRequirement(
        requirement_id="scoped-soil",
        action="FieldOpsApp__irrigate_range",
        actor_id="operations",
        fact_key="soil:root_moisture",
        scope=(22, 32),
    )
    evidence = KnowledgeItem(
        item_id="soil-v1",
        fact_key="soil:root_moisture",
        value=0.19,
        scope=(22, 32),
        status=EpistemicStatus.OBSERVED,
        source_actor="field_intelligence",
        observed_at=10.0,
        learned_at=11.0,
    )
    allowed = GuardResult(
        verdict=GuardVerdict.ALLOW,
        requirement_verdicts={"scoped-soil": RequirementVerdict.TRUE},
    )
    assert (
        selective_repair_trigger_reason(requirement, allowed, evidence, set())
        == "prompt_omission"
    )
    assert (
        selective_repair_trigger_reason(requirement, allowed, evidence, {"soil-v1"})
        is None
    )


@pytest.mark.parametrize(
    ("extra_seconds", "expected_status"),
    [(0.0, "ok"), (1.0, "horizon_overrun_rejected")],
)
def test_horizon_boundary_allowed_and_overrun_rejected(
    tmp_path, extra_seconds, expected_status
):
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
            output_dir=str(tmp_path / expected_status),
        )
    )
    action = next(
        item
        for item in result.trace.events
        if item.action == "SystemApp__advance_time" and item.kind.value == "action"
    )
    assert action.status == expected_status
    assert result.trace.outcome["measurement_at_horizon"] is True
    journal = [
        json.loads(line)
        for line in (tmp_path / expected_status / "progress.dcore.jsonl")
        .read_text()
        .splitlines()
    ]
    time_intents = [
        item
        for item in journal
        if item["kind"] == "native_write_intent"
        and item["payload"].get("action") == "SystemApp__advance_time"
    ]
    time_receipts = [
        item
        for item in journal
        if item["kind"] == "native_write_receipt"
        and item["payload"].get("action") == "SystemApp__advance_time"
    ]
    if expected_status == "ok":
        assert len(time_intents) == len(time_receipts) == 1
        assert (
            time_intents[0]["payload"]["intent_id"]
            == time_receipts[0]["payload"]["intent_id"]
        )
    else:
        assert time_intents == []
        assert time_receipts == []


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


def test_diagnostic_predictions_join_frozen_checkpoint_labels(tmp_path):
    packet = _packet(prompted=False).model_copy(
        update={"campaign_id": "campaign-1", "checkpoint_id": "checkpoint-1"}
    )
    result = run_adapters(packet, ["dcore"])[0]
    (tmp_path / "diagnosis.json").write_text(
        json.dumps(
            {
                "schema_version": "diagnostic_comparison_bundle_v1",
                "packet": packet.model_dump(mode="json"),
                "results": [result.model_dump(mode="json")],
            }
        )
    )
    label = {
        "campaign_id": "campaign-1",
        "checkpoint_id": "checkpoint-1",
        "decision_id": "decision-1",
        "scenario_id": "farm_wetjune_recheck",
        "classification": "repairable_information_failure",
        "mechanism": "context_omission",
        "prerequisite_id": "disease-current",
        "actor_id": "operations",
        "scope": [22, 32],
        "source_version_id": "fact-v1",
    }
    (tmp_path / "labels.json").write_text(
        json.dumps(
            {
                "schema_version": "dcore_miniature_checkpoint_labels_v2",
                "labels": [label],
            }
        )
    )
    (tmp_path / "repair_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "dcore_repair_study_manifest_v2",
                "campaign_id": "campaign-1",
                "checkpoints": [
                    {
                        "checkpoint_id": "checkpoint-1",
                        "continuation_manifest": {
                            "checkpoint_decision_id": "decision-1"
                        },
                        "independent_label": label,
                    }
                ],
            }
        )
    )
    predictions, _ = _read_auxiliary_records(tmp_path)
    labels = _read_frozen_labels(tmp_path)
    assert len(predictions) == len(labels) == 1
    metric = next(
        item
        for item in diagnostic_metric_rows(predictions, labels)
        if item["scenario"] == "farm_wetjune_recheck"
    )
    assert metric["coverage"] == 1.0
    assert metric["mechanism_micro_f1"] == 1.0
    assert metric["exact_prerequisite_accuracy"] == 1.0


def test_live_comparison_keeps_failed_assignment_and_cross_manifest_reference():
    rows = []
    for world in range(3):
        reference_key = f"reference-{world}"
        common = {
            "campaign_id": "miniature",
            "analysis_block": "live",
            "scenario_revision": "r1",
            "process_spec_digest": "spec",
            "team_id": "team",
            "controller_profile_id": "react",
            "model_configuration_id": "model",
            "scenario": "farm_wetjune_recheck",
            "fault": "mixed",
            "world_seed": world,
            "repeat_index": 0,
            "world_cluster_id": f"farm_wetjune_recheck:w{world}",
            "scripted_reference_key": reference_key,
        }
        rows.extend(
            (
                {
                    **common,
                    "assignment_id": f"dcore-{world}",
                    "manifest_digest": "live-manifest",
                    "condition": "dcore_selective_mixed",
                    "live_verification_policy": "dcore_selective",
                    "infrastructure_failure": world == 2,
                    "recovered_harvest_kg": None if world == 2 else 99.0,
                },
                {
                    **common,
                    "assignment_id": f"dcore-all-{world}",
                    "manifest_digest": "live-manifest",
                    "condition": "dcore_all_eligible_mixed",
                    "live_verification_policy": "dcore_always",
                    "infrastructure_failure": False,
                    "recovered_harvest_kg": 100.0,
                },
                {
                    **common,
                    "assignment_id": f"reference-{world}",
                    "manifest_digest": "different-reference-manifest",
                    "analysis_block": "scripted_reference",
                    "model_configuration_id": "scripted",
                    "condition": "scripted_petri_oracle",
                    "infrastructure_failure": False,
                    "recovered_harvest_kg": 100.0,
                },
            )
        )
    aggregate = aggregate_rows(rows)
    comparison = next(
        item
        for item in aggregate["live_verification_noninferiority"]
        if item["policy"] == "dcore_selective"
    )
    assert comparison["assigned"] == 3
    assert comparison["available_pairs"] == 2
    assert comparison["missing_pairs"] == 1


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


def test_existing_guard_defers_incomplete_terminal_finish(tmp_path):
    execution = NativeDistributedSeasonRunner(
        controllers={
            "field_intelligence": _FinishThenWaitController(),
            "operations": _FinishThenWaitController(),
        }
    ).run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            controller_mode="scripted",
            scientific_contract="v5",
            enforcement_mode="enforce",
            live_verification_policy="existing_guard",
            max_logical_steps=4,
            output_dir=str(tmp_path / "existing-guard-finish"),
        )
    )
    interventions = execution.trace.outcome["live_interventions"]
    assert interventions
    assert all(item["kind"] == "finish_deferral" for item in interventions)
    assert all(item["policy"] == "existing_guard" for item in interventions)
    assert "premature_abandonment" not in set(
        execution.trace.outcome["termination_by_actor"].values()
    )
