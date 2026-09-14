from __future__ import annotations

import copy
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from are.simulation.distributed.calibration import (
    assess_calibration,
    drought_target,
    instrument_target,
)
from are.simulation.distributed.cli import main
from are.simulation.distributed.controllers import (
    FarmAREBaseAgentController,
    FarmARELLMController,
)
from are.simulation.distributed.knowledge import KnowledgeStore, knowledge_frontier
from are.simulation.distributed.models import (
    ActorSpec,
    AgentIntent,
    DistributedRunnerConfig,
    EpistemicStatus,
    IntentKind,
    KnowledgeItem,
    LocalView,
)
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.ontology import capability_cards
from are.simulation.distributed.teams import build_builtin_team
from are.simulation.distributed.tool_gateway import RoleToolGateway
from are.simulation.environment import Environment, EnvironmentConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


def fact(item_id, observed, learned, scope=(0, 20)):
    return KnowledgeItem(
        item_id=item_id,
        fact_key="soil:moisture",
        value=observed,
        scope=scope,
        status=EpistemicStatus.OBSERVED,
        source_actor="field_intelligence",
        observed_at=observed,
        learned_at=learned,
        vector_clock={"operations": int(learned)},
    )


def test_receive_order_does_not_replace_newer_evidence_and_scopes_survive():
    store = KnowledgeStore("operations")
    for item in (
        fact("new", 20, 21),
        fact("late-old", 10, 30),
        fact("zone-b", 15, 16, (21, 42)),
    ):
        store.add(item)
    assert store.latest("soil:moisture").item_id == "new"
    assert {item.item_id for item in store.for_keys(["soil:moisture"])} == {
        "new",
        "zone-b",
    }
    assert len(store.items) == 3
    assert knowledge_frontier(reversed(store.items)) == knowledge_frontier(store.items)


@pytest.mark.parametrize(
    "controller_type", [FarmARELLMController, FarmAREBaseAgentController]
)
def test_both_prompt_adapters_use_observation_time(controller_type):
    controller = object.__new__(controller_type)
    controller.knowledge_window = controller.message_window = 10
    if controller_type is FarmAREBaseAgentController:
        from types import SimpleNamespace

        controller.max_model_calls = 10
        controller.base_agent = SimpleNamespace(logs=[])
    view = LocalView(
        actor=ActorSpec(actor_id="operations"),
        logical_time=30,
        world_time=30,
        knowledge=(fact("new", 20, 21), fact("late-old", 10, 30)),
        inbox=(),
        vector_clock={},
    )
    rendered = controller._render_local_context(view)
    assert '"item_id": "new"' in rendered
    assert '"item_id": "late-old"' not in rendered


@pytest.fixture(scope="module")
def scenario():
    return create_native_scenario("farm_wetjune_recheck", world_seed=0)


@pytest.mark.parametrize(
    "team_id", ["wetjune_2agent", "wetjune_3agent", "wetjune_4agent"]
)
def test_capabilities_match_actual_grants_and_legal_routes(scenario, team_id):
    team = build_builtin_team(team_id, scenario.get_tools())
    viewer = team.actors[0].actor_id
    bundle = capability_cards(team, viewer)
    assert bundle == capability_cards(team, viewer)
    for card in bundle["cards"]:
        spec = next(
            actor for actor in team.actors if actor.actor_id == card["actor_id"]
        )
        assert card["can_use"] == sorted(spec.permitted_actions)
        assert "knowledge" not in card
        if card["next_hop"]:
            own_card = next(
                item for item in bundle["cards"] if item["actor_id"] == viewer
            )
            assert card["next_hop"] in own_card["message_recipients"]
    prompt = NativeDistributedSeasonRunner._distributed_prompt(
        "", team.actors[0], "public task", team
    )
    assert bundle["digest"] in prompt
    assert "sensor ID is not a ridge ID" in prompt


