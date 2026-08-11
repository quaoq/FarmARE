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

SOURCE_SLUG = 'hb_high_weed_seedbank_mechanical_only_baseline'
SPEC_SLUG = 'hb_high_weed_seedbank_early_control'
CHECKPOINT_LABEL = 'before_o_emergence_mechanical_weed_0_0_63_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_weed_seedbank_emergence_mechanical_weed_0_63_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbWeedSeedbankEmergenceMechanicalWeed063Action(Scenario):
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
                '准备执行高种子库田块全田机械除草。0-63垄草害竞争已确认，当前是action-ready任务。\n'
                '请按以下步骤操作：\n'
                '1. 查看当前天气和3天天气预报，确认机械进田窗口。\n'
                '2. 读取土壤传感器和冠层传感器，确认通行性和草害压力。\n'
                '3. 读取0-63垄状态，确认全田机械除草边界。\n'
                '4. 查看拖拉机和中耕设备状态。\n'
                '5. 挂接中耕机，对0-63垄执行机械除草；不要使用化学除草替代。\n'
                '6. 作业后卸下中耕机，并复查0-63垄状态。\n'
                '7. 向我汇报作业范围、设备状态、草害压力和冠层响应。'
            )
        else:
            briefing_text = '请复核0-63垄通行性、草害压力和设备状态；条件支持时执行机械除草并复查。'
        build_management_l1_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_emergence_mechanical_weed_0_63')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_high_weed_seedbank_mechanical_only_baseline emergence_mechanical_weed_0_63 L1')
