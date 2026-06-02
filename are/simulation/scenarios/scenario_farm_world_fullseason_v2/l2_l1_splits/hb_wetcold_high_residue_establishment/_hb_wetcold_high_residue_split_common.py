from __future__ import annotations

import ast
import json
from pathlib import Path

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.farm_checkpoint_state import (
    restore_farm_checkpoint_state,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    HEINONG84_SPACING_CM,
    RIDGE_WIDTH_M,
)
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation

SOURCE_L3_SCENARIO_ID = "scenario_full_season_hb_wetcold_high_residue_establishment"
SOURCE_L3_PROFILE_NAME = "harbin_l3_wetcold_high_residue_seed_1514"

SEED_TYPE = "HEINONG84"
SEED_SPACING_CM = HEINONG84_SPACING_CM
PLANT_DEPTH_CM = 4.0
BASE_FERTILIZER_KG = 360.0

CHECKPOINT_INITIAL_BEFORE_FIELD_PREP = "initial_before_field_prep"
CHECKPOINT_BEFORE_PLANTING = "before_whole_field_residue_planting_action"
CHECKPOINT_AFTER_PLANTING = "after_whole_field_residue_planting"
CHECKPOINT_AFTER_EMERGENCE_ROUTINE = "after_emergence_routine_check"
CHECKPOINT_BEFORE_REPLANT = "before_o_emergence_replant_0_0_15_action"
CHECKPOINT_AFTER_REPLANT = "after_o_emergence_replant_0_0_15"
CHECKPOINT_HARVEST_R8_START = "o_wait_harvest_day_034"
CHECKPOINT_REPLANTED_HARVEST_READY = "o_wait_replanted_0_15_harvest_zone_day_021"

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"


def checkpoint_sim_time(checkpoint_label: str) -> float:
    checkpoint_state = json.loads(
        (CHECKPOINT_DIR / f"{checkpoint_label}.json").read_text(encoding="utf-8")
    )
    return float(checkpoint_state["sim_time"])


def populate_hb_wetcold_apps(scenario) -> tuple[FarmWorldApp, WeatherApp]:
    aui = AgentUserInterface()
    farm_world = FarmWorldApp()
    weather = WeatherApp()
    sensor = SensorApp(farm_world_app=farm_world)
    mavic = DroneApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Mavic3M",
        description="DJI Mavic 3 Multispectral - multispectral NDVI mapping drone",
        speed_ms=5.0,
        effective_ridges_per_pass=7,
        battery_pct_per_ridge=1.0,
    )
    robot = RobotApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Robot0",
        description="Ground-level crop inspection robot",
    )
    tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
    system = SystemApp()
    scenario.apps = [aui, farm_world, weather, sensor, mavic, robot, tractor, system]
    farm_world.attach_system_app(system)
    return farm_world, weather


def restore_hb_wetcold_checkpoint(
    scenario, checkpoint_label: str
) -> dict[str, object]:
    farm_world = scenario.get_typed_app(FarmWorldApp)
    weather = scenario.get_typed_app(WeatherApp)
    checkpoint_state = json.loads(
        (CHECKPOINT_DIR / f"{checkpoint_label}.json").read_text(encoding="utf-8")
    )
    return restore_farm_checkpoint_state(
        farm_world=farm_world,
        weather_app=weather,
        checkpoint_state=checkpoint_state,
        target_sim_time=float(checkpoint_state["sim_time"]),
    )


def sync_field_prep_complete(tractor: TractorApp) -> None:
    tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]


def apply_heinong84_planting_blocks(
    tractor: TractorApp,
    previous,
    prefix: str,
    *,
    start_ridge: int = 0,
    end_ridge: int = 63,
):
    current = previous
    events = []
    for start in range(start_ridge, end_ridge + 1, 4):
        end = min(start + 3, end_ridge)
        load = (
            tractor.load_seeds(SEED_TYPE, 300000)
            .oracle()
            .with_id(f"{prefix}_load_seed_before_{start}_{end}")
            .depends_on(current, delay_seconds=1)
        )
        plant = (
            tractor.plant_seeds(start, end, PLANT_DEPTH_CM, SEED_SPACING_CM)
            .oracle()
            .with_id(f"{prefix}_plant_{start}_{end}")
            .depends_on(load, delay_seconds=2)
        )
        events.extend([load, plant])
        current = plant
    return events, current


