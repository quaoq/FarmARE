from __future__ import annotations

from pathlib import Path

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario

from .._harbin_split_common import checkpoint_sim_time, populate_standard_split_apps, restore_split_checkpoint, validate_native_workflow
from .._harbin_split_event_builders import build_preplant_l2_events

SCENARIO_ID = "scenario_l2_hb_storage_capacity_standard_planting"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "initial_before_field_prep"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBStorageCapacityStandardPlanting(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_DIR, CHECKPOINT_LABEL)
    queue_based_loop = True
    time_increment_in_seconds = 60
    detailed_briefing = True
    expects_agent_harvest = False
    source_l3_scenario_id = "scenario_full_season_hb_storage_capacity_limit_batching"
    source_checkpoint_label = CHECKPOINT_LABEL

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_standard_split_apps(self)
        restore_split_checkpoint(self, CHECKPOINT_DIR, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        briefing = (
            "本L2从2026-05-05黑农84标准密度田开局开始。请检查天气、3天预报、土壤、库存和拖拉机状态；"
            "条件合适后完成整地、底肥、1.1 m起垄，并按HEINONG84、播深4.0 cm、seed_spacing_cm=7.9播种0-63垄。"
            if self.detailed_briefing
            else "请完成黑农84标准密度田播前检查、整地、底肥、起垄和全田播种。"
        )
        self.events = build_preplant_l2_events(
            self,
            briefing_text=briefing,
            report_text="已完成储藏容量场景的播前准备和全田播种。",
            prefix="o_storage_preplant",
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "storage capacity planting L2 split")
