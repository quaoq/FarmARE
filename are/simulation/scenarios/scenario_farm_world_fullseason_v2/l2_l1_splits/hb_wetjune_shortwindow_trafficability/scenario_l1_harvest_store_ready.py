from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_harvest_l1_events

SCENARIO_ID = "scenario_l1_hb_wetjune_shortwindow_harvest_store_ready"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "o_wait_harvest_day_036"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBWetjuneShortwindowHarvestStoreReady(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_DIR, CHECKPOINT_LABEL)
    queue_based_loop = True
    time_increment_in_seconds = 60
    detailed_briefing = True
    expects_agent_harvest = True
    source_l3_scenario_id = "scenario_full_season_hb_wetjune_shortwindow_trafficability"
    source_checkpoint_label = CHECKPOINT_LABEL

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_standard_split_apps(self)
        restore_split_checkpoint(self, CHECKPOINT_DIR, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        briefing = (
            "全田已达到可收且需要按水分安全处理的窗口。请最后复核天气、预报、土壤、grain_moisture和资源，"
            "然后按4垄一趟收获0-63垄，中途卸粮，先烘干到安全目标后入库。"
            if self.detailed_briefing
            else "请复核收获条件后完成全田收获、卸粮和安全入库。"
        )
        self.events = build_harvest_l1_events(
            self,
            briefing_text=briefing,
            report_text="已完成湿六月短窗口场景的全田收获和安全入库。",
            start_ridge=0,
            end_ridge=63,
            id_prefix="o_whole_field",
            dry_after_harvest=True,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "wetjune shortwindow harvest L1 split")
