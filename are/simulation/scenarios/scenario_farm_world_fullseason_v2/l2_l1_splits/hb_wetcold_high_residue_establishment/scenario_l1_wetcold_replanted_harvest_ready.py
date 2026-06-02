from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_wetcold_high_residue_split_common import (
    CHECKPOINT_REPLANTED_HARVEST_READY,
    SOURCE_L3_SCENARIO_ID,
    apply_harvest_store_range,
    checkpoint_sim_time,
    populate_hb_wetcold_apps,
    restore_hb_wetcold_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_wetcold_high_residue_replanted_harvest_ready"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBWetcoldHighResidueReplantedHarvestReady(Scenario):
    """L1 split: harvest/store the late replanted 0-15 strip."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_REPLANTED_HARVEST_READY)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_REPLANTED_HARVEST_READY
    source_checkpoint_date = "2026-10-03"
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_wetcold_apps(self)
        restore_hb_wetcold_checkpoint(self, CHECKPOINT_REPLANTED_HARVEST_READY)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "本L1从2026-10-03补种区0-15收获前 checkpoint 开始。"
                "16-63 已经先收并入库；请复核天气、预报、土壤通行性、0-15籽粒水分、"
                "仓储能力，然后执行0-15 harvest -> unload -> dry_grain(target_moisture_pct=13.0) -> store。"
            )
        else:
            briefing_text = "请复核0-15补种区收获窗口，完成收获、卸粮和入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_replanted_0_15_harvest_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_replanted_0_15_harvest_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_replanted_0_15_harvest_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 15).oracle().with_id(
                "o_replanted_0_15_harvest_range_state"
            ).depends_on(o_soil, delay_seconds=1)
            o_capacity = farm_world.get_inventory().oracle().with_id(
                "o_replanted_0_15_capacity_check"
            ).depends_on(o_state, delay_seconds=1)
            harvest_events, o_store = apply_harvest_store_range(
                tractor,
                farm_world,
                o_capacity,
                "o_replanted_0_15",
                start_ridge=0,
                end_ridge=15,
                dry_after_harvest=True,
            )
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_stored_grain"
            ).depends_on(o_store, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成0-15补种区收获和入库。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_capacity,
            *harvest_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "wet-cold high-residue replanted harvest L1"
        )
