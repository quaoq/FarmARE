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

SOURCE_SLUG = 'hb_potassium_deficit_dry_podfill_interaction'
SPEC_SLUG = 'hb_potassium_deficit_dry_podfill_interaction'
CHECKPOINT_LABEL = 'o_wait_harvest_day_001'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_potassium_dry_harvest_window'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbPotassiumDryHarvestWindow(Scenario):
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
                '准备处理钾亏缺叠加干旱灌浆后的全田收获窗口。目标是在成熟、水分、天气和通行性合适时，'
                '回收0-63垄成熟籽粒，并通过烘干/入库避免晚收和湿粮损失。\n'
                '请按以下步骤操作：\n'
                '1. 查看今天天气、3天天气预报、全田概览和土壤传感器，确认是否已经接近收获窗口。\n'
                '2. 若成熟度或水分仍不适合收获，等待到收获就绪窗口；等待后不要直接作业，必须重新观测。\n'
                '3. 重新查看天气和3天预报，确认没有会影响收割、运输或晾干/烘干的降雨风险。\n'
                '4. 读取0-63垄状态，重点确认R8/harvest_allowed、grain_moisture和倒伏/通行风险。\n'
                '5. 条件满足后按全田0-63垄收获；每趟收获后及时卸粮，避免已收粮滞留在设备里。\n'
                '6. 因该田块需要水分处理，收后按要求把粮食烘干到安全入库水分，再入库。\n'
                '7. 不要在水分过高或田间不可通行时强行收获，也不要把湿粮直接长期入库。\n'
                '8. 向我汇报 recovered yield、烘干/入库结果，以及是否还有未收或天气损失风险。'
            )
        else:
            briefing_text = '请判断钾缺干旱田块的成熟收获窗口；条件满足后完成0-63垄收获、卸粮、烘干/入库并汇报回收风险。'
        build_harvest_l2_flow(self, SPEC, 23, briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_potassium_deficit_dry_podfill_interaction harvest L2')
