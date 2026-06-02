from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._heinong84_staggered_split_common import (
    CHECKPOINT_AFTER_PREP,
    EARLY_END,
    EARLY_START,
    SOURCE_L3_SCENARIO_ID,
    apply_hn84_planting_blocks,
    checkpoint_sim_time,
    populate_hn84_staggered_apps,
    restore_hn84_staggered_checkpoint,
    sync_field_prep_complete,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hn84_staggered_early_planting"


@register_scenario(SCENARIO_ID)
class ScenarioL1HN84StaggeredEarlyPlanting(Scenario):
    """L1 split: plant the action-ready early zone."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_PREP)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_PREP
    source_checkpoint_date = "2026-05-06"
    source_dap = 0
    source_growth_stage = "NOT_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hn84_staggered_apps(self)
        restore_hn84_staggered_checkpoint(self, CHECKPOINT_AFTER_PREP)
        sync_field_prep_complete(self.get_typed_app(TractorApp))

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "黑农84田块已完成整地、基肥和1.1 m起垄，尚未播种。"
                "本L1只执行早播区0-20播种：先复核天气、预报、种床土壤、种子库存和播种机状态，"
                "条件合适后按HEINONG84、4.0 cm播深、7.9 cm株距播种0-20。"
            )
        else:
            briefing_text = "请复核条件后，只播种黑农84早播区0-20。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_early_plant_weather_check"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_early_plant_forecast_check"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_early_plant_soil_check"
            ).depends_on(o_forecast, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_early_plant_seed_inventory_check"
            ).depends_on(o_soil, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_tractor_before_early_planting"
            ).depends_on(o_inventory, delay_seconds=1)
            plant_events, o_plant = apply_hn84_planting_blocks(
                tractor, o_tractor, "o_early_zone", EARLY_START, EARLY_END
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_early_zone_planting"
            ).depends_on(o_plant, delay_seconds=1)
            o_recheck = farm_world.get_ridge_range_state(EARLY_START, EARLY_END).oracle().with_id(
                "o_recheck_early_zone_planted"
            ).depends_on(o_commit, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑农84早播区0-20播种。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_inventory,
            o_tractor,
            *plant_events,
            o_commit,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HN84 early-zone planting L1 split")

