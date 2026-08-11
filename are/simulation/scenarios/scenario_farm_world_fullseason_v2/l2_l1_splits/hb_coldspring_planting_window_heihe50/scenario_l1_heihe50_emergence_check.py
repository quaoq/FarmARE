from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, RobotApp, SensorApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_coldspring_heihe50_split_common import (
    CHECKPOINT_AFTER_EMERGENCE_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_coldspring_heihe50_apps,
    restore_hb_coldspring_heihe50_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_coldspring_heihe50_emergence_check"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBColdspringHeihe50EmergenceCheck(Scenario):
    """L1 split: confirm cold-spring emergence status and avoid unsupported action."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_EMERGENCE_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_EMERGENCE_ROUTINE
    source_checkpoint_date = "2026-05-31"
    source_dap = 16
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_coldspring_heihe50_apps(self)
        restore_hb_coldspring_heihe50_checkpoint(
            self, CHECKPOINT_AFTER_EMERGENCE_ROUTINE
        )

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        robot = self.get_typed_app(RobotApp, "Robot0")

        if self.detailed_briefing:
            briefing_text = (
                "2026-05-31冷春黑河50处于出苗复查窗口。本L1只做诊断确认："
                "读取土壤、冠层、全田和地面出苗检查，判断是否有明确缺苗/补种证据。"
                "没有明确证据时不要补肥、喷药或补种。"
            )
        else:
            briefing_text = "请确认冷春黑河50出苗/stand状态，没有证据时不要执行处理动作。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_emergence_soil"
            ).depends_on(briefing, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_confirm_emergence_canopy"
            ).depends_on(o_soil, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_confirm_emergence_overview"
            ).depends_on(o_canopy, delay_seconds=1)
            o_ground = robot.inspect_emergence(0, 15).oracle().with_id(
                "o_confirm_ground_emergence_sample"
            ).depends_on(o_overview, delay_seconds=2)
            o_range = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_confirm_no_single_action_needed"
            ).depends_on(o_ground, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑河50冷春出苗诊断确认，本L1未执行无证据处理。"
            ).oracle().with_id("o_report").depends_on(o_range, delay_seconds=1)

        self.events = [
            briefing,
            o_soil,
            o_canopy,
            o_overview,
            o_ground,
            o_range,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HEIHE50 emergence-check L1")
