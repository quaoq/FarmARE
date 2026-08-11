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

SOURCE_SLUG = 'hb_planter_skip_rows_stand_gap'
SPEC_SLUG = 'hb_planter_skip_rows_stand_gap'
CHECKPOINT_LABEL = 'before_o_emergence_replant_0_12_15_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_skip_rows_emergence_replant_12_15_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbSkipRowsEmergenceReplant1215Action(Scenario):
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
                '准备执行跳播缺苗区补种。12-15垄stand-gap已确认，当前是action-ready任务。\n'
                '请按以下步骤操作：\n'
                '1. 查看当前天气和3天天气预报，确认补种窗口。\n'
                '2. 读取土壤传感器和冠层传感器，确认目标区仍适合补种。\n'
                '3. 读取12-15垄状态，确认目标区和作物阶段。\n'
                '4. 查看种子库存和播种设备状态。\n'
                '5. 条件支持时只对12-15垄补种HEINONG84，播深4.0cm、株距7.9cm。\n'
                '6. 补种后复查12-15垄状态。\n'
                '7. 向我汇报补种范围、资源消耗和健康垄未被重播。'
            )
        else:
            briefing_text = '请复核条件后，对12-15垄已确认缺苗区执行补种并复查。'
        build_management_l1_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_emergence_replant_12_15')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_planter_skip_rows_stand_gap emergence_replant_12_15 L1')
