from __future__ import annotations

from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import DroneApp, FarmWorldApp, RobotApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
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


SCENARIO_ID = "scenario_full_season_hb_base_hn84_std_normal"
PROFILE_NAME = "harbin_hb_base_hn84_std_normal_seed_1101"
SEED_TYPE = "HEINONG84"
R1_NUTRIENT_AMOUNT = 0.24
SCENARIO_DESCRIPTION = """
这是一个哈尔滨正常年份的大豆 full-season baseline 场景。整块田有 64 条 ridges，全田种植黑农84，标准密度。
春季播种条件正常，6月天气正常，R5/R6 阶段没有明显干旱，收获期也没有明显晚雨风险。
土壤水分、肥力和病虫害压力都处于常规水平。

该场景包含完整的大豆基础管理流程：播前准备、种肥/底肥处理、全田播种、出苗检查、早期长势和营养检查、
初花期按需营养检查、中期病虫害和水分巡查、R5/R6 水分管理、成熟收获、晾干/烘干和安全储藏。

这个 L3 的主要作用是作为哈尔滨黑农84标准密度的正常管理 baseline。它不应硬塞病害、虫害、干旱或晚季降雨风险。
场景重点是确认 engine 和 oracle 在正常条件下能否给出合理产量，并为其他 stress scenarios 提供产量和管理路径对照。
""".strip()
BRIEFING_TEXT = (
    "任务：管理一个哈尔滨黑农84标准密度正常年份大豆full-season baseline。"
    "请按真实农事语义完成播前准备、底肥、播种、出苗检查、长势/营养检查、"
    "中期病虫害和水分巡查、R5/R6水分检查、成熟收获、干燥和安全储藏。"
    "约束：不要预设病害、虫害、干旱或晚雨；只有weather、soil、canopy、drone、robot或range-state返回支持异常时，"
    "才采取额外管理动作。成功标准：全田完成正常管理闭环，收获前由工具确认成熟、籽粒水分和可作业性。"
)


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonHBBaseHN84StdNormal(Scenario):
    """Harbin Heinong84 standard-density normal-year full-season baseline."""

    start_time: float | None = harbin_start_time()
    duration: float | None = 170 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    nb_turns: int = 1

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        install_common_farm_apps(self, thermal=True)
        configure_common_field(
            self,
            profile_name=PROFILE_NAME,
            cultivar="黑农84标准密度/哈尔滨正常年份baseline",
            seed_stocks={SEED_TYPE: 1000000},
            pesticide_liters=1200.0,
            fertilizer_kg=2500.0,
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
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        briefing_text = BRIEFING_TEXT

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)

            o_weather_0 = weather.get_current_weather().oracle().with_id("o_weather_before_prep").depends_on(briefing, delay_seconds=2)
            o_forecast_0 = weather.get_forecast(days=5).oracle().with_id("o_forecast_before_prep").depends_on(o_weather_0, delay_seconds=1)
            o_soil_0 = sensor.read_soil_sensors().oracle().with_id("o_soil_before_prep").depends_on(o_forecast_0, delay_seconds=1)
            o_inventory_0 = farm_world.get_inventory().oracle().with_id("o_inventory_before_prep").depends_on(o_soil_0, delay_seconds=1)
            o_tractor_0 = tractor.get_status().oracle().with_id("o_tractor_before_prep").depends_on(o_inventory_0, delay_seconds=1)

            o_attach_grader = tractor.attach_implement("grader").oracle().with_id("o_attach_grader").depends_on(o_tractor_0, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(o_attach_grader, delay_seconds=2)
            o_detach_grader = tractor.detach_implement().oracle().with_id("o_detach_grader_after_leveling").depends_on(o_level, delay_seconds=1)
            o_load_base = tractor.load_fertilizer(250.0).oracle().with_id("o_load_base_fertilizer").depends_on(o_detach_grader, delay_seconds=1)
            o_base = tractor.base_fertilize().oracle().with_id("o_apply_base_fertilizer").depends_on(o_load_base, delay_seconds=2)
            o_attach_furrower = tractor.attach_implement("furrower").oracle().with_id("o_attach_furrower_before_ridging").depends_on(o_base, delay_seconds=1)
            o_ridge = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id("o_form_1p1m_ridges").depends_on(o_attach_furrower, delay_seconds=2)
            o_detach_furrower = tractor.detach_implement().oracle().with_id("o_detach_furrower_after_ridging").depends_on(o_ridge, delay_seconds=1)

            o_plant_weather = weather.get_current_weather().oracle().with_id("o_plant_weather_check").depends_on(o_detach_furrower, delay_seconds=1)
            o_plant_forecast = weather.get_forecast(days=3).oracle().with_id("o_plant_forecast_check").depends_on(o_plant_weather, delay_seconds=1)
            o_plant_soil = sensor.read_soil_sensors().oracle().with_id("o_plant_soil_check").depends_on(o_plant_forecast, delay_seconds=1)
            o_plant_tractor = tractor.get_status().oracle().with_id("o_tractor_before_planting").depends_on(o_plant_soil, delay_seconds=1)
            o_plant = plant_range(
                tractor,
                o_plant_tractor,
                start_ridge=0,
                end_ridge=63,
                seed_type=SEED_TYPE,
                spacing_cm=HEINONG84_SPACING_CM,
                id_prefix="o_whole_field",
            )
            o_commit_plant = farm_world.commit_daily_physics().oracle().with_id("o_commit_planting").depends_on(o_plant, delay_seconds=1)
            o_after_plant = self._after_named_step(o_commit_plant, "after_whole_field_planting")

            o_wait_emergence = advance_days(self, o_after_plant, 15, "o_wait_emergence")
            o_emergence_overview = farm_world.get_farm_overview().oracle().with_id("o_emergence_farm_overview").depends_on(o_wait_emergence, delay_seconds=1)
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_sensor_check").depends_on(o_emergence_overview, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_sensor_check").depends_on(o_emergence_soil, delay_seconds=1)
            o_emergence_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_emergence_whole_field_ndvi_survey").depends_on(o_emergence_canopy, delay_seconds=2)
            o_charge_mavic_after_emergence = mavic.charge().oracle().with_id("o_charge_mavic_after_emergence_survey").depends_on(o_emergence_ndvi, delay_seconds=1)
            o_wait_mavic_after_emergence = system.advance_time(hours=1).oracle().with_id("o_wait_mavic_charge_after_emergence_survey").depends_on(o_charge_mavic_after_emergence, delay_seconds=1)
            o_robot_status_emergence = robot.check_status().oracle().with_id("o_robot_status_before_emergence_check").depends_on(o_wait_mavic_after_emergence, delay_seconds=1)
            o_emergence_ground = robot.inspect_emergence(0, 63).oracle().with_id("o_whole_field_emergence_ground_check").depends_on(o_robot_status_emergence, delay_seconds=2)
            o_charge_after_emergence = robot.charge().oracle().with_id("o_charge_robot_after_emergence").depends_on(o_emergence_ground, delay_seconds=1)

            o_wait_early = advance_days(self, o_charge_after_emergence, 23, "o_wait_early_growth")
            o_early_soil = sensor.read_soil_sensors().oracle().with_id("o_early_soil_check").depends_on(o_wait_early, delay_seconds=1)
            o_early_canopy = sensor.read_canopy_sensors().oracle().with_id("o_early_canopy_check").depends_on(o_early_soil, delay_seconds=1)
            o_early_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_early_whole_field_ndvi").depends_on(o_early_canopy, delay_seconds=2)
            o_charge_mavic_after_early = mavic.charge().oracle().with_id("o_charge_mavic_after_early_ndvi").depends_on(o_early_ndvi, delay_seconds=1)
            o_wait_mavic_after_early = system.advance_time(hours=1).oracle().with_id("o_wait_mavic_charge_after_early_ndvi").depends_on(o_charge_mavic_after_early, delay_seconds=1)
            o_robot_status_early = robot.check_status().oracle().with_id("o_robot_status_before_early_health").depends_on(o_wait_mavic_after_early, delay_seconds=1)
            o_early_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_early_whole_field_health_check").depends_on(o_robot_status_early, delay_seconds=2)

            o_wait_r1 = advance_days(self, o_early_health, 22, "o_wait_r1_nutrient_window")
            o_r1_soil = sensor.read_soil_sensors().oracle().with_id("o_r1_soil_nutrient_check").depends_on(o_wait_r1, delay_seconds=1)
            o_r1_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r1_canopy_nutrient_check").depends_on(o_r1_soil, delay_seconds=1)
            o_robot_status_r1 = robot.check_status().oracle().with_id("o_robot_status_before_r1_health").depends_on(o_r1_canopy, delay_seconds=1)
            o_r1_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_r1_whole_field_health_check").depends_on(o_robot_status_r1, delay_seconds=2)
            o_r1_topdress = farm_world.apply_fertigation(0, 63, nutrient_amount=R1_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r1_light_nutrient_topdress").depends_on(o_r1_health, delay_seconds=2)
            o_commit_r1 = farm_world.commit_daily_physics().oracle().with_id("o_commit_r1_nutrient_management").depends_on(o_r1_topdress, delay_seconds=1)

            o_wait_mid = advance_days(self, o_commit_r1, 18, "o_wait_midseason_scout")
            o_mid_weather = weather.get_current_weather().oracle().with_id("o_midseason_weather_check").depends_on(o_wait_mid, delay_seconds=1)
            o_mid_soil = sensor.read_soil_sensors().oracle().with_id("o_midseason_soil_check").depends_on(o_mid_weather, delay_seconds=1)
            o_mid_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_midseason_whole_field_ndvi").depends_on(o_mid_soil, delay_seconds=2)
            o_charge_mavic_after_mid = mavic.charge().oracle().with_id("o_charge_mavic_after_midseason_ndvi").depends_on(o_mid_ndvi, delay_seconds=1)
            o_wait_mavic_after_mid = system.advance_time(hours=1).oracle().with_id("o_wait_mavic_charge_after_midseason_ndvi").depends_on(o_charge_mavic_after_mid, delay_seconds=1)
            o_mid_robot_status = robot.check_status().oracle().with_id("o_robot_status_before_midseason_pest_check").depends_on(o_wait_mavic_after_mid, delay_seconds=1)
            o_mid_pests = robot.inspect_pests(0, 63).oracle().with_id("o_midseason_whole_field_pest_check").depends_on(o_mid_robot_status, delay_seconds=2)

            o_wait_r5 = advance_days(self, o_mid_pests, 26, "o_wait_r5_r6_window")
            o_r5_weather = weather.get_current_weather().oracle().with_id("o_r5_r6_weather_check").depends_on(o_wait_r5, delay_seconds=1)
            o_r5_forecast = weather.get_forecast(days=4).oracle().with_id("o_r5_r6_forecast_check").depends_on(o_r5_weather, delay_seconds=1)
            o_r5_soil = sensor.read_soil_sensors().oracle().with_id("o_r5_r6_soil_water_check").depends_on(o_r5_forecast, delay_seconds=1)
            o_r5_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r5_r6_canopy_check").depends_on(o_r5_soil, delay_seconds=1)
            o_r5_thermal_west = matrice.fly_survey(0, 31).oracle().with_id("o_r5_r6_west_thermal_reference").depends_on(o_r5_canopy, delay_seconds=2)
            o_charge_matrice_between_r5 = matrice.charge().oracle().with_id("o_charge_matrice_between_r5_thermal_checks").depends_on(o_r5_thermal_west, delay_seconds=1)
            o_wait_matrice_between_r5 = system.advance_time(hours=1).oracle().with_id("o_wait_matrice_charge_between_r5_thermal_checks").depends_on(o_charge_matrice_between_r5, delay_seconds=1)
            o_r5_thermal_east = matrice.fly_survey(32, 63).oracle().with_id("o_r5_r6_east_thermal_reference").depends_on(o_wait_matrice_between_r5, delay_seconds=2)
            o_commit_r5_no_action = farm_world.commit_daily_physics().oracle().with_id("o_commit_r5_r6_no_stress_action").depends_on(o_r5_thermal_east, delay_seconds=1)

            o_wait_harvest = advance_days(self, o_commit_r5_no_action, 53, "o_wait_harvest_window")
            o_harvest_weather = weather.get_current_weather().oracle().with_id("o_harvest_weather_check").depends_on(o_wait_harvest, delay_seconds=1)
            o_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_harvest_forecast_check").depends_on(o_harvest_weather, delay_seconds=1)
            o_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_harvest_trafficability_soil_check").depends_on(o_harvest_forecast, delay_seconds=1)
            o_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_harvest_farm_overview").depends_on(o_harvest_soil, delay_seconds=1)
            o_harvest_range = farm_world.get_ridge_range_state(0, 63).oracle().with_id("o_harvest_ridge_range_state_0_63").depends_on(o_harvest_overview, delay_seconds=1)
            o_attach = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_harvest_range, delay_seconds=1)
            o_harvest = harvest_range(tractor, farm_world, o_attach, start_ridge=0, end_ridge=63, id_prefix="o_whole_field")
            o_after_harvest = self._after_named_step(o_harvest, "after_whole_field_harvest_store")
            o_post_storage = advance_days(self, o_after_harvest, 1, "o_wait_post_storage_stability")
            o_report = aui.send_message_to_user(content="已完成哈尔滨黑农84标准密度正常年份baseline，并完成收获、干燥和入库。").oracle().with_id("o_report").depends_on(o_post_storage, delay_seconds=2)

            self.events = collect_event_graph(briefing)
