from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_storage_two_batch_l2_events

SCENARIO_ID = "scenario_l2_hb_storage_capacity_harvest_batching"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "o_wait_harvest_day_029"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBStorageCapacityHarvestBatching(Scenario):
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
            "全田已进入R8成熟期，但籽粒水分和处理容量仍需要复核。请检查天气、预报、成熟度、grain_moisture、"
            "田间可作业性、烘干能力和仓储容量；等待到批次收获窗口后，按西侧和东侧两个批次分别收获、卸粮、"
            "按水分干燥并入库，每批处理后复查容量，避免把收后处理拖到最后。"
            if self.detailed_briefing
            else "请从R8开始复核水分、天气和容量，按批次完成收获、烘干和入库。"
        )
        self.events = build_storage_two_batch_l2_events(
            self,
            briefing_text=briefing,
            report_text="已完成储藏容量场景的两批次收获、干燥和入库。",
            wait_days=9,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "storage capacity harvest batching L2 split")
