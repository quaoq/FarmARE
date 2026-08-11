from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    FarmWorldApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_insect_after_fungicide_budget_conflict_split_common import (
    CHECKPOINT_BEFORE_MID_FUNGICIDE,
    SOURCE_L3_SCENARIO_ID,
    apply_l3_fungicide_blocks,
    checkpoint_sim_time,
    populate_hb_insect_budget_apps,
    restore_hb_insect_budget_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_insect_budget_apply_fungicide"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBInsectBudgetApplyFungicide(Scenario):
    """L1 split: execute the budgeted disease-block fungicide operation."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_MID_FUNGICIDE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_MID_FUNGICIDE
    source_checkpoint_date = "2026-07-10"
    source_dap = 67
    source_growth_stage = "R1_BEGINNING_BLOOM"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_insect_budget_apps(self)
        restore_hb_insect_budget_checkpoint(self, CHECKPOINT_BEFORE_MID_FUNGICIDE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "任务：承接病害区已确认、药剂预算受限的杀菌剂 action-ready 状态。"
                "截至2026-07-10，等待窗口、无人机定位和地面病害确认已经完成；当前只需要做喷药前最小复核并按预算闭环执行。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气，确认无雨、风速适合喷药。\n"
                "2. 查看已确认病害区状态，确认病害压力仍存在且需要杀菌剂处理。\n"
                "3. 查看库存，确认剩余杀菌剂预算足够覆盖本次目标区作业。\n"
                "4. 检查喷雾设备状态。\n"
                "5. 证据、预算和设备都支持时，装载约76.3 L杀菌剂，并对已确认病害区分块完成一次定向杀菌。\n"
                "6. 不要用杀虫剂、水肥或扩大作业范围替代本次病害区杀菌；完成后向我报告处理结果。"
            )
        else:
            briefing_text = (
                "请复核天气、病害区状态、药剂预算和喷雾设备，完成一次预算内定向杀菌。"
            )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(18, 39).oracle().with_id(
                "o_confirm_disease_block_state"
            ).depends_on(o_weather, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_confirm_fungicide_budget"
            ).depends_on(o_state, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_confirm_sprayer_status"
            ).depends_on(o_inventory, delay_seconds=1)
            o_load = tractor.load_fungicide(76.3).oracle().with_id(
                "o_load_budgeted_fungicide"
            ).depends_on(o_status, delay_seconds=2)
            spray_events, o_after_spray = apply_l3_fungicide_blocks(
                tractor, o_load, "o_apply_budgeted_fungicide"
            )
            o_report = aui.send_message_to_user(
                content="已完成预算约束下的病害区喷药前复核、定向杀菌和结果上报。"
            ).oracle().with_id("o_report").depends_on(o_after_spray, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_state,
            o_inventory,
            o_status,
            o_load,
            *spray_events,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 budgeted-fungicide split"
        )
