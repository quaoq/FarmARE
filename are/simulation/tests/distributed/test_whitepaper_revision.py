from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from are.simulation.apps.farm_world import FarmWorldApp, TractorApp
from are.simulation.distributed.agricultural_review import (
    build_agricultural_review_packets,
    validate_agricultural_reviews,
)
from are.simulation.distributed.authored_specs import author_process
from are.simulation.distributed.evaluation_adapters import (
    DiagnosticPacket,
    DiagnosticWitness,
    RepairCandidate,
    RepairPrimitive,
    available_adapters,
    run_adapters,
)
from are.simulation.distributed.experiments import (
    aggregate_rows,
    load_manifest,
    resolve_manifest,
    run_resolved_matrix,
)
from are.simulation.distributed.journal import (
    DurableRunJournal,
    interruption_status,
    load_journal,
)
from are.simulation.distributed.models import DistributedRunnerConfig, stable_digest
from are.simulation.distributed.prefix_replay import (
    build_checkpoint_manifest,
    execute_fresh_continuation,
    execute_repaired_continuation,
    execute_unchanged_replay,
)
from are.simulation.distributed.repair_study import enumerate_repairs
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    FARM_SCENARIOS,
    create_native_scenario,
)

ROOT = Path(__file__).parents[4]
CONFIG = ROOT / "are/simulation/distributed/configs"
AUTHORED = ROOT / "AAMAS/authored_specifications"


def test_postharvest_tools_preserve_location_and_measured_moisture():
    scenario = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    farm = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    tractor._grain_bin_kg = 100.0
    farm._pending_bin_grain_ridge_ids = [0]
    farm._pending_bin_grain_moistures_pct = [12.5]

    unloaded = tractor.unload_grain()
    assert unloaded["destination"] == "harvest_trailer"
    assert unloaded["grain_bin_kg"] == 0.0
    assert unloaded["trailer_grain_kg"] == 100.0
    assert unloaded["warehouse_grain_kg"] == 0.0

    drying = farm.dry_grain()
    assert drying["drying_skipped"] is True
    assert drying["storage_required"] is True
    assert drying["next_postharvest_action"] == "FarmWorldApp__store_grain"
    stored = farm.store_grain()
    assert stored["moved_kg"] == 100.0
    state = farm.get_state()
    assert state["inventory"]["harvest_grain_kg"] == 0.0
    assert state["inventory"]["warehouse_grain_kg"] == 100.0
    assert state["warehouse_grain_moisture_pct"] == 12.5


def test_durable_journal_distinguishes_three_crash_boundaries(tmp_path: Path):
    empty = tmp_path / "empty.jsonl"
    DurableRunJournal(empty).append("run_started", {"api_key": "never persist"})
    assert interruption_status(empty)["status"] == "interrupted_before_model_request"
    assert "never persist" not in empty.read_text()

    before = tmp_path / "before.jsonl"
    journal = DurableRunJournal(before)
    journal.append("model_exchange", {"response": "{}"})
    assert interruption_status(before)["status"] == "interrupted_before_native_write"

    uncertain = tmp_path / "uncertain.jsonl"
    journal = DurableRunJournal(uncertain)
    journal.append("native_write_intent", {"intent_id": "i1", "action": "write"})
    status = interruption_status(uncertain)
    assert status["status"] == "uncertain_native_write"
    assert status["uncertain_native_writes"][0]["intent_id"] == "i1"

    after = tmp_path / "after.jsonl"
    journal = DurableRunJournal(after)
    journal.append("native_write_intent", {"intent_id": "i1"})
    journal.append("native_write_receipt", {"intent_id": "i1", "status": "ok"})
    assert interruption_status(after)["status"] == "interrupted_after_native_receipt"
    assert [item["sequence"] for item in load_journal(after)] == [0, 1]


