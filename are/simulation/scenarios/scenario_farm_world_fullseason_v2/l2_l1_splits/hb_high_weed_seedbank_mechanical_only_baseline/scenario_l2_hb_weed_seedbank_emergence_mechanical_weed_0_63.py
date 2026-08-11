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

SOURCE_SLUG = 'hb_high_weed_seedbank_mechanical_only_baseline'
SPEC_SLUG = 'hb_high_weed_seedbank_early_control'
CHECKPOINT_LABEL = 'after_emergence_routine_check'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_weed_seedbank_emergence_mechanical_weed_0_63'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbWeedSeedbankEmergenceMechanicalWeed063(Scenario):
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
                '准备诊断高杂草种子库、低化学投入田块的EMERGENCE机械除草窗口。'
                '目标是确认0-63垄草害竞争是否足以支持全田机械除草。\n'
                '请按以下步骤操作：\n'
                '1. 查看全田概览，确认当前处于早期草害判断窗口。\n'
                '2. 查看天气和3天预报，确认机械进田窗口。\n'
                '3. 等待目标窗口后重新复查天气、土壤通行性和冠层。\n'
                '4. 读取0-63垄状态，并用无人机巡查全田。\n'
                '5. 用地面机器人检查全田草害压力，排除营养或水分胁迫主导。\n'
                '6. 查看拖拉机和中耕设备状态。\n'
                '7. 证据和通行性支持时挂接中耕机，完成0-63垄机械除草。\n'
                '8. 作业后卸下中耕机，并复查全田状态。\n'
                '9. 向我汇报草害证据、作业范围、设备状态和复查结果。'
            )
        else:
            briefing_text = '请诊断0-63垄早期草害竞争；证据和通行性支持时完成全田机械除草并复查。'
        build_management_l2_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_emergence_mechanical_weed_0_63')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_high_weed_seedbank_mechanical_only_baseline emergence_mechanical_weed_0_63 L2')
