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

SCENARIO_ID = "scenario_l2_hb_insect_budget_r5_manual_control"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBInsectBudgetR5ManualControl(Scenario):
    """L2 split: diagnose R5 insect pressure, use manual point spray, and recheck."""

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
                "准备诊断R5始粒期虫害预算约束场景。重点关注24-47垄，并用0-15垄作为参考；"
                "目标是确认虫害是否达到处理阈值，而不是把病害残余或水肥问题误判为虫害。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气，确认当天作业和人工点喷安全性。\n"
                "2. 读取土壤传感器和冠层传感器，确认土壤偏湿背景和冠层异常。\n"
                "3. 查看全田概览和库存/预算，确认不适合无差别大面积药剂处理。\n"
                "4. 用无人机巡查目标区，定位异常冠层。\n"
                "5. 读取目标区和参考区状态，比较虫害风险与健康参照。\n"
                "6. 用地面机器人检查目标区虫口、叶片取食损伤和虫害压力。\n"
                "7. 若虫害达到阈值，只对确认区域做人工背负式点喷，每2垄一组，剂量3.2L/垄。\n"
                "8. 点喷后复查预算和目标区状态。\n"
                "9. 向我汇报人工点喷为何比大面积药剂处理更符合预算和产量保护目标。"
            )
        else:
            briefing_text = "请诊断R5期24-47垄虫害阈值和预算约束；确认虫害后只对目标区人工点喷并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_wet_soil_trafficability"
            ).depends_on(o_weather, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_check_canopy_signal"
            ).depends_on(o_soil, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_check_r5_farm_overview"
            ).depends_on(o_canopy, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_remaining_chemical_budget"
            ).depends_on(o_overview, delay_seconds=1)
            o_survey = mavic.fly_survey(24, 47).oracle().with_id(
                "o_map_r5_insect_block"
            ).depends_on(o_inventory, delay_seconds=2)
            o_target = farm_world.get_ridge_range_state(24, 47).oracle().with_id(
                "o_read_insect_block_state"
            ).depends_on(o_survey, delay_seconds=1)
            o_reference = farm_world.get_ridge_range_state(0, 15).oracle().with_id(
                "o_read_reference_state"
            ).depends_on(o_target, delay_seconds=1)
            o_ground = robot.inspect_pests(24, 47).oracle().with_id(
                "o_ground_confirm_insect_threshold"
            ).depends_on(o_reference, delay_seconds=2)
            spray_events, o_after_spray = apply_l3_manual_insecticide_blocks(
                field_ops, o_ground, "o_apply_manual_insecticide"
            )
            o_budget = farm_world.get_inventory().oracle().with_id(
                "o_recheck_budget_after_manual_spray"
            ).depends_on(o_after_spray, delay_seconds=1)
            o_recheck = farm_world.get_ridge_range_state(24, 47).oracle().with_id(
                "o_recheck_insect_block_after_spray"
            ).depends_on(o_budget, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成R5虫害阈值确认、人工点喷和预算/虫害复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_canopy,
            o_overview,
            o_inventory,
            o_survey,
            o_target,
            o_reference,
            o_ground,
            *spray_events,
            o_budget,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 R5 manual-insecticide split"
        )
