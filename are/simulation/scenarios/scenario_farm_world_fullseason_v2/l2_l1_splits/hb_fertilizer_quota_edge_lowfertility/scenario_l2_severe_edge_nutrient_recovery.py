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
    CHECKPOINT_AFTER_EMERGENCE_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_fertilizer_quota_apps,
    restore_hb_fertilizer_quota_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_fertilizer_quota_severe_edge_nutrient_recovery"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBFertilizerQuotaSevereEdgeNutrientRecovery(Scenario):
    """L2 split: diagnose severe edge nutrition and spend quota on the highest priority block."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_EMERGENCE_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_EMERGENCE_ROUTINE
    source_checkpoint_date = "2026-05-21"
    source_dap = 17
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_fertilizer_quota_apps(self)
        restore_hb_fertilizer_quota_checkpoint(
            self, CHECKPOINT_AFTER_EMERGENCE_ROUTINE
        )

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
                "截至2026-05-21，黑农84全田已播种，边缘低肥力历史已知，肥料配额有限。"
                "早期出苗巡查显示边缘区域长势和stand存在差异。"
                "请先复核天气、土壤、canopy和全田概览，再用无人机定位边缘弱势程度，"
                "用地面检查确认主要原因是否为低肥力而不是水分、草害或病虫害。"
                "若证据支持重度边缘低肥力，等待合适窗口后优先对最严重边缘做小水量水肥恢复，"
                "并复查目标区、健康参照区和肥料配额。"
            )
        else:
            briefing_text = "请诊断早期边缘弱苗；证据支持低肥力时优先对重度边缘做水肥恢复并复查配额。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_recovery_forecast"
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
            o_survey = mavic.fly_survey(0, 63).oracle().with_id(
                "o_map_edge_vigor"
            ).depends_on(o_overview, delay_seconds=2)
            o_target = farm_world.get_ridge_range_state(0, 7).oracle().with_id(
                "o_read_severe_edge_state"
            ).depends_on(o_survey, delay_seconds=1)
            o_reference = farm_world.get_ridge_range_state(20, 31).oracle().with_id(
                "o_read_healthy_reference_state"
            ).depends_on(o_target, delay_seconds=1)
            o_ground = robot.inspect_crop_health(0, 7).oracle().with_id(
                "o_ground_confirm_severe_edge_low_fertility"
            ).depends_on(o_reference, delay_seconds=2)
            o_wait = system.advance_time(days=3).oracle().with_id(
                "o_wait_to_fertigation_window"
            ).depends_on(o_ground, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_fertigation_weather"
            ).depends_on(o_wait, delay_seconds=1)
            o_state_ready = farm_world.get_ridge_range_state(0, 7).oracle().with_id(
                "o_recheck_severe_edge_before_fertigation"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_fertilizer_quota_before_recovery"
            ).depends_on(o_state_ready, delay_seconds=1)
            o_fertigate = farm_world.apply_fertigation(0, 7, 0.32, 1.2).oracle().with_id(
                "o_apply_severe_edge_fertigation_0_7"
            ).depends_on(o_inventory, delay_seconds=2)
            o_recheck = farm_world.get_ridge_range_state(0, 7).oracle().with_id(
                "o_recheck_severe_edge_after_fertigation"
            ).depends_on(o_fertigate, delay_seconds=2)
            o_budget = farm_world.get_inventory().oracle().with_id(
                "o_recheck_fertilizer_quota_after_recovery"
            ).depends_on(o_recheck, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成重度边缘低肥力诊断、水肥恢复和配额复查。"
            ).oracle().with_id("o_report").depends_on(o_budget, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_canopy,
            o_overview,
            o_survey,
            o_target,
            o_reference,
            o_ground,
            o_wait,
            o_weather_ready,
            o_state_ready,
            o_inventory,
            o_fertigate,
            o_recheck,
            o_budget,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "fertilizer quota severe-edge nutrient L2 split"
        )
