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
    build_harvest_l1_flow,
)

SOURCE_SLUG = 'hb_high_weed_seedbank_mechanical_only_baseline'
SPEC_SLUG = 'hb_high_weed_seedbank_early_control'
CHECKPOINT_LABEL = 'o_wait_harvest_day_040'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_weed_seedbank_harvest_ready'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbWeedSeedbankHarvestReady(Scenario):
    start_time: float | None = checkpoint_sim_time(SOURCE_SLUG, CHECKPOINT_LABEL)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SPEC.scenario_id
    source_checkpoint_label = CHECKPOINT_LABEL
    source_checkpoint_date = checkpoint_date(SOURCE_SLUG, CHECKPOINT_LABEL)

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        restore_batch_checkpoint(self, SPEC, SOURCE_SLUG, CHECKPOINT_LABEL)
        self.start_time = checkpoint_sim_time(SOURCE_SLUG, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        if self.detailed_briefing:
            briefing_text = '任务：承接成熟后已接近收获执行的状态。请复核当前天气、预报、成熟度、grain_moisture、通行性和仓储/烘干资源；条件满足时完成收获、卸粮，并按实际水分处理入库。'
        else:
            briefing_text = '任务：复核成熟、水分、天气、通行性和资源后，完成收获与入库处理。'
        build_harvest_l1_flow(self, SPEC, briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_high_weed_seedbank_mechanical_only_baseline harvest L1')
