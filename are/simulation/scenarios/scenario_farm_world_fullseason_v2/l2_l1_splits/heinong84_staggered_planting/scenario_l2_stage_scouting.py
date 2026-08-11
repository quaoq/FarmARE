from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    RobotApp,
    SensorApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._heinong84_staggered_split_common import (
    CHECKPOINT_AFTER_LATE_PLANTING,
    EARLY_END,
    EARLY_START,
    LATE_END,
    LATE_START,
    MID_END,
    MID_START,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hn84_staggered_apps,
    restore_hn84_staggered_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hn84_staggered_stage_scouting"


@register_scenario(SCENARIO_ID)
class ScenarioL2HN84StaggeredStageScouting(Scenario):
    """L2 split: manage post-planting scouting across staggered zones."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_LATE_PLANTING)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_LATE_PLANTING
    source_checkpoint_date = "2026-05-23"
    source_dap = 16
    source_growth_stage = "ALL_ZONES_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hn84_staggered_apps(self)
        restore_hn84_staggered_checkpoint(self, CHECKPOINT_AFTER_LATE_PLANTING)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        farm_world = self.get_typed_app(FarmWorldApp)
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "三块黑农84错期播种区均已播完。这个L2是田间管理/巡查闭环："
                "先等待出苗期，再用全田概览、土壤、冠层、机器人和无人机确认早/中/晚三区出苗；"
                "随后等待到阶段分化明显时，再分别读取三区状态并做地面健康检查。"
                "只有观测证据支持压力时才应处理；当前任务重点是确认错期播种造成的阶段差异。"
            )
        else:
            briefing_text = "请完成黑农84错期播种后的出苗巡查和阶段分化巡查。"

        events = []
        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            current = briefing
            events.append(briefing)
            for day in range(1, 17):
                current = system.advance_time(days=1).oracle().with_id(
                    f"o_wait_whole_field_emergence_advance_day_{day:03d}"
                ).depends_on(current, delay_seconds=1)
                events.append(current)
            o_emergence_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_emergence_farm_overview"
            ).depends_on(current, delay_seconds=1)
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_emergence_soil_check"
            ).depends_on(o_emergence_overview, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_emergence_canopy_check"
            ).depends_on(o_emergence_soil, delay_seconds=1)
            o_robot_status_emergence = robot.check_status().oracle().with_id(
                "o_robot_status_before_emergence_check"
            ).depends_on(o_emergence_canopy, delay_seconds=1)
            o_early_emergence = robot.inspect_emergence(EARLY_START, min(EARLY_START + 7, EARLY_END)).oracle().with_id(
                "o_early_zone_emergence_ground_check"
            ).depends_on(o_robot_status_emergence, delay_seconds=2)
            o_mid_emergence = robot.inspect_emergence(MID_START, min(MID_START + 7, MID_END)).oracle().with_id(
                "o_mid_zone_emergence_ground_check"
            ).depends_on(o_early_emergence, delay_seconds=2)
            o_late_emergence = robot.inspect_emergence(LATE_START, min(LATE_START + 7, LATE_END)).oracle().with_id(
                "o_late_zone_emergence_ground_check"
            ).depends_on(o_mid_emergence, delay_seconds=2)
            o_ndvi = mavic.fly_survey(0, 63).oracle().with_id(
                "o_whole_field_emergence_ndvi"
            ).depends_on(o_late_emergence, delay_seconds=2)
            o_charge_mavic = mavic.charge().oracle().with_id(
                "o_charge_mavic_after_emergence_ndvi"
            ).depends_on(o_ndvi, delay_seconds=1)
            o_charge_robot = robot.charge().oracle().with_id(
                "o_charge_robot_after_emergence_check"
            ).depends_on(o_charge_mavic, delay_seconds=1)
            current = o_charge_robot
            for day in range(1, 43):
                current = system.advance_time(days=1).oracle().with_id(
                    f"o_wait_stage_split_scout_advance_day_{day:03d}"
                ).depends_on(current, delay_seconds=1)
                events.append(current)
            o_stage_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_stage_split_farm_overview"
            ).depends_on(current, delay_seconds=1)
            o_early_range = farm_world.get_ridge_range_state(EARLY_START, EARLY_END).oracle().with_id(
                "o_stage_split_early_range_state"
            ).depends_on(o_stage_overview, delay_seconds=1)
            o_mid_range = farm_world.get_ridge_range_state(MID_START, MID_END).oracle().with_id(
                "o_stage_split_mid_range_state"
            ).depends_on(o_early_range, delay_seconds=1)
            o_late_range = farm_world.get_ridge_range_state(LATE_START, LATE_END).oracle().with_id(
                "o_stage_split_late_range_state"
            ).depends_on(o_mid_range, delay_seconds=1)
            o_stage_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_stage_split_soil_check"
            ).depends_on(o_late_range, delay_seconds=1)
            o_stage_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_stage_split_canopy_check"
            ).depends_on(o_stage_soil, delay_seconds=1)
            o_stage_ndvi = mavic.fly_survey(0, 63).oracle().with_id(
                "o_stage_split_whole_field_ndvi"
            ).depends_on(o_stage_canopy, delay_seconds=2)
            o_robot_status_stage = robot.check_status().oracle().with_id(
                "o_robot_status_before_stage_split_health"
            ).depends_on(o_stage_ndvi, delay_seconds=1)
            o_stage_health_early = robot.inspect_crop_health(EARLY_START, min(EARLY_START + 7, EARLY_END)).oracle().with_id(
                "o_stage_split_early_health"
            ).depends_on(o_robot_status_stage, delay_seconds=2)
            o_stage_health_mid = robot.inspect_crop_health(MID_START, min(MID_START + 7, MID_END)).oracle().with_id(
                "o_stage_split_mid_health"
            ).depends_on(o_stage_health_early, delay_seconds=2)
            o_stage_health_late = robot.inspect_crop_health(LATE_START, min(LATE_START + 7, LATE_END)).oracle().with_id(
                "o_stage_split_late_health"
            ).depends_on(o_stage_health_mid, delay_seconds=2)
            o_charge_robot_stage = robot.charge().oracle().with_id(
                "o_charge_robot_after_stage_split_health"
            ).depends_on(o_stage_health_late, delay_seconds=1)
            o_charge_mavic_stage = mavic.charge().oracle().with_id(
                "o_charge_mavic_after_stage_split_ndvi"
            ).depends_on(o_charge_robot_stage, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑农84错期播种三区出苗和阶段分化巡查。"
            ).oracle().with_id("o_report").depends_on(o_charge_mavic_stage, delay_seconds=1)

        self.events = [
            *events,
            o_emergence_overview,
            o_emergence_soil,
            o_emergence_canopy,
            o_robot_status_emergence,
            o_early_emergence,
            o_mid_emergence,
            o_late_emergence,
            o_ndvi,
            o_charge_mavic,
            o_charge_robot,
            o_stage_overview,
            o_early_range,
            o_mid_range,
            o_late_range,
            o_stage_soil,
            o_stage_canopy,
            o_stage_ndvi,
            o_robot_status_stage,
            o_stage_health_early,
            o_stage_health_mid,
            o_stage_health_late,
            o_charge_robot_stage,
            o_charge_mavic_stage,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HN84 stage-scouting management L2 split")

