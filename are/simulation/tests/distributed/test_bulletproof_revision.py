from __future__ import annotations

import json
from pathlib import Path

import pytest

from are.simulation.apps.system import SystemApp
from are.simulation.distributed.agricultural_review import (
    build_agricultural_review_packets,
)
from are.simulation.distributed.authored_specs import author_process
from are.simulation.distributed.evaluation_adapters import (
    DiagnosticWitness,
    build_diagnostic_packet,
)
from are.simulation.distributed.experiments import (
    _config_from_row,
    load_manifest,
    resolve_manifest,
)
from are.simulation.distributed.models import (
    AgentIntent,
    DistributedRunnerConfig,
    IntentKind,
)
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.operation_resolution import (
    resolve_operation_occurrence,
)
from are.simulation.distributed.repair_study import (
    enumerate_repairs,
    resolve_observation_tool,
)
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.distributed.study_templates import (
    future_repair_checkpoint_manifest,
)
from are.simulation.distributed.tool_gateway import (
    RoleToolGateway,
    requires_durable_native_journal,
)
from are.simulation.environment import Environment, EnvironmentConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    create_native_scenario,
)


def _gateway(scenario_id: str = "farm_disease_drought") -> RoleToolGateway:
    scenario = create_native_scenario(scenario_id, world_seed=70)
    environment = Environment(
        config=EnvironmentConfig(
            start_time=scenario.start_time,
            duration=scenario.duration,
            oracle_mode=False,
            verbose=False,
        )
    )
    environment.register_apps(scenario.apps or [])
    return RoleToolGateway(environment, scenario.get_tools())


def test_time_controls_are_semantic_reads_but_durably_journaled():
    gateway = _gateway()
    for action in (
        "SystemApp__advance_time",
        "SystemApp__wait_for_notification",
    ):
        metadata = gateway.metadata(action)
        assert metadata["write"] is False
        assert metadata["observation"] is True
        assert metadata["high_impact"] is False
        assert requires_durable_native_journal(
            action,
            write_operation=metadata["write"],
        )


def test_public_tool_contract_explains_opaque_probe_scope_boundaries():
    gateway = _gateway("farm_three_cultivar")
    schemas = gateway.permitted_tool_schemas("field_intelligence")
    single = schemas["SensorApp__read_soil_sensor"]["description"]
    all_probes = schemas["SensorApp__read_soil_sensors"]["description"]
    assert "opaque label" in single
    assert "cannot" in single and "crosses probe boundaries" in single
    assert "combined" in all_probes and "coverage is ridges 0-63" in all_probes
    briefing = NativeDistributedSeasonRunner._task_briefing(
        "farm_three_cultivar", author_process("farm_three_cultivar")
    )
    assert "observation_scope_guidance" in briefing
    assert "legal route for a requirement spanning multiple probe zones" in briefing


def test_three_cultivar_window_rejects_wrong_seed_without_mutation():
    scenario = create_native_scenario("farm_three_cultivar", world_seed=112)
    farm = next(
        app for app in scenario.apps or () if app.__class__.__name__ == "FarmWorldApp"
    )
    before = farm.get_ridge(43).to_dict()
    error = farm.check_planting_window([43, 44], "HEINONG84")
    assert error is not None
    assert "required cultivar: HEINONG58" in error
    assert farm.get_ridge(43).to_dict() == before
    assert farm.check_planting_window([43, 44], "HEINONG58") is None


def test_wait_for_notification_never_moves_clock_to_a_stale_event():
    scenario = create_native_scenario("farm_three_cultivar", world_seed=92)
    environment = Environment(
        config=EnvironmentConfig(
            start_time=scenario.start_time,
            duration=scenario.duration,
            oracle_mode=False,
            verbose=False,
        )
    )
    environment.register_apps(scenario.apps or [])
    environment.time_manager.reset(start_time=scenario.start_time)
    environment.time_manager.add_offset(30 * 86400)
    before = environment.time_manager.time()
    event_times = iter((before - 20 * 86400, None))
    environment.get_next_event_time = lambda: next(event_times)  # type: ignore[method-assign]
    environment.tick = lambda: None  # type: ignore[method-assign]
    assert environment.notification_system is not None
    environment.notification_system.get_next_notification_time = (  # type: ignore[method-assign]
        lambda: None
    )
    system_app = environment.get_app_with_class(SystemApp)
    assert system_app is not None
    system_app.wait_for_notification(timeout=3600)
    assert environment.time_manager.time() >= before


