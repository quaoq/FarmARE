from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

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
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.farm_checkpoint_state import (
    restore_farm_checkpoint_state,
)
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation

SOURCE_L3_SCENARIO_ID = "scenario_full_season_heinong84_staggered_planting"
SOURCE_L3_PROFILE_NAME = "harbin_heinong84_staggered_planting_seed_616"

SEED_TYPE = "HEINONG84"
RIDGE_WIDTH_M = 1.1
STANDARD_SPACING_CM = 7.9

EARLY_START = 0
EARLY_END = 20
MID_START = 21
MID_END = 42
LATE_START = 43
LATE_END = 63

CHECKPOINT_INITIAL_BEFORE_FIELD_PREP = "initial_before_field_prep"
CHECKPOINT_AFTER_PREP = "after_prep_and_base_fertilizer"
CHECKPOINT_AFTER_EARLY_PLANTING = "after_early_zone_planting"
CHECKPOINT_MID_PLANTING_READY = "o_wait_mid_planting_window_day_007"
CHECKPOINT_AFTER_MID_PLANTING = "after_mid_zone_planting"
CHECKPOINT_LATE_PLANTING_READY = "o_wait_late_planting_window_day_007"
CHECKPOINT_AFTER_LATE_PLANTING = "after_late_zone_planting"
CHECKPOINT_EMERGENCE_READY = "o_wait_whole_field_emergence_day_016"
CHECKPOINT_STAGE_SPLIT_READY = "o_wait_stage_split_scout_day_042"
CHECKPOINT_EARLY_HARVEST_R8_START = "o_wait_early_harvest_window_day_037"
CHECKPOINT_EARLY_HARVEST_READY = "o_wait_early_harvest_window_day_048"
CHECKPOINT_AFTER_EARLY_HARVEST = "after_early_zone_harvest_store"
CHECKPOINT_MID_HARVEST_READY = "o_wait_mid_harvest_window_day_001"
CHECKPOINT_AFTER_MID_HARVEST = "after_mid_zone_harvest_store"
CHECKPOINT_LATE_HARVEST_READY = "o_wait_late_harvest_window_day_014"

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


def populate_hn84_staggered_apps(scenario) -> tuple[FarmWorldApp, WeatherApp]:
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
        description="Ground inspection robot",
    )
    tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
    field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
    system = SystemApp()

    scenario.apps = [
        aui,
        farm_world,
        weather,
        sensor,
        mavic,
        robot,
        tractor,
        field_ops,
        system,
    ]
    farm_world.attach_system_app(system)
    return farm_world, weather


def restore_hn84_staggered_checkpoint(
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


def apply_hn84_planting_blocks(
    tractor: TractorApp,
    previous,
    prefix: str,
    start_ridge: int,
    end_ridge: int,
):
    events = []
    current = (
        tractor.load_seeds(SEED_TYPE, 180000)
        .oracle()
        .with_id(f"{prefix}_load_seed")
        .depends_on(previous, delay_seconds=2)
    )
    events.append(current)
    for start in range(start_ridge, end_ridge + 1, 4):
        end = min(start + 3, end_ridge)
        current = (
            tractor.plant_seeds(start, end, 4.0, STANDARD_SPACING_CM)
            .oracle()
            .with_id(f"{prefix}_plant_{start}_{end}")
            .depends_on(current, delay_seconds=2)
        )
        events.append(current)
    return events, current


def apply_hn84_harvest_zone(
    tractor: TractorApp,
    farm_world: FarmWorldApp,
    previous,
    prefix: str,
    start_ridge: int,
    end_ridge: int,
):
    events = []
    current = previous
    for start in range(start_ridge, end_ridge + 1, 4):
        end = min(start + 3, end_ridge)
        current = (
            tractor.harvest(start, end)
            .oracle()
            .with_id(f"{prefix}_harvest_{start}_{end}")
            .depends_on(current, delay_seconds=2)
        )
        events.append(current)
        current = (
            tractor.unload_grain()
            .oracle()
            .with_id(f"{prefix}_unload_after_{end}")
            .depends_on(current, delay_seconds=1)
        )
        events.append(current)
    current = (
        farm_world.dry_grain(target_moisture_pct=13.0)
        .oracle()
        .with_id(f"{prefix}_dry_grain")
        .depends_on(current, delay_seconds=2)
    )
    events.append(current)
    current = (
        farm_world.store_grain()
        .oracle()
        .with_id(f"{prefix}_store_grain")
        .depends_on(current, delay_seconds=2)
    )
    events.append(current)
    return events, current


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
