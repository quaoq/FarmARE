from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_fungicide_l1_events

SCENARIO_ID = "scenario_l1_hb_wetjune_shortwindow_apply_fungicide"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "before_o_mid_fungicide_0_22_45_action"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBWetjuneShortwindowApplyFungicide(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_DIR, CHECKPOINT_LABEL)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False
    source_l3_scenario_id = "scenario_full_season_hb_wetjune_shortwindow_trafficability"
    source_checkpoint_label = CHECKPOINT_LABEL

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_standard_split_apps(self)
        restore_split_checkpoint(self, CHECKPOINT_DIR, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        briefing = (
            "任务：承接湿六月短喷施窗口下的杀菌剂 action-ready 状态。"
            "病害诊断、等待窗口和目标区定位已经完成；当前重点是确认窗口仍可作业，并在窗口关闭前完成闭环处理。\n"
            "请按以下步骤操作：\n"
            "1. 查看当前天气，确认无雨且风速适合喷药。\n"
            "2. 读取土壤传感器，确认喷雾设备可以下地通行。\n"
            "3. 查看已确认病害目标区状态，确认病害压力仍需要处理。\n"
            "4. 检查喷雾设备状态并装载约92.3 L杀菌剂。\n"
            "5. 对已确认目标区分块完成一次定向杀菌。\n"
            "6. 喷药后复查目标区状态，并向我报告已完成天气/通行性复核、定向杀菌和复查。"
            if self.detailed_briefing
            else "请复核喷药窗口、通行性和目标区病害状态，完成一次定向杀菌并复查。"
        )
        self.events = build_fungicide_l1_events(
            self,
            briefing_text=briefing,
            report_text="已完成短喷施窗口下的条件复核、定向杀菌和处理后复查。",
            start_ridge=22,
            end_ridge=45,
            liters_per_ridge=3.8,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "wetjune shortwindow fungicide L1 split")
