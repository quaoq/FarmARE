from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import (
    checkpoint_sim_time,
    populate_standard_split_apps,
    restore_split_checkpoint,
    validate_native_workflow,
)
from .._harbin_split_event_builders import build_whole_harvest_l2_events

SCENARIO_ID = "scenario_l2_hb_two_dry_patches_harvest_dry_store"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "o_wait_harvest_day_028"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBTwoDryPatchesHarvestDryStore(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_DIR, CHECKPOINT_LABEL)
    queue_based_loop = True
    time_increment_in_seconds = 60
    detailed_briefing = True
    expects_agent_harvest = True
    source_l3_scenario_id = "scenario_full_season_hb_two_dry_patches_one_irrigation"
    source_checkpoint_label = CHECKPOINT_LABEL

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_standard_split_apps(self)
        restore_split_checkpoint(self, CHECKPOINT_DIR, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        briefing = (
            "部分区域已进入R8但全田成熟和籽粒水分尚未完全同步。请持续检查各区域成熟度、grain_moisture、"
            "天气、3天预报、田间可作业性和仓储/烘干资源；等待全田达到可收窗口后按4垄一趟收获，"
            "中途及时卸粮，并按实际水分先降到安全目标后入库。"
            if self.detailed_briefing
            else "请从R8窗口开始复核成熟、水分、天气和资源，等待合适窗口后完成收获和安全入库。"
        )
        self.events = build_whole_harvest_l2_events(
            self,
            briefing_text=briefing,
            report_text="已完成两旱斑场景的收获、卸粮、烘干和入库。",
            wait_days=38,
            dry_after_harvest=True,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "two dry patches harvest L2 split")
