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
    build_harvest_l2_flow,
)

SOURCE_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
SPEC_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
CHECKPOINT_LABEL = 'o_wait_harvest_day_001'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_hn60_water_budget_harvest_window'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbHn60WaterBudgetHarvestWindow(Scenario):
    start_time: float | None = checkpoint_sim_time(SOURCE_SLUG, CHECKPOINT_LABEL)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SPEC.scenario_id
    source_checkpoint_label = CHECKPOINT_LABEL
    source_checkpoint_date = checkpoint_date(SOURCE_SLUG, CHECKPOINT_LABEL)

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        restore_batch_checkpoint(self, SPEC, SOURCE_SLUG, CHECKPOINT_LABEL)
        self.start_time = checkpoint_sim_time(SOURCE_SLUG, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        if self.detailed_briefing:
            briefing_text = (
                '准备处理黑农60高密度水预算田块的R8后分区收获窗口。目标是按成熟、水分和通行性差异安全回收入库。\n'
                '请按以下步骤操作：\n'
                '1. 查看天气、3天预报、全田概览和土壤通行性。\n'
                '2. 等待到第一批收获窗口后重新复查天气和预报。\n'
                '3. 读取成熟区状态，确认R8、harvest_allowed和grain_moisture。\n'
                '4. 条件满足时收获成熟区，收后卸粮、烘干到13.0%并入库。\n'
                '5. 对后续成熟区继续等待并复查，成熟后再收获。\n'
                '6. 不要提前收获仍需等待的水分优先区。\n'
                '7. 向我汇报各分区 recovered yield、水分处理、入库结果和剩余风险。'
            )
        else:
            briefing_text = '请判断黑农60高密度田分区成熟收获窗口；条件满足时完成收获、烘干和入库。'
        build_harvest_l2_flow(self, SPEC, 33, briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong60_highdensity_fertigation_irrigation_water_budget harvest L2')
