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

SOURCE_SLUG = 'hb_disease_then_drought_recovery_tradeoff'
SPEC_SLUG = 'hb_disease_then_drought_recovery_tradeoff'
CHECKPOINT_LABEL = 'o_wait_harvest_day_001'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_disease_drought_harvest_window'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbDiseaseDroughtHarvestWindow(Scenario):
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
                '准备处理病害后又经历R5干旱田块的R8后收获窗口。目标是在成熟、水分、天气和通行性都合适时，'
                '完成0-63垄可回收产量的收获、烘干和入库。\n'
                '请按以下步骤操作：\n'
                '1. 查看当前天气、3天天气预报、全田概览和土壤通行性。\n'
                '2. 若水分或天气未达标，等待到合适窗口；等待后必须重新复查天气和预报。\n'
                '3. 读取0-63垄状态，确认R8、harvest_allowed、grain_moisture和田间风险。\n'
                '4. 条件满足时按每趟4垄完成0-63垄收获，并在每趟后及时卸粮。\n'
                '5. 该田块需要水分处理，收后烘干到13.0%安全水分再入库。\n'
                '6. 不要在田间不可通行或湿粮无法处理时强行收获。\n'
                '7. 向我汇报 recovered yield、烘干/入库结果和剩余天气损失风险。'
            )
        else:
            briefing_text = '请判断病害-干旱田块的成熟收获窗口；条件满足时完成0-63垄收获、烘干到13.0%并入库。'
        build_harvest_l2_flow(self, SPEC, 38, briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_disease_then_drought_recovery_tradeoff harvest L2')
