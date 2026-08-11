from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    FieldOpsApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.farm_checkpoint_state import (
    restore_farm_checkpoint_state,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_catalog import (
    get_spec,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_scenario import (
    ScenarioAction,
    ScenarioSpec,
    init_batch_apps,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    HEINONG84_SPACING_CM,
    RIDGE_WIDTH_M,
    collect_event_graph,
    harvest_range,
    plant_range,
)
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.types import EventRegisterer


def cst_timestamp(year: int, month: int, day: int, hour: int = 8) -> float:
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600


def checkpoint_path(source_slug: str, checkpoint_label: str) -> Path:
    return Path(__file__).resolve().parent / source_slug / "checkpoints" / f"{checkpoint_label}.json"


def checkpoint_sim_time(source_slug: str, checkpoint_label: str) -> float:
    payload = json.loads(checkpoint_path(source_slug, checkpoint_label).read_text(encoding="utf-8"))
    return float(payload["sim_time"])


def checkpoint_date(source_slug: str, checkpoint_label: str) -> str:
    payload = json.loads(checkpoint_path(source_slug, checkpoint_label).read_text(encoding="utf-8"))
    sim_time = float(payload["sim_time"])
    return datetime.fromtimestamp(sim_time, tz=timezone.utc).date().isoformat()


def restore_batch_checkpoint(scenario: Scenario, spec: ScenarioSpec, source_slug: str, checkpoint_label: str) -> None:
    init_batch_apps(scenario, spec)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    weather = scenario.get_typed_app(WeatherApp)
    payload = json.loads(checkpoint_path(source_slug, checkpoint_label).read_text(encoding="utf-8"))
    restore_farm_checkpoint_state(
        farm_world=farm_world,
        weather_app=weather,
        checkpoint_state=payload,
        target_sim_time=float(payload["sim_time"]),
    )
    if checkpoint_label.startswith("before_") and checkpoint_label.endswith("_planting_action"):
        _sync_completed_field_prep_tracker(scenario)


def _sync_completed_field_prep_tracker(scenario: Scenario) -> None:
    tractor = scenario.get_typed_app(TractorApp)
    state = tractor.get_state()
    state["completed_prep_ops"] = ["level", "base_fertilize", "form_ridges"]
    tractor.load_state(state)


def validate_native_workflow(scenario: Scenario, env: Any, rationale: str) -> ScenarioValidationResult:
    errors = _completed_event_error_returns(env)
    if errors:
        return ScenarioValidationResult(success=False, rationale=f"{rationale}\ntool_error_returns={errors[:5]}")
    store_warnings = _completed_store_warning_returns(env)
    if store_warnings:
        return ScenarioValidationResult(success=False, rationale=f"{rationale}\nstore_warning_returns={store_warnings[:5]}")
    return append_workflow_evaluation(scenario, env, ScenarioValidationResult(success=True, rationale=rationale))


def _completed_events(env: Any) -> list[Any]:
    events = getattr(env, "completed_events", None)
    if events is None and getattr(env, "event_log", None) is not None:
        events = env.event_log.list_view()
    return list(events or [])


def _completed_event_return_value(event: Any) -> Any:
    metadata = getattr(event, "metadata", None)
    if metadata is None and isinstance(event, dict):
        metadata = event.get("metadata")
    return_value = metadata.get("return_value") if isinstance(metadata, dict) else getattr(metadata, "return_value", None)
    if isinstance(return_value, str):
        try:
            return ast.literal_eval(return_value)
        except (SyntaxError, ValueError):
            return return_value
    return return_value


def _completed_event_id(event: Any) -> str:
    event_id = getattr(event, "event_id", None)
    if event_id is None and isinstance(event, dict):
        event_id = event.get("event_id")
    return str(event_id)


def _completed_event_error_returns(env: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for event in _completed_events(env):
        parsed = _completed_event_return_value(event)
        if isinstance(parsed, dict) and parsed.get("error"):
            out.append({"event_id": _completed_event_id(event), "error": str(parsed["error"])})
    return out


def _completed_store_warning_returns(env: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for event in _completed_events(env):
        parsed = _completed_event_return_value(event)
        if not isinstance(parsed, dict):
            continue
        warning = parsed.get("warning") or parsed.get("warnings")
        event_id = _completed_event_id(event)
        is_store_event = "store" in event_id or (
            parsed.get("storage_grain_moisture_pct") is not None
            and parsed.get("max_storage_moisture_pct") is not None
        )
        if warning and is_store_event:
            out.append({"event_id": event_id, "warning": str(warning)})
    return out


def _weather_soil_tractor_checks(weather: WeatherApp, sensor: SensorApp, tractor: TractorApp, prev: Any, prefix: str) -> Any:
    prev = weather.get_current_weather().oracle().with_id(f"{prefix}_weather").depends_on(prev, delay_seconds=1)
    prev = weather.get_forecast(days=3).oracle().with_id(f"{prefix}_forecast").depends_on(prev, delay_seconds=1)
    prev = sensor.read_soil_sensors().oracle().with_id(f"{prefix}_soil").depends_on(prev, delay_seconds=1)
    return tractor.get_status().oracle().with_id(f"{prefix}_tractor_status").depends_on(prev, delay_seconds=1)


def _run_field_prep(weather: WeatherApp, sensor: SensorApp, farm_world: FarmWorldApp, tractor: TractorApp, prev: Any, spec: ScenarioSpec, prefix: str) -> Any:
    prev = weather.get_current_weather().oracle().with_id(f"{prefix}_weather_before_prep").depends_on(prev, delay_seconds=1)
    prev = weather.get_forecast(days=5).oracle().with_id(f"{prefix}_forecast_before_prep").depends_on(prev, delay_seconds=1)
    prev = sensor.read_soil_sensors().oracle().with_id(f"{prefix}_soil_before_prep").depends_on(prev, delay_seconds=1)
    prev = farm_world.get_inventory().oracle().with_id(f"{prefix}_inventory_before_prep").depends_on(prev, delay_seconds=1)
    prev = tractor.get_status().oracle().with_id(f"{prefix}_tractor_before_prep").depends_on(prev, delay_seconds=1)
    prev = tractor.attach_implement("grader").oracle().with_id(f"{prefix}_attach_grader").depends_on(prev, delay_seconds=1)
    prev = tractor.level().oracle().with_id(f"{prefix}_level_field").depends_on(prev, delay_seconds=2)
    prev = tractor.detach_implement().oracle().with_id(f"{prefix}_detach_grader").depends_on(prev, delay_seconds=1)
    prev = tractor.load_fertilizer(spec.base_fertilizer_kg).oracle().with_id(f"{prefix}_load_base_fertilizer").depends_on(prev, delay_seconds=1)
    prev = tractor.base_fertilize().oracle().with_id(f"{prefix}_apply_base_fertilizer").depends_on(prev, delay_seconds=2)
    prev = tractor.attach_implement("furrower").oracle().with_id(f"{prefix}_attach_furrower").depends_on(prev, delay_seconds=1)
    prev = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id(f"{prefix}_form_ridges").depends_on(prev, delay_seconds=2)
    return tractor.detach_implement().oracle().with_id(f"{prefix}_detach_furrower").depends_on(prev, delay_seconds=1)


def _plant_zone(weather: WeatherApp, sensor: SensorApp, tractor: TractorApp, prev: Any, zone: Any, prefix: str) -> Any:
    prev = _weather_soil_tractor_checks(weather, sensor, tractor, prev, f"{prefix}_plant_window")
    return plant_range(tractor, prev, start_ridge=zone.start, end_ridge=zone.end, seed_type=zone.seed_type, spacing_cm=zone.spacing_cm, id_prefix=prefix)


def _diagnose_management_action(
    weather: WeatherApp,
    sensor: SensorApp,
    farm_world: FarmWorldApp,
    mavic: DroneApp,
    matrice: DroneApp,
    robot: RobotApp,
    system: SystemApp,
    prev: Any,
    action: ScenarioAction,
    prefix: str,
    include_wait: bool,
) -> Any:
    prev = weather.get_current_weather().oracle().with_id(f"{prefix}_weather_before_action").depends_on(prev, delay_seconds=1)
    prev = weather.get_forecast(days=3).oracle().with_id(f"{prefix}_forecast_before_action").depends_on(prev, delay_seconds=1)
    if include_wait and action.target_wait_days > 0:
        prev = system.advance_time(days=action.target_wait_days).oracle().with_id(f"{prefix}_wait_for_target_window").depends_on(prev, delay_seconds=1)
        prev = weather.get_current_weather().oracle().with_id(f"{prefix}_weather_after_wait").depends_on(prev, delay_seconds=1)
        prev = weather.get_forecast(days=3).oracle().with_id(f"{prefix}_forecast_after_wait").depends_on(prev, delay_seconds=1)
    prev = sensor.read_soil_sensors().oracle().with_id(f"{prefix}_soil_before_action").depends_on(prev, delay_seconds=1)
    prev = sensor.read_canopy_sensors().oracle().with_id(f"{prefix}_canopy_before_action").depends_on(prev, delay_seconds=1)
    prev = farm_world.get_ridge_range_state(action.start, action.end).oracle().with_id(f"{prefix}_range_state_before_action").depends_on(prev, delay_seconds=1)
    prev = mavic.charge().oracle().with_id(f"{prefix}_charge_mavic").depends_on(prev, delay_seconds=1)
    prev = system.advance_time(hours=1).oracle().with_id(f"{prefix}_wait_mavic_charge").depends_on(prev, delay_seconds=1)
    prev = mavic.fly_survey(action.start, action.end).oracle().with_id(f"{prefix}_target_ndvi_survey").depends_on(prev, delay_seconds=2)
    if action.kind == "irrigation":
        prev = matrice.charge().oracle().with_id(f"{prefix}_charge_matrice").depends_on(prev, delay_seconds=1)
        prev = system.advance_time(hours=1).oracle().with_id(f"{prefix}_wait_matrice_charge").depends_on(prev, delay_seconds=1)
        prev = matrice.fly_survey(action.start, action.end).oracle().with_id(f"{prefix}_target_thermal_survey").depends_on(prev, delay_seconds=2)
    prev = robot.check_status().oracle().with_id(f"{prefix}_robot_status").depends_on(prev, delay_seconds=1)
    prev = robot.charge().oracle().with_id(f"{prefix}_charge_robot").depends_on(prev, delay_seconds=1)
    prev = system.advance_time(hours=1).oracle().with_id(f"{prefix}_wait_robot_charge").depends_on(prev, delay_seconds=1)
    if action.kind == "insecticide":
        return robot.inspect_pests(action.start, action.end).oracle().with_id(f"{prefix}_ground_pest_confirmation").depends_on(prev, delay_seconds=2)
    if action.kind == "replant":
        return robot.inspect_emergence(action.start, action.end).oracle().with_id(f"{prefix}_ground_emergence_confirmation").depends_on(prev, delay_seconds=2)
    return robot.inspect_crop_health(action.start, action.end).oracle().with_id(f"{prefix}_ground_health_confirmation").depends_on(prev, delay_seconds=2)


def _apply_management_action(
    farm_world: FarmWorldApp,
    field_ops: FieldOpsApp,
    tractor: TractorApp,
    system: SystemApp,
    prev: Any,
    action: ScenarioAction,
    spec: ScenarioSpec,
    prefix: str,
) -> Any:
    if action.kind == "fertigation":
        return farm_world.apply_fertigation(action.start, action.end, action.amount, action.water_mm).oracle().with_id(f"{prefix}_apply_fertigation").depends_on(prev, delay_seconds=2)
    if action.kind == "irrigation":
        prev = field_ops.irrigate(action.start, action.end, action.hours).oracle().with_id(f"{prefix}_irrigate").depends_on(prev, delay_seconds=2)
        return system.advance_time(hours=6).oracle().with_id(f"{prefix}_wait_irrigation_response").depends_on(prev, delay_seconds=1)
    if action.kind == "fungicide":
        prev = tractor.load_fungicide((action.end - action.start + 1) * action.liters_per_ridge * 1.02).oracle().with_id(f"{prefix}_load_fungicide").depends_on(prev, delay_seconds=1)
        for start in range(action.start, action.end + 1, 10):
            end = min(start + 9, action.end)
            prev = tractor.apply_fungicide(start, end, action.liters_per_ridge).oracle().with_id(f"{prefix}_fungicide_{start}_{end}").depends_on(prev, delay_seconds=2)
        return prev
    if action.kind == "insecticide":
        if action.manual:
            for ridge_id in range(action.start, action.end + 1, 2):
                ridge_count = min(2, action.end - ridge_id + 1)
                prev = field_ops.apply_pesticide_manual(ridge_id=ridge_id, liters_per_ridge=action.liters_per_ridge, advance_time=False, ridge_count=ridge_count).oracle().with_id(f"{prefix}_manual_insecticide_{ridge_id}_{ridge_id + ridge_count - 1}").depends_on(prev, delay_seconds=2)
            return prev
        prev = tractor.load_pesticide((action.end - action.start + 1) * action.liters_per_ridge * 1.02).oracle().with_id(f"{prefix}_load_insecticide").depends_on(prev, delay_seconds=1)
        for start in range(action.start, action.end + 1, 10):
            end = min(start + 9, action.end)
            prev = tractor.spray_pesticide(start, end, action.liters_per_ridge).oracle().with_id(f"{prefix}_insecticide_{start}_{end}").depends_on(prev, delay_seconds=2)
        return prev
    if action.kind == "herbicide":
        prev = tractor.load_pesticide((action.end - action.start + 1) * action.liters_per_ridge * 1.02).oracle().with_id(f"{prefix}_load_herbicide").depends_on(prev, delay_seconds=1)
        for start in range(action.start, action.end + 1, 10):
            end = min(start + 9, action.end)
            prev = tractor.apply_herbicide(start, end, action.liters_per_ridge).oracle().with_id(f"{prefix}_herbicide_{start}_{end}").depends_on(prev, delay_seconds=2)
        return prev
    if action.kind == "mechanical_weed":
        prev = tractor.attach_implement("cultivator").oracle().with_id(f"{prefix}_attach_cultivator").depends_on(prev, delay_seconds=1)
        for start in range(action.start, action.end + 1, 10):
            end = min(start + 9, action.end)
            prev = tractor.mechanical_weed_control(start, end).oracle().with_id(f"{prefix}_mechanical_weed_{start}_{end}").depends_on(prev, delay_seconds=2)
        return tractor.detach_implement().oracle().with_id(f"{prefix}_detach_cultivator").depends_on(prev, delay_seconds=1)
    if action.kind == "replant":
        spacing = (spec.planting_zones[0].spacing_cm if spec.planting_zones else HEINONG84_SPACING_CM)
        for start in range(action.start, action.end + 1, 4):
            end = min(start + 3, action.end)
            prev = tractor.load_seeds(spec.primary_seed, 100000).oracle().with_id(f"{prefix}_load_replant_seed_{start}_{end}").depends_on(prev, delay_seconds=1)
            prev = tractor.replant_seeds(start, end, 4.0, spacing).oracle().with_id(f"{prefix}_replant_{start}_{end}").depends_on(prev, delay_seconds=2)
        return prev
    raise ValueError(f"Unsupported action kind: {action.kind}")


def build_establishment_l2_flow(scenario: Scenario, spec: ScenarioSpec, briefing_text: str) -> None:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    system = scenario.get_typed_app(SystemApp)
    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
        prev = _run_field_prep(weather, sensor, farm_world, tractor, briefing, spec, "o_establishment")
        for zone in spec.planting_zones or ():
            if zone.wait_days_before > 0:
                prev = system.advance_time(days=zone.wait_days_before).oracle().with_id(f"o_wait_before_{zone.label}_planting").depends_on(prev, delay_seconds=1)
            prev = _plant_zone(weather, sensor, tractor, prev, zone, f"o_{zone.label}")
            prev = farm_world.commit_daily_physics().oracle().with_id(f"o_commit_{zone.label}_planting").depends_on(prev, delay_seconds=1)
        if not spec.planting_zones:
            from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_scenario import PlantingZone
            zone = PlantingZone("whole_field", 0, 63, spec.primary_seed)
            prev = _plant_zone(weather, sensor, tractor, prev, zone, "o_whole_field")
            prev = farm_world.commit_daily_physics().oracle().with_id("o_commit_whole_field_planting").depends_on(prev, delay_seconds=1)
        prev = farm_world.get_farm_overview().oracle().with_id("o_recheck_established_field").depends_on(prev, delay_seconds=1)
        aui.send_message_to_user(content="Completed establishment and planting split.").oracle().with_id("o_report").depends_on(prev, delay_seconds=1)
    scenario.events = collect_event_graph(briefing)


def build_planting_l1_flow(scenario: Scenario, spec: ScenarioSpec, zone_label: str, briefing_text: str) -> None:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    zones = list(spec.planting_zones) or []
    if not zones:
        from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_scenario import PlantingZone
        zones = [PlantingZone("whole_field", 0, 63, spec.primary_seed)]
    zone = next(z for z in zones if z.label == zone_label)
    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
        prev = _plant_zone(weather, sensor, tractor, briefing, zone, f"o_{zone.label}")
        prev = farm_world.commit_daily_physics().oracle().with_id(f"o_commit_{zone.label}_planting").depends_on(prev, delay_seconds=1)
        prev = farm_world.get_farm_overview().oracle().with_id("o_recheck_planted_field").depends_on(prev, delay_seconds=1)
        aui.send_message_to_user(content="Completed planting execution split.").oracle().with_id("o_report").depends_on(prev, delay_seconds=1)
    scenario.events = collect_event_graph(briefing)


def build_management_l2_flow(scenario: Scenario, spec: ScenarioSpec, action: ScenarioAction, briefing_text: str, prefix: str) -> None:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    mavic = scenario.get_typed_app(DroneApp, "Mavic3M")
    matrice = scenario.get_typed_app(DroneApp, "Matrice4T")
    robot = scenario.get_typed_app(RobotApp, "Robot0")
    tractor = scenario.get_typed_app(TractorApp)
    field_ops = scenario.get_typed_app(FieldOpsApp)
    system = scenario.get_typed_app(SystemApp)
    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
        prev = farm_world.get_farm_overview().oracle().with_id(f"{prefix}_farm_overview").depends_on(briefing, delay_seconds=1)
        prev = _diagnose_management_action(weather, sensor, farm_world, mavic, matrice, robot, system, prev, action, prefix, include_wait=True)
        prev = farm_world.get_inventory().oracle().with_id(f"{prefix}_inventory_before_action").depends_on(prev, delay_seconds=1)
        prev = tractor.get_status().oracle().with_id(f"{prefix}_tractor_status_before_action").depends_on(prev, delay_seconds=1)
        prev = _apply_management_action(farm_world, field_ops, tractor, system, prev, action, spec, prefix)
        prev = farm_world.get_ridge_range_state(action.start, action.end).oracle().with_id(f"{prefix}_recheck_target_after_action").depends_on(prev, delay_seconds=2)
        aui.send_message_to_user(content="Completed management closed-loop split.").oracle().with_id("o_report").depends_on(prev, delay_seconds=1)
    scenario.events = collect_event_graph(briefing)


def build_management_l1_flow(
    scenario: Scenario,
    spec: ScenarioSpec,
    action: ScenarioAction,
    briefing_text: str,
    prefix: str,
    report_text: str = "Completed action-ready management split.",
) -> None:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    field_ops = scenario.get_typed_app(FieldOpsApp)
    system = scenario.get_typed_app(SystemApp)
    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
        prev = weather.get_current_weather().oracle().with_id(f"{prefix}_weather_ready_check").depends_on(briefing, delay_seconds=1)
        prev = weather.get_forecast(days=3).oracle().with_id(f"{prefix}_forecast_ready_check").depends_on(prev, delay_seconds=1)
        prev = sensor.read_soil_sensors().oracle().with_id(f"{prefix}_soil_ready_check").depends_on(prev, delay_seconds=1)
        prev = sensor.read_canopy_sensors().oracle().with_id(f"{prefix}_canopy_ready_check").depends_on(prev, delay_seconds=1)
        prev = farm_world.get_ridge_range_state(action.start, action.end).oracle().with_id(f"{prefix}_target_ready_state").depends_on(prev, delay_seconds=1)
        prev = farm_world.get_inventory().oracle().with_id(f"{prefix}_inventory_ready_check").depends_on(prev, delay_seconds=1)
        prev = tractor.get_status().oracle().with_id(f"{prefix}_tractor_ready_check").depends_on(prev, delay_seconds=1)
        prev = _apply_management_action(farm_world, field_ops, tractor, system, prev, action, spec, prefix)
        prev = farm_world.get_ridge_range_state(action.start, action.end).oracle().with_id(f"{prefix}_recheck_after_action").depends_on(prev, delay_seconds=2)
        aui.send_message_to_user(content=report_text).oracle().with_id("o_report").depends_on(prev, delay_seconds=1)
    scenario.events = collect_event_graph(briefing)


def build_harvest_l2_flow(scenario: Scenario, spec: ScenarioSpec, ready_wait_days: int, briefing_text: str) -> None:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    system = scenario.get_typed_app(SystemApp)
    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
        prev = weather.get_current_weather().oracle().with_id("o_harvest_window_weather_start").depends_on(briefing, delay_seconds=1)
        prev = weather.get_forecast(days=3).oracle().with_id("o_harvest_window_forecast_start").depends_on(prev, delay_seconds=1)
        prev = farm_world.get_farm_overview().oracle().with_id("o_harvest_window_farm_overview_start").depends_on(prev, delay_seconds=1)
        prev = sensor.read_soil_sensors().oracle().with_id("o_harvest_window_soil_start").depends_on(prev, delay_seconds=1)
        if ready_wait_days > 0:
            prev = system.advance_time(days=ready_wait_days).oracle().with_id("o_wait_to_harvest_ready_window").depends_on(prev, delay_seconds=1)
        for label, start, end in spec.harvest_zones:
            zwait = int(spec.harvest_zone_waits.get(label, 0))
            if zwait:
                prev = system.advance_time(days=zwait).oracle().with_id(f"o_wait_{label}_harvest_zone").depends_on(prev, delay_seconds=1)
            prev = weather.get_current_weather().oracle().with_id(f"o_{label}_harvest_weather").depends_on(prev, delay_seconds=1)
            prev = weather.get_forecast(days=3).oracle().with_id(f"o_{label}_harvest_forecast").depends_on(prev, delay_seconds=1)
            prev = farm_world.get_ridge_range_state(start, end).oracle().with_id(f"o_{label}_harvest_range_state").depends_on(prev, delay_seconds=1)
            prev = harvest_range(tractor, farm_world, prev, start_ridge=start, end_ridge=end, id_prefix=f"o_{label}", dry_after_harvest=label in spec.postharvest_drying_zones)
        aui.send_message_to_user(content="Completed harvest-window split.").oracle().with_id("o_report").depends_on(prev, delay_seconds=1)
    scenario.events = collect_event_graph(briefing)


def build_harvest_l1_flow(
    scenario: Scenario,
    spec: ScenarioSpec,
    briefing_text: str,
    harvest_zone_labels: tuple[str, ...] | None = None,
) -> None:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    harvest_zones = tuple(
        zone
        for zone in spec.harvest_zones
        if harvest_zone_labels is None or zone[0] in harvest_zone_labels
    )
    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
        prev = weather.get_current_weather().oracle().with_id("o_harvest_ready_weather").depends_on(briefing, delay_seconds=1)
        prev = weather.get_forecast(days=3).oracle().with_id("o_harvest_ready_forecast").depends_on(prev, delay_seconds=1)
        prev = farm_world.get_farm_overview().oracle().with_id("o_harvest_ready_overview").depends_on(prev, delay_seconds=1)
        for label, start, end in harvest_zones:
            prev = farm_world.get_ridge_range_state(start, end).oracle().with_id(f"o_{label}_ready_range_state").depends_on(prev, delay_seconds=1)
            prev = harvest_range(tractor, farm_world, prev, start_ridge=start, end_ridge=end, id_prefix=f"o_{label}", dry_after_harvest=label in spec.postharvest_drying_zones)
        aui.send_message_to_user(content="Completed harvest-ready split.").oracle().with_id("o_report").depends_on(prev, delay_seconds=1)
    scenario.events = collect_event_graph(briefing)
