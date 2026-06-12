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

SOURCE_SLUG = 'hb_disease_then_drought_recovery_tradeoff'
SPEC_SLUG = 'hb_disease_then_drought_recovery_tradeoff'
CHECKPOINT_LABEL = 'before_o_mid_fungicide_0_20_43_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_disease_drought_mid_fungicide_20_43_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbDiseaseDroughtMidFungicide2043Action(Scenario):
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
                '准备执行20-43垄MID期病害杀菌剂处理。该任务已经处于action-ready状态，'
                '目标是复核条件后完成一次定向喷施。\n'
                '请按以下步骤操作：\n'
                '1. 查看今天天气和3天天气预报，确认喷施后没有明显降雨冲刷风险。\n'
                '2. 读取土壤传感器和冠层传感器，确认当前状态仍符合病害处理窗口。\n'
                '3. 读取20-43垄状态，确认目标区、作物阶段和病害压力。\n'
                '4. 查看杀菌剂库存和拖拉机/喷雾设备状态。\n'
                '5. 装载约93.024L杀菌剂。\n'
                '6. 对20-43垄执行杀菌剂处理，剂量为3.8L/垄。\n'
                '7. 完成后复查20-43垄状态。\n'
                '8. 向我汇报喷施范围、药剂用量、复查结果，以及为什么本次动作不能用灌溉替代。'
            )
        else:
            briefing_text = '请复核喷施窗口和20-43垄已确认病害区，完成杀菌剂处理并复查。'
        build_management_l1_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_mid_fungicide_20_43')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_disease_then_drought_recovery_tradeoff mid_fungicide_20_43 L1')