def test_durable_journal_compresses_and_selectively_hydrates_prompt_history(
    tmp_path: Path,
):
    from are.simulation.distributed.journal import proposal_checkpoint

    path = tmp_path / "checkpoint.jsonl"
    history = {
        "operations": tuple(
            {"role": "user", "content": "repeated-prefix-" + "x" * 5000}
            for _ in range(20)
        )
    }
    DurableRunJournal(path).append(
        "parsed_proposal",
        {
            "intent_id": "decision-1",
            "checkpoint": {
                "prompt_history": history,
                "prompt_history_digests": {"operations": "digest"},
            },
        },
    )
    raw = path.read_text(encoding="utf-8")
    assert "repeated-prefix" not in raw
    assert len(raw) < 5000

    compact = load_journal(path, hydrate_checkpoints=False)[0]
    assert "prompt_history" not in compact["payload"]["checkpoint"]
    assert compact["payload"]["checkpoint"]["prompt_history_encoding"] == (
        "gzip+base64+json-v1"
    )
    hydrated = proposal_checkpoint(path, "decision-1")
    assert hydrated["prompt_history"] == {
        "operations": [
            {"role": "user", "content": "repeated-prefix-" + "x" * 5000}
            for _ in range(20)
        ]
    }


def test_unchanged_native_replay_has_semantic_and_outcome_equivalence(
    tmp_path: Path,
):
    source = tmp_path / "source"
    DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            max_logical_steps=1000,
            output_dir=str(source),
        )
    )
    result = execute_unchanged_replay(source, tmp_path / "replay")
    assert result["verified"] is True
    assert result["provider_requests"] == 0
    assert result["management_actions_inserted"] == 0


def test_recorded_responses_reenter_normal_react_parser_and_replay(tmp_path: Path):
    source = tmp_path / "response-source"
    DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=6,
            output_dir=str(source),
        )
    )
    trace = json.loads(next(source.glob("trace.dcore_trace*.json")).read_text())
    checkpoint = build_checkpoint_manifest(
        source,
        trace["decisions"][2]["decision_id"],
        remaining_call_budget=4,
    )
    assert checkpoint.prompt_history
    assert all(
        row.get("type") not in {"LLMInputLog", "LLMRetryUsageLog"}
        for history in checkpoint.prompt_history.values()
        for row in history
    )
    result = execute_unchanged_replay(
        source,
        tmp_path / "response-replay",
        response_level=True,
        checkpoint=checkpoint,
    )
    assert result["verified"] is True
    assert result["checkpoint_verified"] is True
    assert result["replay_level"] == "recorded_model_response"
    assert result["provider_requests"] == 0


def test_repaired_suffix_verifies_prefix_discards_future_and_uses_fresh_calls(
    tmp_path: Path,
):
    source = tmp_path / "repair-source"
    DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=6,
            output_dir=str(source),
        )
    )
    trace = json.loads(next(source.glob("trace.dcore_trace*.json")).read_text())
    checkpoint = build_checkpoint_manifest(
        source,
        trace["decisions"][2]["decision_id"],
        remaining_call_budget=4,
    )
    primitive = RepairPrimitive(
        primitive="request_reconsideration",
        actor_id=trace["decisions"][2]["actor_id"],
        fact_key="soil:trafficable",
        scope=(0, 63),
    )
    repair = RepairCandidate(
        candidate_id="offline-repair",
        witness_id="offline-witness",
        primitives=(primitive,),
        total_native_cost=0.0,
        timing_slack_seconds=1.0,
        feasibility="feasible",
        priority_key=(3.0, 1, 0.0, "offline-repair"),
    )
    finish = (
        "Thought: conclude this bounded offline suffix.\nAction:\n"
        '{"action":"dcore_finish","action_input":{}}<end_action>'
    )
    result = execute_repaired_continuation(
        source,
        tmp_path / "repair-suffix",
        checkpoint=checkpoint,
        repair=repair,
        execution_overrides={
            "model_by_actor": {
                "field_intelligence": "offline-mock",
                "operations": "offline-mock",
            },
            "provider_by_actor": {
                "field_intelligence": "mock",
                "operations": "mock",
            },
            "replay_live_responses_by_actor": {
                "field_intelligence": [finish, finish],
                "operations": [finish, finish],
            },
        },
    )
    assert result["checkpoint_verified"] is True
    application = result["repair_application"]
    assert application["live_suffix_started"] is True
    assert sum(application["discarded_future_responses"].values()) > 0
    assert application["applications"][0]["status"] == "applied"


