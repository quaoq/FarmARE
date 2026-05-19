from __future__ import annotations

from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import DroneApp, FarmWorldApp, FieldOpsApp, RobotApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.physics import SoilHydraulicModifier
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
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


SCENARIO_ID = "scenario_full_season_hb_dryr5r6_hn58_std_waterlimit"
PROFILE_NAME = "harbin_hb_dryr5r6_hn58_waterlimit_seed_1202"
SEED_TYPE = "HEINONG58"
STANDARD_SPACING_CM = 7.9
PRIORITY_START = 22
PRIORITY_END = 43
REFERENCE_WEST_START = 0
REFERENCE_WEST_END = 10
REFERENCE_EAST_START = 54
REFERENCE_EAST_END = 63
R1_NUTRIENT_AMOUNT = 0.22
R5_IRRIGATION_HOURS = 2.0
SCENARIO_DESCRIPTION = """
这是一个哈尔滨 R5/R6 干旱 + 抗逆品种 + 灌溉水量限制的大豆 full-season 场景。
整块田有 64 条 ridges，全田种植黑农58，标准密度。春季正常，6月正常，R5/R6 结荚鼓粒期出现干旱，收获期正常。

黑农58具有较强抗逆性，因此轻度水分胁迫不一定立即需要灌溉。但 R5/R6 是大豆产量形成的敏感期，
如果部分 ridge blocks 出现明显缺水，仍然需要及时处理。由于灌溉水量有限，agent 不能全田平均灌水，
而应根据 soil moisture、thermal stress、生育阶段和产量敏感性判断灌溉优先级。

该场景包含完整的大豆基础管理流程：播前准备、种肥/底肥处理、全田播种、出苗检查、早期长势和营养检查、
初花期按需营养检查、中期病虫害和水分巡查、R5/R6 水分胁迫诊断、有限灌溉分配、成熟收获、晾干/烘干和安全储藏。

这个 L3 的核心是：抗逆品种不是完全不用水，水量有限时也不能全田平均灌溉。正确管理应该优先保护
R5/R6 阶段真正缺水且产量损失风险最高的 ridges。
""".strip()
BRIEFING_TEXT = (
    "任务：管理哈尔滨黑农58标准密度大豆full-season，重点关注R5/R6结荚鼓粒期的水分风险和有限灌溉水分配。"
    "请按完整基础流程完成播前、底肥、播种、出苗、早中期长势/营养/病虫巡查，并在R5/R6阶段用soil moisture、"
    "canopy/NDVI、thermal observation、生育阶段和ground check判断是否存在真正缺水的高优先级垄段。"
    "约束：黑农58有抗逆性，轻度压力不等于立即灌溉；灌溉水有限，不能全田平均灌水。"
    "成功标准：只对工具返回支持的高风险区域进行必要灌溉，并在收获前确认成熟、籽粒水分、天气和可作业性。"
)


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonHBDryR5R6HN58StdWaterLimit(Scenario):
    """Harbin Heinong58 R5/R6 drought scenario with limited irrigation water."""

    start_time: float | None = harbin_start_time()
    duration: float | None = 170 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    nb_turns: int = 1

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        install_common_farm_apps(self, thermal=True, field_ops=True)
        configure_common_field(
            self,
            profile_name=PROFILE_NAME,
            cultivar="黑农58标准密度/R5R6干旱/有限灌溉水",
            seed_stocks={SEED_TYPE: 1000000},
            pesticide_liters=1200.0,
            fertilizer_kg=2500.0,
            tractor_fuel_l=190.0,
            initial_vwc=0.30,
        )
        farm_world = self.get_typed_app(FarmWorldApp)
        farm_world.physics.soil.set_hydraulic_modifiers(
            {
                ridge_id: SoilHydraulicModifier(
                    field_capacity_vwc=0.285,
                    top_drainage_rate=0.55,
                    root_drainage_rate=0.42,
                    rainfall_capture_efficiency=0.82,
                    irrigation_efficiency=0.90,
                    max_infiltration_mm_day=34.0,
                )
                for ridge_id in range(PRIORITY_START, PRIORITY_END + 1)
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
        field_ops = self.get_typed_app(FieldOpsApp)
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
            o_load_base = tractor.load_fertilizer(240.0).oracle().with_id("o_load_base_fertilizer").depends_on(o_detach_grader, delay_seconds=1)
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
                spacing_cm=STANDARD_SPACING_CM,
                id_prefix="o_heinong58_whole_field",
            )
            o_commit_plant = farm_world.commit_daily_physics().oracle().with_id("o_commit_planting").depends_on(o_plant, delay_seconds=1)

            o_wait_emergence = advance_days(self, o_commit_plant, 15, "o_wait_emergence")
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_sensor_check").depends_on(o_wait_emergence, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_sensor_check").depends_on(o_emergence_soil, delay_seconds=1)
            o_robot_status_emergence = robot.check_status().oracle().with_id("o_robot_status_before_emergence_check").depends_on(o_emergence_canopy, delay_seconds=1)
            o_emergence_ground = robot.inspect_emergence(0, 63).oracle().with_id("o_whole_field_emergence_ground_check").depends_on(o_robot_status_emergence, delay_seconds=2)
            o_charge_robot_after_emergence = robot.charge().oracle().with_id("o_charge_robot_after_emergence_check").depends_on(o_emergence_ground, delay_seconds=1)
            o_wait_robot_after_emergence = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_after_emergence_check").depends_on(o_charge_robot_after_emergence, delay_seconds=1)

            o_wait_early = advance_days(self, o_wait_robot_after_emergence, 24, "o_wait_early_growth")
            o_early_soil = sensor.read_soil_sensors().oracle().with_id("o_early_soil_check").depends_on(o_wait_early, delay_seconds=1)
            o_early_canopy = sensor.read_canopy_sensors().oracle().with_id("o_early_canopy_check").depends_on(o_early_soil, delay_seconds=1)
            o_robot_status_early = robot.check_status().oracle().with_id("o_robot_status_before_early_health").depends_on(o_early_canopy, delay_seconds=1)
            o_early_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_early_whole_field_health_check").depends_on(o_robot_status_early, delay_seconds=2)
            o_charge_robot_after_early = robot.charge().oracle().with_id("o_charge_robot_after_early_health").depends_on(o_early_health, delay_seconds=1)
            o_wait_robot_after_early = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_after_early_health").depends_on(o_charge_robot_after_early, delay_seconds=1)

            o_wait_r1 = advance_days(self, o_wait_robot_after_early, 21, "o_wait_r1_nutrient_window")
            o_r1_soil = sensor.read_soil_sensors().oracle().with_id("o_r1_soil_nutrient_check").depends_on(o_wait_r1, delay_seconds=1)
            o_r1_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r1_canopy_nutrient_check").depends_on(o_r1_soil, delay_seconds=1)
            o_robot_status_r1 = robot.check_status().oracle().with_id("o_robot_status_before_r1_health").depends_on(o_r1_canopy, delay_seconds=1)
            o_r1_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_r1_whole_field_health_check").depends_on(o_robot_status_r1, delay_seconds=2)
            o_r1_topdress = farm_world.apply_fertigation(0, 63, nutrient_amount=R1_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r1_light_nutrient_topdress").depends_on(o_r1_health, delay_seconds=2)
            o_commit_r1 = farm_world.commit_daily_physics().oracle().with_id("o_commit_r1_nutrient_management").depends_on(o_r1_topdress, delay_seconds=1)
            o_charge_robot_after_r1 = robot.charge().oracle().with_id("o_charge_robot_after_r1_health").depends_on(o_commit_r1, delay_seconds=1)
            o_wait_robot_after_r1 = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_after_r1_health").depends_on(o_charge_robot_after_r1, delay_seconds=1)

            o_wait_mid = advance_days(self, o_wait_robot_after_r1, 17, "o_wait_midseason_scout")
            o_mid_weather = weather.get_current_weather().oracle().with_id("o_midseason_weather_check").depends_on(o_wait_mid, delay_seconds=1)
            o_mid_soil = sensor.read_soil_sensors().oracle().with_id("o_midseason_soil_check").depends_on(o_mid_weather, delay_seconds=1)
            o_mid_canopy = sensor.read_canopy_sensors().oracle().with_id("o_midseason_canopy_check").depends_on(o_mid_soil, delay_seconds=1)
            o_mid_robot_status = robot.check_status().oracle().with_id("o_robot_status_before_midseason_pest_check").depends_on(o_mid_canopy, delay_seconds=1)
            o_mid_pests = robot.inspect_pests(0, 63).oracle().with_id("o_midseason_whole_field_pest_check").depends_on(o_mid_robot_status, delay_seconds=2)
            o_charge_robot_after_mid = robot.charge().oracle().with_id("o_charge_robot_after_midseason_pest_check").depends_on(o_mid_pests, delay_seconds=1)
            o_wait_robot_after_mid = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_after_midseason_pest_check").depends_on(o_charge_robot_after_mid, delay_seconds=1)

            o_wait_r5 = advance_days(self, o_wait_robot_after_mid, 27, "o_wait_r5_r6_drought_window")
            o_r5_weather = weather.get_current_weather().oracle().with_id("o_r5_r6_weather_check").depends_on(o_wait_r5, delay_seconds=1)
            o_r5_forecast = weather.get_forecast(days=4).oracle().with_id("o_r5_r6_forecast_check").depends_on(o_r5_weather, delay_seconds=1)
            o_r5_soil = sensor.read_soil_sensors().oracle().with_id("o_r5_r6_soil_water_check").depends_on(o_r5_forecast, delay_seconds=1)
            o_r5_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r5_r6_canopy_sensor_check").depends_on(o_r5_soil, delay_seconds=1)
            o_priority_ndvi = mavic.fly_survey(PRIORITY_START, PRIORITY_END).oracle().with_id("o_sensor_flagged_priority_r5_r6_ndvi").depends_on(o_r5_canopy, delay_seconds=2)
            o_west_reference_ndvi = mavic.fly_survey(REFERENCE_WEST_START, REFERENCE_WEST_END).oracle().with_id("o_west_reference_r5_r6_ndvi").depends_on(o_priority_ndvi, delay_seconds=2)
            o_east_reference_ndvi = mavic.fly_survey(REFERENCE_EAST_START, REFERENCE_EAST_END).oracle().with_id("o_east_reference_r5_r6_ndvi").depends_on(o_west_reference_ndvi, delay_seconds=2)
            o_priority_thermal = matrice.fly_survey(PRIORITY_START, PRIORITY_END).oracle().with_id("o_sensor_flagged_priority_r5_r6_thermal").depends_on(o_east_reference_ndvi, delay_seconds=2)
            o_robot_status_drought = robot.check_status().oracle().with_id("o_robot_status_before_priority_water_check").depends_on(o_priority_thermal, delay_seconds=1)
            o_priority_health = robot.inspect_crop_health(PRIORITY_START, PRIORITY_END).oracle().with_id("o_priority_r5_r6_health_check").depends_on(o_robot_status_drought, delay_seconds=2)
            o_charge_robot_before_priority_pests = robot.charge().oracle().with_id("o_charge_robot_before_priority_pest_ruleout").depends_on(o_priority_health, delay_seconds=1)
            o_wait_robot_before_priority_pests = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_before_priority_pest_ruleout").depends_on(o_charge_robot_before_priority_pests, delay_seconds=1)
            o_robot_status_priority_pests = robot.check_status().oracle().with_id("o_robot_status_before_priority_pest_ruleout").depends_on(o_wait_robot_before_priority_pests, delay_seconds=1)
            o_priority_pests = robot.inspect_pests(PRIORITY_START, PRIORITY_END).oracle().with_id("o_priority_r5_r6_rule_out_pests").depends_on(o_robot_status_priority_pests, delay_seconds=2)
            o_irrigate = field_ops.irrigate(PRIORITY_START, PRIORITY_END, hours=R5_IRRIGATION_HOURS).oracle().with_id("o_limited_water_targeted_irrigation_22_43").depends_on(o_priority_pests, delay_seconds=2)
            o_wait_response = system.advance_time(hours=6).oracle().with_id("o_wait_limited_irrigation_response").depends_on(o_irrigate, delay_seconds=1)
            o_recheck_soil = sensor.read_soil_sensors().oracle().with_id("o_limited_irrigation_soil_recheck").depends_on(o_wait_response, delay_seconds=1)
            o_recheck_thermal = matrice.fly_survey(PRIORITY_START, PRIORITY_END).oracle().with_id("o_limited_irrigation_thermal_recheck").depends_on(o_recheck_soil, delay_seconds=2)
            o_commit_irrigation = farm_world.commit_daily_physics().oracle().with_id("o_commit_limited_irrigation_management").depends_on(o_recheck_thermal, delay_seconds=1)

            o_wait_harvest = advance_days(self, o_commit_irrigation, 51, "o_wait_harvest_window")
            o_harvest_weather = weather.get_current_weather().oracle().with_id("o_harvest_weather_check").depends_on(o_wait_harvest, delay_seconds=1)
            o_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_harvest_forecast_check").depends_on(o_harvest_weather, delay_seconds=1)
            o_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_harvest_trafficability_soil_check").depends_on(o_harvest_forecast, delay_seconds=1)
            o_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_harvest_farm_overview").depends_on(o_harvest_soil, delay_seconds=1)
            o_harvest_range = farm_world.get_ridge_range_state(0, 63).oracle().with_id("o_harvest_ridge_range_state_0_63").depends_on(o_harvest_overview, delay_seconds=1)
            o_attach = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_harvest_range, delay_seconds=1)
            o_harvest = harvest_range(tractor, farm_world, o_attach, start_ridge=0, end_ridge=63, id_prefix="o_whole_field")
            o_after_harvest = self._after_named_step(o_harvest, "after_whole_field_harvest_store")
            o_post_storage = advance_days(self, o_after_harvest, 1, "o_wait_post_storage_stability")
            o_report = aui.send_message_to_user(content="已完成黑农58 R5/R6干旱有限灌溉场景：仅对工具确认的高风险22-43垄灌溉，并完成收获入库。").oracle().with_id("o_report").depends_on(o_post_storage, delay_seconds=2)

            self.events = collect_event_graph(briefing)
