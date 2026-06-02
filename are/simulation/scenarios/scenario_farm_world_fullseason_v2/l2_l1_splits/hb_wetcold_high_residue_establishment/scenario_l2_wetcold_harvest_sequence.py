from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_wetcold_high_residue_split_common import (
    CHECKPOINT_HARVEST_R8_START,
    SOURCE_L3_SCENARIO_ID,
    apply_harvest_store_range,
    checkpoint_sim_time,
    populate_hb_wetcold_apps,
    restore_hb_wetcold_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_wetcold_high_residue_harvest_sequence"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBWetcoldHighResidueHarvestSequence(Scenario):
    """L2 split: staged harvest for dry reference ridges and late replanted strip."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_R8_START)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_R8_START
    source_checkpoint_date = "2026-09-12"
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_wetcold_apps(self)
        restore_hb_wetcold_checkpoint(self, CHECKPOINT_HARVEST_R8_START)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "2026-09-12全田已R8，但补种区0-15仍比16-63湿。"
                "请不要全田一刀切收获。先复核天气、预报、土壤通行性、分区籽粒水分和仓储能力；"
                "对16-63执行harvest -> unload -> dry_grain(target_moisture_pct=13.0) -> store。"
                "随后等待并复查0-15，在合法窗口执行harvest -> unload -> dry_grain(target_moisture_pct=13.0) -> store。"
            )
        else:
            briefing_text = "请分批收获湿冷高残茬田：先16-63收后烘干入库，再等待0-15收后烘干入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_ready_16_63_harvest_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_ready_16_63_harvest_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_ready_16_63_harvest_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_ready_16_63_harvest_overview"
            ).depends_on(o_soil, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(16, 63).oracle().with_id(
                "o_ready_16_63_harvest_range_state"
            ).depends_on(o_overview, delay_seconds=1)
            first_harvest_events, o_first_store = apply_harvest_store_range(
                tractor,
                farm_world,
                o_state,
                "o_ready_16_63",
                start_ridge=16,
                end_ridge=63,
                dry_after_harvest=True,
            )
            o_wait_late = system.advance_time(days=21).oracle().with_id(
                "o_wait_replanted_0_15_harvest_zone"
            ).depends_on(o_first_store, delay_seconds=1)
            o_weather_late = weather.get_current_weather().oracle().with_id(
                "o_replanted_0_15_harvest_weather"
            ).depends_on(o_wait_late, delay_seconds=1)
            o_forecast_late = weather.get_forecast(days=3).oracle().with_id(
                "o_replanted_0_15_harvest_forecast"
            ).depends_on(o_weather_late, delay_seconds=1)
            o_soil_late = sensor.read_soil_sensors().oracle().with_id(
                "o_replanted_0_15_harvest_soil"
            ).depends_on(o_forecast_late, delay_seconds=1)
            o_overview_late = farm_world.get_farm_overview().oracle().with_id(
                "o_replanted_0_15_harvest_overview"
            ).depends_on(o_soil_late, delay_seconds=1)
            o_state_late = farm_world.get_ridge_range_state(0, 15).oracle().with_id(
                "o_replanted_0_15_harvest_range_state"
            ).depends_on(o_overview_late, delay_seconds=1)
            late_harvest_events, o_late_store = apply_harvest_store_range(
                tractor,
                farm_world,
                o_state_late,
                "o_replanted_0_15",
                start_ridge=0,
                end_ridge=15,
                dry_after_harvest=True,
            )
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_stored_grain"
            ).depends_on(o_late_store, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成湿冷高残茬田分批收获、卸粮、入库和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_overview,
            o_state,
            *first_harvest_events,
            o_wait_late,
            o_weather_late,
            o_forecast_late,
            o_soil_late,
            o_overview_late,
            o_state_late,
            *late_harvest_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "wet-cold high-residue staged harvest L2"
        )
