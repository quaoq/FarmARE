from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_harvest_l1_events

SCENARIO_ID = "scenario_l1_hb_storage_capacity_west_batch_harvest_store"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "o_wait_harvest_day_038"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBStorageCapacityWestBatchHarvestStore(Scenario):
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
            "第一批次已到可收窗口。请复核天气、预报、土壤通行性、0-31垄籽粒水分和仓储/烘干容量，"
            "按4垄一趟收获西侧批次，中途卸粮，先干燥到安全目标后入库，并复查容量。"
            if self.detailed_briefing
            else "请复核西侧批次条件后完成收获、卸粮、干燥和入库。"
        )
        self.events = build_harvest_l1_events(
            self,
            briefing_text=briefing,
            report_text="已完成西侧批次收获、干燥和入库。",
            start_ridge=0,
            end_ridge=31,
            id_prefix="o_west_0_31",
            dry_after_harvest=True,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "storage capacity west batch L1 split")
