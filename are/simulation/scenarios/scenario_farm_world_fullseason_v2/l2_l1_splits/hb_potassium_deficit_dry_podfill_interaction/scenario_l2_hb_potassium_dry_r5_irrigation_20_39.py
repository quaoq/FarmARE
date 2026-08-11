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
CHECKPOINT_LABEL = 'after_r5_routine_check'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_potassium_dry_r5_irrigation_20_39'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbPotassiumDryR5Irrigation2039(Scenario):
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
                '准备诊断钾缺+干旱互作场景的R5灌溉恢复窗口。重点关注20-39垄，'
                '目标是确认该区域是否存在会影响灌浆和biological yield的缺水风险，再决定是否定向灌溉。\n'
                '请按以下步骤操作：\n'
                '1. 查看全田概览，确认当前处于R5灌浆管理窗口。\n'
                '2. 查看今天天气和3天天气预报，判断自然降雨是否不足以解除20-39垄缺水风险。\n'
                '3. 若需要等待到目标窗口，等待后重新查看天气和预报。\n'
                '4. 读取土壤传感器和冠层传感器，检查目标区的土壤水分与冠层压力信号。\n'
                '5. 读取目标区状态，并用无人机巡查该区域的长势/热胁迫差异。\n'
                '6. 用地面机器人检查目标区，确认主要风险是R5缺水而不是病虫草害。\n'
                '7. 查看库存和水资源，再检查灌溉/拖拉机设备状态。\n'
                '8. 证据和窗口都支持时，只对目标区执行一次定向灌溉；不要扩大到非目标垄。\n'
                '9. 等待物理响应后复查目标区状态。\n'
                '10. 向我汇报证据链、灌溉范围、响应结果，以及该动作是否缓解了产量风险。'
            )
        else:
            briefing_text = '请诊断R5窗口20-39垄的缺水恢复需求；证据支持时定向灌溉并复查响应。'
        build_management_l2_flow(self, SPEC, SPEC.actions[1], briefing_text, 'o_r5_irrigation_20_39')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_potassium_deficit_dry_podfill_interaction r5_irrigation_20_39 L2')
