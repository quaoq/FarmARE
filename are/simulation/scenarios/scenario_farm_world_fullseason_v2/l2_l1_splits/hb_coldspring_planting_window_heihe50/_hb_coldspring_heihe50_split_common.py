from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
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
from are.simulation.apps.farm_world.farm_world_app import plants_per_ridge_from_spacing
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.farm_checkpoint_state import (
    restore_farm_checkpoint_state,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    RIDGE_WIDTH_M,
)
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation

SOURCE_L3_SCENARIO_ID = "scenario_full_season_hb_coldspring_planting_window_heihe50"
SOURCE_L3_PROFILE_NAME = "harbin_l3_heihe50_coldspring_seed_1513"

SEED_TYPE = "HEIHE50"
SEED_SPACING_CM = 8.2
PLANT_DEPTH_CM = 4.0
BASE_FERTILIZER_KG = 360.0

CHECKPOINT_INITIAL_BEFORE_FIELD_PREP = "initial_before_field_prep"
CHECKPOINT_BEFORE_PLANTING = "before_whole_field_planting_action"
CHECKPOINT_AFTER_PLANTING = "after_whole_field_planting"
CHECKPOINT_AFTER_EMERGENCE_ROUTINE = "after_emergence_routine_check"
CHECKPOINT_HARVEST_R8_START = "o_wait_harvest_day_020"
CHECKPOINT_HARVEST_DIRECT_STORE_READY = "o_wait_harvest_day_036"

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"


def cst_timestamp(year: int, month: int, day: int, hour: int = 8) -> float:
    return (
        datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).timestamp()
        - 8 * 3600
    )


def checkpoint_sim_time(checkpoint_label: str) -> float:
    checkpoint_state = json.loads(
        (CHECKPOINT_DIR / f"{checkpoint_label}.json").read_text(encoding="utf-8")
    )
    return float(checkpoint_state["sim_time"])


def populate_hb_coldspring_heihe50_apps(scenario) -> tuple[FarmWorldApp, WeatherApp]:
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


def restore_hb_coldspring_heihe50_checkpoint(
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


def apply_heihe50_planting_blocks(tractor: TractorApp, previous, prefix: str):
    current = previous
    events = []
    seeds_per_ridge = plants_per_ridge_from_spacing(SEED_SPACING_CM)
    blocks = [(start, min(start + 3, 63)) for start in range(0, 64, 4)]
    remaining_seed_need = sum((end - start + 1) * seeds_per_ridge for start, end in blocks)
    hopper_estimate = 0
    for start in range(0, 64, 4):
        end = min(start + 3, 63)
        block_seed_need = (end - start + 1) * seeds_per_ridge
        if hopper_estimate < block_seed_need:
            load_count = min(300000 - hopper_estimate, remaining_seed_need)
            load = (
                tractor.load_seeds(SEED_TYPE, load_count)
                .oracle()
                .with_id(f"{prefix}_load_seed_before_{start}_{end}")
                .depends_on(current, delay_seconds=1)
            )
            events.append(load)
            current = load
            hopper_estimate += load_count
        plant = (
            tractor.plant_seeds(start, end, PLANT_DEPTH_CM, SEED_SPACING_CM)
            .oracle()
            .with_id(f"{prefix}_plant_{start}_{end}")
            .depends_on(current, delay_seconds=2)
        )
        events.append(plant)
        current = plant
        hopper_estimate -= block_seed_need
        remaining_seed_need -= block_seed_need
    return events, current


def apply_heihe50_harvest_direct_store(
    tractor: TractorApp,
    farm_world: FarmWorldApp,
    previous,
    prefix: str,
):
    current = previous
    events = []
    for start in range(0, 64, 4):
        end = min(start + 3, 63)
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
