from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._heinong84_staggered_split_common import (
    CHECKPOINT_EARLY_HARVEST_READY,
    EARLY_END,
    EARLY_START,
    SOURCE_L3_SCENARIO_ID,
    apply_hn84_harvest_zone,
    checkpoint_sim_time,
    populate_hn84_staggered_apps,
    restore_hn84_staggered_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hn84_staggered_early_harvest_store"


@register_scenario(SCENARIO_ID)
class ScenarioL1HN84StaggeredEarlyHarvestStore(Scenario):
    """L1 split: harvest, dry, and store the action-ready early zone."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_EARLY_HARVEST_READY)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_EARLY_HARVEST_READY
    source_checkpoint_date = "2026-09-06"
    source_dap = 105
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hn84_staggered_apps(self)
        restore_hn84_staggered_checkpoint(self, CHECKPOINT_EARLY_HARVEST_READY)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "当前早播区0-20已经进入可收窗口。本L1只处理早播区：复核天气、预报、"
                "土壤通行性、早播区R8/harvest_allowed、籽粒水分和仓储/烘干能力；"
                "条件合适后执行 harvest -> unload -> dry(target 13.0%) -> store。"
            )
        else:
            briefing_text = "请复核早播区0-20收获条件，完成收获、卸粮、烘干和入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_early_harvest_weather_check"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_early_harvest_forecast_check"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_early_harvest_soil_check"
            ).depends_on(o_forecast, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_early_harvest_farm_overview"
            ).depends_on(o_soil, delay_seconds=1)
            o_range = farm_world.get_ridge_range_state(EARLY_START, EARLY_END).oracle().with_id(
                "o_early_harvest_range_state"
            ).depends_on(o_overview, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_storage_and_dryer_capacity"
            ).depends_on(o_range, delay_seconds=1)
            o_attach = tractor.attach_implement("harvester").oracle().with_id(
                "o_attach_harvester"
            ).depends_on(o_inventory, delay_seconds=1)
            harvest_events, o_done = apply_hn84_harvest_zone(
                tractor, farm_world, o_attach, "o_early_zone", EARLY_START, EARLY_END
            )
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_stored_grain"
            ).depends_on(o_done, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑农84早播区0-20收获、卸粮、烘干和入库。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_overview,
            o_range,
            o_inventory,
            o_attach,
            *harvest_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HN84 early-zone harvest/store L1 split")
