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

_BAD_START = 12
_BAD_END = 19

@register_scenario("scenario_physics_emergence_replant_decision")
class ScenarioPhysicsEmergenceReplantDecision(Scenario):
    """
    L2 episode: emergence assessment and replant decision.

    Objective:
        Assess uneven emergence after a cold/wet week. The oracle uses sparse
        sensors, UAV NDVI, and ground inspection to identify a low-stand block.
        It replants only the failed block if the calendar is still inside the
        acceptable Heilongjiang planting window.
    """

    start_time: float | None = (
        datetime(2026, 5, 18, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 18000
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
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        tractor = self.get_typed_app(TractorApp)

        weather.set_weather(
            date="2026-05-18",
            temp_c=17.0,
            humidity_pct=55.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=480.0,
            forecast=[
                {"date": "2026-05-19", "temp_c": 18.0, "humidity_pct": 50.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 500.0},
                {"date": "2026-05-20", "temp_c": 19.0, "humidity_pct": 48.0, "wind_speed_ms": 2.5, "rainfall_mm": 0.0, "solar_radiation": 510.0},
            ],
            avg_soil_vwc=0.24,
        )
        farm_world.set_season_phase("emergence")
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 70.0
        mavic._battery_pct = 90.0

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.days_since_planted = 12
            r.growth_stage = "VE" if not (_BAD_START <= i <= _BAD_END) else "PLANTED_PRE_EMERGENCE"
            r.soil_vwc = 0.23 + (i % 4) * 0.01
            r.soil_temp_c = 14.0 + (i % 3) * 0.4
            r.stand_fraction = 0.92 if not (_BAD_START <= i <= _BAD_END) else 0.45
            r.ndvi = 0.30 if not (_BAD_START <= i <= _BAD_END) else 0.18
            r.yield_potential = 0.92 if not (_BAD_START <= i <= _BAD_END) else 0.55

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        farm_world = self.get_typed_app(FarmWorldApp)

        briefing_text = (
            "播种后约12天，检查出苗情况。不要只看是否已经播过种；"
            "需要判断是否有连续垄出苗失败。如果有低stand区域且仍在补种窗口内，"
            "只补种失败区域，不要重播全田。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id("o_check_weather").depends_on(briefing, delay_seconds=2)
            o_forecast = weather.get_forecast(days=3).oracle().with_id("o_check_forecast").depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id("o_read_soil").depends_on(o_forecast, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id("o_read_canopy").depends_on(o_soil, delay_seconds=1)
            o_drone = mavic.check_status().oracle().with_id("o_check_mavic").depends_on(o_canopy, delay_seconds=1)
            o_survey = mavic.fly_survey(_BAD_START - 2, _BAD_END + 2).oracle().with_id("o_survey_low_stand_block").depends_on(o_drone, delay_seconds=2)

            # ASSUMED TOOL: inspect stand/emergence, not only pests.
            o_robot = robot.inspect_emergence(_BAD_START, _BAD_END).oracle().with_id("o_ground_check_emergence").depends_on(o_survey, delay_seconds=2)
            o_tractor = tractor.get_status().oracle().with_id("o_check_tractor").depends_on(o_robot, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id("o_check_seed_inventory").depends_on(o_tractor, delay_seconds=1)
            o_load = tractor.load_seeds("STANDARD", 100000).oracle().with_id("o_load_replant_seed").depends_on(o_inventory, delay_seconds=2)

            # ASSUMED TOOL: replant only failed ridges, preserving already emerged ridges.
            o_replant = tractor.replant_seeds(_BAD_START, _BAD_END, depth_cm=4.0, spacing_cm=5.0).oracle().with_id("o_replant_failed_block").depends_on(o_load, delay_seconds=2)
            o_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_replant_effect").depends_on(o_replant, delay_seconds=1)
            o_report = aui.send_message_to_user(content="已确认12-19垄出苗失败，并只对失败区域补种。").oracle().with_id("o_report").depends_on(o_commit, delay_seconds=2)

        self.events = [briefing, o_weather, o_forecast, o_soil, o_canopy, o_drone, o_survey, o_robot, o_tractor, o_inventory, o_load, o_replant, o_commit, o_report]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(
            success=True,
            rationale="scaffold scenario: oracle/evaluation hooks to be implemented after tool integration",
        )
        return append_workflow_evaluation(self, env, result)
