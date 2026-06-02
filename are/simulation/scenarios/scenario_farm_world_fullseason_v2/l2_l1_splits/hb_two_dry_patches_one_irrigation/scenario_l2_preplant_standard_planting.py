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
from .._harbin_split_event_builders import build_preplant_l2_events

SCENARIO_ID = "scenario_l2_hb_two_dry_patches_standard_planting"
CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
CHECKPOINT_LABEL = "initial_before_field_prep"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBTwoDryPatchesStandardPlanting(Scenario):
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
                "本L2从2026-05-05开局的黑农84标准密度田开始。请先检查天气、3天预报、土壤温度/水分、"
                "库存、燃油和拖拉机状态；条件合适后完成整地、底肥、1.1 m起垄，并按HEINONG84标准密度"
                "在0-63垄按4垄一趟播种，播深4.0 cm、seed_spacing_cm=7.9，最后提交播种物理更新并复查。"
            )
        else:
            briefing = "请完成黑农84标准密度田的播前检查、整地、底肥、起垄和全田播种，并复查建植状态。"
        self.events = build_preplant_l2_events(
            self,
            briefing_text=briefing,
            report_text="已完成两旱斑场景的播前准备和全田标准密度播种。",
            prefix="o_two_dry_preplant",
        )

    def validate(self, env):
        return validate_native_workflow(self, env, "two dry patches planting L2 split")
