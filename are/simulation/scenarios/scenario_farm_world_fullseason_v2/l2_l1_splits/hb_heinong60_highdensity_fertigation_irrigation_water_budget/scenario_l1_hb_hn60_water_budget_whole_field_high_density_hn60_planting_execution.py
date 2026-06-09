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
    build_planting_l1_flow,
)

SOURCE_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
SPEC_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
CHECKPOINT_LABEL = 'before_whole_field_high_density_hn60_planting_action'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l1_hb_hn60_water_budget_whole_field_high_density_hn60_planting_execution'


@register_scenario(SCENARIO_ID)
class ScenarioL1HbHn60WaterBudgetWholeFieldHighDensityHn60PlantingExecution(Scenario):
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
            briefing_text = '任务：承接已完成整地、基肥和起垄后的HEINONG60播种窗口。请复核当前天气、预报、土壤和播种机状态，按可见计划播种0-63垄并提交物理更新。'
        else:
            briefing_text = '任务：复核天气、土壤和设备后，按可见计划完成HEINONG60播种并复查。'
        build_planting_l1_flow(self, SPEC, 'whole_field_high_density_hn60', briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong60_highdensity_fertigation_irrigation_water_budget planting L1')
