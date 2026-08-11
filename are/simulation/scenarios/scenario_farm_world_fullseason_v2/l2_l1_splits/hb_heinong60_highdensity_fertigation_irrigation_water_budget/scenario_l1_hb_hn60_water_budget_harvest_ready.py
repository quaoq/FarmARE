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
    build_harvest_l1_flow,
)

SOURCE_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
SPEC_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
CHECKPOINT_LABEL = 'o_wait_water_priority_40_55_harvest_zone_day_022'
HARVEST_ZONE_LABELS = ('water_priority_40_55',)
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_hn60_water_budget_harvest_ready'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbHn60WaterBudgetHarvestReady(Scenario):
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
                '准备执行黑农60高密度田指定区域harvest-ready任务。当前只处理40-55垄水分优先区，'
                '目标是避免高水分和晚收造成回收损失。\n'
                '请按以下步骤操作：\n'
                '1. 查看当前天气和3天天气预报，确认收获窗口。\n'
                '2. 查看全田概览并读取40-55垄状态，确认成熟度、harvest_allowed和grain_moisture。\n'
                '3. 确认通行性、仓储容量和烘干资源。\n'
                '4. 条件满足时完成40-55垄收获并及时卸粮。\n'
                '5. 烘干到13.0%后入库。\n'
                '6. 向我汇报 recovered yield、水分处理和入库闭环。'
            )
        else:
            briefing_text = '请复核黑农60的40-55垄成熟、水分、天气和资源；条件满足时完成收获、烘干并入库。'
        build_harvest_l1_flow(self, SPEC, briefing_text, harvest_zone_labels=HARVEST_ZONE_LABELS)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong60_highdensity_fertigation_irrigation_water_budget harvest L1')
