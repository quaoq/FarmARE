from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    RobotApp,
    SensorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_coldspring_heihe50_split_common import (
    CHECKPOINT_AFTER_PLANTING,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_coldspring_heihe50_apps,
    restore_hb_coldspring_heihe50_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_coldspring_heihe50_cold_emergence_scouting"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBColdspringHeihe50ColdEmergenceScouting(Scenario):
    """L2 split: monitor post-plant cold/rain stand risk before intervention."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_PLANTING)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_PLANTING
    source_checkpoint_date = "2026-05-17"
    source_dap = 2
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_coldspring_heihe50_apps(self)
        restore_hb_coldspring_heihe50_checkpoint(self, CHECKPOINT_AFTER_PLANTING)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "黑河50已在冷春窗口完成播种，当前仍处于出苗前。"
                "管理重点是冷雨后建苗风险：不要把低NDVI或未出苗直接当成肥力、草害或病虫害。"
                "请先看天气/预报、土壤水分和全田状态；等待冷雨影响期后复查，"
                "再用无人机和地面机器人确认出苗/stand。若没有明确缺苗或其他胁迫证据，"
                "本窗口只记录并继续监测，不做补肥、喷药或补种。"
            )
        else:
            briefing_text = "请监测冷春播后出苗风险，等待后用传感器、无人机和地面检查确认stand，不要无证据处理。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_initial_postplant_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=4).oracle().with_id(
                "o_initial_postplant_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_initial_postplant_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_initial_postplant_overview"
            ).depends_on(o_soil, delay_seconds=1)
            o_wait_cold_rain = system.advance_time(days=4).oracle().with_id(
                "o_wait_through_cold_rain_risk"
            ).depends_on(o_overview, delay_seconds=1)
            o_weather_after = weather.get_current_weather().oracle().with_id(
                "o_weather_after_cold_rain_wait"
            ).depends_on(o_wait_cold_rain, delay_seconds=1)
            o_soil_after = sensor.read_soil_sensors().oracle().with_id(
                "o_soil_after_cold_rain_wait"
            ).depends_on(o_weather_after, delay_seconds=1)
            o_wait_emergence = system.advance_time(days=10).oracle().with_id(
                "o_wait_to_emergence_scouting_window"
            ).depends_on(o_soil_after, delay_seconds=1)
            o_emergence_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_emergence_window_overview"
            ).depends_on(o_wait_emergence, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_emergence_canopy_sensors"
            ).depends_on(o_emergence_overview, delay_seconds=1)
            o_mavic_charge = mavic.charge().oracle().with_id(
                "o_charge_mavic_before_emergence_survey"
            ).depends_on(o_canopy, delay_seconds=1)
            o_mavic_wait = system.advance_time(hours=1).oracle().with_id(
                "o_wait_mavic_charge"
            ).depends_on(o_mavic_charge, delay_seconds=1)
            o_survey = mavic.fly_survey(0, 63).oracle().with_id(
                "o_whole_field_emergence_ndvi_survey"
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
                "o_ground_emergence_stand_check"
            ).depends_on(o_robot_wait, delay_seconds=2)
            o_range = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_confirm_no_replant_action_needed"
            ).depends_on(o_ground, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑河50冷春播后出苗/stand复查；本窗口不做无证据处理。"
            ).oracle().with_id("o_report").depends_on(o_range, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_overview,
            o_wait_cold_rain,
            o_weather_after,
            o_soil_after,
            o_wait_emergence,
            o_emergence_overview,
            o_canopy,
            o_mavic_charge,
            o_mavic_wait,
            o_survey,
            o_robot_status,
            o_robot_charge,
            o_robot_wait,
            o_ground,
            o_range,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "HEIHE50 cold-spring emergence scouting L2"
        )
