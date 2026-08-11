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
from .._harbin_split_event_builders import build_irrigation_l1_events

SCENARIO_ID = "scenario_l1_hb_two_dry_patches_irrigate_priority_patch"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "before_o_r5_irrigation_0_42_53_action"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBTwoDryPatchesIrrigatePriorityPatch(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_DIR, CHECKPOINT_LABEL)
    queue_based_loop = True
    time_increment_in_seconds = 60
    detailed_briefing = True
    expects_agent_harvest = False
    source_l3_scenario_id = "scenario_full_season_hb_two_dry_patches_one_irrigation"
    source_checkpoint_label = CHECKPOINT_LABEL

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_standard_split_apps(self)
        restore_split_checkpoint(self, CHECKPOINT_DIR, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        briefing = (
            "水分诊断和优先级比较已完成。请重新检查天气、土壤、目标区状态和水量预算，"
            "对确认的优先缺水斑块实施一次定向灌溉，等待入渗后复查水分响应。"
            if self.detailed_briefing
            else "请复核目标区和水量预算后执行一次定向灌溉并复查。"
        )
        self.events = build_irrigation_l1_events(
            self,
            briefing_text=briefing,
            report_text="已完成优先旱斑定向灌溉。",
            start_ridge=42,
            end_ridge=53,
            hours=1.25,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "two dry patches irrigation L1 split")