def test_fresh_untreated_suffix_uses_same_verified_boundary(tmp_path: Path):
    source = tmp_path / "fresh-source"
    DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=6,
            output_dir=str(source),
        )
    )
    trace = json.loads(next(source.glob("trace.dcore_trace*.json")).read_text())
    checkpoint = build_checkpoint_manifest(
        source, trace["decisions"][2]["decision_id"], remaining_call_budget=4
    )
    finish = (
        "Thought: conclude this bounded offline suffix.\nAction:\n"
        '{"action":"dcore_finish","action_input":{}}<end_action>'
    )
    result = execute_fresh_continuation(
        source,
        tmp_path / "fresh-suffix",
        checkpoint=checkpoint,
        execution_overrides={
            "model_by_actor": {
                "field_intelligence": "offline-mock",
                "operations": "offline-mock",
            },
            "provider_by_actor": {
                "field_intelligence": "mock",
                "operations": "mock",
            },
            "replay_live_responses_by_actor": {
                "field_intelligence": [finish, finish],
                "operations": [finish, finish],
            },
        },
    )
    assert result["checkpoint_verified"] is True
    assert result["condition"] == "fresh_no_intervention"
    assert result["repair_candidate_id"] is None


def test_observation_and_route_repair_use_native_read_and_team_transport(
    tmp_path: Path,
):
    source = tmp_path / "routed-repair-source"
    DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=6,
            output_dir=str(source),
        )
    )
    trace = json.loads(next(source.glob("trace.dcore_trace*.json")).read_text())
    checkpoint = build_checkpoint_manifest(
        source,
        trace["decisions"][2]["decision_id"],
        remaining_call_budget=4,
    )
    witness = DiagnosticWitness(
        witness_id="route-witness",
        decision_id=checkpoint.checkpoint_decision_id,
        obligation_id="route-obligation",
        prerequisite_id="crop-health-prerequisite",
        actor_id="operations",
        mechanism="missing_observation",
        fact_key="crop:mean_ndvi",
        root_support_group="route-obligation",
        determination="supported",
        target_scope=(0, 3),
        decision_time=1.0,
        deadline=100.0,
    )
    repair = enumerate_repairs(
        witness,
        observer_by_fact={"crop:mean_ndvi": "field_intelligence"},
        native_cost_by_primitive={
            "acquire_observation": 1.0,
            "route_evidence": 0.0,
        },
        duration_by_primitive={
            "acquire_observation": 1.0,
            "route_evidence": 1.0,
        },
    )[0]
    assert [item.primitive for item in repair.primitives] == [
        "acquire_observation",
        "route_evidence",
    ]
    assert repair.primitives[0].native_action == "Mavic3M__fly_survey"
    finish = (
        "Thought: conclude this bounded offline suffix.\nAction:\n"
        '{"action":"dcore_finish","action_input":{}}<end_action>'
    )
    result = execute_repaired_continuation(
        source,
        tmp_path / "routed-repair-suffix",
        checkpoint=checkpoint,
        repair=repair,
        execution_overrides={
            "model_by_actor": {
                "field_intelligence": "offline-mock",
                "operations": "offline-mock",
            },
            "provider_by_actor": {
                "field_intelligence": "mock",
                "operations": "mock",
            },
            "replay_live_responses_by_actor": {
                "field_intelligence": [finish, finish],
                "operations": [finish, finish],
            },
        },
    )
    statuses = [item["status"] for item in result["repair_application"]["applications"]]
    assert statuses == ["applied", "applied"]
    journal = load_journal(tmp_path / "routed-repair-suffix/progress.dcore.jsonl")
    native_receipt = next(
        item for item in journal if item["kind"] == "repair_native_receipt"
    )
    assert native_receipt["payload"]["receipt"]["action"] == "Mavic3M__fly_survey"
    assert any(item["kind"] == "repair_message_send" for item in journal)


