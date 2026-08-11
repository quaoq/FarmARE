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
SCENARIO_ID = 'scenario_l2_hb_hn58_dual_stress_r5_irrigation_8_23'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbHn58DualStressR5Irrigation823(Scenario):
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
                '准备处理HN58双胁迫田块的R5水分风险。本场景同时可能存在局部缺水和局部冠层/病害压力，'
                '供水资源有限；不要预设目标垄号，也不要把低NDVI直接等同于缺水。\n'
                '请按以下步骤操作：\n'
                '1. 查看田块总览，确认当前作物处于R5附近，并了解全田整体状态。\n'
                '2. 查看今天天气和3天天气预报，确认近期降雨是否足以自然缓解缺水风险。\n'
                '3. 如果当前还不是合适处理窗口，等待到目标检查窗口后重新查看天气和预报。\n'
                '4. 读取全田土壤传感器，找出VWC明显低于其他区域的传感器覆盖区；这是疑似缺水主线索。\n'
                '5. 读取全田冠层传感器，把NDVI异常作为辅助证据；低NDVI也可能来自病害或冠层受损，不能单独作为灌溉依据。\n'
                '6. 对低VWC传感器覆盖区及其相邻边界垄查看田块状态，确认连续缺水风险区域和作物阶段。\n'
                '7. 使用多光谱无人机复查疑似缺水区的NDVI，再使用热成像无人机检查冠层温度，区分水分胁迫和病害/冠层损伤。\n'
                '8. 使用地面机器人复查疑似缺水区作物健康，确认缺水是主要限制因素；如果另一区域只是低NDVI但土壤水分正常，不要把它当作灌溉目标。\n'
                '9. 查看库存、水资源和设备状态，确认有限水量允许一次定向处理。\n'
                '10. 证据支持后，只对确认的连续缺水区域执行短时定向灌溉，不要扩大到仅表现为冠层/病害压力的区域。\n'
                '11. 等待约6小时物理响应后，复查处理区域状态。\n'
                '12. 向我汇报：低VWC证据、低NDVI是否被排除为缺水依据、最终灌溉范围、灌溉时长、复查结果，以及本次处理是否保护了产量潜力。'
            )
        else:
            briefing_text = '请诊断HN58双胁迫田的R5水分风险；从全田土壤和冠层传感器中定位异常区域，区分缺水与病害/冠层受损，证据支持时完成定向灌溉和复查。'
        build_management_l2_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_r5_irrigation_8_23')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong58_water_chemical_priority_under_dual_stress r5_irrigation_8_23 L2')
