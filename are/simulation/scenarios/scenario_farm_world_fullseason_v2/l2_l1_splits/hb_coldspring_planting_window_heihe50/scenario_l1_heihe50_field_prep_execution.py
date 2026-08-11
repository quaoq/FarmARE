from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, TractorApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_coldspring_heihe50_split_common import (
    BASE_FERTILIZER_KG,
    CHECKPOINT_INITIAL_BEFORE_FIELD_PREP,
    RIDGE_WIDTH_M,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_coldspring_heihe50_apps,
    restore_hb_coldspring_heihe50_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_coldspring_heihe50_field_prep_execution"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBColdspringHeihe50FieldPrepExecution(Scenario):
    """L1 split: execute field prep, base fertilizer, and ridge forming."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_INITIAL_BEFORE_FIELD_PREP
    source_checkpoint_date = "2026-05-05"
    source_dap = 0
    source_growth_stage = "NOT_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_coldspring_heihe50_apps(self)
        restore_hb_coldspring_heihe50_checkpoint(
            self, CHECKPOINT_INITIAL_BEFORE_FIELD_PREP
        )

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "冷春黑河50田块处于播前作业开始状态。本L1只执行整地作业包："
                "平地、施基肥、起1.1 m垄，并复查设备状态。不要播种。"
            )
        else:
            briefing_text = "请执行黑河50播前整地、基肥和起垄；不要播种。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_attach_grader = tractor.attach_implement("grader").oracle().with_id(
                "o_attach_grader"
            ).depends_on(briefing, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(
                o_attach_grader, delay_seconds=2
            )
            o_detach_grader = tractor.detach_implement().oracle().with_id(
                "o_detach_grader"
            ).depends_on(o_level, delay_seconds=1)
            o_load_base = tractor.load_fertilizer(BASE_FERTILIZER_KG).oracle().with_id(
                "o_load_base_fertilizer"
            ).depends_on(o_detach_grader, delay_seconds=1)
            o_base = tractor.base_fertilize().oracle().with_id(
                "o_apply_base_fertilizer"
            ).depends_on(o_load_base, delay_seconds=2)
            o_attach_furrower = tractor.attach_implement("furrower").oracle().with_id(
                "o_attach_furrower"
            ).depends_on(o_base, delay_seconds=1)
            o_ridge = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id(
                "o_form_1p1m_ridges"
            ).depends_on(o_attach_furrower, delay_seconds=2)
            o_detach_furrower = tractor.detach_implement().oracle().with_id(
                "o_detach_furrower"
            ).depends_on(o_ridge, delay_seconds=1)
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_field_prep_physics"
            ).depends_on(o_detach_furrower, delay_seconds=1)
            o_recheck = tractor.get_status().oracle().with_id(
                "o_recheck_field_prep_status"
            ).depends_on(o_commit, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑河50播前整地作业包，未播种。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_attach_grader,
            o_level,
            o_detach_grader,
            o_load_base,
            o_base,
            o_attach_furrower,
            o_ridge,
            o_detach_furrower,
            o_commit,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HEIHE50 field-prep execution L1")
