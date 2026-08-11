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

SOURCE_SLUG = 'hb_heinong58_water_chemical_priority_under_dual_stress'
SPEC_SLUG = 'hb_heinong58_water_chemical_priority_under_dual_stress'
CHECKPOINT_LABEL = 'o_wait_harvest_day_001'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_hn58_dual_stress_harvest_window'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbHn58DualStressHarvestWindow(Scenario):
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
                '准备处理黑农58双重压力田块的分区收获窗口。目标是按成熟和水分差异分区回收产量，'
                '避免早收湿粮、漏收晚熟区或因粮箱满载造成收获失败。'
                '本田块需要按三个逻辑分区闭环：0-7垄、24-63垄、以及后续晚熟/水分压力区8-23垄；'
                '8-23垄不能漏掉，也不能在未成熟或水分过高时提前收。\n'
                '请按以下步骤操作：\n'
                '1. 查看天气、3天预报、全田概览和土壤通行性。\n'
                '2. 等待到第一批成熟收获窗口后重新复查天气和预报，不要只按当前日期直接抢收。\n'
                '3. 分别读取0-7垄和24-63垄状态，确认目标垄已经R8、harvest_allowed为真、grain_moisture不高于18%。\n'
                '4. 条件满足时先完成0-7垄收获；每个收获小块后都要及时卸粮，避免grain bin overflow；该分区收完后烘干到13.0%并入库。\n'
                '5. 接着完成24-63垄收获；同样每个收获小块后卸粮，分区收完后烘干到13.0%并入库。\n'
                '6. 0-7垄和24-63垄闭环后，继续等待并复查8-23垄。8-23垄是晚熟/水分压力区，必须等到成熟且水分安全后再收，不能因为前两区完成就结束任务。\n'
                '7. 8-23垄达到条件后，完成8-23垄收获、逐块卸粮、烘干到13.0%并入库。\n'
                '8. 最终向我汇报三个分区的收获范围、recovered yield、水分处理、入库结果，以及是否还有未收或未入库风险。'
            )
        else:
            briefing_text = '请判断黑农58双重压力田块的分区收获窗口；条件满足时按成熟顺序收获、烘干并入库。'
        build_harvest_l2_flow(self, SPEC, 29, briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong58_water_chemical_priority_under_dual_stress harvest L2')
