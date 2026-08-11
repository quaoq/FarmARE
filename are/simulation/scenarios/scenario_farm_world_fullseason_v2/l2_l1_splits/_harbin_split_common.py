from __future__ import annotations

import ast
import json
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
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.farm_checkpoint_state import (
    restore_farm_checkpoint_state,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    plant_range,
)
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation


def checkpoint_sim_time(checkpoint_dir: Path, checkpoint_label: str) -> float:
    checkpoint_state = json.loads(
        (checkpoint_dir / f"{checkpoint_label}.json").read_text(encoding="utf-8")
    )
    return float(checkpoint_state["sim_time"])


def populate_standard_split_apps(scenario) -> tuple[FarmWorldApp, WeatherApp]:
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
    matrice = DroneApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Matrice300",
        description="DJI Matrice 300 RTK - thermal/canopy scouting drone",
        speed_ms=4.0,
        effective_ridges_per_pass=5,
        battery_pct_per_ridge=1.2,
    )
    robot = RobotApp(
        farm_world_app=farm_world,
        weather_app=weather,
        name="Robot0",
        description="Ground-level crop inspection robot",
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


def restore_split_checkpoint(
    scenario, checkpoint_dir: Path, checkpoint_label: str
) -> dict[str, object]:
    farm_world = scenario.get_typed_app(FarmWorldApp)
    weather = scenario.get_typed_app(WeatherApp)
    checkpoint_state = json.loads(
        (checkpoint_dir / f"{checkpoint_label}.json").read_text(encoding="utf-8")
    )
    return restore_farm_checkpoint_state(
        farm_world=farm_world,
        weather_app=weather,
        checkpoint_state=checkpoint_state,
        target_sim_time=float(checkpoint_state["sim_time"]),
    )


def prep_and_plant_whole_field(
    tractor: TractorApp,
    prev: Any,
    *,
    prefix: str,
    seed_type: str = "HEINONG84",
    seed_spacing_cm: float = 7.9,
) -> Any:
    prev = (
        tractor.attach_implement("grader")
        .oracle()
        .with_id(f"{prefix}_attach_grader")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.level()
        .oracle()
        .with_id(f"{prefix}_level_field")
        .depends_on(prev, delay_seconds=2)
    )
    prev = (
        tractor.detach_implement()
        .oracle()
        .with_id(f"{prefix}_detach_grader")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.load_fertilizer(360.0)
        .oracle()
        .with_id(f"{prefix}_load_base_fertilizer")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.base_fertilize()
        .oracle()
        .with_id(f"{prefix}_apply_base_fertilizer")
        .depends_on(prev, delay_seconds=2)
    )
    prev = (
        tractor.attach_implement("furrower")
        .oracle()
        .with_id(f"{prefix}_attach_furrower")
        .depends_on(prev, delay_seconds=1)
    )
    prev = (
        tractor.form_ridges(ridge_width_m=1.1)
        .oracle()
        .with_id(f"{prefix}_form_ridges")
        .depends_on(prev, delay_seconds=2)
    )
    prev = (
        tractor.detach_implement()
        .oracle()
        .with_id(f"{prefix}_detach_furrower")
        .depends_on(prev, delay_seconds=1)
    )
    return plant_range(
        tractor,
        prev,
        start_ridge=0,
        end_ridge=63,
        seed_type=seed_type,
        spacing_cm=seed_spacing_cm,
        id_prefix=f"{prefix}_whole_field",
    )


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
