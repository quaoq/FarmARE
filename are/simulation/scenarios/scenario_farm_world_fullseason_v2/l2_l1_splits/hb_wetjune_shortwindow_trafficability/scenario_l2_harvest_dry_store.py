from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_whole_harvest_l2_events

SCENARIO_ID = "scenario_l2_hb_wetjune_shortwindow_harvest_dry_store"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "o_wait_harvest_day_028"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBWetjuneShortwindowHarvestDryStore(Scenario):
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
            "全田已进入R8，但籽粒水分仍偏高。请持续复核天气、预报、土壤通行性、grain_moisture和仓储/烘干资源；"
            "等待到可收且可烘干窗口后按4垄一趟收获，中途卸粮，并先降到安全水分后入库。"
            if self.detailed_briefing
            else "请从R8开始等待合适水分和天气窗口，完成收获、卸粮、烘干和入库。"
        )
        self.events = build_whole_harvest_l2_events(
            self,
            briefing_text=briefing,
            report_text="已完成湿六月短窗口场景的收获、卸粮、烘干和入库。",
            wait_days=8,
            dry_after_harvest=True,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "wetjune shortwindow harvest L2 split")
