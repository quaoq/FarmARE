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

SOURCE_SLUG = 'hb_heinong58_water_chemical_priority_under_dual_stress'
SPEC_SLUG = 'hb_heinong58_water_chemical_priority_under_dual_stress'
CHECKPOINT_LABEL = 'after_r5_routine_check'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_hn58_dual_stress_r5_fungicide_40_55'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbHn58DualStressR5Fungicide4055(Scenario):
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
                '准备诊断黑农58双重压力场景的R5杀菌剂窗口。重点关注40-55垄，'
                '目标是确认该区域是否应优先杀菌，而不是误把缺水压力当作病害处理。\n'
                '请按以下步骤操作：\n'
                '1. 查看全田概览，确认当前处于R5管理窗口。\n'
                '2. 查看天气和3天预报，判断喷施窗口和病害风险。\n'
                '3. 等待目标窗口后重新复查天气和预报。\n'
                '4. 读取土壤传感器和冠层传感器，区分水分压力与病害压力。\n'
                '5. 读取目标区状态，并用无人机巡查该区域。\n'
                '6. 用地面机器人检查目标区，确认病害证据。\n'
                '7. 查看杀菌剂库存和喷雾设备状态。\n'
                '8. 证据支持时只对目标区喷施杀菌剂。\n'
                '9. 完成后复查目标区状态。\n'
                '10. 向我汇报证据链、喷施范围、药剂用量，以及为什么该动作优先于灌溉。'
            )
        else:
            briefing_text = '请诊断黑农58 R5期40-55垄病害风险；证据支持时定向喷施杀菌剂并复查。'
        build_management_l2_flow(self, SPEC, SPEC.actions[1], briefing_text, 'o_r5_fungicide_40_55')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong58_water_chemical_priority_under_dual_stress r5_fungicide_40_55 L2')