def test_gateway_duplicate_ids_cannot_leak_or_change_requests(scenario):
    env = Environment(
        config=EnvironmentConfig(
            start_time=scenario.start_time,
            duration=scenario.duration,
            oracle_mode=False,
            verbose=False,
        )
    )
    env.register_apps(scenario.apps or [])
    gateway = RoleToolGateway(env, scenario.get_tools())
    request = dict(
        actor_id="field_intelligence",
        intent_id="read-1",
        action="WeatherApp__get_current_weather",
        arguments={},
    )
    first = gateway.execute(**request)
    receipt = first.receipt("read-1")
    before = len(env.event_log.list_view())
    with pytest.raises(PermissionError):
        gateway.execute(**{**request, "actor_id": "operations"})
    with pytest.raises(ValueError, match="different request"):
        gateway.execute(
            **{
                **request,
                "action": "WeatherApp__get_forecast",
                "arguments": {"days": 3},
            }
        )
    duplicate = gateway.execute(**request)
    assert duplicate.receipt("read-1")["receipt_digest"] == receipt["receipt_digest"]
    assert duplicate.receipt("read-1")["duplicate"] is True
    assert len(env.event_log.list_view()) == before
    if isinstance(first.result, dict):
        first.result["injected"] = "must not pollute the cache"
        assert "injected" not in gateway.execute(**request).result
    forecast = dict(
        actor_id="field_intelligence",
        intent_id="forecast-1",
        action="WeatherApp__get_forecast",
        arguments={"days": 1},
    )
    gateway.execute(**forecast)
    after_forecast = len(env.event_log.list_view())
    with pytest.raises(ValueError, match="different request"):
        gateway.execute(**{**forecast, "arguments": {"days": 2}})
    assert len(env.event_log.list_view()) == after_forecast


def test_handoff_preserves_multiple_regions(scenario):
    team = build_builtin_team("wetjune_2agent", scenario.get_tools())
    store = KnowledgeStore("field_intelligence")
    store.add(fact("zone-a", 10, 10))
    store.add(fact("zone-b", 11, 11, (21, 42)))
    envelopes = NativeDistributedSeasonRunner._build_envelopes(
        config=DistributedRunnerConfig(scenario_id="farm_wetjune_recheck"),
        team=team,
        actor_id="field_intelligence",
        intent=AgentIntent(
            kind=IntentKind.SEND,
            recipient="operations",
            claim_fact_keys=("soil:moisture",),
        ),
        stores={"field_intelligence": store},
        message_versions=defaultdict(int),
        world_time=12,
    )
    assert {claim.scope for claim in envelopes[0].claims} == {(0, 20), (21, 42)}


def valid_pair(seed):
    record = {
        "target_event_id": "r5",
        "scope": [20, 43],
        "world_time": 100,
        "root_vwc_by_ridge": {str(ridge): 0.15 for ridge in range(20, 44)},
        "stress_threshold_by_ridge": {str(ridge): 0.18 for ridge in range(20, 44)},
        "stressed_fraction": 1.0,
        "accepted": True,
        "omitted": False,
    }
    control = {
        "exogenous_world_digest": "a" * 64,
        "target_records": [record],
        "validation_success": True,
        "harvest_complete": True,
        "storage_complete": True,
        "marketable_yield_kg": 1000,
    }
    omission = copy.deepcopy(control)
    omission["target_records"][0].update(accepted=False, omitted=True)
    omission["marketable_yield_kg"] = 970
    return {"world_seed": seed, "control": control, "omission": omission}


def test_calibration_requires_all_worlds_and_retains_negative_effects():
    pairs = [valid_pair(seed) for seed in range(5)]
    kwargs = dict(
        expected_seeds=list(range(5)), min_shortfall=0.01, min_stressed_fraction=0.5
    )
    assert assess_calibration(pairs, **kwargs)["engineering_acceptance_passed"]
    assert not assess_calibration(pairs[:-1], **kwargs)["engineering_acceptance_passed"]
    assert not assess_calibration(pairs + pairs[:1], **kwargs)[
        "engineering_acceptance_passed"
    ]
    pairs[0]["omission"]["marketable_yield_kg"] = 1001
    result = assess_calibration(pairs, **kwargs)
    assert not result["engineering_acceptance_passed"]
    assert result["checks"][0]["shortfall"] < 0


@pytest.mark.parametrize("corruption", ["world", "stress", "nan", "target", "prestate"])
def test_calibration_rejects_invalid_evidence(corruption):
    pairs = [valid_pair(seed) for seed in range(5)]
    pair = pairs[0]
    if corruption == "world":
        pair["omission"]["exogenous_world_digest"] = "b" * 64
    elif corruption == "stress":
        pair["control"]["target_records"][0]["stressed_fraction"] = 0
    elif corruption == "nan":
        pair["control"]["marketable_yield_kg"] = float("nan")
    elif corruption == "target":
        pair["omission"]["target_records"] = []
    else:
        pair["omission"]["target_records"][0]["world_time"] += 1
    assert not assess_calibration(
        pairs,
        expected_seeds=list(range(5)),
        min_shortfall=0.01,
        min_stressed_fraction=0.5,
    )["engineering_acceptance_passed"]