@pytest.mark.parametrize("scenario_id", tuple(FARM_SCENARIOS))
def test_authored_high_impact_obligations_bind_explicit_prerequisites(scenario_id):
    settings = (
        {
            "scenario_revision": "drought_water_balance_v5",
            "calibration_candidate": True,
            "reference_harvest_calendar": True,
            "reference_harvest_opening": True,
        }
        if scenario_id == "farm_disease_drought"
        else {}
    )
    process = author_process(scenario_id, **settings)
    assert process.causal_obligations
    assert all(item.prerequisites for item in process.causal_obligations)
    assert any(len(item.prerequisites) > 1 for item in process.causal_obligations)
    assert all(
        prerequisite.fact_key and prerequisite.actor_id and prerequisite.guard_id
        for item in process.causal_obligations
        for prerequisite in item.prerequisites
        if not prerequisite.transition_edges
    )


def test_revised_study_allocation_and_disabled_reserve(tmp_path: Path):
    expected = {
        "farm_dcore_primary_pass1.yaml": 480,
        "farm_dcore_primary_pass2.yaml": 450,
        "farm_dcore_live_verification.yaml": 300,
        "farm_dcore_reserve.yaml": 15,
    }
    for name, count in expected.items():
        rows = resolve_manifest(load_manifest(CONFIG / name))
        assert len(rows) == count
        assert all("REPLACE_WITH" not in json.dumps(row) for row in rows)
    reserve = resolve_manifest(load_manifest(CONFIG / "farm_dcore_reserve.yaml"))
    with pytest.raises(RuntimeError, match="not released for execution"):
        run_resolved_matrix(reserve[:1], tmp_path)


def test_live_verification_reports_clustered_noninferiority_without_imputation():
    rows = []
    for world, audit, policy, reference in (
        (100, 90.0, 89.5, 100.0),
        (101, 180.0, 179.0, 200.0),
    ):
        common = {
            "scenario": "farm_wetjune_recheck",
            "team_id": "wetjune_2agent",
            "fault": "none",
            "world_seed": world,
            "repeat_index": 0,
            "world_cluster_id": f"farm_wetjune_recheck:w{world}",
            "success": True,
            "safety_success": True,
            "infrastructure_failure": False,
        }
        rows.extend(
            [
                {
                    **common,
                    "condition": "scripted_petri_oracle",
                    "recovered_harvest_kg": reference,
                },
                {
                    **common,
                    "condition": "audit_only_reliable",
                    "live_verification_policy": "audit_only",
                    "recovered_harvest_kg": audit,
                },
                {
                    **common,
                    "condition": "always_verify_reliable",
                    "live_verification_policy": "always_verify",
                    "recovered_harvest_kg": audit,
                },
                {
                    **common,
                    "condition": "dcore_selective_reliable",
                    "live_verification_policy": "dcore_selective",
                    "recovered_harvest_kg": policy,
                },
            ]
        )
    result = aggregate_rows(rows)["live_verification_noninferiority"]
    row = next(item for item in result if item["policy"] == "dcore_selective")
    assert row["available_pairs"] == 2
    assert row["missing_pairs"] == 0
    assert row["world_clusters"] == 2
    assert row["mean_normalized_harvest_difference"] == pytest.approx(-0.005)
    assert row["noninferior"] is True
    assert row["comparison_policy"] == "always_verify"


