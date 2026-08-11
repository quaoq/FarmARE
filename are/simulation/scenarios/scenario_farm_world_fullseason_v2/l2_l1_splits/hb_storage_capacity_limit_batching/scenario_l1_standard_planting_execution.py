from __future__ import annotations

from pathlib import Path

from are.simulation.apps.farm_world import TractorApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_planting_l1_events

SCENARIO_ID = "scenario_l1_hb_storage_capacity_planting_execution"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "before_whole_field_planting_action"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBStorageCapacityPlantingExecution(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_DIR, CHECKPOINT_LABEL)
    queue_based_loop = True
    time_increment_in_seconds = 60
    detailed_briefing = True
    expects_agent_harvest = False
    source_l3_scenario_id = "scenario_full_season_hb_storage_capacity_limit_batching"
    source_checkpoint_label = CHECKPOINT_LABEL

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_standard_split_apps(self)
        restore_split_checkpoint(self, CHECKPOINT_DIR, CHECKPOINT_LABEL)
        tractor = self.get_typed_app(TractorApp)
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]

    def build_events_flow(self) -> None:
        briefing = (
            "整地、底肥和起垄已完成。请重新检查天气、预报、土壤和播种机状态，"
            "然后装载HEINONG84种子，按4垄一趟播种0-63垄，播深4.0 cm、seed_spacing_cm=7.9，并提交物理更新。"
            if self.detailed_briefing
            else "请复核播种条件后完成0-63垄HEINONG84标准密度播种。"
        )
        self.events = build_planting_l1_events(
            self,
            briefing_text=briefing,
            report_text="已完成储藏容量场景的全田播种执行。",
            prefix="o_storage_planting",
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "storage capacity planting L1 split")