def test_candidate_is_isolated_and_target_instrumentation_preserves_graph():
    from are.simulation.apps.farm_world import FarmWorldApp

    base = create_native_scenario("farm_disease_drought", world_seed=0)
    candidate = create_native_scenario(
        "farm_disease_drought", world_seed=0, calibration_candidate=True
    )
    base_again = create_native_scenario("farm_disease_drought", world_seed=0)

    def manifest(scenario):
        return scenario.get_typed_app(FarmWorldApp).physics.dcore_exogenous_manifest

    assert manifest(base) == manifest(base_again)
    assert manifest(candidate) != manifest(base)
    target, scope = drought_target(candidate)
    deps, successors = list(target.dependencies), list(target.successors)
    records = []
    farm = candidate.get_typed_app(FarmWorldApp)
    instrument_target(target, farm, scope, omit=True, records=records)
    observation_time = candidate.start_time
    fake_env = SimpleNamespace(
        time_manager=SimpleNamespace(time=lambda: observation_time)
    )
    event = target.make_event(fake_env)
    farm.advance_physics_time(observation_time)
    before = copy.deepcopy(farm.physics.soil.states)
    result = event.action.execute()
    assert result["calibration_omitted"] is True
    assert len(records) == 1 and records[0]["omitted"] is True
    assert farm.physics.soil.states == before
    assert list(target.dependencies) == deps and list(target.successors) == successors


def test_calibration_dry_run_starts_no_season_and_writes_nothing(tmp_path, monkeypatch):
    import are.simulation.distributed.calibration as calibration

    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run must not initialize or execute a season")

    monkeypatch.setattr(calibration, "create_native_scenario", forbidden)
    result = CliRunner().invoke(
        main,
        ["calibrate-scenario", "--output-dir", str(tmp_path / "runs"), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["season_count"] == 20
    assert not (tmp_path / "runs").exists()


def test_drought_release_cannot_claim_readiness_without_sensitivity_evidence():
    from pydantic import ValidationError

    from are.simulation.distributed.scientific_v5 import ScientificGateManifestV5

    payload = dict(
        scenario_id="farm_disease_drought",
        status="complete",
        confirmed_process_digest="a" * 64,
        confirmed_team_digest="b" * 64,
        offline_semantic_gates=True,
        oracle_yield_equivalence=True,
        prompt_leakage_check=True,
        saved_trace_replay=True,
        blocked_write_nonmutation=True,
        allowed_write_exactly_once=True,
        mock_matrix_complete=True,
        code_commit="c" * 40,
        release_tag="reviewed",
        completed_at_utc="2026-09-12T00:00:00Z",
        test_report_digest="d" * 64,
        environment_lock_digest="e" * 64,
        analysis_protocol_digest="f" * 64,
    )
    with pytest.raises(ValidationError, match="sensitivity evidence"):
        ScientificGateManifestV5(**payload)
    gate = ScientificGateManifestV5(
        **payload,
        scenario_sensitivity_passed=True,
        scenario_sensitivity_report_digest="a" * 64,
        scenario_sensitivity_report_path="sensitivity.json",
    )
    assert gate.status == "complete"


def test_receipts_are_shareable_local_history_without_phase_evidence():
    from are.simulation.distributed.models import EventKind
    from are.simulation.distributed.trace import CausalTraceRecorder

    recorder = CausalTraceRecorder(
        "receipts", "farm", ("operations", "field_intelligence")
    )
    action = recorder.record(
        EventKind.ACTION, "operations", 0, world_time=10, action="FieldOpsApp__irrigate"
    )
    stores = {actor: KnowledgeStore(actor) for actor in recorder.actor_ids}
    NativeDistributedSeasonRunner._record_observation_facts(
        recorder=recorder,
        store=stores["operations"],
        all_stores=stores,
        shared=False,
        evidence_actor=False,
        adapter=None,
        actor_id="operations",
        action_event_id=action.event_id,
        farmare_event_id="native-1",
        action="dcore.tool_receipt",
        args={"start": 20, "end": 43},
        result={"status": "error", "error": "weather window closed"},
        logical_time=0.01,
        world_time=10,
        phase="r5",
        provenance_ids=set(),
        receipt_fact_key="tool_receipt:FieldOpsApp__irrigate",
    )
    assert len(stores["operations"].items) == 1
    assert stores["field_intelligence"].items == ()
    receipt = stores["operations"].items[0]
    assert receipt.scope == (20, 43)
    assert receipt.value["status"] == "error"
    assert not receipt.fact_key.startswith("phase_evidence:")
    assert recorder.fact_versions[0].farmare_event_id == "native-1"