def test_live_noninferiority_uses_always_verify_not_audit_only():
    common = {
        "scenario": "farm_wetjune_recheck",
        "team_id": "wetjune_2agent",
        "fault": "none",
        "world_seed": 100,
        "repeat_index": 0,
        "world_cluster_id": "farm_wetjune_recheck:w100",
        "success": True,
        "safety_success": True,
        "infrastructure_failure": False,
    }
    rows = [
        {**common, "condition": "scripted_petri_oracle", "recovered_harvest_kg": 100.0},
        {
            **common,
            "condition": "audit",
            "live_verification_policy": "audit_only",
            "recovered_harvest_kg": 80.0,
        },
        {
            **common,
            "condition": "always",
            "live_verification_policy": "always_verify",
            "recovered_harvest_kg": 100.0,
        },
        {
            **common,
            "condition": "dcore",
            "live_verification_policy": "dcore_selective",
            "recovered_harvest_kg": 81.0,
        },
    ]
    row = aggregate_rows(rows)["live_verification_noninferiority"][0]
    assert row["mean_normalized_harvest_difference"] == pytest.approx(-0.19)
    assert row["noninferior"] is False


def test_repair_catalogue_is_evidence_bound_and_at_most_two_primitives():
    witness = DiagnosticWitness(
        witness_id="w1",
        decision_id="d1",
        obligation_id="o1",
        prerequisite_id="p1",
        actor_id="operations",
        mechanism="missing_observation",
        fact_key="soil:root_moisture",
        fact_version_ids=("f1",),
        root_support_group="o1",
        determination="supported",
        target_scope=(22, 32),
        decision_time=10.0,
        deadline=20.0,
    )
    candidates = enumerate_repairs(
        witness,
        native_cost_by_primitive={"acquire_observation": 2.0, "route_evidence": 1.0},
        duration_by_primitive={"acquire_observation": 2.0, "route_evidence": 1.0},
    )
    assert candidates
    assert all(1 <= len(item.primitives) <= 2 for item in candidates)
    assert all(item.required_evidence_ids == ("f1",) for item in candidates)
    assert all(item.timing_slack_seconds is not None for item in candidates)
    disease = witness.model_copy(
        update={
            "fact_key": "disease:confirmed",
            "target_scope": (22, 32),
            "fact_version_ids": (),
            "source_version_id": None,
        }
    )
    disease_repair = enumerate_repairs(
        disease,
        observer_by_fact={"disease:confirmed": "field_intelligence"},
    )[0]
    assert disease_repair.primitives[0].native_action == ("Robot0__inspect_crop_health")
    assert disease_repair.primitives[0].native_arguments == {
        "start_ridge": 22,
        "end_ridge": 32,
    }


def test_comparator_registry_returns_typed_unavailable_external_methods():
    contract = "same public contract"
    raw = {
        "run_id": "r1",
        "scenario_id": "farm_wetjune_recheck",
        "specification_digest": "a" * 64,
        "public_task_contract": contract,
        "public_task_contract_digest": stable_digest(contract),
        "actors": ("field_intelligence", "operations"),
        "events": (),
        "local_contexts": (),
        "messages": (),
        "receipts": (),
        "decisions": (),
        "fact_versions": (),
        "requirements": (),
    }
    packet = DiagnosticPacket(**raw, packet_digest=stable_digest(raw))
    methods = set(available_adapters())
    assert {
        "who_when_all_at_once",
        "agentrx",
        "dcfa_style_reimplementation",
        "dover_adaptation",
        "dcore_bounded_repair",
    } <= methods
    result = run_adapters(packet, ["who_when_all_at_once"])[0]
    assert result.status == "unavailable"
    assert result.error == "external_adapter_not_configured"
    dover = run_adapters(packet, ["dover_adaptation"])[0]
    assert dover.status == "unavailable"
    assert dover.capability == "repair"
    assert dover.error == "defining_hypothesis_intervention_selection_not_implemented"


