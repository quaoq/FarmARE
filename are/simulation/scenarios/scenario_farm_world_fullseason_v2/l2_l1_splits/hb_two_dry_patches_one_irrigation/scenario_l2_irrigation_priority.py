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
from .._harbin_split_event_builders import build_irrigation_l2_events

SCENARIO_ID = "scenario_l2_hb_two_dry_patches_irrigation_priority"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "after_r5_routine_check"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBTwoDryPatchesIrrigationPriority(Scenario):
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
        if self.detailed_briefing:
            briefing = (
                "截至2026-07-26，黑农84田进入R5附近，两处局部低持水斑块都可能缺水，但水量预算只够优先处理一处。"
                "请比较根区VWC、冠层热信号、生育阶段和目标/参考区状态；不要只凭低NDVI灌溉。"
                "等待并复查窗口后，只对证据支持且收益更高的缺水斑块灌溉，随后复查水分响应和预算。"
            )
        else:
            briefing = "请诊断两个旱斑的水分风险和水量预算，等待合适窗口后只灌溉优先级最高的确认区域。"
        self.events = build_irrigation_l2_events(
            self,
            briefing_text=briefing,
            report_text="已完成两旱斑水量优先级诊断、定向灌溉和复查。",
            start_ridge=42,
            end_ridge=53,
            reference_start=24,
            reference_end=35,
            hours=1.25,
            wait_days=2,
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "two dry patches irrigation L2 split")
