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

SOURCE_SLUG = 'hb_potassium_deficit_dry_podfill_interaction'
SPEC_SLUG = 'hb_potassium_deficit_dry_podfill_interaction'
CHECKPOINT_LABEL = 'before_o_r5_irrigation_0_20_39_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_potassium_dry_r5_irrigation_20_39_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbPotassiumDryR5Irrigation2039Action(Scenario):
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
                '准备执行钾缺+干旱互作场景中的R5灌溉恢复。目标区是20-39垄，当前已处于action-ready状态。\n'
                '请按以下步骤操作：\n'
                '1. 查看今天天气，确认没有会替代灌溉的有效降雨。\n'
                '2. 查看3天天气预报，确认近期降雨不足以解除20-39垄缺水风险。\n'
                '3. 读取土壤传感器，确认目标区仍存在水分不足。\n'
                '4. 读取冠层传感器，确认作物冠层状态与R5缺水风险一致。\n'
                '5. 查看20-39垄田块状态，确认目标区、作物阶段和缺水信号。\n'
                '6. 查看库存、水资源和设备状态，确认可以完成一次定向灌溉。\n'
                '7. 检查拖拉机/灌溉设备状态。\n'
                '8. 对20-39垄执行一次约0.65小时定向灌溉，不要扩大到非目标垄。\n'
                '9. 等待约6小时物理响应。\n'
                '10. 复查20-39垄状态，确认土壤水分和冠层状态是否改善。\n'
                '11. 向我汇报灌溉范围、时长、复查结果，以及本次动作是否缓解了biological yield风险。'
            )
        else:
            briefing_text = '请复核条件后，对20-39垄已确认R5缺水区执行一次灌溉并复查响应。'
        build_management_l1_flow(self, SPEC, SPEC.actions[1], briefing_text, 'o_r5_irrigation_20_39')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_potassium_deficit_dry_podfill_interaction r5_irrigation_20_39 L1')
