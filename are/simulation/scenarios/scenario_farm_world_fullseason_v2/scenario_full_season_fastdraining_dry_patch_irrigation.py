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
from are.simulation.physics import SoilHydraulicModifier
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.types import EventRegisterer


SCENARIO_ID = "scenario_full_season_fastdraining_dry_patch_irrigation"
PROFILE_NAME = "harbin_fastdraining_dry_patch_seed_515"
RIDGE_WIDTH_M = 1.1

SEED_TYPE = "HEINONG84"
STANDARD_SPACING_CM = 7.9

AFFECTED_START = 20
AFFECTED_END = 31
REFERENCE_WEST_START = 0
REFERENCE_WEST_END = 11
REFERENCE_EAST_START = 44
REFERENCE_EAST_END = 53

R1_NUTRIENT_AMOUNT = 0.22
R5_IRRIGATION_HOURS = 3.0


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonFastDrainingDryPatchIrrigation(Scenario):
    """
    L3 full-season scenario: Harbin Heinong84 soybean with a local
    fast-draining dry patch during R5/R6.

    The production scenario keeps the expert oracle path fixed. Daily trace
    probes live in the companion script.
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
            seed_type=SEED_TYPE,
            cultivar="黑农84标准密度/局部快排水土壤",
            density_target_plants_m2=23.0,
            location="Harbin/Heilongjiang",
            start_date="2026-05-05",
        )
        farm_world.physics.soil.set_hydraulic_modifiers(
            {
                ridge_id: SoilHydraulicModifier(
                    field_capacity_vwc=0.30,
                    top_drainage_rate=0.74,
                    root_drainage_rate=0.55,
                    rainfall_capture_efficiency=0.78,
                    irrigation_efficiency=0.88,
                    max_infiltration_mm_day=32.0,
                )
                for ridge_id in range(AFFECTED_START, AFFECTED_END + 1)
            }
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
            ridge = farm_world.get_ridge(i)
            ridge.planted = False
            ridge.seed_type = None
            ridge.days_since_planted = 0
            ridge.growth_stage = "NOT_PLANTED"
            ridge.soil_vwc = 0.30 + ((i % 5) - 2) * 0.003
            ridge.soil_temp_c = 13.1 + (i % 4) * 0.25
            ridge.ndvi = 0.18
            ridge.yield_potential = 0.0
            ridge.pest_pressure_base = 0.02
            ridge.pest_pressure = 0.02
            ridge.disease_pressure_base = 0.02
            ridge.disease_pressure = 0.02
            ridge.nutrient_index = 0.76
            ridge.stand_fraction = 1.0

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
            "这是哈尔滨局部fast-draining土壤加R5/R6干旱的大豆full-season场景。"
            "全田64条垄统一种植黑农84，标准密度；春季和6月正常，R5/R6出现干旱。"
            "部分中部垄因为土壤持水能力差，比其他区域更早进入水分胁迫。"
            "不要根据全田平均值灌溉；请用soil sensors、热红外和冠层状态定位真正缺水的affected ridges，"
            "排除病虫害和营养问题后做targeted irrigation，然后完成成熟收获、干燥和安全储藏。"
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

            o_tractor_plant = tractor.get_status().oracle().with_id("o_tractor_before_heinong84_planting").depends_on(o_after_prep, delay_seconds=1)
            o_plant_first = self._plant_range(tractor, o_tractor_plant, 0, 31, SEED_TYPE, STANDARD_SPACING_CM, "o_heinong84_first_half")
            o_plant_second = self._plant_range(tractor, o_plant_first, 32, 63, SEED_TYPE, STANDARD_SPACING_CM, "o_heinong84_second_half")
            o_commit_plant = farm_world.commit_daily_physics().oracle().with_id("o_commit_heinong84_planting_physics").depends_on(o_plant_second, delay_seconds=1)
            o_after_plant = self._after_named_step(o_commit_plant, "after_heinong84_field_planting")

            o_wait_emergence = self._advance_days(o_after_plant, 12, "o_wait_emergence")
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_check").depends_on(o_wait_emergence, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_check").depends_on(o_emergence_soil, delay_seconds=1)
            o_robot_status_emergence = robot.check_status().oracle().with_id("o_robot_status_before_emergence_check").depends_on(o_emergence_canopy, delay_seconds=1)
            o_emergence_all = robot.inspect_emergence(0, 63).oracle().with_id("o_whole_field_emergence_check").depends_on(o_robot_status_emergence, delay_seconds=2)
            o_emergence_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_whole_field_emergence_ndvi").depends_on(o_emergence_all, delay_seconds=2)
            o_charge_mavic_after_emergence = mavic.charge().oracle().with_id("o_charge_mavic_after_emergence_ndvi").depends_on(o_emergence_ndvi, delay_seconds=1)
            o_charge_robot_after_emergence = robot.charge().oracle().with_id("o_charge_robot_after_emergence_check").depends_on(o_charge_mavic_after_emergence, delay_seconds=1)

            o_wait_early = self._advance_days(o_charge_robot_after_emergence, 24, "o_wait_early_growth")
            o_early_soil = sensor.read_soil_sensors().oracle().with_id("o_early_soil_check").depends_on(o_wait_early, delay_seconds=1)
            o_early_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_early_growth_ndvi_check").depends_on(o_early_soil, delay_seconds=2)
            o_robot_status_early = robot.check_status().oracle().with_id("o_robot_status_before_early_health_check").depends_on(o_early_ndvi, delay_seconds=1)
            o_early_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_early_nutrient_health_check").depends_on(o_robot_status_early, delay_seconds=2)
            o_charge_robot_after_early = robot.charge().oracle().with_id("o_charge_robot_after_early_health_check").depends_on(o_early_health, delay_seconds=1)
            o_charge_mavic_after_early = mavic.charge().oracle().with_id("o_charge_mavic_after_early_growth").depends_on(o_charge_robot_after_early, delay_seconds=1)

            o_wait_r1 = self._advance_days(o_charge_mavic_after_early, 22, "o_wait_r1_nutrient_window")
            o_r1_soil = sensor.read_soil_sensors().oracle().with_id("o_r1_soil_nutrient_check").depends_on(o_wait_r1, delay_seconds=1)
            o_r1_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_r1_ndvi_check").depends_on(o_r1_soil, delay_seconds=2)
            o_robot_status_r1 = robot.check_status().oracle().with_id("o_robot_status_before_r1_health_check").depends_on(o_r1_ndvi, delay_seconds=1)
            o_r1_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_r1_ground_nutrient_check").depends_on(o_robot_status_r1, delay_seconds=2)
            o_r1_topdress = farm_world.apply_fertigation(0, 63, nutrient_amount=R1_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r1_light_nutrient_topdress").depends_on(o_r1_health, delay_seconds=2)
            o_commit_r1 = farm_world.commit_daily_physics().oracle().with_id("o_commit_r1_nutrient_management").depends_on(o_r1_topdress, delay_seconds=1)
            o_after_r1 = self._after_named_step(o_commit_r1, "after_r1_light_nutrient_topdress")
            o_charge_robot_after_r1 = robot.charge().oracle().with_id("o_charge_robot_after_r1_health_check").depends_on(o_after_r1, delay_seconds=1)
            o_charge_mavic_after_r1 = mavic.charge().oracle().with_id("o_charge_mavic_after_r1_ndvi").depends_on(o_charge_robot_after_r1, delay_seconds=1)

            o_wait_mid = self._advance_days(o_charge_mavic_after_r1, 14, "o_wait_midseason_scout")
            o_mid_weather = weather.get_current_weather().oracle().with_id("o_midseason_weather_check").depends_on(o_wait_mid, delay_seconds=1)
            o_mid_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_midseason_whole_field_ndvi").depends_on(o_mid_weather, delay_seconds=2)
            o_mid_robot_status = robot.check_status().oracle().with_id("o_robot_status_before_midseason_pest_check").depends_on(o_mid_ndvi, delay_seconds=1)
            o_mid_pests = robot.inspect_pests(0, 63).oracle().with_id("o_midseason_pest_check").depends_on(o_mid_robot_status, delay_seconds=2)
            o_charge_robot_after_mid = robot.charge().oracle().with_id("o_charge_robot_after_midseason_scout").depends_on(o_mid_pests, delay_seconds=1)
            o_charge_mavic_after_mid = mavic.charge().oracle().with_id("o_charge_mavic_after_midseason_scout").depends_on(o_charge_robot_after_mid, delay_seconds=1)

            o_wait_r5 = self._advance_days(o_charge_mavic_after_mid, 23, "o_wait_r5_r6_dry_patch_window")
            o_pod_weather = weather.get_current_weather().oracle().with_id("o_r5_r6_weather_check").depends_on(o_wait_r5, delay_seconds=1)
            o_pod_forecast = weather.get_forecast(days=4).oracle().with_id("o_r5_r6_forecast_check").depends_on(o_pod_weather, delay_seconds=1)
            o_pod_soil = sensor.read_soil_sensors().oracle().with_id("o_r5_r6_soil_water_check").depends_on(o_pod_forecast, delay_seconds=1)
            o_pod_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r5_r6_canopy_check").depends_on(o_pod_soil, delay_seconds=1)
            o_affected_ndvi = mavic.fly_survey(AFFECTED_START, AFFECTED_END).oracle().with_id("o_affected_patch_ndvi_check").depends_on(o_pod_canopy, delay_seconds=2)
            o_west_reference_ndvi = mavic.fly_survey(REFERENCE_WEST_START, REFERENCE_WEST_END).oracle().with_id("o_west_reference_ndvi_check").depends_on(o_affected_ndvi, delay_seconds=2)
            o_east_reference_ndvi = mavic.fly_survey(REFERENCE_EAST_START, REFERENCE_EAST_END).oracle().with_id("o_east_reference_ndvi_check").depends_on(o_west_reference_ndvi, delay_seconds=2)
            o_affected_thermal = matrice.fly_survey(AFFECTED_START, AFFECTED_END).oracle().with_id("o_affected_patch_thermal_check").depends_on(o_east_reference_ndvi, delay_seconds=2)
            o_reference_thermal = matrice.fly_survey(REFERENCE_EAST_START, REFERENCE_EAST_END).oracle().with_id("o_reference_thermal_check").depends_on(o_affected_thermal, delay_seconds=2)
            o_robot_status_dry_patch = robot.check_status().oracle().with_id("o_robot_status_before_dry_patch_check").depends_on(o_reference_thermal, delay_seconds=1)
            o_affected_health = robot.inspect_crop_health(AFFECTED_START, AFFECTED_END).oracle().with_id("o_affected_patch_health_check").depends_on(o_robot_status_dry_patch, delay_seconds=2)
            o_affected_pests = robot.inspect_pests(AFFECTED_START, AFFECTED_END).oracle().with_id("o_affected_patch_rule_out_pests").depends_on(o_affected_health, delay_seconds=2)
            o_irrigate_patch = field_ops.irrigate(AFFECTED_START, AFFECTED_END, hours=R5_IRRIGATION_HOURS).oracle().with_id("o_targeted_irrigation_20_31").depends_on(o_affected_pests, delay_seconds=2)
            o_wait_irrigation_response = system.advance_time(hours=6).oracle().with_id("o_wait_targeted_irrigation_response").depends_on(o_irrigate_patch, delay_seconds=1)
            o_recheck_soil = sensor.read_soil_sensors().oracle().with_id("o_targeted_irrigation_soil_recheck").depends_on(o_wait_irrigation_response, delay_seconds=1)
            o_recheck_thermal = matrice.fly_survey(AFFECTED_START, AFFECTED_END).oracle().with_id("o_targeted_irrigation_thermal_recheck").depends_on(o_recheck_soil, delay_seconds=2)
            o_commit_irrigation = farm_world.commit_daily_physics().oracle().with_id("o_commit_targeted_irrigation_management").depends_on(o_recheck_thermal, delay_seconds=1)
            o_after_irrigation = self._after_named_step(o_commit_irrigation, "after_targeted_irrigation_20_31")

            o_wait_main_harvest = self._advance_days(o_after_irrigation, 31, "o_wait_main_harvest_window")
            o_main_harvest_weather = weather.get_current_weather().oracle().with_id("o_main_harvest_weather_check").depends_on(o_wait_main_harvest, delay_seconds=1)
            o_main_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_main_harvest_forecast_check").depends_on(o_main_harvest_weather, delay_seconds=1)
            o_main_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_check_main_maturity_and_grain_moisture").depends_on(o_main_harvest_forecast, delay_seconds=1)
            o_main_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_main_harvest_trafficability_soil_check").depends_on(o_main_harvest_overview, delay_seconds=1)
            o_detach = tractor.detach_implement().oracle().with_id("o_detach_grader_before_main_harvest").depends_on(o_main_harvest_soil, delay_seconds=1)
            o_attach_harvester = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_detach, delay_seconds=1)
            o_main_harvest = self._harvest_ridge_batches(
                tractor,
                o_attach_harvester,
                [(0, 19), (32, 63)],
                "o_main_harvest",
            )
            o_commit_main_harvest = farm_world.commit_daily_physics().oracle().with_id("o_commit_main_harvest_recovery").depends_on(o_main_harvest, delay_seconds=1)
            o_after_main_harvest = self._after_named_step(o_commit_main_harvest, "after_main_harvest_commit")

            o_wait_affected_harvest = self._advance_days(o_after_main_harvest, 21, "o_wait_affected_patch_harvest_window")
            o_affected_harvest_weather = weather.get_current_weather().oracle().with_id("o_affected_harvest_weather_check").depends_on(o_wait_affected_harvest, delay_seconds=1)
            o_affected_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_affected_harvest_forecast_check").depends_on(o_affected_harvest_weather, delay_seconds=1)
            o_affected_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_check_affected_maturity_and_grain_moisture").depends_on(o_affected_harvest_forecast, delay_seconds=1)
            o_affected_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_affected_harvest_trafficability_soil_check").depends_on(o_affected_harvest_overview, delay_seconds=1)
            o_affected_harvest = self._harvest_ridge_batches(
                tractor,
                o_affected_harvest_soil,
                [(AFFECTED_START, AFFECTED_END)],
                "o_affected_harvest",
            )
            o_commit_harvest = farm_world.commit_daily_physics().oracle().with_id("o_commit_all_recovered_yield").depends_on(o_affected_harvest, delay_seconds=1)
            o_after_harvest = self._after_named_step(o_commit_harvest, "after_all_harvest_commit")
            o_dry = farm_world.dry_grain(target_moisture_pct=13.0).oracle().with_id("o_dry_grain_to_safe_storage").depends_on(o_after_harvest, delay_seconds=2)
            o_store = farm_world.store_grain().oracle().with_id("o_store_grain").depends_on(o_dry, delay_seconds=2)

            self.events = self._collect_event_graph(briefing)