def test_upstream_bridges_project_the_same_packet_without_future_leakage():
    bridge = runpy.run_path(str(ROOT / "AAMAS/comparators/upstream_bridge.py"))
    packet = {
        "run_id": "r1",
        "public_task_contract": "frozen public briefing",
        "requirements": [{"requirement_id": "required-r1"}],
        "decisions": [
            {
                "decision_id": "decision-1",
                "actor_id": "field_intelligence",
                "logical_time": 1.0,
                "proposed_intent": {"action": "inspect"},
                "knowledge_snapshot": {"item_ids": []},
            }
        ],
        "outcome": None,
    }
    who_when = bridge["who_when_dataset"](packet)
    assert who_when["question"] == "frozen public briefing"
    assert who_when["ground_truth"] == ""
    assert len(who_when["history"]) == 1

    ordinary = bridge["agentrx_markdown"](packet)
    reviewed = bridge["agentrx_markdown"](packet, reviewed_constraints=True)
    assert "frozen public briefing" in ordinary
    assert "required-r1" not in ordinary
    assert "required-r1" in reviewed
    assert "outcome" not in ordinary


def test_external_comparator_bridge_runs_in_isolated_json_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "print(json.dumps({'schema_version':'comparator_result_v1',"
        "'method':request['method'],'capability':request['capability'],"
        "'status':'ok','packet_digest':request['packet']['packet_digest']}))\n"
    )
    config = tmp_path / "adapters.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "dcore_comparator_adapter_config_v1",
                "adapters": {
                    "who_when_all_at_once": {
                        "command": [sys.executable, str(bridge)],
                        "source_revision": ("f4d2b6da464a826580e59b3a0eae15ea2d642d7c"),
                        "timeout_seconds": 10,
                    }
                },
            }
        )
    )
    monkeypatch.setenv("DCORE_COMPARATOR_ADAPTERS", str(config))
    contract = "same public contract"
    raw = {
        "run_id": "r1",
        "scenario_id": "farm_wetjune_recheck",
        "specification_digest": "a" * 64,
        "public_task_contract": contract,
        "public_task_contract_digest": stable_digest(contract),
        "actors": ("field_intelligence", "operations"),
        "events": (),
        "local_contexts": (),
        "messages": (),
        "receipts": (),
        "decisions": (),
        "fact_versions": (),
        "requirements": (),
    }
    packet = DiagnosticPacket(**raw, packet_digest=stable_digest(raw))
    result = run_adapters(packet, ["who_when_all_at_once"])[0]
    assert result.status == "ok"
    assert result.packet_digest == packet.packet_digest


def test_agricultural_packet_builder_freezes_eight_per_scenario(tmp_path: Path):
    manifest = build_agricultural_review_packets(
        tuple(AUTHORED / f"{scenario}.process.json" for scenario in FARM_SCENARIOS),
        tmp_path / "review",
    )
    assert manifest["packet_count"] == 24
    packets = json.loads((tmp_path / "review/packets.json").read_text())
    assert len(packets["packets"]) == 24
    assert len({item["process_digest"] for item in packets["packets"]}) == 3
    assert (
        json.loads((tmp_path / "review/reviewer_a.json").read_text())["packet_digest"]
        == manifest["packet_digest"]
    )


def test_agricultural_review_requires_two_complete_distinct_submissions(
    tmp_path: Path,
):
    root = tmp_path / "review"
    build_agricultural_review_packets(
        tuple(AUTHORED / f"{scenario}.process.json" for scenario in FARM_SCENARIOS),
        root,
    )
    with pytest.raises(ValueError, match="identity is missing"):
        validate_agricultural_reviews(
            root / "packets.json", (root / "reviewer_a.json", root / "reviewer_b.json")
        )
    for index, name in enumerate(("reviewer_a.json", "reviewer_b.json"), start=1):
        path = root / name
        payload = json.loads(path.read_text())
        payload["reviewer_id"] = f"reviewer-{index}"
        payload["submitted_at"] = f"2026-09-15T12:0{index}:00Z"
        for review in payload["reviews"]:
            review["determination"] = "approve"
            review["rationale"] = "The scoped authored choice is acceptable."
        path.write_text(json.dumps(payload))
    validation = validate_agricultural_reviews(
        root / "packets.json", (root / "reviewer_a.json", root / "reviewer_b.json")
    )
    assert validation["review_complete"] is True
    assert validation["confirmation_permitted"] is True
