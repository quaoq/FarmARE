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

SOURCE_SLUG = 'hb_r5_leaf_feeder_defoliation'
SPEC_SLUG = 'hb_r5_leaf_feeder_defoliation'
CHECKPOINT_LABEL = 'before_o_r5_insecticide_0_16_39_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_r5_leaf_feeder_r5_insecticide_16_39_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbR5LeafFeederR5Insecticide1639Action(Scenario):
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
                '任务：承接R5期叶片取食虫害已确认的杀虫剂 action-ready 状态。'
                '前序巡查已把虫害压力定位到16-39垄，当前只需要复核窗口、资源和目标区状态，并完成闭环处理。\n'
                '请按以下步骤操作：\n'
                '1. 查看当前天气和3天天气预报，确认近期没有会冲刷药效的降雨窗口。\n'
                '2. 读取土壤和冠层传感器，确认田间可通行且目标区仍有虫害/冠层受损信号。\n'
                '3. 查看16-39垄状态，确认目标区仍处于需要处理的R5叶片取食压力。\n'
                '4. 查看库存并检查喷雾设备状态，确认杀虫剂和设备都可用。\n'
                '5. 条件支持时，装载约93.0 L杀虫剂，并对16-39垄分块完成一次定向杀虫。\n'
                '6. 处理后复查16-39垄状态，并向我报告已完成复核、杀虫和复查。'
            )
        else:
            briefing_text = '请复核天气、目标区虫害状态、库存和设备，完成一次定向杀虫并复查。'
        build_management_l1_flow(
            self,
            SPEC,
            SPEC.actions[0],
            briefing_text,
            'o_r5_insecticide_16_39',
            report_text='已完成R5叶片取食虫害区的条件复核、定向杀虫和处理后复查。',
        )

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_r5_leaf_feeder_defoliation r5_insecticide_16_39 L1')