def test_exact_operation_resolution_separates_phase_scope_and_occurrence():
    process = author_process("farm_wetjune_recheck")
    disease_time = next(
        window.start_world_time
        for window in process.phase_windows
        if window.phase == "disease"
    )
    spray = resolve_operation_occurrence(
        process,
        actor_id="operations",
        action="TractorApp__apply_fungicide",
        arguments={
            "start_ridge": 20,
            "end_ridge": 29,
            "liters_per_ridge": 3.6,
        },
        world_time=disease_time + 1,
    )
    assert spray.status == "unique"
    assert spray.transition_id == "o_mid_fungicide_0_20_43_spray_20_29"
    assert "o_r5_fungicide_0_20_43_spray_40_43" not in spray.candidate_transition_ids

    planting = resolve_operation_occurrence(
        process,
        actor_id="operations",
        action="TractorApp__plant_seeds",
        arguments={
            "start_ridge": 0,
            "end_ridge": 3,
            "depth_cm": 4.0,
            "seed_spacing_cm": 7.9,
        },
        world_time=process.phase_windows[0].start_world_time + 1,
    )
    assert planting.status == "unique"
    assert planting.candidate_transition_ids == ("o_whole_field_plant_0_3",)

    harvest_transition = next(
        item
        for item in process.occurrence_net.transitions
        if item.action == "TractorApp__harvest"
    )
    harvest_arguments = {
        item.name: item.expected for item in harvest_transition.arguments
    }
    harvest_time = next(
        window.start_world_time
        for window in process.phase_windows
        if window.phase == "harvest"
    )
    harvest = resolve_operation_occurrence(
        process,
        actor_id="operations",
        action="TractorApp__harvest",
        arguments=harvest_arguments,
        scope=harvest_transition.scope,
        world_time=harvest_time + 1,
    )
    assert harvest.status == "unique"
    horizon = process.metadata["scenario_horizon"]
    assert harvest.deadline == horizon
    assert any(
        item["source"] == "scenario_horizon" and item["deadline"] == horizon
        for item in harvest.deadline_sources
    )


