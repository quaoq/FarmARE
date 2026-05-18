from __future__ import annotations

from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import DroneApp, FarmWorldApp, RobotApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    HEINONG84_SPACING_CM,
    RIDGE_WIDTH_M,
    advance_days,
    collect_event_graph,
    configure_common_field,
    harbin_start_time,
    harvest_range,
    install_common_farm_apps,
    plant_range,
)
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.types import EventRegisterer


SCENARIO_ID = "scenario_full_season_early_vs_standard_late_rain_harvest"
PROFILE_NAME = "harbin_early_standard_late_rain_seed_919"
A_SEED_TYPE = "HEIKE71"
B_SEED_TYPE = "HEINONG84"
A_START = 0
A_END = 31
B_START = 32
B_END = 63


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonEarlyVsStandardLateRainHarvest(Scenario):
    """A/B maturity split where late rain changes harvest-window decisions."""

    start_time: float | None = harbin_start_time()
    duration: float | None = 175 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    nb_turns: int = 1

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        install_common_farm_apps(self, thermal=True)
        configure_common_field(
            self,
            profile_name=PROFILE_NAME,
            cultivar="A区黑科71早熟品种/B区黑农84标准品种/晚季降雨收获窗口",
            seed_stocks={A_SEED_TYPE: 600000, B_SEED_TYPE: 700000},
            pesticide_liters=800.0,
            tractor_fuel_l=190.0,
            initial_vwc=0.30,
        )

    def _after_named_step(self, prev: Any, label: str) -> Any:
        return prev

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)

        briefing_text = (
            "你负责完成一个哈尔滨大豆full-season任务。全田分A/B两区，A区0-31垄种黑科71，"
            "B区32-63垄种黑农84。按真实农事流程完成播前检查、整地、基肥、分区播种、"
            "生长期巡查和收获。收获前必须读取天气、预报、土壤、全田概览和分区状态；"
            "只能收已经成熟、籽粒水分合格且田间可通行的区。每批收获后立即卸粮、干燥、入库，"
            "不能因为一个区ready就全田统一收。"
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

            o_plant_weather = weather.get_current_weather().oracle().with_id("o_plant_weather_check").depends_on(o_ridge, delay_seconds=1)
            o_plant_forecast = weather.get_forecast(days=3).oracle().with_id("o_plant_forecast_check").depends_on(o_plant_weather, delay_seconds=1)
            o_plant_soil = sensor.read_soil_sensors().oracle().with_id("o_plant_soil_check").depends_on(o_plant_forecast, delay_seconds=1)
            o_plant_tractor = tractor.get_status().oracle().with_id("o_tractor_before_planting").depends_on(o_plant_soil, delay_seconds=1)
            o_plant_a = plant_range(
                tractor,
                o_plant_tractor,
                start_ridge=A_START,
                end_ridge=A_END,
                seed_type=A_SEED_TYPE,
                spacing_cm=HEINONG84_SPACING_CM,
                id_prefix="o_a_early_zone",
            )
            o_plant_b = plant_range(
                tractor,
                o_plant_a,
                start_ridge=B_START,
                end_ridge=B_END,
                seed_type=B_SEED_TYPE,
                spacing_cm=HEINONG84_SPACING_CM,
                id_prefix="o_b_standard_zone",
            )
            o_commit_plant = farm_world.commit_daily_physics().oracle().with_id("o_commit_planting").depends_on(o_plant_b, delay_seconds=1)
            o_after_plant = self._after_named_step(o_commit_plant, "after_ab_zone_planting")

            o_wait_emergence = advance_days(self, o_after_plant, 16, "o_wait_emergence")
            o_emergence_overview = farm_world.get_farm_overview().oracle().with_id("o_emergence_farm_overview").depends_on(o_wait_emergence, delay_seconds=1)
            o_a_emergence = farm_world.get_ridge_range_state(A_START, A_END).oracle().with_id("o_a_emergence_range_state").depends_on(o_emergence_overview, delay_seconds=1)
            o_b_emergence = farm_world.get_ridge_range_state(B_START, B_END).oracle().with_id("o_b_emergence_range_state").depends_on(o_a_emergence, delay_seconds=1)
            o_emergence_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_emergence_ndvi_survey").depends_on(o_b_emergence, delay_seconds=2)
            o_robot_check = robot.inspect_emergence(0, 15).oracle().with_id("o_emergence_ground_check").depends_on(o_emergence_ndvi, delay_seconds=2)

            o_wait_split = advance_days(self, o_robot_check, 45, "o_wait_ab_stage_split")
            o_split_overview = farm_world.get_farm_overview().oracle().with_id("o_ab_stage_split_farm_overview").depends_on(o_wait_split, delay_seconds=1)
            o_a_split = farm_world.get_ridge_range_state(A_START, A_END).oracle().with_id("o_a_stage_split_range_state").depends_on(o_split_overview, delay_seconds=1)
            o_b_split = farm_world.get_ridge_range_state(B_START, B_END).oracle().with_id("o_b_stage_split_range_state").depends_on(o_a_split, delay_seconds=1)
            o_split_soil = sensor.read_soil_sensors().oracle().with_id("o_ab_stage_split_soil_check").depends_on(o_b_split, delay_seconds=1)
            o_split_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_ab_stage_split_ndvi").depends_on(o_split_soil, delay_seconds=2)

            o_wait_a_harvest = advance_days(self, o_split_ndvi, 71, "o_wait_a_early_harvest_window")
            o_a_weather = weather.get_current_weather().oracle().with_id("o_a_harvest_weather_check").depends_on(o_wait_a_harvest, delay_seconds=1)
            o_a_forecast = weather.get_forecast(days=5).oracle().with_id("o_a_harvest_forecast_check").depends_on(o_a_weather, delay_seconds=1)
            o_a_soil = sensor.read_soil_sensors().oracle().with_id("o_a_harvest_soil_check").depends_on(o_a_forecast, delay_seconds=1)
            o_a_overview = farm_world.get_farm_overview().oracle().with_id("o_a_harvest_farm_overview").depends_on(o_a_soil, delay_seconds=1)
            o_a_range = farm_world.get_ridge_range_state(A_START, A_END).oracle().with_id("o_a_harvest_range_state").depends_on(o_a_overview, delay_seconds=1)
            o_b_not_ready = farm_world.get_ridge_range_state(B_START, B_END).oracle().with_id("o_b_same_day_not_ready_or_wetter").depends_on(o_a_range, delay_seconds=1)
            o_detach = tractor.detach_implement().oracle().with_id("o_detach_before_a_harvest").depends_on(o_b_not_ready, delay_seconds=1)
            o_attach = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester_for_a").depends_on(o_detach, delay_seconds=1)
            o_harvest_a = harvest_range(tractor, farm_world, o_attach, start_ridge=A_START, end_ridge=A_END, id_prefix="o_a_early_zone")
            o_after_a_harvest = self._after_named_step(o_harvest_a, "after_a_heike71_harvest_store")

            o_wait_b_harvest = advance_days(self, o_after_a_harvest, 1, "o_wait_b_standard_harvest_window")
            o_b_weather = weather.get_current_weather().oracle().with_id("o_b_harvest_weather_check").depends_on(o_wait_b_harvest, delay_seconds=1)
            o_b_forecast = weather.get_forecast(days=5).oracle().with_id("o_b_harvest_forecast_check").depends_on(o_b_weather, delay_seconds=1)
            o_b_soil = sensor.read_soil_sensors().oracle().with_id("o_b_harvest_soil_check").depends_on(o_b_forecast, delay_seconds=1)
            o_b_overview = farm_world.get_farm_overview().oracle().with_id("o_b_harvest_farm_overview").depends_on(o_b_soil, delay_seconds=1)
            o_b_range = farm_world.get_ridge_range_state(B_START, B_END).oracle().with_id("o_b_harvest_range_state").depends_on(o_b_overview, delay_seconds=1)
            o_harvest_b = harvest_range(tractor, farm_world, o_b_range, start_ridge=B_START, end_ridge=B_END, id_prefix="o_b_standard_zone")
            o_after_b_harvest = self._after_named_step(o_harvest_b, "after_b_heinong84_harvest_store")
            o_report = aui.send_message_to_user(content="已完成A/B早熟与标准品种晚雨收获窗口场景：A区先收并干燥入库，B区按自己的成熟和水分窗口后收。").oracle().with_id("o_report").depends_on(o_after_b_harvest, delay_seconds=2)

            self.events = collect_event_graph(briefing)
