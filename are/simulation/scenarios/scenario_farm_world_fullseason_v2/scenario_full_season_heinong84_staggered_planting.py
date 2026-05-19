from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FieldOpsApp,
    FarmWorldApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.types import EventRegisterer


SCENARIO_ID = "scenario_full_season_heinong84_staggered_planting"
PROFILE_NAME = "harbin_heinong84_staggered_planting_seed_616"
RIDGE_WIDTH_M = 1.1

SEED_TYPE = "HEINONG84"
STANDARD_SPACING_CM = 7.9

EARLY_START = 0
EARLY_END = 20
MID_START = 21
MID_END = 42
LATE_START = 43
LATE_END = 63


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonHeinong84StaggeredPlanting(Scenario):
    """
    L3 full-season scenario: Harbin Heinong84 soybean staggered planting.

    The expert oracle uses the same field and cultivar across three planting
    windows, then harvests each zone only when the agent-facing checks support
    that zone's maturity and grain-moisture window.
    """

    start_time: float | None = (
        datetime(2026, 5, 5, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 175 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    nb_turns: int = 1

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

        farm_world.configure_physics_profile(
            profile_name=PROFILE_NAME,
            seed_type=SEED_TYPE,
            cultivar="黑农84标准密度/错期播种",
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
        farm_world._inventory.seed_stock[SEED_TYPE] = 1000000
        farm_world._inventory.fertilizer_kg = 2500.0
        farm_world._inventory.pesticide_liters = 2000.0
        farm_world._inventory.fuel_liters = 1000.0

        tractor._fuel_tank_l = 120.0
        tractor._seed_hopper = 0
        tractor._fertilizer_spreader_kg = 0.0
        tractor._pesticide_tank_l = 0.0
        tractor._fungicide_tank_l = 0.0
        mavic._battery_pct = 95.0

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

    def _plant_zone(
        self,
        tractor: TractorApp,
        prev: Any,
        start_ridge: int,
        end_ridge: int,
        id_prefix: str,
    ) -> Any:
        prev = (
            tractor.load_seeds(SEED_TYPE, 180000)
            .oracle()
            .with_id(f"{id_prefix}_load_seed")
            .depends_on(prev, delay_seconds=2)
        )
        for start in range(start_ridge, end_ridge + 1, 4):
            end = min(start + 3, end_ridge)
            prev = (
                tractor.plant_seeds(start, end, 4.0, STANDARD_SPACING_CM)
                .oracle()
                .with_id(f"{id_prefix}_plant_{start}_{end}")
                .depends_on(prev, delay_seconds=2)
            )
        return prev

    def _harvest_zone(
        self,
        tractor: TractorApp,
        farm_world: FarmWorldApp,
        prev: Any,
        start_ridge: int,
        end_ridge: int,
        id_prefix: str,
    ) -> Any:
        for start in range(start_ridge, end_ridge + 1, 4):
            end = min(start + 3, end_ridge)
            prev = (
                tractor.harvest(start, end)
                .oracle()
                .with_id(f"{id_prefix}_harvest_{start}_{end}")
                .depends_on(prev, delay_seconds=2)
            )
            prev = (
                tractor.unload_grain()
                .oracle()
                .with_id(f"{id_prefix}_unload_after_{end}")
                .depends_on(prev, delay_seconds=1)
            )
        prev = (
            farm_world.dry_grain(target_moisture_pct=13.0)
            .oracle()
            .with_id(f"{id_prefix}_dry_grain")
            .depends_on(prev, delay_seconds=2)
        )
        prev = (
            farm_world.store_grain()
            .oracle()
            .with_id(f"{id_prefix}_store_grain")
            .depends_on(prev, delay_seconds=2)
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
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        field_ops = self.get_typed_app(FieldOpsApp)

        briefing_text = (
            "这是哈尔滨黑农84错期播种full-season场景。全田64条垄分为早播0-20、"
            "中播21-42、晚播43-63，三个播期相隔7天。春夏正常，无默认病虫害或"
            "水肥陷阱。请根据weather、soil、canopy、NDVI、ground inspection和"
            "farm overview/ridge range返回值判断各区状态；后续作业必须与工具返回值匹配，"
            "尤其收获要按实际进入窗口的区分批进行，收上来的粮食每批及时卸粮、干燥和入库。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)

            o_weather_0 = weather.get_current_weather().oracle().with_id("o_weather_before_prep").depends_on(briefing, delay_seconds=2)
            o_forecast_0 = weather.get_forecast(days=5).oracle().with_id("o_forecast_before_prep").depends_on(o_weather_0, delay_seconds=1)
            o_soil_0 = sensor.read_soil_sensors().oracle().with_id("o_soil_before_prep").depends_on(o_forecast_0, delay_seconds=1)
            o_inventory_0 = farm_world.get_inventory().oracle().with_id("o_inventory_before_prep").depends_on(o_soil_0, delay_seconds=1)
            o_tractor_0 = tractor.get_status().oracle().with_id("o_tractor_before_prep").depends_on(o_inventory_0, delay_seconds=1)

            o_attach_grader = tractor.attach_implement("grader").oracle().with_id("o_attach_grader").depends_on(o_tractor_0, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(o_attach_grader, delay_seconds=2)
            o_load_base = tractor.load_fertilizer(250.0).oracle().with_id("o_load_base_fertilizer").depends_on(o_level, delay_seconds=1)
            o_base = tractor.base_fertilize().oracle().with_id("o_apply_base_fertilizer").depends_on(o_load_base, delay_seconds=2)
            o_ridge = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id("o_form_1p1m_ridges").depends_on(o_base, delay_seconds=2)
            o_commit_prep = farm_world.commit_daily_physics().oracle().with_id("o_commit_prep_physics").depends_on(o_ridge, delay_seconds=1)
            o_after_prep = self._after_named_step(o_commit_prep, "after_prep_and_base_fertilizer")

            o_early_weather = weather.get_current_weather().oracle().with_id("o_early_plant_weather_check").depends_on(o_after_prep, delay_seconds=1)
            o_early_forecast = weather.get_forecast(days=3).oracle().with_id("o_early_plant_forecast_check").depends_on(o_early_weather, delay_seconds=1)
            o_early_soil = sensor.read_soil_sensors().oracle().with_id("o_early_plant_soil_check").depends_on(o_early_forecast, delay_seconds=1)
            o_early_tractor = tractor.get_status().oracle().with_id("o_tractor_before_early_planting").depends_on(o_early_soil, delay_seconds=1)
            o_early_plant = self._plant_zone(tractor, o_early_tractor, EARLY_START, EARLY_END, "o_early_zone")
            o_commit_early = farm_world.commit_daily_physics().oracle().with_id("o_commit_early_zone_planting").depends_on(o_early_plant, delay_seconds=1)
            o_after_early = self._after_named_step(o_commit_early, "after_early_zone_planting")

            o_wait_mid_plant = self._advance_days(o_after_early, 7, "o_wait_mid_planting_window")
            o_mid_weather = weather.get_current_weather().oracle().with_id("o_mid_plant_weather_check").depends_on(o_wait_mid_plant, delay_seconds=1)
            o_mid_forecast = weather.get_forecast(days=3).oracle().with_id("o_mid_plant_forecast_check").depends_on(o_mid_weather, delay_seconds=1)
            o_mid_soil = sensor.read_soil_sensors().oracle().with_id("o_mid_plant_soil_check").depends_on(o_mid_forecast, delay_seconds=1)
            o_mid_tractor = tractor.get_status().oracle().with_id("o_tractor_before_mid_planting").depends_on(o_mid_soil, delay_seconds=1)
            o_mid_plant = self._plant_zone(tractor, o_mid_tractor, MID_START, MID_END, "o_mid_zone")
            o_commit_mid = farm_world.commit_daily_physics().oracle().with_id("o_commit_mid_zone_planting").depends_on(o_mid_plant, delay_seconds=1)
            o_after_mid = self._after_named_step(o_commit_mid, "after_mid_zone_planting")

            o_wait_late_plant = self._advance_days(o_after_mid, 7, "o_wait_late_planting_window")
            o_late_weather = weather.get_current_weather().oracle().with_id("o_late_plant_weather_check").depends_on(o_wait_late_plant, delay_seconds=1)
            o_late_forecast = weather.get_forecast(days=3).oracle().with_id("o_late_plant_forecast_check").depends_on(o_late_weather, delay_seconds=1)
            o_late_soil = sensor.read_soil_sensors().oracle().with_id("o_late_plant_soil_check").depends_on(o_late_forecast, delay_seconds=1)
            o_late_seedbed_water = field_ops.irrigate(LATE_START, LATE_END, hours=1.0).oracle().with_id("o_late_seedbed_targeted_irrigation_after_dry_soil_check").depends_on(o_late_soil, delay_seconds=1)
            o_wait_late_seedbed_response = self.get_typed_app(SystemApp).advance_time(hours=6).oracle().with_id("o_wait_late_seedbed_irrigation_response").depends_on(o_late_seedbed_water, delay_seconds=1)
            o_late_soil_recheck = sensor.read_soil_sensors().oracle().with_id("o_late_plant_soil_recheck_after_seedbed_irrigation").depends_on(o_wait_late_seedbed_response, delay_seconds=1)
            o_late_tractor = tractor.get_status().oracle().with_id("o_tractor_before_late_planting").depends_on(o_late_soil_recheck, delay_seconds=1)
            o_late_plant = self._plant_zone(tractor, o_late_tractor, LATE_START, LATE_END, "o_late_zone")
            o_commit_late = farm_world.commit_daily_physics().oracle().with_id("o_commit_late_zone_planting").depends_on(o_late_plant, delay_seconds=1)
            o_after_late = self._after_named_step(o_commit_late, "after_late_zone_planting")

            o_wait_emergence = self._advance_days(o_after_late, 16, "o_wait_whole_field_emergence")
            o_emergence_overview = farm_world.get_farm_overview().oracle().with_id("o_emergence_farm_overview").depends_on(o_wait_emergence, delay_seconds=1)
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_check").depends_on(o_emergence_overview, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_check").depends_on(o_emergence_soil, delay_seconds=1)
            o_robot_status_emergence = robot.check_status().oracle().with_id("o_robot_status_before_emergence_check").depends_on(o_emergence_canopy, delay_seconds=1)
            o_early_emergence = robot.inspect_emergence(EARLY_START, min(EARLY_START + 7, EARLY_END)).oracle().with_id("o_early_zone_emergence_ground_check").depends_on(o_robot_status_emergence, delay_seconds=2)
            o_mid_emergence = robot.inspect_emergence(MID_START, min(MID_START + 7, MID_END)).oracle().with_id("o_mid_zone_emergence_ground_check").depends_on(o_early_emergence, delay_seconds=2)
            o_late_emergence = robot.inspect_emergence(LATE_START, min(LATE_START + 7, LATE_END)).oracle().with_id("o_late_zone_emergence_ground_check").depends_on(o_mid_emergence, delay_seconds=2)
            o_emergence_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_whole_field_emergence_ndvi").depends_on(o_late_emergence, delay_seconds=2)
            o_charge_mavic_emergence = mavic.charge().oracle().with_id("o_charge_mavic_after_emergence_ndvi").depends_on(o_emergence_ndvi, delay_seconds=1)
            o_charge_robot_emergence = robot.charge().oracle().with_id("o_charge_robot_after_emergence_check").depends_on(o_charge_mavic_emergence, delay_seconds=1)

            o_wait_stage_split = self._advance_days(o_charge_robot_emergence, 42, "o_wait_stage_split_scout")
            o_stage_overview = farm_world.get_farm_overview().oracle().with_id("o_stage_split_farm_overview").depends_on(o_wait_stage_split, delay_seconds=1)
            o_early_range = farm_world.get_ridge_range_state(EARLY_START, EARLY_END).oracle().with_id("o_stage_split_early_range_state").depends_on(o_stage_overview, delay_seconds=1)
            o_mid_range = farm_world.get_ridge_range_state(MID_START, MID_END).oracle().with_id("o_stage_split_mid_range_state").depends_on(o_early_range, delay_seconds=1)
            o_late_range = farm_world.get_ridge_range_state(LATE_START, LATE_END).oracle().with_id("o_stage_split_late_range_state").depends_on(o_mid_range, delay_seconds=1)
            o_stage_soil = sensor.read_soil_sensors().oracle().with_id("o_stage_split_soil_check").depends_on(o_late_range, delay_seconds=1)
            o_stage_canopy = sensor.read_canopy_sensors().oracle().with_id("o_stage_split_canopy_check").depends_on(o_stage_soil, delay_seconds=1)
            o_stage_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_stage_split_whole_field_ndvi").depends_on(o_stage_canopy, delay_seconds=2)
            o_robot_status_stage = robot.check_status().oracle().with_id("o_robot_status_before_stage_split_health").depends_on(o_stage_ndvi, delay_seconds=1)
            o_stage_health_early = robot.inspect_crop_health(EARLY_START, min(EARLY_START + 7, EARLY_END)).oracle().with_id("o_stage_split_early_health").depends_on(o_robot_status_stage, delay_seconds=2)
            o_stage_health_mid = robot.inspect_crop_health(MID_START, min(MID_START + 7, MID_END)).oracle().with_id("o_stage_split_mid_health").depends_on(o_stage_health_early, delay_seconds=2)
            o_stage_health_late = robot.inspect_crop_health(LATE_START, min(LATE_START + 7, LATE_END)).oracle().with_id("o_stage_split_late_health").depends_on(o_stage_health_mid, delay_seconds=2)
            o_charge_robot_stage = robot.charge().oracle().with_id("o_charge_robot_after_stage_split_health").depends_on(o_stage_health_late, delay_seconds=1)
            o_charge_mavic_stage = mavic.charge().oracle().with_id("o_charge_mavic_after_stage_split_ndvi").depends_on(o_charge_robot_stage, delay_seconds=1)

            o_wait_early_harvest = self._advance_days(o_charge_mavic_stage, 47, "o_wait_early_harvest_window")
            o_early_harvest_weather = weather.get_current_weather().oracle().with_id("o_early_harvest_weather_check").depends_on(o_wait_early_harvest, delay_seconds=1)
            o_early_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_early_harvest_forecast_check").depends_on(o_early_harvest_weather, delay_seconds=1)
            o_early_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_early_harvest_soil_check").depends_on(o_early_harvest_forecast, delay_seconds=1)
            o_early_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_early_harvest_farm_overview").depends_on(o_early_harvest_soil, delay_seconds=1)
            o_early_harvest_range = farm_world.get_ridge_range_state(EARLY_START, EARLY_END).oracle().with_id("o_early_harvest_range_state").depends_on(o_early_harvest_overview, delay_seconds=1)
            o_detach = tractor.detach_implement().oracle().with_id("o_detach_grader_before_harvest").depends_on(o_early_harvest_range, delay_seconds=1)
            o_attach_harvester = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_detach, delay_seconds=1)
            o_early_harvest = self._harvest_zone(tractor, farm_world, o_attach_harvester, EARLY_START, EARLY_END, "o_early_zone")
            o_after_early_harvest = self._after_named_step(o_early_harvest, "after_early_zone_harvest_store")

            o_wait_mid_harvest = self._advance_days(o_after_early_harvest, 7, "o_wait_mid_harvest_window")
            o_mid_harvest_weather = weather.get_current_weather().oracle().with_id("o_mid_harvest_weather_check").depends_on(o_wait_mid_harvest, delay_seconds=1)
            o_mid_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_mid_harvest_forecast_check").depends_on(o_mid_harvest_weather, delay_seconds=1)
            o_mid_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_mid_harvest_soil_check").depends_on(o_mid_harvest_forecast, delay_seconds=1)
            o_mid_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_mid_harvest_farm_overview").depends_on(o_mid_harvest_soil, delay_seconds=1)
            o_mid_harvest_range = farm_world.get_ridge_range_state(MID_START, MID_END).oracle().with_id("o_mid_harvest_range_state").depends_on(o_mid_harvest_overview, delay_seconds=1)
            o_mid_harvest = self._harvest_zone(tractor, farm_world, o_mid_harvest_range, MID_START, MID_END, "o_mid_zone")
            o_after_mid_harvest = self._after_named_step(o_mid_harvest, "after_mid_zone_harvest_store")

            o_wait_late_harvest = self._advance_days(o_after_mid_harvest, 4, "o_wait_late_harvest_window")
            o_late_harvest_weather = weather.get_current_weather().oracle().with_id("o_late_harvest_weather_check").depends_on(o_wait_late_harvest, delay_seconds=1)
            o_late_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_late_harvest_forecast_check").depends_on(o_late_harvest_weather, delay_seconds=1)
            o_late_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_late_harvest_soil_check").depends_on(o_late_harvest_forecast, delay_seconds=1)
            o_late_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_late_harvest_farm_overview").depends_on(o_late_harvest_soil, delay_seconds=1)
            o_late_harvest_range = farm_world.get_ridge_range_state(LATE_START, LATE_END).oracle().with_id("o_late_harvest_range_state").depends_on(o_late_harvest_overview, delay_seconds=1)
            o_late_harvest = self._harvest_zone(tractor, farm_world, o_late_harvest_range, LATE_START, LATE_END, "o_late_zone")
            o_commit_final = farm_world.commit_daily_physics().oracle().with_id("o_commit_final_harvest_state").depends_on(o_late_harvest, delay_seconds=1)
            self._after_named_step(o_commit_final, "after_all_zones_harvested_stored")

            self.events = self._collect_event_graph(briefing)
