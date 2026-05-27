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

SOURCE_L3_SCENARIO_ID = (
    "scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence"
)
SOURCE_L3_PROFILE_NAME = "harbin_l3_three_cultivar_wet_disease_dry_harvest_seed_1904"

CHECKPOINT_INITIAL_BEFORE_FIELD_PREP = "initial_before_field_prep"
CHECKPOINT_AFTER_MID_ROUTINE = "after_mid_routine_check"
CHECKPOINT_BEFORE_MID_FUNGICIDE = "before_o_mid_fungicide_0_21_42_action"
CHECKPOINT_AFTER_MID_FUNGICIDE = "after_o_mid_fungicide_0_21_42"
CHECKPOINT_AFTER_R5_ROUTINE = "after_r5_routine_check"
CHECKPOINT_BEFORE_R5_IRRIGATION = "before_o_r5_irrigation_0_43_63_action"
CHECKPOINT_AFTER_R5_IRRIGATION = "after_o_r5_irrigation_0_43_63"
CHECKPOINT_ZONE_A_HARVEST_READY = "o_wait_harvest_day_016"
CHECKPOINT_AFTER_ZONE_A_HARVEST = "after_zone_a_heihe50_0_20_harvest"
CHECKPOINT_ZONE_B_HARVEST_READY = "o_wait_zone_b_heinong84_21_42_harvest_zone_day_014"
CHECKPOINT_AFTER_ZONE_B_HARVEST = "after_zone_b_heinong84_21_42_harvest"
CHECKPOINT_ZONE_C_HARVEST_READY = "o_wait_zone_c_heinong58_43_63_harvest_zone_day_012"
CHECKPOINT_AFTER_ZONE_C_HARVEST = "after_zone_c_heinong58_43_63_harvest"

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"


def cst_timestamp(year: int, month: int, day: int, hour: int = 8) -> float:
    return (
        datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).timestamp()
        - 8 * 3600
    )


def populate_three_cultivar_apps(scenario) -> tuple[FarmWorldApp, WeatherApp]:
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
    matrice = DroneApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Matrice4T",
        description="DJI Matrice 4T -- thermal imaging drone",
        speed_ms=4.0,
        effective_ridges_per_pass=5,
        battery_pct_per_ridge=1.5,
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
        matrice,
        robot,
        tractor,
        field_ops,
        system,
    ]
    farm_world.attach_system_app(system)
    return farm_world, weather


def restore_three_cultivar_checkpoint(
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


def apply_hn84_fungicide_blocks(
    tractor: TractorApp,
    previous,
    prefix: str,
    *,
    liters_per_ridge: float = 3.8,
):
    current = previous
    events = []
    for start, end in ((21, 30), (31, 40), (41, 42)):
        event = (
            tractor.apply_fungicide(start, end, liters_per_ridge=liters_per_ridge)
            .oracle()
            .with_id(f"{prefix}_{start}_{end}")
            .depends_on(current, delay_seconds=2)
        )
        events.append(event)
        current = event
    return events, current


def validate_native_workflow(
    scenario, env, rationale: str
) -> ScenarioValidationResult:
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
