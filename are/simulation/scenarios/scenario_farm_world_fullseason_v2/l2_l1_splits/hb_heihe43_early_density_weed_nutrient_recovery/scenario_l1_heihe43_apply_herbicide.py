from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_heihe43_split_common import (
    CHECKPOINT_BEFORE_HERBICIDE,
    SOURCE_L3_SCENARIO_ID,
    apply_heihe43_herbicide_blocks,
    cst_timestamp,
    populate_hb_heihe43_apps,
    restore_hb_heihe43_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_heihe43_apply_targeted_herbicide"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBHeihe43ApplyTargetedHerbicide(Scenario):
    """L1 split: apply targeted herbicide to the confirmed weed-competition block."""

    start_time: float | None = cst_timestamp(2026, 6, 2, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_HERBICIDE
    source_checkpoint_date = "2026-06-02"
    source_dap = 29
    source_growth_stage = "V2"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_heihe43_apps(self)
        restore_hb_heihe43_checkpoint(self, CHECKPOINT_BEFORE_HERBICIDE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-06-02，黑河43 V2期的地面检查已确认一处早期草害竞争区域。"
                "请在施药前复核天气、三日预报、土壤通行性、目标区草害状态、参照区状态、药剂和喷雾设备。"
                "若喷施窗口合适，只对确认的草害竞争区域定向除草，不要扩展到参考区或营养恢复区；"
                "完成后复查目标区和投入品使用。"
            )
        else:
            briefing_text = "请复核喷施窗口、目标草害区、参照区、药剂和设备，然后完成一次定向除草和复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_herbicide_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_confirm_herbicide_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_soil_trafficability"
            ).depends_on(o_forecast, delay_seconds=1)
            o_target = farm_world.get_ridge_range_state(44, 55).oracle().with_id(
                "o_confirm_weed_competition_block"
            ).depends_on(o_soil, delay_seconds=1)
            o_reference = farm_world.get_ridge_range_state(24, 35).oracle().with_id(
                "o_confirm_reference_block"
            ).depends_on(o_target, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_confirm_herbicide_inventory"
            ).depends_on(o_reference, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_confirm_sprayer_status"
            ).depends_on(o_inventory, delay_seconds=1)
            o_load = tractor.load_pesticide(29.4).oracle().with_id(
                "o_load_herbicide_volume"
            ).depends_on(o_status, delay_seconds=1)
            spray_events, o_after_spray = apply_heihe43_herbicide_blocks(
                tractor, o_load, "o_apply_targeted_herbicide"
            )
            o_recheck = farm_world.get_ridge_range_state(44, 55).oracle().with_id(
                "o_recheck_weed_block_after_herbicide"
            ).depends_on(o_after_spray, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成草害竞争区域定向除草和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_target,
            o_reference,
            o_inventory,
            o_status,
            o_load,
            *spray_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "HEIHE43 targeted herbicide L1 split"
        )

