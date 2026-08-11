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
    build_planting_l1_flow,
)

SOURCE_SLUG = 'hb_planter_skip_rows_stand_gap'
SPEC_SLUG = 'hb_planter_skip_rows_stand_gap'
CHECKPOINT_LABEL = 'before_whole_field_planting_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_skip_rows_whole_field_planting_execution'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbSkipRowsWholeFieldPlantingExecution(Scenario):
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
                '准备执行跳播风险场景的全田播种。整地、基肥和起垄已完成，当前目标是完成0-63垄HEINONG84播种。\n'
                '请按以下步骤操作：\n'
                '1. 查看当前天气和3天天气预报，确认播种窗口。\n'
                '2. 读取土壤传感器，确认种床温湿度和通行性。\n'
                '3. 检查拖拉机/播种机状态。\n'
                '4. 装载HEINONG84种子。\n'
                '5. 按播深4.0cm、株距7.9cm完成0-63垄播种；中途种子不足时先补装。\n'
                '6. 播种后提交物理更新并查看全田概览。\n'
                '7. 向我汇报全田已播状态和是否存在后续跳播/出苗复查风险。'
            )
        else:
            briefing_text = '请复核天气、土壤和播种设备后，完成0-63垄HEINONG84播种并复查已播状态。'
        build_planting_l1_flow(self, SPEC, 'whole_field', briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_planter_skip_rows_stand_gap planting L1')
