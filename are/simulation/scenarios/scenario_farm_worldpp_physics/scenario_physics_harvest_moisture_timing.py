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
from are.simulation.scenarios.fos import GateSpec, append_fos_evaluation
from are.simulation.scenarios.fos.predicates import (
    after_any_of,
    after_observation,
    and_,
    arg_equals,
    max_arg,
    min_arg,
    or_,
    targets_ridges_overlap,
)
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

@register_scenario("scenario_physics_harvest_moisture_timing")
class ScenarioPhysicsHarvestMoistureTiming(Scenario):
    """
    L2 episode: harvest timing from grain moisture and weather risk.

    Objective:
        R8 is reached but grain moisture is initially high. The oracle waits
        through one dry-down day, then harvests before a rain event and before
        delayed-harvest shattering risk grows.
    """

    start_time: float | None = (
        datetime(2026, 9, 18, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
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
        farm_world.attach_system_app(system)
        self._configure_physics_layers()
    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        tractor = self.get_typed_app(TractorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")

        weather.set_weather(
            date="2026-09-18",
            temp_c=17.0,
            humidity_pct=55.0,
            wind_speed_ms=3.0,
            rainfall_mm=0.0,
            solar_radiation=420.0,
            forecast=[
                {"date": "2026-09-19", "temp_c": 19.0, "humidity_pct": 45.0, "wind_speed_ms": 4.0, "rainfall_mm": 0.0, "solar_radiation": 450.0},
                {"date": "2026-09-20", "temp_c": 15.0, "humidity_pct": 80.0, "wind_speed_ms": 5.0, "rainfall_mm": 12.0, "solar_radiation": 170.0},
            ],
            avg_soil_vwc=0.24,
        )
        farm_world.set_season_phase("harvest")
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 25.0  # needs refill for full harvest
        mavic._battery_pct = 85.0

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.days_since_planted = 126
            r.growth_stage = "R8"
            r.grain_moisture_pct = 17.5 + (i % 4) * 0.3  # too wet today for ideal harvest
            r.soil_vwc = 0.24
            r.ndvi = 0.32
            r.yield_potential = 0.96

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        briefing_text = (
            "大豆已到R8，但今天籽粒含水偏高。请不要因为R8就立刻收；"
            "根据天气和干燥趋势选择收获时间。若等太久会遇到降雨和落荚风险。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id("o_day0_weather").depends_on(briefing, delay_seconds=2)
            o_forecast = weather.get_forecast(days=3).oracle().with_id("o_forecast_rain_risk").depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id("o_soil_trafficable").depends_on(o_forecast, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id("o_overview_r8_high_moisture").depends_on(o_soil, delay_seconds=1)

            # ASSUMED TOOL: uses yield recovery engine dry-down for one day.
            o_wait = system.advance_time(hours=24).oracle().with_id("o_wait_one_drydown_day").depends_on(o_overview, delay_seconds=1)
            o_weather1 = weather.get_current_weather().oracle().with_id("o_day1_weather").depends_on(o_wait, delay_seconds=1)
            o_overview1 = farm_world.get_farm_overview().oracle().with_id("o_day1_moisture_ready").depends_on(o_weather1, delay_seconds=1)
            o_survey = mavic.fly_survey(0, 63).oracle().with_id("o_confirm_uniform_senescence").depends_on(o_overview1, delay_seconds=2)
            o_tractor = tractor.get_status().oracle().with_id("o_check_tractor_low_fuel").depends_on(o_survey, delay_seconds=1)
            o_refuel = tractor.refuel(80.0).oracle().with_id("o_refuel_before_harvest").depends_on(o_tractor, delay_seconds=2)
            o_attach = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_refuel, delay_seconds=1)

            prev = o_attach
            harvest_events = []
            for start in range(0, 64, 4):
                end = start + 3
                ev = tractor.harvest(start, end).oracle().with_id(f"o_harvest_{start}_{end}").depends_on(prev, delay_seconds=2)
                harvest_events.append(ev)
                prev = ev
                if (end + 1) % 8 == 0:
                    unload = tractor.unload_grain().oracle().with_id(f"o_unload_after_{end}").depends_on(prev, delay_seconds=1)
                    harvest_events.append(unload)
                    prev = unload

            o_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_recovered_yield").depends_on(prev, delay_seconds=1)
            o_report = aui.send_message_to_user(content="已等待一天干燥后，在降雨前完成全田收获并入库。").oracle().with_id("o_report").depends_on(o_commit, delay_seconds=2)

        self.events = [briefing, o_weather, o_forecast, o_soil, o_overview, o_wait, o_weather1, o_overview1, o_survey, o_tractor, o_refuel, o_attach, *harvest_events, o_commit, o_report]

    def _configure_physics_layers(self) -> None:
        """Activate physics for this round-3 episode."""
        farm_world = self.get_typed_app(FarmWorldApp)
        farm_world.configure_physics_profile(
            profile_name="physics_harvest_moisture",
            location="Harbin/Heilongjiang",
            scenario_type="harvest_moisture_timing",
        )
        # R8 ridges with grain moisture above safe storage; needs drydown.
        physics = farm_world.physics
        for i in range(64):
            soil = physics.soil.states[i]
            ridge = farm_world._ridges[i]
            soil.top_vwc = float(ridge.soil_vwc)
            soil.root_vwc = float(ridge.soil_vwc)
            yld = physics.yield_recovery.states[i]
            yld.r8_reached = ridge.growth_stage == "R8"
            yld.grain_moisture_frac = float(ridge.grain_moisture_pct) / 100.0
            yld.biological_yield_g_m2 = 350.0

    def _gates(self) -> list[GateSpec]:
        """FOS Decision-component gates for this episode."""
        return [
            GateSpec(
                name="G1_check_grain_status",
                intent="agent inspects ridge state for moisture readiness",
                window_days=(0.0, 0.5),
                eligible_tools=[("FarmWorldApp", "get_farm_overview")],
            ),
            GateSpec(
                name="G2_check_weather_for_dry_window",
                intent="forecast must show dry harvest window",
                window_days=(0.0, 0.5),
                eligible_tools=[("WeatherApp", "get_forecast")],
            ),
            GateSpec(
                name="G3_advance_time_for_drydown",
                intent="wait at least one day for grain dry-down",
                window_days=(0.0, 3.0),
                eligible_tools=[("SystemApp", "advance_time")],
            ),
            GateSpec(
                name="G4_harvest_in_window",
                intent="harvest after drydown when moisture in 13-18% window",
                window_days=(0.5, 4.0),
                eligible_tools=[("TractorApp", "harvest")],
                requires=after_observation("SystemApp", "advance_time"),
            ),
            GateSpec(
                name="G5_unload_grain",
                intent="agent unloads grain to warehouse after harvest",
                window_days=(0.5, 4.0),
                eligible_tools=[("TractorApp", "unload_grain")],
                requires=after_observation("TractorApp", "harvest"),
            ),
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="round-3 episode")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result
