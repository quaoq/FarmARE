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

from ._hb_heihe43_split_common import (
    CHECKPOINT_AFTER_EMERGENCE_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    apply_heihe43_herbicide_blocks,
    cst_timestamp,
    populate_hb_heihe43_apps,
    restore_hb_heihe43_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_heihe43_early_weed_control"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBHeihe43EarlyWeedControl(Scenario):
    """L2 split: diagnose and control the early HEIHE43 weed-competition block."""

    start_time: float | None = cst_timestamp(2026, 5, 21, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_EMERGENCE_ROUTINE
    source_checkpoint_date = "2026-05-21"
    source_dap = 17
    source_growth_stage = "VC"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_heihe43_apps(self)
        restore_hb_heihe43_checkpoint(self, CHECKPOINT_AFTER_EMERGENCE_ROUTINE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备诊断黑河43 VC期早期草害竞争。重点关注44-55垄，并用24-35垄作为健康参照；"
                "目标是区分草害竞争、营养弱势和水分问题。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气和3天天气预报，确认是否适合后续喷施判断。\n"
                "2. 读取土壤传感器和冠层传感器，判断是否存在全田性水分或冠层异常。\n"
                "3. 查看全田概览，并用无人机巡查0-63垄，定位早期长势差异。\n"
                "4. 等待草害信号更清晰后，读取目标区和参照区状态。\n"
                "5. 用地面机器人检查目标区，确认主要风险是草害竞争，而不是营养不足或缺水。\n"
                "6. 等待到喷施窗口后，重新查看天气和目标区状态。\n"
                "7. 查看药剂库存和喷雾设备状态。\n"
                "8. 证据支持时只对确认草害区定向除草，完成后复查目标区。\n"
                "9. 向我汇报草害证据、处理范围、药剂使用，以及该操作如何减少早期竞争性产量损失。"
            )
        else:
            briefing_text = "请诊断黑河43 VC期44-55垄早期草害竞争；证据支持时等待喷施窗口，定向除草并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_weed_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_soil_trafficability"
            ).depends_on(o_forecast, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_check_canopy_context"
            ).depends_on(o_soil, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_check_whole_field_overview"
            ).depends_on(o_canopy, delay_seconds=1)
            o_survey = mavic.fly_survey(0, 63).oracle().with_id(
                "o_map_early_canopy_variability"
            ).depends_on(o_overview, delay_seconds=2)
            o_wait_growth = system.advance_time(days=8).oracle().with_id(
                "o_wait_until_weed_signal_visible"
            ).depends_on(o_survey, delay_seconds=1)
            o_target = farm_world.get_ridge_range_state(44, 55).oracle().with_id(
                "o_read_weed_competition_block"
            ).depends_on(o_wait_growth, delay_seconds=1)
            o_reference = farm_world.get_ridge_range_state(24, 35).oracle().with_id(
                "o_read_reference_block"
            ).depends_on(o_target, delay_seconds=1)
            o_ground = robot.inspect_crop_health(44, 55).oracle().with_id(
                "o_ground_confirm_weed_competition"
            ).depends_on(o_reference, delay_seconds=2)
            o_wait = system.advance_time(days=4).oracle().with_id(
                "o_wait_to_herbicide_window"
            ).depends_on(o_ground, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_herbicide_weather"
            ).depends_on(o_wait, delay_seconds=1)
            o_state_ready = farm_world.get_ridge_range_state(44, 55).oracle().with_id(
                "o_recheck_weed_block_before_herbicide"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_herbicide_inventory"
            ).depends_on(o_state_ready, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_check_sprayer_status"
            ).depends_on(o_inventory, delay_seconds=1)
            o_load = tractor.load_pesticide(29.4).oracle().with_id(
                "o_load_herbicide_volume"
            ).depends_on(o_status, delay_seconds=1)
            spray_events, o_after_spray = apply_heihe43_herbicide_blocks(
                tractor, o_load, "o_apply_targeted_herbicide"
            )
            o_recheck = farm_world.get_ridge_range_state(44, 55).oracle().with_id(
                "o_recheck_weed_block_after_control"
            ).depends_on(o_after_spray, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成草害竞争区域诊断、定向除草和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_canopy,
            o_overview,
            o_survey,
            o_wait_growth,
            o_target,
            o_reference,
            o_ground,
            o_wait,
            o_weather_ready,
            o_state_ready,
            o_inventory,
            o_status,
            o_load,
            *spray_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HEIHE43 weed-control L2 split")
