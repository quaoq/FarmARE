from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_replant_after_crusting_short_season_split_common import (
    CHECKPOINT_BEFORE_REPLANT,
    SOURCE_L3_SCENARIO_ID,
    apply_replant_blocks,
    checkpoint_sim_time,
    populate_hb_replant_apps,
    restore_hb_replant_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_replant_crusting_action_ready"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBReplantCrustingActionReady(Scenario):
    """L1 split: perform action-ready targeted replanting after confirmation."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_REPLANT)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_REPLANT
    source_checkpoint_date = "2026-05-25"
    source_dap = 21
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_replant_apps(self)
        restore_hb_replant_checkpoint(self, CHECKPOINT_BEFORE_REPLANT)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-25，播后板结和局部出苗不足已经完成地面确认。"
                "请做补种前最小复核：当前天气、短期预报、种床土壤、目标区域stand状态、"
                "黑农84种子库存和播种设备状态。若条件合适，只对确认的局部缺苗区域补种，"
                "播深4.0 cm、株距7.9 cm，并复查补种记录和资源余量。"
            )
        else:
            briefing_text = "请完成局部补种前复核，条件合适时执行黑农84补种并复查资源和状态。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_replant_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_confirm_replant_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_replant_seedbed"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 11).oracle().with_id(
                "o_confirm_replant_range_state"
            ).depends_on(o_soil, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_confirm_seed_stock"
            ).depends_on(o_state, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_confirm_replant_equipment"
            ).depends_on(o_inventory, delay_seconds=1)
            replant_events, o_after_replant = apply_replant_blocks(
                tractor, o_tractor, "o_action_ready_crusting"
            )
            o_recheck = farm_world.get_ridge_range_state(0, 11).oracle().with_id(
                "o_recheck_action_ready_replant"
            ).depends_on(o_after_replant, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成局部补种执行和补种后复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_inventory,
            o_tractor,
            *replant_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 action-ready crusting replant split"
        )
