from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_wetcold_high_residue_split_common import (
    CHECKPOINT_AFTER_EMERGENCE_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    apply_replant_blocks,
    checkpoint_sim_time,
    populate_hb_wetcold_apps,
    restore_hb_wetcold_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_wetcold_high_residue_replant_recovery"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBWetcoldHighResidueReplantRecovery(Scenario):
    """L2 split: diagnose slow-emergence strip and replant 0-15."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_EMERGENCE_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_EMERGENCE_ROUTINE
    source_checkpoint_date = "2026-05-27"
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_wetcold_apps(self)
        restore_hb_wetcold_checkpoint(self, CHECKPOINT_AFTER_EMERGENCE_ROUTINE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "湿冷高残茬田块已完成出苗期例行巡查，0-15 是慢出苗风险带，32-47 是参考区。"
                "本L2要形成闭环：不要直接补种；先复查天气/预报并等待目标补种窗口，"
                "随后用土壤、冠层、全田状态、无人机和地面机器人确认 0-15 的 stand 问题，"
                "排除把低冠层误判为肥力、草害或病害后，再对 0-15 补种并复查。"
            )
        else:
            briefing_text = "请诊断湿冷高残茬慢出苗区，等待窗口后确认证据并补种0-15。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_initial_replant_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_initial_replant_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_wait = system.advance_time(days=8).oracle().with_id(
                "o_wait_for_replant_action_window"
            ).depends_on(o_forecast, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_replant_window_soil"
            ).depends_on(o_wait, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_replant_window_canopy"
            ).depends_on(o_soil, delay_seconds=1)
            o_range = farm_world.get_ridge_range_state(0, 15).oracle().with_id(
                "o_replant_target_range_state"
            ).depends_on(o_canopy, delay_seconds=1)
            o_mavic_charge = mavic.charge().oracle().with_id(
                "o_charge_mavic_before_target_survey"
            ).depends_on(o_range, delay_seconds=1)
            o_mavic_wait = system.advance_time(hours=1).oracle().with_id(
                "o_wait_mavic_charge"
            ).depends_on(o_mavic_charge, delay_seconds=1)
            o_survey = mavic.fly_survey(0, 15).oracle().with_id(
                "o_target_ndvi_survey"
            ).depends_on(o_mavic_wait, delay_seconds=2)
            o_robot_status = robot.check_status().oracle().with_id(
                "o_robot_status_before_ground_check"
            ).depends_on(o_survey, delay_seconds=1)
            o_robot_charge = robot.charge().oracle().with_id(
                "o_charge_robot_before_ground_check"
            ).depends_on(o_robot_status, delay_seconds=1)
            o_robot_wait = system.advance_time(hours=1).oracle().with_id(
                "o_wait_robot_charge"
            ).depends_on(o_robot_charge, delay_seconds=1)
            o_ground = robot.inspect_emergence(0, 15).oracle().with_id(
                "o_ground_emergence_confirmation"
            ).depends_on(o_robot_wait, delay_seconds=2)
            replant_events, o_after_replant = apply_replant_blocks(
                tractor, o_ground, "o_replant_slow_emergence_0_15"
            )
            o_recheck = farm_world.get_ridge_range_state(0, 15).oracle().with_id(
                "o_recheck_replanted_range"
            ).depends_on(o_after_replant, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成湿冷高残茬慢出苗区0-15诊断、补种和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_wait,
            o_soil,
            o_canopy,
            o_range,
            o_mavic_charge,
            o_mavic_wait,
            o_survey,
            o_robot_status,
            o_robot_charge,
            o_robot_wait,
            o_ground,
            *replant_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "wet-cold high-residue replant recovery L2"
        )

