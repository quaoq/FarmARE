from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_fertilizer_quota_edge_lowfertility_split_common import (
    CHECKPOINT_BEFORE_MILD_EDGE_FERTIGATION,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_fertilizer_quota_apps,
    restore_hb_fertilizer_quota_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_fertilizer_quota_apply_mild_edge_fertigation"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBFertilizerQuotaApplyMildEdgeFertigation(Scenario):
    """L1 split: apply the remaining-quota mild-edge fertigation pass."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_MILD_EDGE_FERTIGATION)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_MILD_EDGE_FERTIGATION
    source_checkpoint_date = "2026-06-27"
    source_dap = 54
    source_growth_stage = "V4_PLUS"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_fertilizer_quota_apps(self)
        restore_hb_fertilizer_quota_checkpoint(
            self, CHECKPOINT_BEFORE_MILD_EDGE_FERTIGATION
        )

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行轻度边缘营养补充。目标区是8-15垄，地面确认已完成，当前是action-ready任务。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气，确认小剂量水肥作业窗口。\n"
                "2. 读取土壤传感器，确认目标区水分和通行条件。\n"
                "3. 读取8-15垄状态，确认轻度边缘营养弱势仍存在。\n"
                "4. 查看库存和剩余肥料配额。\n"
                "5. 对8-15垄执行小剂量水肥补充：fertigation amount=0.16，water_mm=1.0。\n"
                "6. 完成后复查剩余配额。\n"
                "7. 向我汇报处理范围、配额消耗，以及没有挤占重度区所需配额。"
            )
        else:
            briefing_text = "请复核8-15垄轻度边缘小剂量水肥窗口和配额；条件合适时执行并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_recheck_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_soil"
            ).depends_on(o_weather, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(8, 15).oracle().with_id(
                "o_recheck_mild_edge_state"
            ).depends_on(o_soil, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_remaining_fertilizer_quota"
            ).depends_on(o_state, delay_seconds=1)
            o_fertigate = farm_world.apply_fertigation(8, 15, 0.16, 1.0).oracle().with_id(
                "o_apply_mild_edge_fertigation_8_15"
            ).depends_on(o_inventory, delay_seconds=2)
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_quota_after_topup"
            ).depends_on(o_fertigate, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成轻度边缘小剂量水肥补充和配额复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_state,
            o_inventory,
            o_fertigate,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "fertilizer quota mild-edge fertigation L1 split"
        )
