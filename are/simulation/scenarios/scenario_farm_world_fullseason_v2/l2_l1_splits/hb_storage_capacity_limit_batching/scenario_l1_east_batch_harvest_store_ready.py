from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_harvest_l1_events

SCENARIO_ID = "scenario_l1_hb_storage_capacity_east_batch_harvest_store_ready"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "after_west_0_31_harvest"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBStorageCapacityEastBatchHarvestStoreReady(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_DIR, CHECKPOINT_LABEL)
    queue_based_loop = True
    time_increment_in_seconds = 60
    detailed_briefing = True
    expects_agent_harvest = True
    source_l3_scenario_id = "scenario_full_season_hb_storage_capacity_limit_batching"
    source_checkpoint_label = CHECKPOINT_LABEL

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_standard_split_apps(self)
        restore_split_checkpoint(self, CHECKPOINT_DIR, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        briefing = (
            "西侧批次已入库，东侧批次处于行动就绪状态。请最后复核容量、天气、土壤和32-63垄水分，"
            "然后完成东侧批次收获、卸粮、干燥到安全目标并入库。"
            if self.detailed_briefing
            else "请复核东侧批次条件后完成收获、卸粮、干燥和入库。"
        )
        self.events = build_harvest_l1_events(
            self,
            briefing_text=briefing,
            report_text="已完成东侧行动就绪批次收获和入库。",
            start_ridge=32,
            end_ridge=63,
            id_prefix="o_east_32_63",
            dry_after_harvest=True,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "storage capacity east batch L1 split")
