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

SOURCE_SLUG = 'hb_potassium_deficit_dry_podfill_interaction'
SPEC_SLUG = 'hb_potassium_deficit_dry_podfill_interaction'
CHECKPOINT_LABEL = 'after_r1_routine_check'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_potassium_dry_r1_fertigation_20_39'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbPotassiumDryR1Fertigation2039(Scenario):
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
                '准备诊断钾缺+干旱互作场景的R1水肥恢复窗口。重点关注20-39垄，'
                '目标是先用传感器、无人机和地面检查确认该区域是否仍需要水肥补偿，再在资源约束内处理。\n'
                '请按以下步骤操作：\n'
                '1. 查看全田概览，确认当前处于R1管理窗口而不是收获或播种任务。\n'
                '2. 查看今天天气和3天天气预报，确认近期没有足以替代水肥恢复的降雨。\n'
                '3. 若需要等待到目标窗口，等待后重新查看天气和预报。\n'
                '4. 读取土壤传感器和冠层传感器，判断目标区是否存在水分/营养共同压力。\n'
                '5. 读取目标区状态，并用无人机巡查该区域的长势差异。\n'
                '6. 用地面机器人检查目标区，确认问题符合水肥恢复而不是病虫草害主导。\n'
                '7. 查看库存、肥料和水资源，再检查拖拉机/水肥系统状态。\n'
                '8. 证据和窗口都支持时，只对目标区执行一次水肥恢复；不要扩展到非目标垄。\n'
                '9. 完成后复查目标区状态。\n'
                '10. 向我汇报证据链、处理范围、资源消耗，以及该动作如何降低biological yield风险。'
            )
        else:
            briefing_text = '请诊断R1窗口20-39垄的水肥恢复需求；证据支持时定向处理并复查。'
        build_management_l2_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_r1_fertigation_20_39')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_potassium_deficit_dry_podfill_interaction r1_fertigation_20_39 L2')
