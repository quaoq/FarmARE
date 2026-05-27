from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_fertilizer_quota_edge_lowfertility_split_common import (
    CHECKPOINT_BEFORE_SEVERE_EDGE_FERTIGATION,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_fertilizer_quota_apps,
    restore_hb_fertilizer_quota_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_fertilizer_quota_apply_severe_edge_fertigation"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBFertilizerQuotaApplySevereEdgeFertigation(Scenario):
    """L1 split: execute the confirmed severe-edge fertigation pass."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_SEVERE_EDGE_FERTIGATION)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_SEVERE_EDGE_FERTIGATION
    source_checkpoint_date = "2026-05-24"
    source_dap = 20
    source_growth_stage = "VC"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_fertilizer_quota_apps(self)
        restore_hb_fertilizer_quota_checkpoint(
            self, CHECKPOINT_BEFORE_SEVERE_EDGE_FERTIGATION
        )

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-24，重度边缘低肥力的地面确认已完成，当前任务是执行前复核。"
                "请复核天气、短期预报、土壤水分、目标边缘作物状态和肥料配额；"
                "若窗口和配额支持，只对重度边缘区域执行水肥恢复，随后复查目标区和剩余配额。"
            )
        else:
            briefing_text = "请完成重度边缘水肥作业前复核，窗口合适时执行定向水肥并复查配额。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_recheck_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_recheck_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 7).oracle().with_id(
                "o_recheck_severe_edge_state"
            ).depends_on(o_soil, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_fertilizer_quota"
            ).depends_on(o_state, delay_seconds=1)
            o_fertigate = farm_world.apply_fertigation(0, 7, 0.32, 1.2).oracle().with_id(
                "o_apply_severe_edge_fertigation_0_7"
            ).depends_on(o_inventory, delay_seconds=2)
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_fertilizer_quota_after_action"
            ).depends_on(o_fertigate, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成重度边缘定向水肥和配额复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_inventory,
            o_fertigate,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "fertilizer quota severe-edge fertigation L1 split"
        )
