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

@register_scenario("scenario_physics_planting_window_reschedule")
class ScenarioPhysicsPlantingWindowReschedule(Scenario):
    """
    L2 episode: planting window selection and execution under weather/soil uncertainty.

    Objective:
        Plant all 64 soybean ridges, but do not plant into cold or wet seed-zone
        conditions. The oracle waits through a rain/cold event, rechecks the
        physics-updated soil state, then plants when topsoil temperature and VWC
        return to the acceptable range.

    Physics used:
        weather generator, soil engine, phenology engine, management-effect model.
    """

    start_time: float | None = (
        datetime(2026, 5, 3, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 3 * 24 * 3600
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

        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 80.0

        # Day 0 looks marginal: rain just passed, soil is wet, cold night expected.
        weather.set_weather(
            date="2026-05-03",
            temp_c=11.0,
            humidity_pct=80.0,
            wind_speed_ms=3.0,
            rainfall_mm=6.0,
            solar_radiation=250.0,
            forecast=[
                {"date": "2026-05-04", "temp_c": 9.0, "humidity_pct": 82.0, "wind_speed_ms": 4.0, "rainfall_mm": 4.0, "solar_radiation": 220.0},
                {"date": "2026-05-05", "temp_c": 13.0, "humidity_pct": 60.0, "wind_speed_ms": 2.5, "rainfall_mm": 0.0, "solar_radiation": 420.0},
                {"date": "2026-05-06", "temp_c": 16.0, "humidity_pct": 50.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 500.0},
            ],
            avg_soil_vwc=0.34,
        )
        farm_world.set_season_phase("planting")

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.soil_vwc = 0.32 + (i % 4) * 0.01   # wet/marginal to blocked
            r.soil_temp_c = 9.0 + (i % 3) * 0.4  # below 10 C on some ridges

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        system = self.get_typed_app(SystemApp)

        briefing_text = (
            "准备播种大豆，但不要机械地立刻播。请先判断播种窗口："
            "土壤温度需要大于10°C，表层VWC应在0.20-0.30附近，避免湿冷播种。"
            "如果今天不合适，请等待并重新检查；一旦条件合适，按4垄/趟完成64垄播种。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)

            o_weather_0 = weather.get_current_weather().oracle().with_id("o_check_weather_day0").depends_on(briefing, delay_seconds=2)
            o_forecast = weather.get_forecast(days=4).oracle().with_id("o_check_forecast").depends_on(o_weather_0, delay_seconds=1)
            o_soil_0 = sensor.read_soil_sensors().oracle().with_id("o_read_soil_day0_blocked").depends_on(o_forecast, delay_seconds=1)

            # ASSUMED TOOL: advances the global clock and runs daily weather/soil physics.
            # Oracle waits rather than planting into wet/cold seed-zone conditions.
            o_wait_1 = system.advance_time(hours=24).oracle().with_id("o_wait_one_day").depends_on(o_soil_0, delay_seconds=1)
            o_weather_1 = weather.get_current_weather().oracle().with_id("o_check_weather_day1").depends_on(o_wait_1, delay_seconds=1)
            o_soil_1 = sensor.read_soil_sensors().oracle().with_id("o_read_soil_day1_still_marginal").depends_on(o_weather_1, delay_seconds=1)

            o_wait_2 = system.advance_time(hours=24).oracle().with_id("o_wait_second_day").depends_on(o_soil_1, delay_seconds=1)
            o_weather_2 = weather.get_current_weather().oracle().with_id("o_check_weather_day2").depends_on(o_wait_2, delay_seconds=1)
            o_soil_2 = sensor.read_soil_sensors().oracle().with_id("o_read_soil_day2_ready").depends_on(o_weather_2, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id("o_check_tractor").depends_on(o_soil_2, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id("o_check_seed_inventory").depends_on(o_tractor, delay_seconds=1)

            o_load = tractor.load_seeds("STANDARD", 300000).oracle().with_id("o_load_seeds_1").depends_on(o_inventory, delay_seconds=2)
            prev = o_load
            plant_events = []
            for start in range(0, 64, 4):
                end = start + 3
                ev = tractor.plant_seeds(start, end, 4.0, 5.0).oracle().with_id(f"o_plant_{start}_{end}").depends_on(prev, delay_seconds=2)
                plant_events.append(ev)
                prev = ev
                if end in {23, 47}:
                    load = tractor.load_seeds("STANDARD", 300000).oracle().with_id(f"o_reload_after_{end}").depends_on(prev, delay_seconds=2)
                    plant_events.append(load)
                    prev = load

            # ASSUMED TOOL: registers management-effect + phenology initialization.
            o_physics_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_planting_effects").depends_on(prev, delay_seconds=1)
            o_report = aui.send_message_to_user(content="已等待合适播种窗口，并完成64垄播种。").oracle().with_id("o_report").depends_on(o_physics_commit, delay_seconds=2)

        self.events = [briefing, o_weather_0, o_forecast, o_soil_0, o_wait_1, o_weather_1, o_soil_1, o_wait_2, o_weather_2, o_soil_2, o_tractor, o_inventory, o_load, *plant_events, o_physics_commit, o_report]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(
            success=True,
            rationale="scaffold scenario: oracle/evaluation hooks to be implemented after tool integration",
        )
        return append_workflow_evaluation(self, env, result)
