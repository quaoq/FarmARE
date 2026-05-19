from __future__ import annotations

from datetime import datetime, timezone
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
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.types import EventRegisterer


SCENARIO_ID = "scenario_full_season_wet_june_ab_zoned_disease"
PROFILE_NAME = "harbin_wet_june_ab_zoned_seed_313"
RIDGE_WIDTH_M = 1.1

A_ZONE_START = 0
A_ZONE_END = 31
B_ZONE_START = 32
B_ZONE_END = 63
AFFECTED_START = 40
AFFECTED_END = 55

A_SEED_TYPE = "HEINONG84"
B_SEED_TYPE = "HEINONG60"
A_STANDARD_SPACING_CM = 7.9
B_HIGH_DENSITY_SPACING_CM = 6.1

R1_NUTRIENT_AMOUNT = 0.30
R5_NUTRIENT_AMOUNT = 0.16
FUNGICIDE_L_PER_RIDGE = 5.0


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonWetJuneABZonedDisease(Scenario):
    """
    L3 full-season scenario: Harbin wet June plus A/B zoned soybean planting.

    The scenario itself is a clean farm-management reference path. Trace/export
    probes live outside this file in scripts so the oracle event graph remains a
    normal agronomic operation plan rather than a diagnostic script.
    """

    start_time: float | None = (
        datetime(2026, 5, 5, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 160 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
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
            name="Matrice4T",
            description="DJI Matrice 4T - thermal imaging drone",
            speed_ms=4.0,
            effective_ridges_per_pass=5,
            battery_pct_per_ridge=1.5,
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

        self.apps = [
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
        self._configure_initial_state()
        farm_world.attach_system_app(system)

    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        tractor = self.get_typed_app(TractorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")

        farm_world.configure_physics_profile(
            profile_name=PROFILE_NAME,
            seed_type=A_SEED_TYPE,
            cultivar="A区黑农84标准密度/B区黑农60高密度",
            density_target_plants_m2=23.0,
            location="Harbin/Heilongjiang",
            start_date="2026-05-05",
        )

        weather.set_weather(
            date="2026-05-05",
            temp_c=16.0,
            humidity_pct=58.0,
            wind_speed_ms=2.4,
            rainfall_mm=0.0,
            solar_radiation=20.0,
            forecast=[
                {"date": "2026-05-06", "temp_c": 16.5, "humidity_pct": 56.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 20.5},
                {"date": "2026-05-07", "temp_c": 17.0, "humidity_pct": 55.0, "wind_speed_ms": 2.4, "rainfall_mm": 0.0, "solar_radiation": 21.0},
                {"date": "2026-05-08", "temp_c": 18.0, "humidity_pct": 54.0, "wind_speed_ms": 2.2, "rainfall_mm": 0.0, "solar_radiation": 21.0},
            ],
            avg_soil_vwc=0.30,
        )

        farm_world.set_season_phase("full_season")
        farm_world._inventory.seed_stock[A_SEED_TYPE] = 1000000
        farm_world._inventory.seed_stock[B_SEED_TYPE] = 1000000
        farm_world._inventory.fertilizer_kg = 2500.0
        farm_world._inventory.pesticide_liters = 2000.0
        farm_world._inventory.fuel_liters = 1000.0

        tractor._fuel_tank_l = 100.0
        tractor._seed_hopper = 0
        tractor._fertilizer_spreader_kg = 0.0
        tractor._pesticide_tank_l = 0.0
        tractor._fungicide_tank_l = 0.0
        mavic._battery_pct = 95.0
        matrice._battery_pct = 92.0

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = False
            r.seed_type = None
            r.days_since_planted = 0
            r.growth_stage = "NOT_PLANTED"
            r.soil_vwc = 0.30 + ((i % 5) - 2) * 0.003
            r.soil_temp_c = 13.1 + (i % 4) * 0.25
            r.ndvi = 0.18
            r.yield_potential = 0.0
            r.pest_pressure_base = 0.02
            r.pest_pressure = 0.02
            r.disease_pressure_base = 0.02
            r.disease_pressure = 0.02
            r.nutrient_index = 0.75
            r.stand_fraction = 1.0

    def _advance_days(self, prev: Any, days: int, prefix: str) -> Any:
        system = self.get_typed_app(SystemApp)
        for day_index in range(1, days + 1):
            prev = (
                system.advance_time(days=1)
                .oracle()
                .with_id(f"{prefix}_advance_day_{day_index:03d}")
                .depends_on(prev, delay_seconds=1)
            )
            prev = self._after_daily_advance(prev, f"{prefix}_day_{day_index:03d}")
        return prev

    def _after_daily_advance(self, prev: Any, label: str) -> Any:
        return prev

    def _after_named_step(self, prev: Any, label: str) -> Any:
        return prev

    def _plant_range(
        self,
        tractor: TractorApp,
        prev: Any,
        start_ridge: int,
        end_ridge: int,
        seed_type: str,
        spacing_cm: float,
        id_prefix: str,
    ) -> Any:
        prev = (
            tractor.load_seeds(seed_type, 300000)
            .oracle()
            .with_id(f"{id_prefix}_load_seed")
            .depends_on(prev, delay_seconds=2)
        )
        for start in range(start_ridge, end_ridge + 1, 4):
            end = min(start + 3, end_ridge)
            prev = (
                tractor.plant_seeds(start, end, 4.0, spacing_cm)
                .oracle()
                .with_id(f"{id_prefix}_plant_{start}_{end}")
                .depends_on(prev, delay_seconds=2)
            )
        return prev

    def _harvest_all_ridges(self, tractor: TractorApp, prev: Any) -> Any:
        for start in range(0, 64, 4):
            end = start + 3
            prev = (
                tractor.harvest(start, end)
                .oracle()
                .with_id(f"o_harvest_{start}_{end}")
                .depends_on(prev, delay_seconds=2)
            )
            prev = (
                tractor.unload_grain()
                .oracle()
                .with_id(f"o_unload_after_{end}")
                .depends_on(prev, delay_seconds=1)
            )
        return prev

    def _harvest_ridge_batches(
        self,
        tractor: TractorApp,
        prev: Any,
        batches: list[tuple[int, int]],
        id_prefix: str,
    ) -> Any:
        for start, end in batches:
            for block_start in range(start, end + 1, 4):
                block_end = min(block_start + 3, end)
                prev = (
                    tractor.harvest(block_start, block_end)
                    .oracle()
                    .with_id(f"{id_prefix}_harvest_{block_start}_{block_end}")
                    .depends_on(prev, delay_seconds=2)
                )
                prev = (
                    tractor.unload_grain()
                    .oracle()
                    .with_id(f"{id_prefix}_unload_after_{block_end}")
                    .depends_on(prev, delay_seconds=1)
                )
        return prev

    @staticmethod
    def _collect_event_graph(root: Any) -> list[Any]:
        ordered: list[Any] = []
        seen: set[int] = set()
        stack = [root]
        while stack:
            event = stack.pop(0)
            event_key = id(event)
            if event_key in seen:
                continue
            seen.add(event_key)
            ordered.append(event)
            stack[0:0] = list(getattr(event, "successors", []))
        return ordered

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
            "这是哈尔滨6月湿+A/B分区种植的大豆full-season场景。"
            "全田64条垄：A区0-31垄种黑农84标准密度；B区32-63垄种黑农60高密度。"
            "春季正常，6月偏湿，R5/R6和收获期整体正常。"
            "核心任务是在湿期后比较A/B区：A区通风较好应基本正常，"
            "B区40-55垄更容易出现病害相关异常。"
            "请完成播前准备、底肥、分区播种、分区出苗检查、早期长势和营养检查、"
            "初花期按需营养检查、6月湿期巡查、R5/R6水分检查、成熟收获、干燥和入库。"
            "如果确认病害，只能对B区受影响垄做targeted fungicide，不能全场统一喷药。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)

            o_weather_0 = weather.get_current_weather().oracle().with_id("o_weather_before_prep").depends_on(briefing, delay_seconds=2)
            o_forecast_0 = weather.get_forecast(days=5).oracle().with_id("o_forecast_before_prep").depends_on(o_weather_0, delay_seconds=1)
            o_soil_0 = sensor.read_soil_sensors().oracle().with_id("o_soil_before_prep").depends_on(o_forecast_0, delay_seconds=1)
            o_inventory_0 = farm_world.get_inventory().oracle().with_id("o_inventory_before_prep").depends_on(o_soil_0, delay_seconds=1)

            o_attach_grader = tractor.attach_implement("grader").oracle().with_id("o_attach_grader").depends_on(o_inventory_0, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(o_attach_grader, delay_seconds=2)
            o_load_base = tractor.load_fertilizer(250.0).oracle().with_id("o_load_base_fertilizer").depends_on(o_level, delay_seconds=1)
            o_base = tractor.base_fertilize().oracle().with_id("o_apply_base_fertilizer").depends_on(o_load_base, delay_seconds=2)
            o_ridge = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id("o_form_1p1m_ridges").depends_on(o_base, delay_seconds=2)
            o_commit_prep = farm_world.commit_daily_physics().oracle().with_id("o_commit_prep_physics").depends_on(o_ridge, delay_seconds=1)
            o_after_prep = self._after_named_step(o_commit_prep, "after_prep_and_base_fertilizer")

            o_tractor_plant = tractor.get_status().oracle().with_id("o_tractor_before_zone_planting").depends_on(o_after_prep, delay_seconds=1)
            o_last_a = self._plant_range(tractor, o_tractor_plant, A_ZONE_START, A_ZONE_END, A_SEED_TYPE, A_STANDARD_SPACING_CM, "o_a_zone")
            o_last_b = self._plant_range(tractor, o_last_a, B_ZONE_START, B_ZONE_END, B_SEED_TYPE, B_HIGH_DENSITY_SPACING_CM, "o_b_zone")
            o_commit_plant = farm_world.commit_daily_physics().oracle().with_id("o_commit_zoned_planting_physics").depends_on(o_last_b, delay_seconds=1)
            o_after_plant = self._after_named_step(o_commit_plant, "after_ab_zoned_planting")

            o_wait_emergence = self._advance_days(o_after_plant, 12, "o_wait_emergence")
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_check").depends_on(o_wait_emergence, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_check").depends_on(o_emergence_soil, delay_seconds=1)
            o_robot_status_emergence = robot.check_status().oracle().with_id("o_robot_status_before_emergence_check").depends_on(o_emergence_canopy, delay_seconds=1)
            o_emergence_a = robot.inspect_emergence(A_ZONE_START, A_ZONE_END).oracle().with_id("o_a_zone_emergence_check").depends_on(o_robot_status_emergence, delay_seconds=2)
            o_emergence_b = robot.inspect_emergence(B_ZONE_START, B_ZONE_END).oracle().with_id("o_b_zone_emergence_check").depends_on(o_emergence_a, delay_seconds=2)
            o_emergence_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_emergence_ab_ndvi_check").depends_on(o_emergence_b, delay_seconds=2)
            o_charge_robot_after_emergence = robot.charge().oracle().with_id("o_charge_robot_after_emergence_check").depends_on(o_emergence_ndvi, delay_seconds=1)

            o_wait_early = self._advance_days(o_charge_robot_after_emergence, 22, "o_wait_early_growth")
            o_early_soil = sensor.read_soil_sensors().oracle().with_id("o_early_soil_check").depends_on(o_wait_early, delay_seconds=1)
            o_early_canopy = sensor.read_canopy_sensors().oracle().with_id("o_early_ab_canopy_sensor_check").depends_on(o_early_soil, delay_seconds=1)
            o_robot_status_early = robot.check_status().oracle().with_id("o_robot_status_before_early_health_check").depends_on(o_early_canopy, delay_seconds=1)
            o_early_a = robot.inspect_crop_health(A_ZONE_START, A_ZONE_END).oracle().with_id("o_a_zone_early_health_check").depends_on(o_robot_status_early, delay_seconds=2)
            o_early_b = robot.inspect_crop_health(B_ZONE_START, B_ZONE_END).oracle().with_id("o_b_zone_early_health_check").depends_on(o_early_a, delay_seconds=2)
            o_charge_robot_after_early = robot.charge().oracle().with_id("o_charge_robot_after_early_health_check").depends_on(o_early_b, delay_seconds=1)

            o_wait_wet = self._advance_days(o_charge_robot_after_early, 13, "o_wait_wet_june_window")
            o_wet_weather = weather.get_current_weather().oracle().with_id("o_wet_june_weather_check").depends_on(o_wait_wet, delay_seconds=1)
            o_wet_forecast = weather.get_forecast(days=4).oracle().with_id("o_wet_june_forecast_check").depends_on(o_wet_weather, delay_seconds=1)
            o_wet_soil = sensor.read_soil_sensors().oracle().with_id("o_wet_june_soil_water_check").depends_on(o_wet_forecast, delay_seconds=1)
            o_wait_post_rain_scout = system.advance_time(hours=72).oracle().with_id("o_wait_for_post_rain_ab_scout_window").depends_on(o_wet_soil, delay_seconds=1)
            o_post_rain_weather = weather.get_current_weather().oracle().with_id("o_post_rain_ab_scout_weather_check").depends_on(o_wait_post_rain_scout, delay_seconds=1)
            o_wet_ndvi_a = mavic.fly_survey(A_ZONE_START, A_ZONE_END).oracle().with_id("o_a_zone_wet_period_ndvi").depends_on(o_post_rain_weather, delay_seconds=2)
            o_charge_mavic_before_b_zone = mavic.charge().oracle().with_id("o_charge_mavic_before_b_zone_wet_period_ndvi").depends_on(o_wet_ndvi_a, delay_seconds=1)
            o_wait_mavic_before_b_zone = system.advance_time(hours=1).oracle().with_id("o_wait_mavic_charge_before_b_zone_wet_period_ndvi").depends_on(o_charge_mavic_before_b_zone, delay_seconds=1)
            o_wet_ndvi_b = mavic.fly_survey(B_ZONE_START, B_ZONE_END).oracle().with_id("o_b_zone_wet_period_ndvi").depends_on(o_wait_mavic_before_b_zone, delay_seconds=2)
            o_wet_thermal_b = matrice.fly_survey(B_ZONE_START, B_ZONE_END).oracle().with_id("o_b_zone_wet_period_thermal").depends_on(o_wet_ndvi_b, delay_seconds=2)
            o_robot_status_disease = robot.check_status().oracle().with_id("o_robot_status_before_b_zone_disease_check").depends_on(o_wet_thermal_b, delay_seconds=1)
            o_ground_a = robot.inspect_crop_health(8, 23).oracle().with_id("o_a_zone_ground_reference_check").depends_on(o_robot_status_disease, delay_seconds=2)
            o_ground_b = robot.inspect_crop_health(AFFECTED_START, AFFECTED_END).oracle().with_id("o_b_zone_ground_confirm_disease").depends_on(o_ground_a, delay_seconds=2)
            o_charge_robot_after_disease = robot.charge().oracle().with_id("o_charge_robot_after_disease_check").depends_on(o_ground_b, delay_seconds=1)
            o_spray_weather = weather.get_current_weather().oracle().with_id("o_targeted_fungicide_weather_check").depends_on(o_charge_robot_after_disease, delay_seconds=1)
            o_load_fungicide = tractor.load_fungicide(120.0).oracle().with_id("o_load_targeted_fungicide").depends_on(o_spray_weather, delay_seconds=2)
            o_fungicide_40_49 = tractor.apply_fungicide(40, 49, liters_per_ridge=FUNGICIDE_L_PER_RIDGE).oracle().with_id("o_apply_fungicide_b_40_49").depends_on(o_load_fungicide, delay_seconds=2)
            o_fungicide_50_55 = tractor.apply_fungicide(50, 55, liters_per_ridge=FUNGICIDE_L_PER_RIDGE).oracle().with_id("o_apply_fungicide_b_50_55").depends_on(o_fungicide_40_49, delay_seconds=2)
            o_after_fungicide = self._after_named_step(o_fungicide_50_55, "immediately_after_targeted_b_zone_fungicide")
            o_commit_disease = farm_world.commit_daily_physics().oracle().with_id("o_commit_targeted_disease_management").depends_on(o_after_fungicide, delay_seconds=1)
            o_after_disease = self._after_named_step(o_commit_disease, "after_targeted_b_zone_fungicide")

            o_wait_fungicide_recheck = system.advance_time(days=7).oracle().with_id("o_wait_for_b_zone_fungicide_recheck_window").depends_on(o_after_disease, delay_seconds=1)
            o_recheck_weather = weather.get_current_weather().oracle().with_id("o_b_zone_recheck_weather").depends_on(o_wait_fungicide_recheck, delay_seconds=1)
            o_recheck_a = robot.inspect_crop_health(8, 23).oracle().with_id("o_a_zone_recheck_reference").depends_on(o_recheck_weather, delay_seconds=2)
            o_recheck_b = robot.inspect_crop_health(AFFECTED_START, AFFECTED_END).oracle().with_id("o_b_zone_recheck_residual_disease").depends_on(o_recheck_a, delay_seconds=2)
            o_second_spray_weather = weather.get_current_weather().oracle().with_id("o_second_targeted_fungicide_weather_check").depends_on(o_recheck_b, delay_seconds=1)
            o_load_second_fungicide = tractor.load_fungicide(90.0).oracle().with_id("o_load_second_targeted_fungicide").depends_on(o_second_spray_weather, delay_seconds=2)
            o_second_fungicide_40_49 = tractor.apply_fungicide(40, 49, liters_per_ridge=FUNGICIDE_L_PER_RIDGE).oracle().with_id("o_second_apply_fungicide_b_40_49").depends_on(o_load_second_fungicide, delay_seconds=2)
            o_second_fungicide_50_55 = tractor.apply_fungicide(50, 55, liters_per_ridge=FUNGICIDE_L_PER_RIDGE).oracle().with_id("o_second_apply_fungicide_b_50_55").depends_on(o_second_fungicide_40_49, delay_seconds=2)
            o_after_second_fungicide = self._after_named_step(o_second_fungicide_50_55, "immediately_after_second_targeted_b_zone_fungicide")
            o_commit_second_disease = farm_world.commit_daily_physics().oracle().with_id("o_commit_second_targeted_disease_management").depends_on(o_after_second_fungicide, delay_seconds=1)
            o_after_recheck = self._after_named_step(o_commit_second_disease, "after_second_targeted_b_zone_fungicide")

            o_wait_r1 = self._advance_days(o_after_recheck, 9, "o_wait_r1_nutrient_window")
            o_r1_soil = sensor.read_soil_sensors().oracle().with_id("o_r1_soil_nutrient_check").depends_on(o_wait_r1, delay_seconds=1)
            o_r1_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r1_ab_canopy_sensor_check").depends_on(o_r1_soil, delay_seconds=1)
            o_robot_status_r1 = robot.check_status().oracle().with_id("o_robot_status_before_r1_health_check").depends_on(o_r1_canopy, delay_seconds=1)
            o_charge_robot_before_followup = robot.charge().oracle().with_id("o_charge_robot_before_followup_disease_check").depends_on(o_robot_status_r1, delay_seconds=1)
            o_wait_robot_followup_charge = system.advance_time(hours=1).oracle().with_id("o_wait_robot_followup_charge").depends_on(o_charge_robot_before_followup, delay_seconds=1)
            o_followup_disease = robot.inspect_crop_health(AFFECTED_START, AFFECTED_END).oracle().with_id("o_followup_b_zone_disease_check").depends_on(o_wait_robot_followup_charge, delay_seconds=2)
            o_after_r1_health = self._after_named_step(o_followup_disease, "after_r1_b_zone_health_recheck")
            o_charge_robot_after_r1 = robot.charge().oracle().with_id("o_charge_robot_after_r1_health_check").depends_on(o_after_r1_health, delay_seconds=1)
            o_r1_topdress = farm_world.apply_fertigation(0, 63, nutrient_amount=R1_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r1_light_nutrient_topdress").depends_on(o_charge_robot_after_r1, delay_seconds=2)
            o_commit_r1 = farm_world.commit_daily_physics().oracle().with_id("o_commit_r1_nutrient_management").depends_on(o_r1_topdress, delay_seconds=1)
            o_after_r1 = self._after_named_step(o_commit_r1, "after_r1_light_nutrient_topdress")

            o_wait_r5 = self._advance_days(o_after_r1, 14, "o_wait_r5_r6_window")
            o_pod_weather = weather.get_current_weather().oracle().with_id("o_r5_r6_weather_check").depends_on(o_wait_r5, delay_seconds=1)
            o_pod_forecast = weather.get_forecast(days=4).oracle().with_id("o_r5_r6_forecast_check").depends_on(o_pod_weather, delay_seconds=1)
            o_pod_soil = sensor.read_soil_sensors().oracle().with_id("o_r5_r6_soil_water_check").depends_on(o_pod_forecast, delay_seconds=1)
            o_r5_nutrient = farm_world.apply_fertigation(0, 63, nutrient_amount=R5_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r5_pod_fill_nutrient_support").depends_on(o_pod_soil, delay_seconds=2)
            o_commit_podfill = farm_world.commit_daily_physics().oracle().with_id("o_commit_r5_nutrient_management").depends_on(o_r5_nutrient, delay_seconds=1)
            o_after_podfill = self._after_named_step(o_commit_podfill, "after_r5_nutrient_support")

            o_wait_maturity = self._advance_days(o_after_podfill, 42, "o_wait_first_harvest_window")
            o_harvest_weather = weather.get_current_weather().oracle().with_id("o_first_harvest_weather_check").depends_on(o_wait_maturity, delay_seconds=1)
            o_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_first_harvest_forecast_check").depends_on(o_harvest_weather, delay_seconds=1)
            o_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_check_first_batch_maturity_and_moisture").depends_on(o_harvest_forecast, delay_seconds=1)
            o_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_first_harvest_trafficability_soil_check").depends_on(o_harvest_overview, delay_seconds=1)
            o_detach = tractor.detach_implement().oracle().with_id("o_detach_grader_before_harvest").depends_on(o_harvest_soil, delay_seconds=1)
            o_attach_harvester = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_detach, delay_seconds=1)
            o_first_batch = self._harvest_ridge_batches(
                tractor,
                o_attach_harvester,
                [(0, 31), (40, 55)],
                "o_first_batch",
            )
            o_commit_first_harvest = farm_world.commit_daily_physics().oracle().with_id("o_commit_first_batch_recovered_yield").depends_on(o_first_batch, delay_seconds=1)
            o_after_first_harvest = self._after_named_step(o_commit_first_harvest, "after_first_batch_harvest_commit")
            o_wait_second_batch = self._advance_days(o_after_first_harvest, 0, "o_wait_second_harvest_window")
            o_second_harvest_weather = weather.get_current_weather().oracle().with_id("o_second_harvest_weather_check").depends_on(o_wait_second_batch, delay_seconds=1)
            o_second_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_second_harvest_trafficability_soil_check").depends_on(o_second_harvest_weather, delay_seconds=1)
            o_last_harvest = self._harvest_ridge_batches(
                tractor,
                o_second_harvest_soil,
                [(32, 39), (56, 63)],
                "o_second_batch",
            )
            o_commit_harvest = farm_world.commit_daily_physics().oracle().with_id("o_commit_recovered_yield").depends_on(o_last_harvest, delay_seconds=1)
            o_after_harvest = self._after_named_step(o_commit_harvest, "after_harvest_commit")
            o_dry = farm_world.dry_grain(target_moisture_pct=13.0).oracle().with_id("o_dry_grain_to_safe_storage").depends_on(o_after_harvest, delay_seconds=2)
            o_store = farm_world.store_grain().oracle().with_id("o_store_grain").depends_on(o_dry, delay_seconds=2)

            self.events = self._collect_event_graph(briefing)