def test_operation_resolution_binds_bad_parameters_without_losing_occurrence():
    process = author_process("farm_three_cultivar")
    establishment = next(
        window for window in process.phase_windows if window.phase == "establishment"
    )
    planting = resolve_operation_occurrence(
        process,
        actor_id="operations",
        action="TractorApp__plant_seeds",
        arguments={
            "start_ridge": 0,
            "end_ridge": 3,
            "depth_cm": 4.0,
            # The authored reference is 8.2.  A different native-valid spacing
            # is an argument error on this occurrence, not an unknown action.
            "seed_spacing_cm": 5.0,
        },
        world_time=establishment.start_world_time + 1,
    )
    assert planting.status == "unique"
    assert planting.transition_id == "o_zone_a_heihe50_plant_0_3"
    assert planting.match_basis["scope_conforming"] is True
    assert planting.match_basis["phase_conforming"] is True
    assert planting.match_basis["argument_conforming"] is False
    requirements = NativeDistributedSeasonRunner._guard_requirements(
        petri_net=process.occurrence_net,
        process_spec=process,
        actor_id="operations",
        action="TractorApp__plant_seeds",
        args={
            "start_ridge": 0,
            "end_ridge": 3,
            "depth_cm": 4.0,
            "seed_spacing_cm": 5.0,
        },
        scope=(0, 3),
        phase="establishment",
        world_time=establishment.start_world_time + 1,
    )
    assert [item.fact_key for item in requirements] == ["planting:soil_suitable"]
    assert requirements[0].scope == (0, 3)

    harvest_b_time = next(
        window.start_world_time
        for window in process.phase_windows
        if window.phase == "harvest_b"
    )
    harvest_requirements = NativeDistributedSeasonRunner._guard_requirements(
        petri_net=process.occurrence_net,
        process_spec=process,
        actor_id="operations",
        action="TractorApp__harvest",
        args={"start_ridge": 21, "end_ridge": 24},
        scope=(21, 24),
        phase="harvest_b",
        world_time=harvest_b_time + 1,
    )
    assert {item.scope for item in harvest_requirements} == {(21, 24)}

    after_establishment = resolve_operation_occurrence(
        process,
        actor_id="operations",
        action="TractorApp__plant_seeds",
        arguments={
            "start_ridge": 0,
            "end_ridge": 3,
            "depth_cm": 4.0,
            "seed_spacing_cm": 8.2,
        },
        world_time=establishment.end_world_time + 1,
    )
    assert after_establishment.status == "unmatched"
    assert (
        after_establishment.match_basis["reason"]
        == "no_phase_scope_conforming_occurrence"
    )

    crossed_cultivar_boundary = resolve_operation_occurrence(
        process,
        actor_id="operations",
        action="TractorApp__harvest",
        arguments={"start_ridge": 40, "end_ridge": 43},
        world_time=next(
            window.start_world_time
            for window in process.phase_windows
            if window.phase == "harvest_b"
        )
        + 1,
    )
    assert crossed_cultivar_boundary.status == "unmatched"
    cross_requirements = NativeDistributedSeasonRunner._guard_requirements(
        petri_net=process.occurrence_net,
        process_spec=process,
        actor_id="operations",
        action="TractorApp__harvest",
        args={"start_ridge": 40, "end_ridge": 43},
        scope=(40, 43),
        phase="harvest_b",
        world_time=next(
            window.start_world_time
            for window in process.phase_windows
            if window.phase == "harvest_b"
        )
        + 1,
    )
    resolution_guard = NativeDistributedSeasonRunner._unresolved_operation_guard(
        requirements=cross_requirements,
        resolution=crossed_cultivar_boundary,
        process_spec=process,
        actor_id="operations",
        action="TractorApp__harvest",
        scope=(40, 43),
        phase="harvest_b",
    )
    assert resolution_guard is not None
    assert resolution_guard.verdict.value == "block"
    assert "proposed inclusive scope=40-43" in resolution_guard.reasons[0]
    assert "21-24" in resolution_guard.reasons[0]
    assert "missing observation" in resolution_guard.reasons[0]


def test_state_changing_observations_are_not_management_verification_targets():
    robot_observation = AgentIntent(
        kind=IntentKind.OBSERVE,
        action="Robot0__inspect_crop_health",
        args={"start_ridge": 0, "end_ridge": 3},
    )
    planting = AgentIntent(
        kind=IntentKind.ACT,
        action="TractorApp__plant_seeds",
        args={"start_ridge": 0, "end_ridge": 3},
    )
    assert not NativeDistributedSeasonRunner._is_high_impact_decision(
        intent=robot_observation,
        metadata={"observation": True, "high_impact": True, "write": True},
    )
    assert NativeDistributedSeasonRunner._is_high_impact_decision(
        intent=planting,
        metadata={"observation": False, "high_impact": True, "write": True},
    )


def test_ambiguous_prerequisite_free_unload_does_not_invent_guard_evidence():
    process = author_process("farm_wetjune_recheck")
    harvest_time = next(
        window.start_world_time
        for window in process.phase_windows
        if window.phase == "harvest"
    )
    prior_events = [
        {
            "kind": "action",
            "status": "ok",
            "actor_id": "operations",
            "action": "TractorApp__harvest",
            "args": {"start_ridge": start, "end_ridge": end},
        }
        for start, end in ((0, 3), (4, 7), (8, 11))
    ]
    resolution = resolve_operation_occurrence(
        process,
        actor_id="operations",
        action="TractorApp__unload_grain",
        arguments={},
        world_time=harvest_time + 1,
        prior_events=prior_events,
    )
    assert resolution.status == "ambiguous"

    requirements = NativeDistributedSeasonRunner._guard_requirements(
        petri_net=process.occurrence_net,
        process_spec=process,
        actor_id="operations",
        action="TractorApp__unload_grain",
        args={},
        scope=None,
        phase="harvest",
        world_time=harvest_time + 1,
        prior_events=prior_events,
    )
    assert requirements == ()


