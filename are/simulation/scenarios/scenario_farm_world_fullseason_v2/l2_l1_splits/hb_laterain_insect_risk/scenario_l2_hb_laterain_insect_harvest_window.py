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
    build_harvest_l2_flow,
)

SOURCE_SLUG = 'hb_laterain_insect_risk'
SPEC_SLUG = 'hb_laterain_insect_risk'
CHECKPOINT_LABEL = 'o_wait_harvest_day_001'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_laterain_insect_harvest_window'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbLaterainInsectHarvestWindow(Scenario):
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
            briefing_text = '任务：从R8后收获窗口起点出发，持续检查成熟度、籽粒水分、天气、预报、土壤通行性和仓储/烘干资源；在条件满足后完成收获、卸粮，并按实际水分和资源状态处理入库。'
        else:
            briefing_text = '任务：从成熟后窗口开始判断收获时机；检查籽粒水分、天气、通行性和资源，条件满足后完成收获和入库处理。'
        build_harvest_l2_flow(self, SPEC, 40, briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_laterain_insect_risk harvest L2')
