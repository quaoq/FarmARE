from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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
from are.simulation.apps.farm_world.farm_world_app import plants_per_ridge_from_spacing
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.farm_checkpoint_state import (
    restore_farm_checkpoint_state,
)
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation

SOURCE_L3_SCENARIO_ID = (
    "scenario_full_season_hb_insect_after_fungicide_budget_conflict"
)
SOURCE_L3_PROFILE_NAME = "harbin_l3_insect_after_fungicide_budget_conflict_seed_1627"

CHECKPOINT_INITIAL_BEFORE_FIELD_PREP = "initial_before_field_prep"
CHECKPOINT_AFTER_PLANTING = "after_whole_field_planting"
CHECKPOINT_AFTER_MID_ROUTINE = "after_mid_routine_check"
CHECKPOINT_BEFORE_MID_FUNGICIDE = "before_o_mid_fungicide_0_18_39_action"
CHECKPOINT_AFTER_MID_FUNGICIDE = "after_o_mid_fungicide_0_18_39"
CHECKPOINT_AFTER_R5_ROUTINE = "after_r5_routine_check"
CHECKPOINT_AFTER_R5_INSECTICIDE = "after_o_r5_insecticide_0_24_47"
CHECKPOINT_HARVEST_R8_START = "o_wait_harvest_day_022"
CHECKPOINT_HARVEST_READY = "o_wait_harvest_day_033"
CHECKPOINT_AFTER_HARVEST = "after_whole_field_harvest"

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
BASE_FERTILIZER_KG = 360.0
RIDGE_WIDTH_M = 1.1
SEED_TYPE = "HEINONG84"
PLANT_DEPTH_CM = 4.0
SEED_SPACING_CM = 7.9


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


def populate_hb_insect_budget_apps(scenario) -> tuple[FarmWorldApp, WeatherApp]:
    aui = AgentUserInterface()
    farm_world = FarmWorldApp()
    weather = WeatherApp()
    sensor = SensorApp(farm_world_app=farm_world)
    mavic = DroneApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Mavic3M",
        description="DJI Mavic 3 Multispectral -- multispectral NDVI mapping drone",
        speed_ms=5.0,
        effective_ridges_per_pass=7,
        battery_pct_per_ridge=1.0,
    )
    robot = RobotApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Robot0",
        description="Zhiyuan D1 Max -- ground-level crop inspection robot",
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


def restore_hb_insect_budget_checkpoint(
    scenario, checkpoint_label: str
) -> dict[str, object]:
    farm_world = scenario.get_typed_app(FarmWorldApp)
    weather = scenario.get_typed_app(WeatherApp)
    path = CHECKPOINT_DIR / f"{checkpoint_label}.json"
    checkpoint_state = json.loads(path.read_text(encoding="utf-8"))
    return restore_farm_checkpoint_state(
        farm_world=farm_world,
        weather_app=weather,
        checkpoint_state=checkpoint_state,
        target_sim_time=float(checkpoint_state["sim_time"]),
    )


def apply_hb_insect_budget_planting_blocks(
    tractor: TractorApp,
    previous,
    prefix: str,
):
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


def apply_l3_fungicide_blocks(
    tractor: TractorApp,
    previous,
    prefix: str,
    *,
    liters_per_ridge: float = 3.4,
):
    current = previous
    events = []
    for start, end in ((18, 27), (28, 37), (38, 39)):
        event = (
            tractor.apply_fungicide(start, end, liters_per_ridge=liters_per_ridge)
            .oracle()
            .with_id(f"{prefix}_{start}_{end}")
            .depends_on(current, delay_seconds=2)
        )
        events.append(event)
        current = event
    return events, current


def apply_l3_manual_insecticide_blocks(
    field_ops: FieldOpsApp,
    previous,
    prefix: str,
    *,
    liters_per_ridge: float = 3.2,
):
    current = previous
    events = []
    for ridge_id in range(24, 48, 2):
        event = (
            field_ops.apply_pesticide_manual(
                ridge_id=ridge_id,
                liters_per_ridge=liters_per_ridge,
                advance_time=False,
                ridge_count=2,
            )
            .oracle()
            .with_id(f"{prefix}_{ridge_id}_{ridge_id + 1}")
            .depends_on(current, delay_seconds=2)
        )
        events.append(event)
        current = event
    return events, current


def validate_native_workflow(scenario, env, rationale: str) -> ScenarioValidationResult:
    error_returns = _completed_event_error_returns(env)
    if error_returns:
        return ScenarioValidationResult(
            success=False,
            rationale=f"{rationale}\ntool_error_returns={error_returns[:5]}",
        )
    result = ScenarioValidationResult(success=True, rationale=rationale)
    return append_workflow_evaluation(scenario, env, result)


def range_text(ranges: Iterable[tuple[int, int]]) -> str:
    return "; ".join(f"ridges {start}-{end}" for start, end in ranges)


def _completed_event_error_returns(env) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    events = getattr(env, "completed_events", None)
    if events is None and getattr(env, "event_log", None) is not None:
        events = env.event_log.list_view()
    for event in events or []:
        metadata = getattr(event, "metadata", None)
        if metadata is None and isinstance(event, dict):
            metadata = event.get("metadata")
        if isinstance(metadata, dict):
            return_value = metadata.get("return_value")
        else:
            return_value = getattr(metadata, "return_value", None)
        if return_value is None:
            continue
        parsed = return_value
        if isinstance(return_value, str):
            try:
                parsed = ast.literal_eval(return_value)
            except (SyntaxError, ValueError):
                parsed = return_value
        if isinstance(parsed, dict) and parsed.get("error"):
            event_id = getattr(event, "event_id", None)
            if event_id is None and isinstance(event, dict):
                event_id = event.get("event_id")
            errors.append({"event_id": str(event_id), "error": str(parsed["error"])})
    return errors
