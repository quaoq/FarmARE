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

SOURCE_SLUG = 'hb_high_weed_seedbank_mechanical_only_baseline'
SPEC_SLUG = 'hb_high_weed_seedbank_early_control'
CHECKPOINT_LABEL = 'before_o_emergence_mechanical_weed_0_0_63_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_weed_seedbank_emergence_mechanical_weed_0_63_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbWeedSeedbankEmergenceMechanicalWeed063Action(Scenario):
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
            briefing_text = '任务：承接已确认的mechanical weed control action-ready 状态。请重新复核当前天气、短期预报、土壤/冠层、目标区状态、库存和设备；条件支持时完成一次处理并复查目标区。'
        else:
            briefing_text = '任务：复核条件后，对已确认区域执行一次mechanical weed control并复查。'
        build_management_l1_flow(self, SPEC, SPEC.actions[0], 'o_emergence_mechanical_weed_0_63', briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_high_weed_seedbank_mechanical_only_baseline emergence_mechanical_weed_0_63 L1')
