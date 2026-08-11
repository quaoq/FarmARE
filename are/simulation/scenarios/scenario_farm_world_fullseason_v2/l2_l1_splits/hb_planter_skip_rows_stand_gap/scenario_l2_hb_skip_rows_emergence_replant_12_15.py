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
    build_management_l2_flow,
)

SOURCE_SLUG = 'hb_planter_skip_rows_stand_gap'
SPEC_SLUG = 'hb_planter_skip_rows_stand_gap'
CHECKPOINT_LABEL = 'after_emergence_routine_check'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_skip_rows_emergence_replant_12_15'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbSkipRowsEmergenceReplant1215(Scenario):
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
                '准备诊断跳播风险场景的EMERGENCE补种窗口。重点关注12-15垄，'
                '目标是确认是否为连续缺苗，而不是正常晚出苗。\n'
                '请按以下步骤操作：\n'
                '1. 查看全田概览，确认当前处于出苗诊断阶段。\n'
                '2. 查看当前天气和3天天气预报，判断补种窗口。\n'
                '3. 等待目标窗口后重新复查天气、土壤和冠层。\n'
                '4. 读取目标区状态，并用无人机巡查该区域。\n'
                '5. 用地面机器人检查目标区，确认是否存在真实stand-gap。\n'
                '6. 查看种子库存和播种设备状态。\n'
                '7. 证据支持时只对目标区补种HEINONG84。\n'
                '8. 补种后复查目标区状态。\n'
                '9. 向我汇报缺苗证据、补种范围和健康垄未被重播。'
            )
        else:
            briefing_text = '请诊断12-15垄是否存在跳播造成的真实stand-gap；证据支持且窗口允许时局部补种并复查。'
        build_management_l2_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_emergence_replant_12_15')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_planter_skip_rows_stand_gap emergence_replant_12_15 L2')
