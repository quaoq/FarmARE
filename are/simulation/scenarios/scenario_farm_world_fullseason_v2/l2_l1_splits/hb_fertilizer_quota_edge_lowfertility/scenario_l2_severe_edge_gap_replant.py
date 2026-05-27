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

from ._hb_fertilizer_quota_edge_lowfertility_split_common import (
    CHECKPOINT_AFTER_SEVERE_EDGE_FERTIGATION,
    SOURCE_L3_SCENARIO_ID,
    apply_heilong84_replant_block,
    checkpoint_sim_time,
    populate_hb_fertilizer_quota_apps,
    restore_hb_fertilizer_quota_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_fertilizer_quota_severe_edge_gap_replant"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBFertilizerQuotaSevereEdgeGapReplant(Scenario):
    """L2 split: separate stand-gap correction from nutrient recovery."""

    start_time: float | None = checkpoint_sim_time(
        CHECKPOINT_AFTER_SEVERE_EDGE_FERTIGATION
    )
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_SEVERE_EDGE_FERTIGATION
    source_checkpoint_date = "2026-05-24"
    source_dap = 20
    source_growth_stage = "VC"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_fertilizer_quota_apps(self)
        restore_hb_fertilizer_quota_checkpoint(
            self, CHECKPOINT_AFTER_SEVERE_EDGE_FERTIGATION
        )

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
                "截至2026-05-24，重度边缘已经完成一次水肥恢复，但边缘stand仍需单独复核。"
                "请等待合适补苗窗口，复核天气、土壤温度/水分、目标边缘stand、种子和机具状态；"
                "用无人机和地面出苗检查确认是否存在需要补苗的缺株区。"
                "若证据支持补苗，只对确认的重度边缘缺株小块执行补苗，并复查stand、种子和配额状态。"
            )
        else:
            briefing_text = "请在水肥后单独复核重度边缘stand；窗口合适且证据支持时执行局部补苗。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_replant_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_wait = system.advance_time(days=6).oracle().with_id(
                "o_wait_to_replant_window"
            ).depends_on(o_forecast, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_replant_weather"
            ).depends_on(o_wait, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_seedbed"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_recheck_canopy"
            ).depends_on(o_soil, delay_seconds=1)
            o_survey = mavic.fly_survey(0, 7).oracle().with_id(
                "o_map_severe_edge_stand_gaps"
            ).depends_on(o_canopy, delay_seconds=2)
            o_state = farm_world.get_ridge_range_state(0, 7).oracle().with_id(
                "o_read_severe_edge_stand"
            ).depends_on(o_survey, delay_seconds=1)
            o_ground = robot.inspect_emergence(0, 3).oracle().with_id(
                "o_ground_confirm_gap_replant_block"
            ).depends_on(o_state, delay_seconds=2)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_seed_and_quota_before_replant"
            ).depends_on(o_ground, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_check_planter_status"
            ).depends_on(o_inventory, delay_seconds=1)
            replant_events, o_after_replant = apply_heilong84_replant_block(
                tractor, o_status, "o_apply_gap_replant"
            )
            o_recheck = farm_world.get_ridge_range_state(0, 3).oracle().with_id(
                "o_recheck_replanted_block"
            ).depends_on(o_after_replant, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成重度边缘缺株复核、局部补苗和stand复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_wait,
            o_weather_ready,
            o_soil,
            o_canopy,
            o_survey,
            o_state,
            o_ground,
            o_inventory,
            o_status,
            *replant_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "fertilizer quota severe-edge gap replant L2 split"
        )