def test_public_contract_states_inclusive_scope_and_boundary_batch_rule():
    process = author_process("farm_three_cultivar")
    briefing = NativeDistributedSeasonRunner._task_briefing(
        "farm_three_cultivar", process
    )
    encoded = briefing.split("<declared_process_contract>\n", 1)[1].split(
        "\n</declared_process_contract>", 1
    )[0]
    contract = json.loads(encoded)
    assert "endpoints are inclusive" in contract["inclusive_scope_rule"]
    assert "40-43" in contract["inclusive_scope_rule"]
    harvest_scopes = {
        tuple(scope)
        for policy in contract["information_policy_assignments"]
        if policy["policy_id"].startswith("farm_three_cultivar:harvest_")
        for scope in policy["scopes"]
    }
    assert harvest_scopes == {(0, 20), (21, 42), (43, 63)}
    routes = {item["fact_key"]: item for item in contract["fact_observation_routes"]}
    assert routes["weather:harvest_window_open"]["observation_actions"] == [
        "WeatherApp__get_current_weather"
    ]
    assert (
        "SensorApp__read_soil_sensors"
        in routes["soil:trafficable"]["observation_actions"]
    )
    assert routes["crop:mature"]["observation_actions"] == [
        "FarmWorldApp__get_ridge_range_state"
    ]
    assert all(item["valid_for_seconds"] for item in routes.values())


def test_prefix_packet_excludes_postproposal_and_terminal_information(tmp_path):
    run_dir = tmp_path / "source"
    DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            controller_mode="mock_llm",
            max_logical_steps=6,
            output_dir=str(run_dir),
        )
    )
    trace_path = next(run_dir.glob("trace.dcore_trace*.json"))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    target = trace["decisions"][1]
    target.update(
        {
            "guard_result": {"verdict": "allow"},
            "execution_receipt": {"status": "accepted"},
            "result": {"future": True},
            "terminal_outcome": {"harvest": 1},
        }
    )
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    packet = build_diagnostic_packet(run_dir, prefix_decision_id=target["decision_id"])
    assert packet.outcome is None
    assert packet.existing_metrics == {}
    assert set(packet.target_decision or {}).isdisjoint(
        {"guard_result", "execution_receipt", "result", "terminal_outcome"}
    )
    assert packet.cutoff_event_index is not None
    assert len(packet.events) == packet.cutoff_event_index + 1


def test_native_estimates_are_scoped_side_effect_free_and_deadline_aware():
    gateway = _gateway()
    process = author_process("farm_disease_drought")
    action, arguments, estimate = resolve_observation_tool(
        fact_key="soil:trafficable",
        scope=(22, 32),
        process_spec=process,
        gateway=gateway,
    )
    assert action == "SensorApp__read_soil_sensor"
    assert arguments == {"sensor_id": "S3"}
    assert estimate and estimate["duration_seconds"] == 0.0

    action, arguments, estimate = resolve_observation_tool(
        fact_key="soil:trafficable",
        scope=(21, 30),
        process_spec=process,
        gateway=gateway,
    )
    assert action == "SensorApp__read_soil_sensors"
    assert arguments == {}
    assert estimate and estimate["duration_seconds"] == 0.0

    before = gateway.feasibility_state()
    drone = gateway.estimate(
        action="Mavic3M__fly_survey",
        arguments={"start_ridge": 0, "end_ridge": 3},
    )
    assert drone.duration_seconds == 83.0
    assert gateway.feasibility_state() == before
    witness = DiagnosticWitness(
        witness_id="deadline",
        decision_id="decision",
        obligation_id="obligation",
        prerequisite_id="prerequisite",
        actor_id="field_intelligence",
        mechanism="missing_observation",
        fact_key="crop:mean_ndvi",
        root_support_group="obligation",
        determination="supported",
        target_scope=(0, 3),
        decision_time=0.0,
        deadline=1.0,
    )
    repair = enumerate_repairs(
        witness,
        observer_by_fact={"crop:mean_ndvi": "field_intelligence"},
        native_action_by_fact={"crop:mean_ndvi": "Mavic3M__fly_survey"},
        duration_by_primitive={"acquire_observation": drone.duration_seconds},
        native_cost_by_primitive={"acquire_observation": 1.0},
        native_estimates_by_action={"Mavic3M__fly_survey": drone.model_dump()},
        response_lead_time_seconds=1.0,
    )[0]
    assert repair.feasibility == "infeasible"
    assert repair.timing_slack_seconds == -83.0


