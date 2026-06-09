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
            briefing_text = '任务：从R5窗口巡查后的真实状态出发，围绕disease-control fungicide application做短闭环。请先用天气、预报、土壤、冠层、目标区状态、无人机和地面检查建立证据链；若证据和窗口支持，按当前资源约束对确认区域执行处理并复查响应。'
        else:
            briefing_text = '任务：诊断R5窗口的disease-control fungicide application需求；证据支持时处理确认区域并复查。'
        build_management_l2_flow(self, SPEC, SPEC.actions[1], 'o_r5_fungicide_40_55', briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong58_water_chemical_priority_under_dual_stress r5_fungicide_40_55 L2')
