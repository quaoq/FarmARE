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
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.farm_checkpoint_state import (
    restore_farm_checkpoint_state,
)
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation

SOURCE_L3_SCENARIO_ID = "scenario_full_season_hb_fertilizer_quota_edge_lowfertility"
SOURCE_L3_PROFILE_NAME = "harbin_l3_fertilizer_quota_edge_lowfertility_seed_1626"

CHECKPOINT_AFTER_EMERGENCE_ROUTINE = "after_emergence_routine_check"
CHECKPOINT_BEFORE_SEVERE_EDGE_FERTIGATION = "before_o_emergence_fertigation_0_0_7_action"
CHECKPOINT_AFTER_SEVERE_EDGE_FERTIGATION = "after_o_emergence_fertigation_0_0_7"
CHECKPOINT_BEFORE_SEVERE_EDGE_REPLANT = "before_o_emergence_replant_1_0_3_action"
CHECKPOINT_AFTER_R1_ROUTINE = "after_r1_routine_check"
CHECKPOINT_BEFORE_MILD_EDGE_FERTIGATION = "before_o_r1_fertigation_0_8_15_action"
CHECKPOINT_HARVEST_R8_START = "o_wait_harvest_day_015"
CHECKPOINT_HARVEST_READY = "o_wait_harvest_day_028"

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


def populate_hb_fertilizer_quota_apps(scenario) -> tuple[FarmWorldApp, WeatherApp]:
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


def restore_hb_fertilizer_quota_checkpoint(
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


def apply_heilong84_replant_block(
    tractor: TractorApp,
    previous,
    prefix: str,
    *,
    start_ridge: int = 0,
    end_ridge: int = 3,
):
    load = (
        tractor.load_seeds("HEINONG84", 30000)
        .oracle()
        .with_id(f"{prefix}_load_seed_{start_ridge}_{end_ridge}")
        .depends_on(previous, delay_seconds=1)
    )
    replant = (
        tractor.replant_seeds(start_ridge, end_ridge, 4.0, 7.9, False)
        .oracle()
        .with_id(f"{prefix}_replant_{start_ridge}_{end_ridge}")
        .depends_on(load, delay_seconds=2)
    )
    return [load, replant], replant


def validate_native_workflow(scenario, env, rationale: str) -> ScenarioValidationResult:
    error_returns = _completed_event_error_returns(env)
    if error_returns:
        return ScenarioValidationResult(
            success=False,
            rationale=f"{rationale}\ntool_error_returns={error_returns[:5]}",
        )
    result = ScenarioValidationResult(success=True, rationale=rationale)
    return append_workflow_evaluation(scenario, env, result)


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
