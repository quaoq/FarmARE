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
    build_establishment_l2_flow,
)

SOURCE_SLUG = 'hb_lowcarbon_batch_operations_wetdisease'
SPEC_SLUG = 'hb_lowcarbon_batch_operations_wetdisease'
CHECKPOINT_LABEL = 'initial_before_field_prep'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_lowcarbon_wetdisease_establishment_planting'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbLowcarbonWetdiseaseEstablishmentPlanting(Scenario):
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
            briefing_text = '任务：在大垄密植、一垄两行模式下，从源L3的真实开局状态完成HEINONG84建植窗口。请检查天气、预报、土壤、库存和拖拉机，完成整地、基肥、起垄后，按可见种植计划播种并复查建植结果。'
        else:
            briefing_text = '任务：完成HEINONG84大豆田建植；先检查天气、土壤、库存和设备，再整地、施基肥、起垄、播种并复查。'
        build_establishment_l2_flow(self, SPEC, briefing_text)

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_lowcarbon_batch_operations_wetdisease establishment planting L2')
