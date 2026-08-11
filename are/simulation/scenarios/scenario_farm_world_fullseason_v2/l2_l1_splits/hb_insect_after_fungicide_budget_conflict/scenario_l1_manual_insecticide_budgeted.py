from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    FieldOpsApp,
    RobotApp,
    SensorApp,
    WeatherApp,
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_insect_after_fungicide_budget_conflict_split_common import (
    CHECKPOINT_AFTER_R5_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    apply_l3_manual_insecticide_blocks,
    checkpoint_sim_time,
    populate_hb_insect_budget_apps,
    restore_hb_insect_budget_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_insect_budget_manual_insecticide"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBInsectBudgetManualInsecticide(Scenario):
    """L1 split: execute the confirmed R5 manual insecticide operation."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_R5_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_R5_ROUTINE
    source_checkpoint_date = "2026-08-03"
    source_dap = 91
    source_growth_stage = "R5_BEGINNING_SEED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_insect_budget_apps(self)
        restore_hb_insect_budget_checkpoint(self, CHECKPOINT_AFTER_R5_ROUTINE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        field_ops = self.get_typed_app(FieldOpsApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行R5虫害预算约束下的人工点喷。目标区是24-47垄，当前是action-ready虫害处理任务。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气，确认人工点喷窗口。\n"
                "2. 读取土壤传感器，确认土壤偏湿背景。\n"
                "3. 查看库存和剩余预算。\n"
                "4. 用无人机复核24-47垄冠层异常。\n"
                "5. 用地面机器人确认目标区虫口和叶片取食损伤。\n"
                "6. 若虫害阈值仍成立，使用人工背负式点喷处理24-47垄，每2垄一组，剂量3.2L/垄。\n"
                "7. 不要用杀菌剂或水肥处理虫害，也不要扩展到健康参照区。\n"
                "8. 向我汇报人工点喷范围、预算余量和虫害压力复查结果。"
            )
        else:
            briefing_text = "请复核24-47垄R5虫害证据和预算；确认阈值后人工点喷并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_soil_wetness"
            ).depends_on(o_weather, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_confirm_remaining_budget"
            ).depends_on(o_soil, delay_seconds=1)
            o_survey = mavic.fly_survey(24, 47).oracle().with_id(
                "o_confirm_insect_block_by_drone"
            ).depends_on(o_inventory, delay_seconds=2)
            o_ground = robot.inspect_pests(24, 47).oracle().with_id(
                "o_confirm_insect_threshold_on_ground"
            ).depends_on(o_survey, delay_seconds=2)
            spray_events, o_after_spray = apply_l3_manual_insecticide_blocks(
                field_ops, o_ground, "o_apply_budgeted_manual_insecticide"
            )
            o_report = aui.send_message_to_user(
                content="已完成预算约束下的R5人工点喷杀虫。"
            ).oracle().with_id("o_report").depends_on(o_after_spray, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_inventory,
            o_survey,
            o_ground,
            *spray_events,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 manual-insecticide split"
        )
