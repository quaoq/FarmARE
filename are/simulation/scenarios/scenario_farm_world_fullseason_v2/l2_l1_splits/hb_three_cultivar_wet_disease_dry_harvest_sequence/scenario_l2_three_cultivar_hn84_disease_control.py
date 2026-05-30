from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_three_cultivar_split_common import (
    CHECKPOINT_AFTER_MID_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    apply_hn84_fungicide_blocks,
    cst_timestamp,
    populate_three_cultivar_apps,
    restore_three_cultivar_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_three_cultivar_hn84_disease_control"


@register_scenario(SCENARIO_ID)
class ScenarioL2ThreeCultivarHN84DiseaseControl(Scenario):
    """L2 split: observe, diagnose, spray, and recheck HEINONG84 disease."""

    start_time: float | None = cst_timestamp(2026, 7, 4, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_MID_ROUTINE
    source_checkpoint_date = "2026-07-04"
    source_dap = 60
    source_growth_stage = "R1_BEGINNING_BLOOM/V4_PLUS"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_three_cultivar_apps(self)
        restore_three_cultivar_checkpoint(self, CHECKPOINT_AFTER_MID_ROUTINE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "已知本田块按品种分为三个可见管理分区：HEIHE50早熟分区为ridges 0-20，HEINONG84分区为ridges 21-42，HEINONG58分区为ridges 43-63。"
                "截至2026-07-04，田块完成中期例行巡查。"
                "当天有降雨，不能安排无人机飞行或喷药；请先用天气、土壤、canopy和地面机器人确认HEINONG84区病害风险。"
                "随后等待天气窗口，重新检查天气、土壤、canopy、目标区状态，再用无人机定位异常区并用地面机器人确认病斑。"
                "若证据支持湿六月病害，只对HEINONG84区定向杀菌，完成后复查目标区。"
                "不要把水分、肥力或虫害问题误用杀菌剂处理。"
            )
        else:
            briefing_text = "请在7月4日雨后先确认病害风险，等待可作业窗口后完成HEINONG84区定向杀菌和复查。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_check_rainy_day_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_forecast = (
                weather.get_forecast(days=3)
                .oracle()
                .with_id("o_check_spray_window_forecast")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_check_soil_wetness")
                .depends_on(o_forecast, delay_seconds=1)
            )
            o_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_check_canopy_signal")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(21, 42)
                .oracle()
                .with_id("o_read_hn84_state_rainy_day")
                .depends_on(o_canopy, delay_seconds=1)
            )
            o_ground_initial = (
                robot.inspect_crop_health(21, 42)
                .oracle()
                .with_id("o_ground_confirm_hn84_rainy_day")
                .depends_on(o_state, delay_seconds=2)
            )
            o_wait = (
                system.advance_time(days=5)
                .oracle()
                .with_id("o_wait_to_source_l3_spray_window")
                .depends_on(o_ground_initial, delay_seconds=1)
            )
            o_weather_ready = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_recheck_spray_weather")
                .depends_on(o_wait, delay_seconds=1)
            )
            o_soil_ready = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_recheck_soil_trafficability")
                .depends_on(o_weather_ready, delay_seconds=1)
            )
            o_canopy_ready = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_recheck_canopy_signal")
                .depends_on(o_soil_ready, delay_seconds=1)
            )
            o_state_ready = (
                farm_world.get_ridge_range_state(21, 42)
                .oracle()
                .with_id("o_recheck_hn84_before_survey")
                .depends_on(o_canopy_ready, delay_seconds=1)
            )
            o_survey = (
                mavic.fly_survey(21, 42)
                .oracle()
                .with_id("o_map_hn84_disease_area")
                .depends_on(o_state_ready, delay_seconds=2)
            )
            o_ground = (
                robot.inspect_crop_health(21, 42)
                .oracle()
                .with_id("o_ground_confirm_hn84_disease")
                .depends_on(o_survey, delay_seconds=2)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_check_fungicide_inventory")
                .depends_on(o_ground, delay_seconds=1)
            )
            o_status = (
                tractor.get_status()
                .oracle()
                .with_id("o_check_sprayer_status")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_load = (
                tractor.load_fungicide(85.3)
                .oracle()
                .with_id("o_load_hn84_fungicide")
                .depends_on(o_status, delay_seconds=1)
            )
            spray_events, o_after_spray = apply_hn84_fungicide_blocks(
                tractor, o_load, "o_apply_hn84_fungicide"
            )
            o_recheck = (
                farm_world.get_ridge_range_state(21, 42)
                .oracle()
                .with_id("o_recheck_hn84_after_fungicide")
                .depends_on(o_after_spray, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(content="已完成HEINONG84病害闭环诊断、定向杀菌和复查。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_canopy,
            o_state,
            o_ground_initial,
            o_wait,
            o_weather_ready,
            o_soil_ready,
            o_canopy_ready,
            o_state_ready,
            o_survey,
            o_ground,
            o_inventory,
            o_status,
            o_load,
            *spray_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 HEINONG84 disease-control split"
        )