def test_future_template_has_frozen_sixty_checkpoint_denominator():
    manifest = future_repair_checkpoint_manifest()
    assert manifest["checkpoint_count"] == 60
    assert len(manifest["checkpoints"]) == 60
    assert manifest["assigned_suffixes_after_selection"] == 900
    assert manifest["execution_allowed"] is False
    assert all(not item["dcore_output_opened"] for item in manifest["checkpoints"])


def test_miniature_manifest_preserves_full_season_logical_step_limit():
    repository = Path(__file__).parents[4]
    manifest = load_manifest(
        repository / "AAMAS/handover_development/progression_v19_worlds70_71.yaml"
    )
    rows = resolve_manifest(manifest)
    assert {row["max_logical_steps"] for row in rows} == {10000}
    assert {_config_from_row(row).max_logical_steps for row in rows} == {10000}


@pytest.mark.parametrize(
    ("name", "harvest_phases"),
    [
        ("farm_wetjune_recheck.process.json", {"harvest"}),
        ("farm_disease_drought.process.json", {"harvest"}),
        (
            "farm_three_cultivar.process.json",
            {"harvest_a", "harvest_b", "harvest_c"},
        ),
    ],
)
def test_released_specs_bound_reference_harvest_retries(name, harvest_phases):
    repository = Path(__file__).parents[4]
    process = json.loads(
        (repository / "AAMAS/authored_specifications" / name).read_text()
    )
    metadata = process["occurrence_net"]["metadata"]
    assert metadata["reference_harvest_policy"] in {
        "authored_harvest_calendar_v5",
        "authored_opening_harvest_v6",
    }
    assert set(metadata["reference_harvest_deadlines"]) == harvest_phases
    assert metadata["reference_harvest_deadline_rule"] == (
        "min(authored_phase_end,native_scenario_horizon)"
    )
    horizon = process["metadata"]["scenario_horizon"]
    assert metadata["scenario_horizon"] == horizon
    assert all(
        deadline <= horizon
        for deadline in metadata["reference_harvest_deadlines"].values()
    )


def test_agricultural_review_selection_records_required_case_coverage(
    tmp_path,
):
    repository = Path(__file__).parents[4]
    authored = repository / "AAMAS/authored_specifications"
    process_paths = tuple(
        authored / name
        for name in (
            "farm_wetjune_recheck.process.json",
            "farm_disease_drought.process.json",
            "farm_three_cultivar.process.json",
        )
    )
    output = tmp_path / "agricultural"
    build_agricultural_review_packets(process_paths, output)
    payload = json.loads((output / "packets.json").read_text(encoding="utf-8"))
    assert len(payload["packets"]) == 24
    for scenario, coverage in payload["coverage"].items():
        assert "required:postharvest" in coverage["required_tags"], scenario
        assert "mechanism:transition_order" in coverage["covered_tags"], scenario
        assert set(coverage["required_tags"]) <= set(coverage["covered_tags"]), scenario
        assert coverage["selection_method"].startswith("deterministic_stratified")
