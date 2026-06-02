from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_fungicide_l2_events

SCENARIO_ID = "scenario_l2_hb_wetjune_shortwindow_fungicide_window"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "after_mid_routine_check"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBWetjuneShortwindowFungicideWindow(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_DIR, CHECKPOINT_LABEL)
    queue_based_loop = True
    time_increment_in_seconds = 60
    detailed_briefing = True
    expects_agent_harvest = False
    source_l3_scenario_id = "scenario_full_season_hb_wetjune_shortwindow_trafficability"
    source_checkpoint_label = CHECKPOINT_LABEL

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_standard_split_apps(self)
        restore_split_checkpoint(self, CHECKPOINT_DIR, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        briefing = (
            "截至2026-07-02，湿六月后冠层病害压力已升高，但土壤偏湿，喷药窗口很短。"
            "请先用天气、预报、土壤通行性、canopy、全田概览、无人机和地面检查确认病害而不是草虫水肥问题；"
            "等待并复查喷药窗口后，在天气、风速和进地条件允许时执行定向杀菌，并复查病害压力。"
            if self.detailed_briefing
            else "请诊断湿六月病害压力并寻找可喷窗口，证据和通行条件成立后定向杀菌。"
        )
        self.events = build_fungicide_l2_events(
            self,
            briefing_text=briefing,
            report_text="已完成短喷药窗口病害诊断、杀菌和复查。",
            start_ridge=22,
            end_ridge=45,
            reference_start=0,
            reference_end=15,
            liters_per_ridge=3.8,
            wait_days=4,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "wetjune shortwindow fungicide L2 split")
