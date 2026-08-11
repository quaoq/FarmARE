from __future__ import annotations

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult

from .._batch_generated_split_common import (
    checkpoint_date,
    checkpoint_sim_time,
    get_spec,
    restore_batch_checkpoint,
    validate_native_workflow,
    build_management_l1_flow,
)

SOURCE_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
SPEC_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
CHECKPOINT_LABEL = 'before_o_emergence_fertigation_0_8_19_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_hn60_water_budget_emergence_fertigation_8_19_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbHn60WaterBudgetEmergenceFertigation819Action(Scenario):
    start_time: float | None = checkpoint_sim_time(SOURCE_SLUG, CHECKPOINT_LABEL)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SPEC.scenario_id
    source_checkpoint_label = CHECKPOINT_LABEL
    source_checkpoint_date = checkpoint_date(SOURCE_SLUG, CHECKPOINT_LABEL)

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        restore_batch_checkpoint(self, SPEC, SOURCE_SLUG, CHECKPOINT_LABEL)
        self.start_time = checkpoint_sim_time(SOURCE_SLUG, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        if self.detailed_briefing:
            briefing_text = (
                '准备执行黑农60高密度田8-19垄出苗期水肥恢复。该任务已经处于action-ready状态，'
                '目标是在有限水预算下对已确认弱势区做一次小水量水肥补偿。\n'
                '请按以下步骤操作：\n'
                '1. 查看今天天气和3天天气预报，确认没有会冲刷或中断水肥作业的降雨风险。\n'
                '2. 读取土壤传感器和冠层传感器，确认8-19垄仍表现为水肥弱势而不是全田性异常。\n'
                '3. 读取8-19垄状态，确认目标区、作物阶段和处理边界。\n'
                '4. 查看库存、肥料和水预算；该批次总灌溉预算有限，不要扩展到其他垄。\n'
                '5. 检查拖拉机/水肥系统状态。\n'
                '6. 对8-19垄执行水肥恢复：fertigation amount=0.24，water_mm=2.0。\n'
                '7. 完成后复查8-19垄状态和资源消耗。\n'
                '8. 向我汇报处理范围、供水参数、肥料参数和剩余水预算风险。'
            )
        else:
            briefing_text = '请复核条件后，对黑农60高密度田8-19垄执行一次小水量水肥恢复并复查资源消耗。'
        build_management_l1_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_emergence_fertigation_8_19')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong60_highdensity_fertigation_irrigation_water_budget emergence_fertigation_8_19 L1')
