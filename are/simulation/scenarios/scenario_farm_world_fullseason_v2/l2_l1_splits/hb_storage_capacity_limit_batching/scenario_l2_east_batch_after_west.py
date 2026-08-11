from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_harvest_l1_events

SCENARIO_ID = "scenario_l2_hb_storage_capacity_east_batch_after_west"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "after_west_0_31_harvest"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBStorageCapacityEastBatchAfterWest(Scenario):
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
            "西侧批次已经完成收获、干燥和入库。请复查剩余容量、东侧32-63垄成熟度和籽粒水分、天气和通行性；"
            "若容量与水分条件允许，继续完成东侧批次收获、卸粮、干燥和入库，并最终复查库存。"
            if self.detailed_briefing
            else "请在西侧批次入库后复核容量和东侧状态，完成东侧批次后处理闭环。"
        )
        self.events = build_harvest_l1_events(
            self,
            briefing_text=briefing,
            report_text="已完成东侧批次收获、干燥和入库。",
            start_ridge=32,
            end_ridge=63,
            id_prefix="o_east_32_63",
            dry_after_harvest=True,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "storage capacity east batch L2 split")
