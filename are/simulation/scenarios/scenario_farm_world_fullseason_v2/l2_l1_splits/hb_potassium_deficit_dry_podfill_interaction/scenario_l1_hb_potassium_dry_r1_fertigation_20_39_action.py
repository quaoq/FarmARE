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

SOURCE_SLUG = 'hb_potassium_deficit_dry_podfill_interaction'
SPEC_SLUG = 'hb_potassium_deficit_dry_podfill_interaction'
CHECKPOINT_LABEL = 'before_o_r1_fertigation_0_20_39_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_potassium_dry_r1_fertigation_20_39_action'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbPotassiumDryR1Fertigation2039Action(Scenario):
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
            briefing_text = '任务：承接已确认的water-fertilizer recovery action-ready 状态。请重新复核当前天气、短期预报、土壤/冠层、目标区状态、库存和设备；条件支持时完成一次处理并复查目标区。'
        else:
            briefing_text = '任务：复核条件后，对已确认区域执行一次water-fertilizer recovery并复查。'
        build_management_l1_flow(self, SPEC, SPEC.actions[0], 'o_r1_fertigation_20_39', briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_potassium_deficit_dry_podfill_interaction r1_fertigation_20_39 L1')
