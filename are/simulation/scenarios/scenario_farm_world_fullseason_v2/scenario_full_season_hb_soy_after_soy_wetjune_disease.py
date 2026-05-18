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
    spray_blocks,
)
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.types import EventRegisterer


SCENARIO_ID = "scenario_full_season_hb_soy_after_soy_wetjune_disease"
PROFILE_NAME = "harbin_hb_soy_after_soy_wetjune_disease_seed_1404"
SEED_TYPE = "HEINONG84"
AFFECTED_START = 22
AFFECTED_END = 43
REFERENCE_START = 0
REFERENCE_END = 10
R1_NUTRIENT_AMOUNT = 0.22
FUNGICIDE_L_PER_RIDGE = 3.5
FIELD_HISTORY_DISEASE_BASELINE = 0.055
HISTORY_HOTSPOT_DISEASE_BASELINE = 0.12
SCENARIO_DESCRIPTION = """
这是一个哈尔滨前茬大豆 / 病害历史 + 6月湿的大豆 full-season 场景。整块田有 64 条 ridges，
全田种植黑农84，标准密度。春季播种和出苗条件正常，6月偏湿，R5/R6 和收获期整体正常。

该场景的主要风险不是高密度，也不是排水差，而是田块历史。由于前茬为大豆，或田块有过病害发生历史，
season 开始时的 disease baseline 高于普通田块。进入 6月后，连续降雨和高湿度进一步提高病害发生风险。
即使全田是标准密度，agent 也需要在 6月湿后更早、更认真地进行病害巡查。

该场景包含完整的大豆基础管理流程：播前准备、种肥/底肥处理、全田播种、出苗检查、早期长势和营养检查、
初花期按需营养检查、6月湿后病害巡查、病害诊断、喷药窗口判断、targeted fungicide、R5/R6 水分检查、
成熟收获、晾干/烘干和安全储藏。

这个 L3 的核心是：病害风险来自 prior-field history，而不是高密度或局部排水差。
正确管理应该体现“前茬/病害历史改变 scouting priority”：6月湿后需要更早发现病害风险，
区分病害与缺水、虫害或营养不足，并在确认病害后对 affected ridges 进行 targeted fungicide。
""".strip()
BRIEFING_TEXT = (
    "任务：管理哈尔滨黑农84标准密度大豆full-season，重点关注前茬大豆/病害历史在6月湿后带来的病害风险。"
    "请按基础流程完成播前、底肥、播种、出苗、早期长势/营养检查；6月湿后提高scouting优先级，"
    "用soil/canopy sensors、drone、thermal observation和ground robot区分病害、缺水、虫害或营养不足。"
    "约束：风险来源是field history，不是高密度或排水差；不能默认全田喷药，targeted fungicide必须由工具返回支持。"
    "成功标准：确认病害后只处理受影响区域，并完成R5/R6水分检查、成熟收获、干燥和安全储藏。"
)


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonHBSoyAfterSoyWetJuneDisease(Scenario):
    """Harbin soy-after-soy wet-June disease-history full-season scenario."""

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
            cultivar="黑农84标准密度/前茬大豆病害历史/湿六月",
            seed_stocks={SEED_TYPE: 1000000},
            pesticide_liters=1200.0,
            fertilizer_kg=2500.0,
            tractor_fuel_l=190.0,
            initial_vwc=0.30,
        )
        farm_world = self.get_typed_app(FarmWorldApp)
        for ridge_id in range(64):
            ridge = farm_world.get_ridge(ridge_id)
            baseline = (
                HISTORY_HOTSPOT_DISEASE_BASELINE
                if AFFECTED_START <= ridge_id <= AFFECTED_END
                else FIELD_HISTORY_DISEASE_BASELINE
            )
            ridge.disease_pressure_base = baseline
            ridge.disease_pressure = baseline

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

            o_wait_early = advance_days(self, o_emergence_ground, 20, "o_wait_early_growth")
            o_early_soil = sensor.read_soil_sensors().oracle().with_id("o_early_soil_check").depends_on(o_wait_early, delay_seconds=1)
            o_early_canopy = sensor.read_canopy_sensors().oracle().with_id("o_early_canopy_check").depends_on(o_early_soil, delay_seconds=1)
            o_robot_status_early = robot.check_status().oracle().with_id("o_robot_status_before_early_health").depends_on(o_early_canopy, delay_seconds=1)
            o_early_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_early_whole_field_health_check").depends_on(o_robot_status_early, delay_seconds=2)

            o_wait_history_scout = advance_days(self, o_early_health, 15, "o_wait_early_wet_june_history_scout")
            o_history_weather = weather.get_current_weather().oracle().with_id("o_soy_history_wet_june_weather_check").depends_on(o_wait_history_scout, delay_seconds=1)
            o_history_forecast = weather.get_forecast(days=4).oracle().with_id("o_soy_history_wet_june_forecast_check").depends_on(o_history_weather, delay_seconds=1)
            o_history_soil = sensor.read_soil_sensors().oracle().with_id("o_soy_history_wet_june_soil_check").depends_on(o_history_forecast, delay_seconds=1)
            o_history_canopy = sensor.read_canopy_sensors().oracle().with_id("o_soy_history_sensor_priority_canopy_check").depends_on(o_history_soil, delay_seconds=1)
            o_charge_mavic_before_history_detail = mavic.charge().oracle().with_id("o_charge_mavic_before_soy_history_ndvi_detail").depends_on(o_history_canopy, delay_seconds=1)
            o_wait_mavic_before_history_detail = system.advance_time(hours=1).oracle().with_id("o_wait_mavic_charge_before_soy_history_ndvi_detail").depends_on(o_charge_mavic_before_history_detail, delay_seconds=1)
            o_affected_ndvi = mavic.fly_survey(AFFECTED_START, AFFECTED_END).oracle().with_id("o_soy_history_sensor_flagged_ndvi_detail").depends_on(o_wait_mavic_before_history_detail, delay_seconds=2)
            o_reference_ndvi = mavic.fly_survey(REFERENCE_START, REFERENCE_END).oracle().with_id("o_soy_history_reference_ndvi").depends_on(o_affected_ndvi, delay_seconds=2)
            o_affected_thermal = matrice.fly_survey(AFFECTED_START, AFFECTED_END).oracle().with_id("o_soy_history_sensor_flagged_thermal").depends_on(o_reference_ndvi, delay_seconds=2)
            o_robot_status_disease = robot.check_status().oracle().with_id("o_robot_status_before_soy_history_disease_check").depends_on(o_affected_thermal, delay_seconds=1)
            o_reference_ground = robot.inspect_crop_health(REFERENCE_START, REFERENCE_END).oracle().with_id("o_soy_history_reference_ground_check").depends_on(o_robot_status_disease, delay_seconds=2)
            o_charge_robot_before_affected = robot.charge().oracle().with_id("o_charge_robot_before_soy_history_ground_confirm").depends_on(o_reference_ground, delay_seconds=1)
            o_wait_robot_before_affected = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_before_soy_history_ground_confirm").depends_on(o_charge_robot_before_affected, delay_seconds=1)
            o_robot_status_affected = robot.check_status().oracle().with_id("o_robot_status_before_soy_history_ground_confirm").depends_on(o_wait_robot_before_affected, delay_seconds=1)
            o_affected_ground = robot.inspect_crop_health(AFFECTED_START, AFFECTED_END).oracle().with_id("o_soy_history_ground_confirm_disease").depends_on(o_robot_status_affected, delay_seconds=2)
            o_charge_robot_before_pest_ruleout = robot.charge().oracle().with_id("o_charge_robot_before_soy_history_pest_ruleout").depends_on(o_affected_ground, delay_seconds=1)
            o_wait_robot_before_pest_ruleout = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_before_soy_history_pest_ruleout").depends_on(o_charge_robot_before_pest_ruleout, delay_seconds=1)
            o_robot_status_pest_ruleout = robot.check_status().oracle().with_id("o_robot_status_before_soy_history_pest_ruleout").depends_on(o_wait_robot_before_pest_ruleout, delay_seconds=1)
            o_ruleout_pests = robot.inspect_pests(AFFECTED_START, AFFECTED_END).oracle().with_id("o_soy_history_rule_out_insects").depends_on(o_robot_status_pest_ruleout, delay_seconds=2)
            # o_wait_spray_window = advance_days(self, o_ruleout_pests, 3, "o_wait_for_soy_history_spray_window")
            o_spray_weather = weather.get_current_weather().oracle().with_id("o_targeted_fungicide_weather_check").depends_on(o_ruleout_pests, delay_seconds=1)
            o_spray_soil = sensor.read_soil_sensors().oracle().with_id("o_targeted_fungicide_trafficability_soil_check").depends_on(o_spray_weather, delay_seconds=1)
            o_load_fungicide = tractor.load_fungicide(90.0).oracle().with_id("o_load_targeted_fungicide").depends_on(o_spray_soil, delay_seconds=1)
            o_spray = spray_blocks(
                tractor,
                o_load_fungicide,
                start_ridge=AFFECTED_START,
                end_ridge=AFFECTED_END,
                liters_per_ridge=FUNGICIDE_L_PER_RIDGE,
                fungicide=True,
                id_prefix="o_soy_history_targeted_fungicide",
            )
            o_commit_spray = farm_world.commit_daily_physics().oracle().with_id("o_commit_soy_history_targeted_fungicide").depends_on(o_spray, delay_seconds=1)

            o_charge_robot_after_spray = robot.charge().oracle().with_id("o_charge_robot_after_soy_history_disease_check").depends_on(o_commit_spray, delay_seconds=1)
            o_wait_robot_after_spray = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_after_soy_history_disease_check").depends_on(o_charge_robot_after_spray, delay_seconds=1)

            o_wait_r1 = advance_days(self, o_wait_robot_after_spray, 10, "o_wait_r1_nutrient_window")
            o_r1_soil = sensor.read_soil_sensors().oracle().with_id("o_r1_soil_nutrient_check").depends_on(o_wait_r1, delay_seconds=1)
            o_r1_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r1_canopy_nutrient_check").depends_on(o_r1_soil, delay_seconds=1)
            o_robot_status_r1 = robot.check_status().oracle().with_id("o_robot_status_before_r1_health").depends_on(o_r1_canopy, delay_seconds=1)
            o_r1_health = robot.inspect_crop_health(0, 63).oracle().with_id("o_r1_whole_field_health_check").depends_on(o_robot_status_r1, delay_seconds=2)
            o_r1_topdress = farm_world.apply_fertigation(0, 63, nutrient_amount=R1_NUTRIENT_AMOUNT, water_mm=2.0).oracle().with_id("o_r1_light_nutrient_topdress").depends_on(o_r1_health, delay_seconds=2)
            o_commit_r1 = farm_world.commit_daily_physics().oracle().with_id("o_commit_r1_nutrient_management").depends_on(o_r1_topdress, delay_seconds=1)

            o_wait_r5 = advance_days(self, o_commit_r1, 25, "o_wait_r5_r6_window")
            o_r5_weather = weather.get_current_weather().oracle().with_id("o_r5_r6_weather_check").depends_on(o_wait_r5, delay_seconds=1)
            o_r5_soil = sensor.read_soil_sensors().oracle().with_id("o_r5_r6_soil_water_check").depends_on(o_r5_weather, delay_seconds=1)
            o_r5_canopy = sensor.read_canopy_sensors().oracle().with_id("o_r5_r6_canopy_check").depends_on(o_r5_soil, delay_seconds=1)
            o_r5_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_r5_r6_no_extra_water_action").depends_on(o_r5_canopy, delay_seconds=1)

            o_wait_harvest = advance_days(self, o_r5_commit, 51, "o_wait_harvest_window")
            o_harvest_weather = weather.get_current_weather().oracle().with_id("o_harvest_weather_check").depends_on(o_wait_harvest, delay_seconds=1)
            o_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_harvest_forecast_check").depends_on(o_harvest_weather, delay_seconds=1)
            o_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_harvest_trafficability_soil_check").depends_on(o_harvest_forecast, delay_seconds=1)
            o_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_harvest_farm_overview").depends_on(o_harvest_soil, delay_seconds=1)
            o_harvest_range = farm_world.get_ridge_range_state(0, 63).oracle().with_id("o_harvest_ridge_range_state_0_63").depends_on(o_harvest_overview, delay_seconds=1)
            o_attach = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_harvest_range, delay_seconds=1)
            o_harvest = harvest_range(tractor, farm_world, o_attach, start_ridge=0, end_ridge=63, id_prefix="o_whole_field")
            o_after_harvest = self._after_named_step(o_harvest, "after_whole_field_harvest_store")
            o_post_storage = advance_days(self, o_after_harvest, 1, "o_wait_post_storage_stability")
            o_report = aui.send_message_to_user(content="已完成前茬大豆/病害历史湿六月场景：根据历史风险提前scout，并仅对22-43垄靶向fungicide后完成收获入库。").oracle().with_id("o_report").depends_on(o_post_storage, delay_seconds=2)

            self.events = collect_event_graph(briefing)
