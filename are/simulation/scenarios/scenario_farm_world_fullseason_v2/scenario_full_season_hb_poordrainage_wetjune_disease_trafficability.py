from __future__ import annotations

from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import DroneApp, FarmWorldApp, RobotApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.physics import SoilHydraulicModifier
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


SCENARIO_ID = "scenario_full_season_hb_poordrainage_wetjune_disease_trafficability"
PROFILE_NAME = "harbin_hb_poordrainage_wetjune_disease_seed_1303"
SEED_TYPE = "HEINONG84"
AFFECTED_START = 44
AFFECTED_END = 53
REFERENCE_START = 0
REFERENCE_END = 10
R1_NUTRIENT_AMOUNT = 0.22
FUNGICIDE_L_PER_RIDGE = 4.0
SCENARIO_DESCRIPTION = """
这是一个哈尔滨局部排水差 + 6月湿 + 病害和作业窗口约束的大豆 full-season 场景。
整块田有 64 条 ridges，全田种植黑农84，标准密度。春季播种条件正常，出苗和早期生长整体正常；
6月偏湿，连续降雨后局部低洼或排水差 ridges 土壤水分偏高、冠层湿度较高，病害风险上升。
R5/R6 和收获期整体正常。

该场景的特殊性不只是“湿6月导致病害”，而是“湿6月 + 排水差”同时改变了病害风险和操作时机。
排水差区域更容易积湿，病害压力更高；但土壤过湿时，tractor 进地、喷药和其他机械操作可能受到限制。
因此，即使发现病害迹象，agent 也不能立刻默认可以喷药，而需要同时判断病害是否成立、土壤是否可作业、天气是否适合喷药。

该场景包含完整的大豆基础管理流程：播前准备、种肥/底肥处理、全田播种、出苗检查、早期长势和营养检查、
初花期按需营养检查、6月湿后局部病害诊断、trafficability / spray window 判断、targeted fungicide、
R5/R6 水分检查、成熟收获、晾干/烘干和安全储藏。

这个 L3 的核心是：病害处理不只取决于是否有 disease signs，还取决于排水条件、土壤湿度、天气窗口和可作业性。
正确管理应该是先诊断 affected ridges，再等待合适作业窗口，只对 affected ridges 进行 targeted fungicide，
而不是湿土条件下强行全田喷药。
""".strip()
BRIEFING_TEXT = (
    "任务：管理哈尔滨黑农84标准密度大豆full-season，6月偏湿时重点评估局部积湿、病害风险和机械可作业窗口。"
    "请按基础流程完成播前、底肥、播种、出苗、早期长势/营养检查；湿后先用soil/canopy sensors定位异常zone，"
    "再用drone和ground robot确认病害，并结合weather、forecast和soil trafficability判断是否能喷药。"
    "约束：不能因为看到病害迹象就立刻全田喷药；土壤过湿或天气不适合时应等待合适窗口；"
    "targeted fungicide 只能覆盖工具返回支持的 affected ridges。成功标准：病害诊断、喷药窗口、喷药范围和收获窗口都能由前置工具返回解释。"
)


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonHBPoorDrainageWetJuneDiseaseTrafficability(Scenario):
    """Harbin poor-drainage wet-June disease scenario with trafficability gating."""

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
            cultivar="黑农84标准密度/局部排水差/湿六月病害与可作业窗口",
            seed_stocks={SEED_TYPE: 1000000},
            pesticide_liters=1200.0,
            fertilizer_kg=2500.0,
            tractor_fuel_l=190.0,
            initial_vwc=0.31,
        )
        farm_world = self.get_typed_app(FarmWorldApp)
        farm_world.physics.soil.set_hydraulic_modifiers(
            {
                ridge_id: SoilHydraulicModifier(
                    field_capacity_vwc=0.34,
                    top_drainage_rate=0.30,
                    root_drainage_rate=0.22,
                    rainfall_capture_efficiency=1.02,
                    irrigation_efficiency=0.92,
                    max_infiltration_mm_day=38.0,
                )
                for ridge_id in range(AFFECTED_START, AFFECTED_END + 1)
            }
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

            o_wait_emergence = advance_days(self, o_commit_plant, 15, "o_wait_emergence")
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_sensor_check").depends_on(o_wait_emergence, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_sensor_check").depends_on(o_emergence_soil, delay_seconds=1)
            o_emergence_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_emergence_whole_field_ndvi_survey").depends_on(o_emergence_canopy, delay_seconds=2)
            o_robot_status_emergence = robot.check_status().oracle().with_id("o_robot_status_before_emergence_check").depends_on(o_emergence_ndvi, delay_seconds=1)
            o_emergence_ground = robot.inspect_emergence(0, 63).oracle().with_id("o_whole_field_emergence_ground_check").depends_on(o_robot_status_emergence, delay_seconds=2)

            o_wait_early = advance_days(self, o_emergence_ground, 24, "o_wait_early_growth")
            o_early_soil = sensor.read_soil_sensors().oracle().with_id("o_early_soil_check").depends_on(o_wait_early, delay_seconds=1)
            o_early_canopy = sensor.read_canopy_sensors().oracle().with_id("o_early_canopy_check").depends_on(o_early_soil, delay_seconds=1)
            o_robot_status_early = robot.check_status().oracle().with_id("o_robot_status_before_early_health").depends_on(o_early_canopy, delay_seconds=1)
            o_early_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_early_whole_field_health_check").depends_on(o_robot_status_early, delay_seconds=2)

            o_wait_wet = advance_days(self, o_early_health, 18, "o_wait_wet_june_after_rain")
            o_wet_weather = weather.get_current_weather().oracle().with_id("o_wet_june_weather_check").depends_on(o_wait_wet, delay_seconds=1)
            o_wet_forecast = weather.get_forecast(days=4).oracle().with_id("o_wet_june_forecast_check").depends_on(o_wet_weather, delay_seconds=1)
            o_wet_soil = sensor.read_soil_sensors().oracle().with_id("o_wet_june_soil_trafficability_check").depends_on(o_wet_forecast, delay_seconds=1)
            o_wait_spray_window = advance_days(self, o_wet_soil, 4, "o_wait_for_trafficable_post_rain_spray_window")
            o_spray_weather = weather.get_current_weather().oracle().with_id("o_post_rain_spray_weather_check").depends_on(o_wait_spray_window, delay_seconds=1)
            o_spray_soil = sensor.read_soil_sensors().oracle().with_id("o_post_rain_spray_trafficability_soil_check").depends_on(o_spray_weather, delay_seconds=1)
            o_spray_canopy = sensor.read_canopy_sensors().oracle().with_id("o_post_rain_sensor_flagged_canopy_check").depends_on(o_spray_soil, delay_seconds=1)
            o_affected_ndvi = mavic.fly_survey(AFFECTED_START, AFFECTED_END).oracle().with_id("o_sensor_flagged_poordrainage_ndvi_detail").depends_on(o_spray_canopy, delay_seconds=2)
            o_charge_mavic_before_reference = mavic.charge().oracle().with_id("o_charge_mavic_before_reference_drainage_ndvi").depends_on(o_affected_ndvi, delay_seconds=1)
            o_wait_mavic_before_reference = system.advance_time(hours=1).oracle().with_id("o_wait_mavic_charge_before_reference_drainage_ndvi").depends_on(o_charge_mavic_before_reference, delay_seconds=1)
            o_reference_ndvi = mavic.fly_survey(REFERENCE_START, REFERENCE_END).oracle().with_id("o_reference_normal_drainage_ndvi").depends_on(o_wait_mavic_before_reference, delay_seconds=2)
            o_affected_thermal = matrice.fly_survey(AFFECTED_START, AFFECTED_END).oracle().with_id("o_sensor_flagged_poordrainage_thermal").depends_on(o_reference_ndvi, delay_seconds=2)
            o_robot_status_disease = robot.check_status().oracle().with_id("o_robot_status_before_poordrainage_disease_check").depends_on(o_affected_thermal, delay_seconds=1)
            o_reference_ground = robot.inspect_crop_health(REFERENCE_START, REFERENCE_END).oracle().with_id("o_reference_normal_drainage_ground_check").depends_on(o_robot_status_disease, delay_seconds=2)
            o_charge_robot_before_affected = robot.charge().oracle().with_id("o_charge_robot_before_poordrainage_ground_confirm").depends_on(o_reference_ground, delay_seconds=1)
            o_wait_robot_before_affected = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_before_poordrainage_ground_confirm").depends_on(o_charge_robot_before_affected, delay_seconds=1)
            o_robot_status_affected = robot.check_status().oracle().with_id("o_robot_status_before_poordrainage_ground_confirm").depends_on(o_wait_robot_before_affected, delay_seconds=1)
            o_affected_ground = robot.inspect_crop_health(AFFECTED_START, AFFECTED_END).oracle().with_id("o_poordrainage_ground_confirm_disease").depends_on(o_robot_status_affected, delay_seconds=2)
            o_final_spray_weather = weather.get_current_weather().oracle().with_id("o_final_targeted_fungicide_weather_check").depends_on(o_affected_ground, delay_seconds=1)
            o_final_spray_soil = sensor.read_soil_sensors().oracle().with_id("o_final_targeted_fungicide_trafficability_check").depends_on(o_final_spray_weather, delay_seconds=1)
            o_load_fungicide = tractor.load_fungicide(60.0).oracle().with_id("o_load_targeted_fungicide").depends_on(o_final_spray_soil, delay_seconds=1)
            o_fungicide = tractor.apply_fungicide(AFFECTED_START, AFFECTED_END, liters_per_ridge=FUNGICIDE_L_PER_RIDGE).oracle().with_id("o_apply_fungicide_poordrainage_44_53").depends_on(o_load_fungicide, delay_seconds=2)
            o_commit_disease = farm_world.commit_daily_physics().oracle().with_id("o_commit_targeted_poordrainage_disease_management").depends_on(o_fungicide, delay_seconds=1)

            o_charge_robot_after_disease = robot.charge().oracle().with_id("o_charge_robot_after_poordrainage_disease_check").depends_on(o_commit_disease, delay_seconds=1)
            o_wait_robot_after_disease = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_after_poordrainage_disease_check").depends_on(o_charge_robot_after_disease, delay_seconds=1)

            o_wait_r1 = advance_days(self, o_wait_robot_after_disease, 10, "o_wait_r1_nutrient_window")
            o_r1_soil = sensor.read_soil_sensors().oracle().with_id("o_r1_soil_nutrient_check").depends_on(o_wait_r1, delay_seconds=1)
            o_r1_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r1_canopy_nutrient_check").depends_on(o_r1_soil, delay_seconds=1)
            o_robot_status_r1 = robot.check_status().oracle().with_id("o_robot_status_before_r1_health").depends_on(o_r1_canopy, delay_seconds=1)
            o_r1_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_r1_whole_field_health_check").depends_on(o_robot_status_r1, delay_seconds=2)
            o_r1_topdress = farm_world.apply_fertigation(0, 63, nutrient_amount=R1_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r1_light_nutrient_topdress").depends_on(o_r1_health, delay_seconds=2)
            o_commit_r1 = farm_world.commit_daily_physics().oracle().with_id("o_commit_r1_nutrient_management").depends_on(o_r1_topdress, delay_seconds=1)

            o_wait_r5 = advance_days(self, o_commit_r1, 24, "o_wait_r5_r6_window")
            o_r5_weather = weather.get_current_weather().oracle().with_id("o_r5_r6_weather_check").depends_on(o_wait_r5, delay_seconds=1)
            o_r5_soil = sensor.read_soil_sensors().oracle().with_id("o_r5_r6_soil_water_check").depends_on(o_r5_weather, delay_seconds=1)
            o_r5_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r5_r6_canopy_check").depends_on(o_r5_soil, delay_seconds=1)
            o_r5_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_r5_r6_no_extra_water_action").depends_on(o_r5_canopy, delay_seconds=1)

            o_wait_harvest = advance_days(self, o_r5_commit, 25, "o_wait_harvest_window")
            o_harvest_weather = weather.get_current_weather().oracle().with_id("o_harvest_weather_check").depends_on(o_wait_harvest, delay_seconds=1)
            o_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_harvest_forecast_check").depends_on(o_harvest_weather, delay_seconds=1)
            o_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_harvest_trafficability_soil_check").depends_on(o_harvest_forecast, delay_seconds=1)
            o_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_harvest_farm_overview").depends_on(o_harvest_soil, delay_seconds=1)
            o_harvest_range = farm_world.get_ridge_range_state(0, 63).oracle().with_id("o_harvest_ridge_range_state_0_63").depends_on(o_harvest_overview, delay_seconds=1)
            o_attach = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_harvest_range, delay_seconds=1)
            o_harvest = harvest_range(tractor, farm_world, o_attach, start_ridge=0, end_ridge=63, id_prefix="o_whole_field")
            o_after_harvest = self._after_named_step(o_harvest, "after_whole_field_harvest_store")
            o_post_storage = advance_days(self, o_after_harvest, 1, "o_wait_post_storage_stability")
            o_report = aui.send_message_to_user(content="已完成局部排水差湿六月病害场景：等待可作业窗口后仅对44-53垄靶向fungicide，并完成收获入库。").oracle().with_id("o_report").depends_on(o_post_storage, delay_seconds=2)

            self.events = collect_event_graph(briefing)
