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
CHECKPOINT_LABEL = 'before_o_r5_fungicide_1_40_55_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_hn58_dual_stress_r5_fungicide_40_55_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbHn58DualStressR5Fungicide4055Action(Scenario):
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
                '准备执行黑农58双重压力场景中的R5杀菌剂处理。目标区是40-55垄，'
                '当前已处于action-ready状态。\n'
                '请按以下步骤操作：\n'
                '1. 查看今天天气和3天天气预报，确认喷施窗口仍可用。\n'
                '2. 读取土壤传感器和冠层传感器，确认不要把缺水信号误当作需要灌溉优先处理。\n'
                '3. 读取40-55垄状态，确认目标区、作物阶段和病害压力。\n'
                '4. 查看杀菌剂库存和喷雾设备状态。\n'
                '5. 装载约48.96L杀菌剂。\n'
                '6. 对40-55垄执行杀菌剂处理，剂量为3.0L/垄。\n'
                '7. 完成后复查40-55垄状态。\n'
                '8. 向我汇报喷施范围、药剂用量、复查结果，以及为什么本次动作优先于灌溉。'
            )
        else:
            briefing_text = '请复核黑农58的40-55垄病害喷施窗口，完成杀菌剂处理并复查。'
        build_management_l1_flow(self, SPEC, SPEC.actions[1], briefing_text, 'o_r5_fungicide_40_55')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong58_water_chemical_priority_under_dual_stress r5_fungicide_40_55 L1')
