from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_coldspring_heihe50_split_common import (
    CHECKPOINT_HARVEST_R8_START,
    SOURCE_L3_SCENARIO_ID,
    apply_heihe50_harvest_direct_store,
    checkpoint_sim_time,
    populate_hb_coldspring_heihe50_apps,
    restore_hb_coldspring_heihe50_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_coldspring_heihe50_harvest_drydown_store"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBColdspringHeihe50HarvestDrydownStore(Scenario):
    """L2 split: start at R8 and find the direct-storage harvest window."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_R8_START)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_R8_START
    source_checkpoint_date = "2026-08-21"
    source_dap = 98
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_coldspring_heihe50_apps(self)
        restore_hb_coldspring_heihe50_checkpoint(self, CHECKPOINT_HARVEST_R8_START)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "2026-08-21黑河50全田已进入R8成熟期，但籽粒水分仍偏高，不能只因R8就立即收。"
                "请从R8起点开始找窗口：持续复核天气、预报、土壤通行性、全田籽粒水分和仓储能力。"
                "雨后或水分高时等待自然降水分；当水分降到安全直接入库水平且天气/通行性合适时，"
                "按harvest -> unload -> store执行。低于或等于13.5%不需要烘干，也不要过度等到8%-10%。"
            )
        else:
            briefing_text = "请从R8开始等待并复查水分/天气/通行性，找到安全直接入库窗口后收获、卸粮、入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_initial_harvest_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_initial_harvest_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_initial_harvest_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_initial_r8_moisture_state"
            ).depends_on(o_soil, delay_seconds=1)
            o_wait_1 = system.advance_time(days=5).oracle().with_id(
                "o_wait_first_drydown_interval"
            ).depends_on(o_state, delay_seconds=1)
            o_state_1 = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_moisture_after_first_wait"
            ).depends_on(o_wait_1, delay_seconds=1)
            o_wait_2 = system.advance_time(days=5).oracle().with_id(
                "o_wait_second_drydown_interval"
            ).depends_on(o_state_1, delay_seconds=1)
            o_weather_2 = weather.get_current_weather().oracle().with_id(
                "o_recheck_weather_after_second_wait"
            ).depends_on(o_wait_2, delay_seconds=1)
            o_state_2 = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_moisture_after_second_wait"
            ).depends_on(o_weather_2, delay_seconds=1)
            o_wait_ready = system.advance_time(days=6).oracle().with_id(
                "o_wait_to_direct_store_window"
            ).depends_on(o_state_2, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_confirm_direct_store_weather"
            ).depends_on(o_wait_ready, delay_seconds=1)
            o_forecast_ready = weather.get_forecast(days=3).oracle().with_id(
                "o_confirm_direct_store_forecast"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_soil_ready = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_direct_store_soil"
            ).depends_on(o_forecast_ready, delay_seconds=1)
            o_state_ready = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_confirm_direct_store_moisture"
            ).depends_on(o_soil_ready, delay_seconds=1)
            o_capacity = farm_world.get_inventory().oracle().with_id(
                "o_confirm_storage_capacity"
            ).depends_on(o_state_ready, delay_seconds=1)
            harvest_events, o_store = apply_heihe50_harvest_direct_store(
                tractor, farm_world, o_capacity, "o_whole_field_heihe50"
            )
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_stored_grain"
            ).depends_on(o_store, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑河50R8后干燥等待、全田收获、卸粮和直接入库。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_wait_1,
            o_state_1,
            o_wait_2,
            o_weather_2,
            o_state_2,
            o_wait_ready,
            o_weather_ready,
            o_forecast_ready,
            o_soil_ready,
            o_state_ready,
            o_capacity,
            *harvest_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "HEIHE50 R8 harvest drydown direct-store L2"
        )
