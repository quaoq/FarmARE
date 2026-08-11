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

SOURCE_SLUG = 'hb_heinong58_water_chemical_priority_under_dual_stress'
SPEC_SLUG = 'hb_heinong58_water_chemical_priority_under_dual_stress'
CHECKPOINT_LABEL = 'before_o_r5_irrigation_0_8_23_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_hn58_dual_stress_r5_irrigation_8_23_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbHn58DualStressR5Irrigation823Action(Scenario):
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
                '准备执行黑农58双重压力场景中的R5灌溉恢复。目标区是8-23垄，当前已处于action-ready状态。\n'
                '请按以下步骤操作：\n'
                '1. 查看当前天气和3天天气预报，确认近期降雨不足以解除目标区缺水风险。\n'
                '2. 读取土壤传感器和冠层传感器，确认目标区仍存在水分压力。\n'
                '3. 读取8-23垄状态，确认目标区、作物阶段和缺水信号。\n'
                '4. 查看水资源和设备状态，确认灌溉优先级高于药剂操作。\n'
                '5. 对8-23垄执行一次约0.55小时定向灌溉。\n'
                '6. 等待约6小时物理响应。\n'
                '7. 完成后复查8-23垄状态。\n'
                '8. 向我汇报灌溉范围、时长、复查结果和产量风险缓解情况。'
            )
        else:
            briefing_text = '请复核黑农58的8-23垄R5缺水区；条件合适时完成一次定向灌溉并复查。'
        build_management_l1_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_r5_irrigation_8_23')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong58_water_chemical_priority_under_dual_stress r5_irrigation_8_23 L1')
