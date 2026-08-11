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

SOURCE_SLUG = 'hb_disease_then_drought_recovery_tradeoff'
SPEC_SLUG = 'hb_disease_then_drought_recovery_tradeoff'
CHECKPOINT_LABEL = 'after_mid_routine_check'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_disease_drought_mid_fungicide_20_43'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbDiseaseDroughtMidFungicide2043(Scenario):
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
                '准备诊断病害-干旱权衡场景的MID期杀菌剂窗口。重点关注20-43垄，'
                '目标是确认该区域是否存在需要防控的病害压力，而不是把干旱卷叶误判为病害。\n'
                '请按以下步骤操作：\n'
                '1. 查看全田概览，确认当前处于MID期病害/干旱权衡窗口。\n'
                '2. 查看今天天气和3天天气预报，判断近期湿度、降雨和喷施窗口。\n'
                '3. 若需要等待到目标窗口，等待后重新查看天气和预报。\n'
                '4. 读取土壤传感器和冠层传感器，区分缺水压力与病害压力。\n'
                '5. 读取目标区状态，并用无人机巡查该区域的长势/病害差异。\n'
                '6. 用地面机器人检查目标区，确认病斑或病害压力，而不是干旱症状主导。\n'
                '7. 查看杀菌剂库存和拖拉机/喷雾设备状态。\n'
                '8. 证据和窗口都支持时，只对目标区执行一次杀菌剂处理。\n'
                '9. 完成后复查目标区状态。\n'
                '10. 向我汇报证据链、喷施范围、药剂使用，以及该动作如何降低病害导致的产量风险。'
            )
        else:
            briefing_text = '请诊断MID期20-43垄病害风险；证据支持时定向喷施杀菌剂并复查，避免把干旱症状误判为病害。'
        build_management_l2_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_mid_fungicide_20_43')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_disease_then_drought_recovery_tradeoff mid_fungicide_20_43 L2')
