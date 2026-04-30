from __future__ import annotations

from datetime import datetime, timezone

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
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

# NOTE:
# These are physics-aware scenario scaffolds. They intentionally reference
# several tools that may not exist yet in the current FarmWorld apps.
# Assumed tools are marked inline. The goal is to define oracle structure
# and scenario logic first, then update the apps/tools to support them.

@register_scenario("scenario_physics_postharvest_drying_storage")
class ScenarioPhysicsPostharvestDryingStorage(Scenario):
    """
    L2 episode: post-harvest drying, storage, and residue handling.

    Objective:
        Harvest is complete but grain moisture is above safe storage moisture.
        The oracle checks inventory/grain moisture, schedules drying before
        storage, and handles residue by chopping/incorporation rather than
        immediate burning.
    """

    start_time: float | None = (
        datetime(2026, 9, 21, 9, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 2 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        aui = AgentUserInterface()
        farm_world = FarmWorldApp()
        weather = WeatherApp()
        sensor = SensorApp(farm_world_app=farm_world)
        mavic = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Mavic3M",
            description="DJI Mavic 3 Multispectral — multispectral NDVI mapping drone",
            speed_ms=5.0,
            effective_ridges_per_pass=7,
            battery_pct_per_ridge=1.0,
        )
        matrice = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Matrice4T",
            description="DJI Matrice 4T — thermal imaging drone",
            speed_ms=4.0,
            effective_ridges_per_pass=5,
            battery_pct_per_ridge=1.5,
        )
        robot_0 = RobotApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Robot0",
            description="Zhiyuan D1 Max #1 — ground-level inspection robot",
        )
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()

        self.apps = [aui, farm_world, weather, sensor, mavic, matrice, robot_0, tractor, field_ops, system]
        self._configure_initial_state()

    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        tractor = self.get_typed_app(TractorApp)

        weather.set_weather(
            date="2026-09-21",
            temp_c=16.0,
            humidity_pct=65.0,
            wind_speed_ms=3.0,
            rainfall_mm=0.0,
            solar_radiation=360.0,
            forecast=[
                {"date": "2026-09-22", "temp_c": 15.0, "humidity_pct": 70.0, "wind_speed_ms": 3.0, "rainfall_mm": 2.0, "solar_radiation": 300.0},
            ],
            avg_soil_vwc=0.25,
        )
        farm_world.set_season_phase("post_harvest")
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges", "harvest"]
        tractor._fuel_tank_l = 55.0

        # ASSUMED state fields used by storage/yield modules.
        farm_world._grain_in_trailer_kg = 4200.0
        farm_world._grain_moisture_pct = 16.8
        farm_world._storage_safe_moisture_pct = 13.5
        farm_world._residue_status = "chopped_spread"

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.growth_stage = "HARVESTED"
            r.harvested = True
            r.residue_status = "chopped_spread"

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        briefing_text = (
            "收获已完成，但不要直接把湿粮长期入仓。请检查粮食含水率和库存状态；"
            "如果高于安全储藏含水率，先烘干/通风处理，再入仓。收获残茬默认还田，不做露天焚烧。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id("o_check_postharvest_weather").depends_on(briefing, delay_seconds=2)
            o_inventory = farm_world.get_inventory().oracle().with_id("o_check_grain_inventory_moisture").depends_on(o_weather, delay_seconds=1)

            # ASSUMED TOOL: grain drying / aeration model.
            o_dry = farm_world.dry_grain(target_moisture_pct=13.5).oracle().with_id("o_dry_grain_to_safe_storage").depends_on(o_inventory, delay_seconds=2)
            o_wait = system.advance_time(hours=12).oracle().with_id("o_wait_for_drying_completion").depends_on(o_dry, delay_seconds=1)
            o_store = farm_world.store_grain().oracle().with_id("o_store_dried_grain").depends_on(o_wait, delay_seconds=1)

            o_tractor = tractor.get_status().oracle().with_id("o_check_tractor_for_residue").depends_on(o_store, delay_seconds=1)

            # ASSUMED TOOL: residue handling distinct from burning.
            o_residue = tractor.incorporate_residue(0, 63).oracle().with_id("o_incorporate_residue").depends_on(o_tractor, delay_seconds=2)
            o_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_postharvest_state").depends_on(o_residue, delay_seconds=1)
            o_report = aui.send_message_to_user(content="湿粮已烘干至安全储藏含水率并入仓，残茬已按还田处理。").oracle().with_id("o_report").depends_on(o_commit, delay_seconds=2)

        self.events = [briefing, o_weather, o_inventory, o_dry, o_wait, o_store, o_tractor, o_residue, o_commit, o_report]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(
            success=True,
            rationale="scaffold scenario: oracle/evaluation hooks to be implemented after tool integration",
        )
        return append_workflow_evaluation(self, env, result)
