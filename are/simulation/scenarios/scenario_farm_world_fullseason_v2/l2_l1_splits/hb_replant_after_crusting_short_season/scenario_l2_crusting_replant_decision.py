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

from ._hb_replant_after_crusting_short_season_split_common import (
    CHECKPOINT_AFTER_EMERGENCE_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    apply_replant_blocks,
    checkpoint_sim_time,
    populate_hb_replant_apps,
    restore_hb_replant_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_replant_crusting_replant_decision"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBReplantCrustingReplantDecision(Scenario):
    """L2 split: diagnose crusting-related emergence loss, wait for window, replant."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_EMERGENCE_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_EMERGENCE_ROUTINE
    source_checkpoint_date = "2026-05-22"
    source_dap = 18
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_replant_apps(self)
        restore_hb_replant_checkpoint(self, CHECKPOINT_AFTER_EMERGENCE_ROUTINE)

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
                "准备诊断播后强雨造成的板结缺苗风险。重点关注0-11垄，"
                "目标是判断是否存在真实stand缺口，而不是把正常延迟出苗误判为缺苗。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气和5天天气预报，确认近期补种窗口。\n"
                "2. 读取土壤传感器，判断表层板结、墒情和通行性。\n"
                "3. 查看全田概览，确认整体出苗阶段。\n"
                "4. 等待到缺苗信号更清楚后，复查土壤和冠层。\n"
                "5. 读取目标区状态，并用无人机和地面机器人确认stand缺口。\n"
                "6. 检查种子库存和播种设备状态。\n"
                "7. 若证据支持且季节仍允许，只对确认缺苗区域补种。\n"
                "8. 补种后复查目标区和资源余量。\n"
                "9. 向我汇报补种区域为何影响产量潜力，以及哪些健康垄没有被重复播种。"
            )
        else:
            briefing_text = "请诊断0-11垄板结缺苗风险；确认缺苗会影响产量潜力且窗口支持时，只补种必要区域并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_emergence_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=5).oracle().with_id(
                "o_check_replant_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_crusting_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_check_emergence_overview"
            ).depends_on(o_soil, delay_seconds=1)
            o_wait_window = system.advance_time(days=3).oracle().with_id(
                "o_wait_to_replant_window"
            ).depends_on(o_overview, delay_seconds=1)
            o_soil_ready = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_seedbed_before_survey"
            ).depends_on(o_wait_window, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_recheck_canopy_emergence_signal"
            ).depends_on(o_soil_ready, delay_seconds=1)
            o_range = farm_world.get_ridge_range_state(0, 11).oracle().with_id(
                "o_check_suspect_emergence_range"
            ).depends_on(o_canopy, delay_seconds=1)
            o_drone = mavic.fly_survey(0, 11).oracle().with_id(
                "o_localize_slow_emergence_by_drone"
            ).depends_on(o_range, delay_seconds=1)
            o_robot_status = robot.check_status().oracle().with_id(
                "o_check_robot_before_ground_confirmation"
            ).depends_on(o_drone, delay_seconds=1)
            o_ground = robot.inspect_emergence(0, 11).oracle().with_id(
                "o_confirm_crusting_emergence_loss_ground"
            ).depends_on(o_robot_status, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_check_replant_equipment"
            ).depends_on(o_ground, delay_seconds=1)
            replant_events, o_after_replant = apply_replant_blocks(
                tractor, o_tractor, "o_targeted_crusting"
            )
            o_recheck = farm_world.get_ridge_range_state(0, 11).oracle().with_id(
                "o_recheck_replanted_area"
            ).depends_on(o_after_replant, delay_seconds=2)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_recheck_seed_and_resource_balance"
            ).depends_on(o_recheck, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成板结出苗风险诊断、局部补种和补种后资源/状态复查。"
            ).oracle().with_id("o_report").depends_on(o_inventory, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_overview,
            o_wait_window,
            o_soil_ready,
            o_canopy,
            o_range,
            o_drone,
            o_robot_status,
            o_ground,
            o_tractor,
            *replant_events,
            o_recheck,
            o_inventory,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 crusting replant decision split"
        )