def apply_replant_blocks(
    tractor: TractorApp,
    previous,
    prefix: str,
    *,
    start_ridge: int = 0,
    end_ridge: int = 15,
):
    load = (
        tractor.load_seeds(SEED_TYPE, 200000)
        .oracle()
        .with_id(f"{prefix}_load_replant_seed")
        .depends_on(previous, delay_seconds=1)
    )
    current = load
    events = [load]
    for start in range(start_ridge, end_ridge + 1, 4):
        end = min(start + 3, end_ridge)
        replant = (
            tractor.replant_seeds(start, end, PLANT_DEPTH_CM, SEED_SPACING_CM)
            .oracle()
            .with_id(f"{prefix}_replant_{start}_{end}")
            .depends_on(current, delay_seconds=2)
        )
        events.append(replant)
        current = replant
    return events, current


def apply_harvest_store_range(
    tractor: TractorApp,
    farm_world: FarmWorldApp,
    previous,
    prefix: str,
    *,
    start_ridge: int,
    end_ridge: int,
    dry_after_harvest: bool,
):
    current = previous
    events = []
    for start in range(start_ridge, end_ridge + 1, 4):
        end = min(start + 3, end_ridge)
        harvest = (
            tractor.harvest(start, end)
            .oracle()
            .with_id(f"{prefix}_harvest_{start}_{end}")
            .depends_on(current, delay_seconds=2)
        )
        unload = (
            tractor.unload_grain()
            .oracle()
            .with_id(f"{prefix}_unload_after_{end}")
            .depends_on(harvest, delay_seconds=1)
        )
        events.extend([harvest, unload])
        current = unload
    if dry_after_harvest:
        dry = (
            farm_world.dry_grain(target_moisture_pct=13.0)
            .oracle()
            .with_id(f"{prefix}_dry_grain")
            .depends_on(current, delay_seconds=2)
        )
        events.append(dry)
        current = dry
    store = (
        farm_world.store_grain()
        .oracle()
        .with_id(f"{prefix}_store_grain")
        .depends_on(current, delay_seconds=2)
    )
    events.append(store)
    return events, store


def validate_native_workflow(scenario, env, rationale: str) -> ScenarioValidationResult:
    error_returns = _completed_event_error_returns(env)
    if error_returns:
        return ScenarioValidationResult(
            success=False,
            rationale=f"{rationale}\ntool_error_returns={error_returns[:5]}",
        )
    store_warnings = _completed_store_warning_returns(env)
    if store_warnings:
        return ScenarioValidationResult(
            success=False,
            rationale=f"{rationale}\nstore_warning_returns={store_warnings[:5]}",
        )
    result = ScenarioValidationResult(success=True, rationale=rationale)
    return append_workflow_evaluation(scenario, env, result)


def _completed_event_error_returns(env) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for event in _completed_events(env):
        parsed = _completed_event_return_value(event)
        if isinstance(parsed, dict) and parsed.get("error"):
            errors.append(
                {"event_id": _completed_event_id(event), "error": str(parsed["error"])}
            )
    return errors


def _completed_store_warning_returns(env) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
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
            warnings.append({"event_id": event_id, "warning": str(warning)})
    return warnings


def _completed_events(env) -> list[object]:
    events = getattr(env, "completed_events", None)
    if events is None and getattr(env, "event_log", None) is not None:
        events = env.event_log.list_view()
    return list(events or [])


def _completed_event_return_value(event):
    metadata = getattr(event, "metadata", None)
    if metadata is None and isinstance(event, dict):
        metadata = event.get("metadata")
    if isinstance(metadata, dict):
        return_value = metadata.get("return_value")
    else:
        return_value = getattr(metadata, "return_value", None)
    if return_value is None:
        return None
    if isinstance(return_value, str):
        try:
            return ast.literal_eval(return_value)
        except (SyntaxError, ValueError):
            return return_value
    return return_value


def _completed_event_id(event) -> str:
    event_id = getattr(event, "event_id", None)
    if event_id is None and isinstance(event, dict):
        event_id = event.get("event_id")
    return str(event_id)
