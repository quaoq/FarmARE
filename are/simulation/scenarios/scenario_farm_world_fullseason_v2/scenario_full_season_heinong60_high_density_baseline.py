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


SEED_TYPE = "HEINONG60"
RIDGE_WIDTH_M = 1.1
SEED_SPACING_CM = 6.1
SCENARIO_ID = "scenario_full_season_heinong60_high_density_baseline"
R1_TOPDRESS_NUTRIENT_AMOUNT = 0.35
R5_POD_FILL_NUTRIENT_AMOUNT = 0.18
R5_IRRIGATION_HOURS = 0.8


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonHeinong60HighDensityBaseline(Scenario):
    """
    L3 full-season baseline: normal-year Harbin high-density Heinong60 soybean.

    The oracle is intentionally an expert-style baseline path: complete field
    preparation, base fertilizer, high-density planting, periodic scouting, one
    R1 nutrient topdress for the high-density stand, one R5 water support
    action when root-zone stress appears, no default fungicide, then harvest
    in the first safe R8 dry window.
    """

    start_time: float | None = (
        datetime(2026, 5, 4, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
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
            profile_name="harbin_baseline_2026_seed_101",
            seed_type=SEED_TYPE,
            cultivar="黑农60",
            density_target_plants_m2=29.8,
            location="Harbin/Heilongjiang",
            start_date="2026-05-04",
        )

        weather.set_weather(
            date="2026-05-04",
            temp_c=16.0,
            humidity_pct=58.0,
            wind_speed_ms=2.4,
            rainfall_mm=0.0,
            solar_radiation=20.0,
            forecast=[
                {"date": "2026-05-05", "temp_c": 16.5, "humidity_pct": 56.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 20.5},
                {"date": "2026-05-06", "temp_c": 17.0, "humidity_pct": 55.0, "wind_speed_ms": 2.4, "rainfall_mm": 0.0, "solar_radiation": 21.0},
                {"date": "2026-05-07", "temp_c": 18.0, "humidity_pct": 54.0, "wind_speed_ms": 2.2, "rainfall_mm": 0.0, "solar_radiation": 21.0},
            ],
            avg_soil_vwc=0.30,
        )

        farm_world.set_season_phase("full_season")
        farm_world._inventory.seed_stock[SEED_TYPE] = 1000000
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
            r.soil_temp_c = 13.2 + (i % 4) * 0.25
            r.ndvi = 0.18
            r.yield_potential = 0.0
            r.pest_pressure_base = 0.02
            r.pest_pressure = 0.02
            r.disease_pressure_base = 0.025
            r.disease_pressure = 0.025
            r.nutrient_index = 0.75
            r.stand_fraction = 1.0

    def _after_daily_advance(self, prev: Any, label: str) -> Any:
        return prev

    def _after_named_step(self, prev: Any, label: str) -> Any:
        return prev

    def _advance_days(
        self,
        prev: Any,
        days: int,
        prefix: str,
    ) -> Any:
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

    def _plant_all_ridges(self, tractor: TractorApp, prev: Any) -> Any:
        for start in range(0, 64, 4):
            if start == 32:
                prev = (
                    tractor.load_seeds(SEED_TYPE, 300000)
                    .oracle()
                    .with_id("o_reload_heinong60_seed_after_31")
                    .depends_on(prev, delay_seconds=2)
                )
            end = start + 3
            prev = (
                tractor.plant_seeds(start, end, 4.0, SEED_SPACING_CM)
                .oracle()
                .with_id(f"o_plant_{start}_{end}")
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

    @staticmethod
    def _collect_event_graph(root: Any) -> list[Any]:
        """Return every event reachable from the root in dependency order."""
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
            "这是哈尔滨正常年份的黑农60高密度大豆full-season baseline。"
            "全田64条垄，目标是按真实生产语义完成播前准备、底肥、高密播种、"
            "出苗和密度检查、早期长势和营养检查、初花期按需营养检查、"
            "R5/R6水分检查、成熟收获、干燥和安全入库。"
            "正常年份下不要默认严重病害或干旱；每次管理前读取天气、土壤、冠层、"
            "病虫害或库存状态，再决定是否需要额外动作。"
        )

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )

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

            o_tractor_plant = tractor.get_status().oracle().with_id("o_tractor_before_planting").depends_on(o_after_prep, delay_seconds=1)
            o_load_seed = tractor.load_seeds(SEED_TYPE, 300000).oracle().with_id("o_load_heinong60_seed").depends_on(o_tractor_plant, delay_seconds=2)
            o_last_plant = self._plant_all_ridges(tractor, o_load_seed)
            o_commit_plant = farm_world.commit_daily_physics().oracle().with_id("o_commit_planting_physics").depends_on(o_last_plant, delay_seconds=1)
            o_after_plant = self._after_named_step(o_commit_plant, "after_high_density_planting")

            o_wait_emergence = self._advance_days(o_after_plant, 12, "o_wait_emergence")
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_check").depends_on(o_wait_emergence, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_check").depends_on(o_emergence_soil, delay_seconds=1)
            o_emergence_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_emergence_ndvi_check").depends_on(o_emergence_canopy, delay_seconds=2)
            o_robot_status_emergence = robot.check_status().oracle().with_id("o_robot_status_before_emergence_check").depends_on(o_emergence_ndvi, delay_seconds=1)
            o_emergence_robot = robot.inspect_emergence(0, 63).oracle().with_id("o_emergence_density_ground_check").depends_on(o_robot_status_emergence, delay_seconds=2)
            o_charge_robot_after_emergence = robot.charge().oracle().with_id("o_charge_robot_after_emergence_check").depends_on(o_emergence_ndvi, delay_seconds=1)

            o_wait_early = self._advance_days(o_charge_robot_after_emergence, 24, "o_wait_early_growth")
            o_early_soil = sensor.read_soil_sensors().oracle().with_id("o_early_soil_check").depends_on(o_wait_early, delay_seconds=1)
            o_early_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_early_growth_ndvi_check").depends_on(o_early_soil, delay_seconds=2)
            o_robot_status_early = robot.check_status().oracle().with_id("o_robot_status_before_early_health_check").depends_on(o_early_ndvi, delay_seconds=1)
            o_early_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_early_nutrient_health_check").depends_on(o_robot_status_early, delay_seconds=2)
            o_charge_robot_after_early = robot.charge().oracle().with_id("o_charge_robot_after_early_health_check").depends_on(o_early_health, delay_seconds=1)

            o_wait_r1 = self._advance_days(o_charge_robot_after_early, 22, "o_wait_r1_nutrient_window")
            o_r1_soil = sensor.read_soil_sensors().oracle().with_id("o_r1_soil_nutrient_check").depends_on(o_wait_r1, delay_seconds=1)
            o_r1_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_r1_ndvi_check").depends_on(o_r1_soil, delay_seconds=2)
            o_robot_status_r1 = robot.check_status().oracle().with_id("o_robot_status_before_r1_health_check").depends_on(o_r1_ndvi, delay_seconds=1)
            o_r1_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_r1_ground_nutrient_check").depends_on(o_robot_status_r1, delay_seconds=2)
            o_charge_robot_after_r1 = robot.charge().oracle().with_id("o_charge_robot_after_r1_health_check").depends_on(o_r1_health, delay_seconds=1)
            o_r1_topdress = farm_world.apply_fertigation(0, 63, nutrient_amount=R1_TOPDRESS_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r1_light_nutrient_topdress").depends_on(o_charge_robot_after_r1, delay_seconds=2)
            o_commit_r1 = farm_world.commit_daily_physics().oracle().with_id("o_commit_r1_nutrient_management").depends_on(o_r1_topdress, delay_seconds=1)
            o_after_r1 = self._after_named_step(o_commit_r1, "after_r1_light_nutrient_topdress")

            o_wait_mid = self._advance_days(o_after_r1, 24, "o_wait_midseason_scouting")
            o_mid_weather = weather.get_current_weather().oracle().with_id("o_midseason_weather").depends_on(o_wait_mid, delay_seconds=1)
            o_robot_status_mid_pests = robot.check_status().oracle().with_id("o_robot_status_before_midseason_pest_check").depends_on(o_mid_weather, delay_seconds=1)
            o_mid_pests = robot.inspect_pests(0, 63).oracle().with_id("o_midseason_pest_check").depends_on(o_robot_status_mid_pests, delay_seconds=2)
            o_charge_robot_mid = robot.charge().oracle().with_id("o_charge_robot_between_midseason_checks").depends_on(o_mid_pests, delay_seconds=1)
            o_wait_robot_mid_charge = system.advance_time(hours=1).oracle().with_id("o_wait_robot_midseason_recharge").depends_on(o_charge_robot_mid, delay_seconds=1)
            o_robot_status_mid_disease = robot.check_status().oracle().with_id("o_robot_status_before_midseason_disease_check").depends_on(o_wait_robot_mid_charge, delay_seconds=1)
            o_mid_disease = robot.inspect_crop_health(0, 63).oracle().with_id("o_midseason_disease_health_check").depends_on(o_robot_status_mid_disease, delay_seconds=2)

            o_pod_weather = weather.get_current_weather().oracle().with_id("o_r5_r6_weather_check").depends_on(o_mid_disease, delay_seconds=1)
            o_pod_forecast = weather.get_forecast(days=4).oracle().with_id("o_r5_r6_forecast_check").depends_on(o_pod_weather, delay_seconds=1)
            o_pod_soil = sensor.read_soil_sensors().oracle().with_id("o_r5_r6_soil_water_check").depends_on(o_pod_forecast, delay_seconds=1)
            o_pod_thermal = matrice.fly_survey(0, 63).oracle().with_id("o_r5_r6_thermal_check").depends_on(o_pod_soil, delay_seconds=2)
            o_r5_nutrient = farm_world.apply_fertigation(0, 63, nutrient_amount=R5_POD_FILL_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r5_pod_fill_nutrient_support").depends_on(o_pod_thermal, delay_seconds=2)
            o_irrigate_pod = field_ops.irrigate(0, 63, hours=R5_IRRIGATION_HOURS).oracle().with_id("o_r5_light_irrigation").depends_on(o_r5_nutrient, delay_seconds=2)
            o_wait_irrigation_response = system.advance_time(hours=6).oracle().with_id("o_wait_r5_irrigation_response").depends_on(o_irrigate_pod, delay_seconds=1)
            o_commit_podfill = farm_world.commit_daily_physics().oracle().with_id("o_commit_r5_water_management").depends_on(o_wait_irrigation_response, delay_seconds=1)
            o_after_podfill = self._after_named_step(o_commit_podfill, "after_r5_light_irrigation_and_nutrient")

            o_wait_maturity = self._advance_days(o_after_podfill, 39, "o_wait_r8_harvest_window")
            o_harvest_weather = weather.get_current_weather().oracle().with_id("o_harvest_weather_check").depends_on(o_wait_maturity, delay_seconds=1)
            o_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_harvest_forecast_check").depends_on(o_harvest_weather, delay_seconds=1)
            o_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_check_maturity_and_moisture").depends_on(o_harvest_forecast, delay_seconds=1)
            o_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_harvest_trafficability_soil_check").depends_on(o_harvest_overview, delay_seconds=1)
            o_detach = tractor.detach_implement().oracle().with_id("o_detach_grader_before_harvest").depends_on(o_harvest_soil, delay_seconds=1)
            o_attach_harvester = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_detach, delay_seconds=1)
            o_last_harvest = self._harvest_all_ridges(tractor, o_attach_harvester)
            o_commit_harvest = farm_world.commit_daily_physics().oracle().with_id("o_commit_recovered_yield").depends_on(o_last_harvest, delay_seconds=1)
            o_after_harvest = self._after_named_step(o_commit_harvest, "after_harvest_commit")
            o_dry = farm_world.dry_grain(target_moisture_pct=13.0).oracle().with_id("o_dry_grain_to_safe_storage").depends_on(o_after_harvest, delay_seconds=2)
            o_store = farm_world.store_grain().oracle().with_id("o_store_grain").depends_on(o_dry, delay_seconds=2)

            self.events = self._collect_event_graph(briefing)
