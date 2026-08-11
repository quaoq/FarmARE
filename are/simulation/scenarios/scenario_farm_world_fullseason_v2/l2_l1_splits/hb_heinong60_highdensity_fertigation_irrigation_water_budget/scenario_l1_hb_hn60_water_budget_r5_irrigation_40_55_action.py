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
CHECKPOINT_LABEL = 'before_o_r5_irrigation_0_40_55_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_hn60_water_budget_r5_irrigation_40_55_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbHn60WaterBudgetR5Irrigation4055Action(Scenario):
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
                '准备执行黑农60高密度田R5缺水恢复。目标区是40-55垄，当前已处于action-ready状态，'
                '且剩余水预算有限。\n'
                '请按以下步骤操作：\n'
                '1. 查看今天天气和3天天气预报，确认近期降雨不足以解除目标区缺水风险。\n'
                '2. 读取土壤传感器和冠层传感器，确认目标区仍存在水分压力。\n'
                '3. 读取40-55垄状态，确认目标区、作物阶段和缺水信号。\n'
                '4. 查看库存、水资源和剩余水预算，不要扩展到其他垄。\n'
                '5. 检查灌溉/拖拉机设备状态。\n'
                '6. 对40-55垄执行一次约0.55小时定向灌溉。\n'
                '7. 等待约6小时物理响应。\n'
                '8. 完成后复查40-55垄状态。\n'
                '9. 向我汇报灌溉范围、时长、水预算消耗和复查结果。'
            )
        else:
            briefing_text = '请复核黑农60高密度田40-55垄R5缺水区，完成一次定向灌溉并复查水分响应。'
        build_management_l1_flow(self, SPEC, SPEC.actions[1], briefing_text, 'o_r5_irrigation_40_55')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong60_highdensity_fertigation_irrigation_water_budget r5_irrigation_40_55 L1')
