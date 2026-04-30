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

# Full-season scenario scaffold.
# These scenarios intentionally use physics-aware tools that may need to be added
# to the FarmWorld apps:
#
#   system.advance_time(hours=...)
#   farm_world.commit_daily_physics()
#   farm_world.configure_physics_profile(...)
#   farm_world.apply_fertigation(...)
#   farm_world.dry_grain(...)
#   farm_world.store_grain()
#   tractor.apply_fungicide(...)
#   tractor.load_fungicide(...)
#   robot.inspect_crop_health(...)
#   robot.inspect_pests(...)
#   robot.inspect_emergence(...)
#
# The purpose is to define the oracle-level long-horizon control logic first.

@register_scenario("scenario_full_season_late_harvest_rain_risk")
class ScenarioFullSeasonLateHarvestRainRisk(Scenario):
    """
    L3 full-season scenario: Late-season harvest rain and shattering risk.

    Scope:
        The agent is handed farm operation responsibility from planting through
        storage. The oracle sequence combines multiple L2 episodes and uses the
        physics engines to make intermediate decisions matter.

    Physics profile:
        harbin_late_harvest_seed_909

    Weather / pressure regime:
        stable early season, late September rain risk

    Initial condition:
        stress-tolerant seed, later harvest moisture timing challenge
    """

    start_time: float | None = (
        datetime(2026, 5, 3, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 160 * 24 * 3600
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

        self.apps = [
            aui,
            farm_world,
            weather,
            sensor,
            mavic,
            matrice,
            robot_0,
            tractor,
            field_ops,
            system,
        ]
        self._configure_initial_state()
    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        tractor = self.get_typed_app(TractorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")

        # ASSUMED TOOL/STATE: bind the stochastic physics engines to this scenario.
        # The profile name selects weather events, soil initial state, biotic-pressure
        # schedule, and hidden oracle/agent world seeds.
        try:
            farm_world.configure_physics_profile(
                profile_name="harbin_late_harvest_seed_909",
                seed_type="STRESS_TOLERANT",
                location="Harbin/Heilongjiang",
                start_date="2026-05-03",
            )
        except AttributeError:
            pass

        weather.set_weather(
            date="2026-05-03",
            temp_c=16.0,
            humidity_pct=60.0,
            wind_speed_ms=2.5,
            rainfall_mm=0.0,
            solar_radiation=460.0,
            forecast=[
                {"date": "2026-05-05", "temp_c": 16.0, "humidity_pct": 55.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 500.0},
                {"date": "2026-05-06", "temp_c": 17.0, "humidity_pct": 52.0, "wind_speed_ms": 2.5, "rainfall_mm": 0.0, "solar_radiation": 510.0},
                {"date": "2026-05-07", "temp_c": 18.0, "humidity_pct": 50.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 520.0},
            ],
            avg_soil_vwc=0.24,
        )

        farm_world.set_season_phase("full_season")
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 35.0
        tractor._seed_hopper_count = 0
        tractor._fertilizer_spreader_kg = 0.0
        tractor._pesticide_tank_l = 0.0
        mavic._battery_pct = 90.0
        matrice._battery_pct = 85.0

        # ASSUMED inventory fields; map to existing inventory implementation as needed.
        farm_world._seed_inventory = 900000
        farm_world._fertilizer_inventory_kg = 300
        farm_world._pesticide_inventory_l = 500
        farm_world._diesel_inventory_l = 800

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = False
            r.seed_type = None
            r.days_since_planted = 0
            r.growth_stage = "NOT_PLANTED"
            r.soil_vwc = 0.24 + ((i % 5) - 2) * 0.005
            r.soil_temp_c = 16.0 - 3.0 + (i % 4) * 0.4
            r.ndvi = 0.18
            r.yield_potential = 0.0
            r.pest_pressure = 0.02
            r.disease_pressure = 0.02
            r.nutrient_index = 0.85

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        field_ops = self.get_typed_app(FieldOpsApp)
        system = self.get_typed_app(SystemApp)

        briefing_text = (
            "从播种到收获管理全季。后期重点是成熟后不要只看R8状态，必须结合籽粒含水率、降雨预报和落荚风险决定收获窗口。如果含水率偏高可以等待短期干燥，但不能错过降雨前窗口。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)

            # 1) Planting window and planting execution.
            o_weather_0 = weather.get_current_weather().oracle().with_id("o_weather_before_planting").depends_on(briefing, delay_seconds=2)
            o_forecast_0 = weather.get_forecast(days=5).oracle().with_id("o_forecast_before_planting").depends_on(o_weather_0, delay_seconds=1)
            o_soil_0 = sensor.read_soil_sensors().oracle().with_id("o_soil_before_planting").depends_on(o_forecast_0, delay_seconds=1)

            o_tractor_0 = tractor.get_status().oracle().with_id("o_check_tractor_before_planting").depends_on(o_soil_0, delay_seconds=1)
            o_inventory_0 = farm_world.get_inventory().oracle().with_id("o_check_seed_fuel_inventory").depends_on(o_tractor_0, delay_seconds=1)
            o_load_seed_1 = tractor.load_seeds("STRESS_TOLERANT", 300000).oracle().with_id("o_load_seed_1").depends_on(o_inventory_0, delay_seconds=2)
            o_plant_0_3 = tractor.plant_seeds(0, 3, 4.0, 5.0).oracle().with_id("o_plant_0_3").depends_on(o_load_seed_1, delay_seconds=2)
            o_plant_4_7 = tractor.plant_seeds(4, 7, 4.0, 5.0).oracle().with_id("o_plant_4_7").depends_on(o_plant_0_3, delay_seconds=2)
            o_plant_8_11 = tractor.plant_seeds(8, 11, 4.0, 5.0).oracle().with_id("o_plant_8_11").depends_on(o_plant_4_7, delay_seconds=2)
            o_plant_12_15 = tractor.plant_seeds(12, 15, 4.0, 5.0).oracle().with_id("o_plant_12_15").depends_on(o_plant_8_11, delay_seconds=2)
            o_plant_16_19 = tractor.plant_seeds(16, 19, 4.0, 5.0).oracle().with_id("o_plant_16_19").depends_on(o_plant_12_15, delay_seconds=2)
            o_plant_20_23 = tractor.plant_seeds(20, 23, 4.0, 5.0).oracle().with_id("o_plant_20_23").depends_on(o_plant_16_19, delay_seconds=2)
            o_reload_after_23 = tractor.load_seeds("STRESS_TOLERANT", 300000).oracle().with_id("o_reload_after_23").depends_on(o_plant_20_23, delay_seconds=2)
            o_plant_24_27 = tractor.plant_seeds(24, 27, 4.0, 5.0).oracle().with_id("o_plant_24_27").depends_on(o_reload_after_23, delay_seconds=2)
            o_plant_28_31 = tractor.plant_seeds(28, 31, 4.0, 5.0).oracle().with_id("o_plant_28_31").depends_on(o_plant_24_27, delay_seconds=2)
            o_plant_32_35 = tractor.plant_seeds(32, 35, 4.0, 5.0).oracle().with_id("o_plant_32_35").depends_on(o_plant_28_31, delay_seconds=2)
            o_plant_36_39 = tractor.plant_seeds(36, 39, 4.0, 5.0).oracle().with_id("o_plant_36_39").depends_on(o_plant_32_35, delay_seconds=2)
            o_plant_40_43 = tractor.plant_seeds(40, 43, 4.0, 5.0).oracle().with_id("o_plant_40_43").depends_on(o_plant_36_39, delay_seconds=2)
            o_plant_44_47 = tractor.plant_seeds(44, 47, 4.0, 5.0).oracle().with_id("o_plant_44_47").depends_on(o_plant_40_43, delay_seconds=2)
            o_reload_after_47 = tractor.load_seeds("STRESS_TOLERANT", 300000).oracle().with_id("o_reload_after_47").depends_on(o_plant_44_47, delay_seconds=2)
            o_plant_48_51 = tractor.plant_seeds(48, 51, 4.0, 5.0).oracle().with_id("o_plant_48_51").depends_on(o_reload_after_47, delay_seconds=2)
            o_plant_52_55 = tractor.plant_seeds(52, 55, 4.0, 5.0).oracle().with_id("o_plant_52_55").depends_on(o_plant_48_51, delay_seconds=2)
            o_plant_56_59 = tractor.plant_seeds(56, 59, 4.0, 5.0).oracle().with_id("o_plant_56_59").depends_on(o_plant_52_55, delay_seconds=2)
            o_plant_60_63 = tractor.plant_seeds(60, 63, 4.0, 5.0).oracle().with_id("o_plant_60_63").depends_on(o_plant_56_59, delay_seconds=2)

            o_commit_planting = farm_world.commit_daily_physics().oracle().with_id("o_commit_planting_physics").depends_on(o_plant_60_63, delay_seconds=1)

            # 2) Emergence and early monitoring.
            o_wait_emergence = system.advance_time(days=12).oracle().with_id("o_wait_to_emergence_window").depends_on(o_commit_planting, delay_seconds=1)
            o_monitor_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_check").depends_on(o_wait_emergence, delay_seconds=1)
            o_monitor_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_check").depends_on(o_monitor_soil, delay_seconds=1)
            o_monitor_1 = mavic.fly_survey(0, 63).oracle().with_id("o_initial_uav_stand_monitoring").depends_on(o_monitor_canopy, delay_seconds=2)


            # Routine growth branch: monitor without unnecessary intervention.
            o_wait_routine = system.advance_time(days=35).oracle().with_id("o_wait_to_routine_growth_check").depends_on(o_monitor_1, delay_seconds=1)
            o_routine_soil = sensor.read_soil_sensors().oracle().with_id("o_routine_soil_check").depends_on(o_wait_routine, delay_seconds=1)
            o_routine_survey = mavic.fly_survey(0, 63).oracle().with_id("o_routine_uav_survey").depends_on(o_routine_soil, delay_seconds=2)
            o_commit_routine = farm_world.commit_daily_physics().oracle().with_id("o_commit_routine_growth").depends_on(o_routine_survey, delay_seconds=1)


            # Pod-fill monitoring branch with no unnecessary irrigation.
            o_wait_podfill = system.advance_time(days=35).oracle().with_id("o_wait_to_pod_fill_monitoring").depends_on(o_commit_routine, delay_seconds=1)
            o_pod_weather = weather.get_current_weather().oracle().with_id("o_pod_fill_weather").depends_on(o_wait_podfill, delay_seconds=1)
            o_pod_forecast = weather.get_forecast(days=4).oracle().with_id("o_pod_fill_forecast").depends_on(o_pod_weather, delay_seconds=1)
            o_pod_soil = sensor.read_soil_sensors().oracle().with_id("o_pod_fill_soil_check_no_irrigation").depends_on(o_pod_forecast, delay_seconds=1)
            o_commit_podfill = farm_world.commit_daily_physics().oracle().with_id("o_commit_pod_fill_monitoring").depends_on(o_pod_soil, delay_seconds=1)

            # 5) Harvest branch: wait to maturity, assess moisture, dry-down if needed, harvest, dry/store.
            o_wait_maturity = system.advance_time(days=45).oracle().with_id("o_wait_to_r8_maturity").depends_on(o_commit_podfill, delay_seconds=1)
            o_harvest_weather = weather.get_current_weather().oracle().with_id("o_harvest_weather").depends_on(o_wait_maturity, delay_seconds=1)
            o_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_harvest_forecast").depends_on(o_harvest_weather, delay_seconds=1)
            o_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_check_r8_and_grain_moisture").depends_on(o_harvest_forecast, delay_seconds=1)
            o_drydown_wait = system.advance_time(hours=24).oracle().with_id("o_wait_drydown_if_needed").depends_on(o_harvest_overview, delay_seconds=1)
            o_recheck_moisture = farm_world.get_farm_overview().oracle().with_id("o_recheck_grain_moisture").depends_on(o_drydown_wait, delay_seconds=1)
            o_tractor_harvest = tractor.get_status().oracle().with_id("o_check_tractor_before_harvest").depends_on(o_recheck_moisture, delay_seconds=1)
            o_refuel_harvest = tractor.refuel(80.0).oracle().with_id("o_refuel_for_harvest_if_needed").depends_on(o_tractor_harvest, delay_seconds=2)
            o_attach_harvester = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_refuel_harvest, delay_seconds=1)
            o_harvest_0_3 = tractor.harvest(0, 3).oracle().with_id("o_harvest_0_3").depends_on(o_attach_harvester, delay_seconds=2)
            o_harvest_4_7 = tractor.harvest(4, 7).oracle().with_id("o_harvest_4_7").depends_on(o_harvest_0_3, delay_seconds=2)
            o_unload_after_7 = tractor.unload_grain().oracle().with_id("o_unload_after_7").depends_on(o_harvest_4_7, delay_seconds=1)
            o_harvest_8_11 = tractor.harvest(8, 11).oracle().with_id("o_harvest_8_11").depends_on(o_unload_after_7, delay_seconds=2)
            o_harvest_12_15 = tractor.harvest(12, 15).oracle().with_id("o_harvest_12_15").depends_on(o_harvest_8_11, delay_seconds=2)
            o_unload_after_15 = tractor.unload_grain().oracle().with_id("o_unload_after_15").depends_on(o_harvest_12_15, delay_seconds=1)
            o_harvest_16_19 = tractor.harvest(16, 19).oracle().with_id("o_harvest_16_19").depends_on(o_unload_after_15, delay_seconds=2)
            o_harvest_20_23 = tractor.harvest(20, 23).oracle().with_id("o_harvest_20_23").depends_on(o_harvest_16_19, delay_seconds=2)
            o_unload_after_23 = tractor.unload_grain().oracle().with_id("o_unload_after_23").depends_on(o_harvest_20_23, delay_seconds=1)
            o_harvest_24_27 = tractor.harvest(24, 27).oracle().with_id("o_harvest_24_27").depends_on(o_unload_after_23, delay_seconds=2)
            o_harvest_28_31 = tractor.harvest(28, 31).oracle().with_id("o_harvest_28_31").depends_on(o_harvest_24_27, delay_seconds=2)
            o_unload_after_31 = tractor.unload_grain().oracle().with_id("o_unload_after_31").depends_on(o_harvest_28_31, delay_seconds=1)
            o_harvest_32_35 = tractor.harvest(32, 35).oracle().with_id("o_harvest_32_35").depends_on(o_unload_after_31, delay_seconds=2)
            o_harvest_36_39 = tractor.harvest(36, 39).oracle().with_id("o_harvest_36_39").depends_on(o_harvest_32_35, delay_seconds=2)
            o_unload_after_39 = tractor.unload_grain().oracle().with_id("o_unload_after_39").depends_on(o_harvest_36_39, delay_seconds=1)
            o_harvest_40_43 = tractor.harvest(40, 43).oracle().with_id("o_harvest_40_43").depends_on(o_unload_after_39, delay_seconds=2)
            o_harvest_44_47 = tractor.harvest(44, 47).oracle().with_id("o_harvest_44_47").depends_on(o_harvest_40_43, delay_seconds=2)
            o_unload_after_47 = tractor.unload_grain().oracle().with_id("o_unload_after_47").depends_on(o_harvest_44_47, delay_seconds=1)
            o_harvest_48_51 = tractor.harvest(48, 51).oracle().with_id("o_harvest_48_51").depends_on(o_unload_after_47, delay_seconds=2)
            o_harvest_52_55 = tractor.harvest(52, 55).oracle().with_id("o_harvest_52_55").depends_on(o_harvest_48_51, delay_seconds=2)
            o_unload_after_55 = tractor.unload_grain().oracle().with_id("o_unload_after_55").depends_on(o_harvest_52_55, delay_seconds=1)
            o_harvest_56_59 = tractor.harvest(56, 59).oracle().with_id("o_harvest_56_59").depends_on(o_unload_after_55, delay_seconds=2)
            o_harvest_60_63 = tractor.harvest(60, 63).oracle().with_id("o_harvest_60_63").depends_on(o_harvest_56_59, delay_seconds=2)
            o_unload_after_63 = tractor.unload_grain().oracle().with_id("o_unload_after_63").depends_on(o_harvest_60_63, delay_seconds=1)

            o_commit_harvest = farm_world.commit_daily_physics().oracle().with_id("o_commit_recovered_yield").depends_on(o_unload_after_63, delay_seconds=1)
            o_inventory_post = farm_world.get_inventory().oracle().with_id("o_check_postharvest_inventory").depends_on(o_commit_harvest, delay_seconds=1)
            o_dry_grain = farm_world.dry_grain(target_moisture_pct=13.5).oracle().with_id("o_dry_grain_if_needed").depends_on(o_inventory_post, delay_seconds=2)
            o_store_grain = farm_world.store_grain().oracle().with_id("o_store_grain").depends_on(o_dry_grain, delay_seconds=2)
            o_report = aui.send_message_to_user(content="全季管理完成：播种、监测、必要干预、收获、干燥和入库已完成。").oracle().with_id("o_report").depends_on(o_store_grain, delay_seconds=2)

            # In final integration, replace this dynamic scaffold list with an
            # explicit ordered event list if strict oracle validation requires it.
            self.events = [
                value for name, value in locals().items()
                if name.startswith("o_") or name == "briefing"
            ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(
            success=True,
            rationale="full-season scaffold: implement physics-aware queue/oracle validation after tool integration",
        )
        return append_workflow_evaluation(self, env, result)
