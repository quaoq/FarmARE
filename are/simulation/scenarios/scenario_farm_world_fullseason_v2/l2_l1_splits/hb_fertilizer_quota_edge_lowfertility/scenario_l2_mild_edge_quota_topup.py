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

from ._hb_fertilizer_quota_edge_lowfertility_split_common import (
    CHECKPOINT_AFTER_R1_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_fertilizer_quota_apps,
    restore_hb_fertilizer_quota_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_fertilizer_quota_mild_edge_topup"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBFertilizerQuotaMildEdgeTopup(Scenario):
    """L2 split: decide whether the remaining quota supports mild-edge top-up."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_R1_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_R1_ROUTINE
    source_checkpoint_date = "2026-06-23"
    source_dap = 50
    source_growth_stage = "V4_PLUS"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_fertilizer_quota_apps(self)
        restore_hb_fertilizer_quota_checkpoint(self, CHECKPOINT_AFTER_R1_ROUTINE)

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
                "截至2026-06-23，黑农84进入开花前后营养复核阶段，早期重度边缘已消耗一部分肥料配额。"
                "请重新复核天气、土壤、canopy、全田概览和剩余肥料配额，"
                "用无人机和地面检查判断轻度边缘是否仍有营养恢复价值，并与健康参照区对照。"
                "若证据支持且配额仍允许，等待合适窗口后只对轻度边缘做较小剂量水肥补充，"
                "并复查目标区和配额，避免把有限肥料扩展到健康区。"
            )
        else:
            briefing_text = "请在R1前后复核轻度边缘营养和剩余配额；证据支持时做小剂量定向水肥并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_topup_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_soil_context"
            ).depends_on(o_forecast, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_check_canopy_context"
            ).depends_on(o_soil, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_check_whole_field_overview"
            ).depends_on(o_canopy, delay_seconds=1)
            o_budget = farm_world.get_inventory().oracle().with_id(
                "o_check_remaining_fertilizer_quota"
            ).depends_on(o_overview, delay_seconds=1)
            o_wait = system.advance_time(days=4).oracle().with_id(
                "o_wait_to_topup_window"
            ).depends_on(o_budget, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_topup_weather"
            ).depends_on(o_wait, delay_seconds=1)
            o_survey = mavic.fly_survey(0, 63).oracle().with_id(
                "o_map_mild_edge_vigor"
            ).depends_on(o_weather_ready, delay_seconds=2)
            o_target = farm_world.get_ridge_range_state(8, 15).oracle().with_id(
                "o_read_mild_edge_state"
            ).depends_on(o_survey, delay_seconds=1)
            o_reference = farm_world.get_ridge_range_state(20, 31).oracle().with_id(
                "o_read_healthy_reference_state"
            ).depends_on(o_target, delay_seconds=1)
            o_ground = robot.inspect_crop_health(8, 15).oracle().with_id(
                "o_ground_confirm_mild_edge_nutrition"
            ).depends_on(o_reference, delay_seconds=2)
            o_state_ready = farm_world.get_ridge_range_state(8, 15).oracle().with_id(
                "o_recheck_mild_edge_before_topup"
            ).depends_on(o_ground, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_recheck_quota_before_topup"
            ).depends_on(o_state_ready, delay_seconds=1)
            o_fertigate = farm_world.apply_fertigation(8, 15, 0.16, 1.0).oracle().with_id(
                "o_apply_mild_edge_fertigation_8_15"
            ).depends_on(o_inventory, delay_seconds=2)
            o_recheck = farm_world.get_ridge_range_state(8, 15).oracle().with_id(
                "o_recheck_mild_edge_after_topup"
            ).depends_on(o_fertigate, delay_seconds=2)
            o_budget_after = farm_world.get_inventory().oracle().with_id(
                "o_recheck_quota_after_topup"
            ).depends_on(o_recheck, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成轻度边缘营养复核、小剂量水肥补充和配额复查。"
            ).oracle().with_id("o_report").depends_on(o_budget_after, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_canopy,
            o_overview,
            o_budget,
            o_wait,
            o_weather_ready,
            o_survey,
            o_target,
            o_reference,
            o_ground,
            o_state_ready,
            o_inventory,
            o_fertigate,
            o_recheck,
            o_budget_after,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "fertilizer quota mild-edge top-up L2 split"
        )
