from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._heinong84_staggered_split_common import (
    CHECKPOINT_MID_PLANTING_READY,
    MID_END,
    MID_START,
    SOURCE_L3_SCENARIO_ID,
    apply_hn84_planting_blocks,
    checkpoint_sim_time,
    populate_hn84_staggered_apps,
    restore_hn84_staggered_checkpoint,
    sync_field_prep_complete,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hn84_staggered_mid_planting"


@register_scenario(SCENARIO_ID)
class ScenarioL1HN84StaggeredMidPlanting(Scenario):
    """L1 split: plant the action-ready mid zone."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_MID_PLANTING_READY)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_MID_PLANTING_READY
    source_checkpoint_date = "2026-05-14"
    source_dap = 7
    source_growth_stage = "EARLY_ZONE_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hn84_staggered_apps(self)
        restore_hn84_staggered_checkpoint(self, CHECKPOINT_MID_PLANTING_READY)
        sync_field_prep_complete(self.get_typed_app(TractorApp))

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "当前已到中播区21-42的可执行窗口，早播区0-20已播，晚播区43-63仍需等待。"
                "本L1只做中播区动作：复核天气、预报、土壤、种子和设备后，按HEINONG84、"
                "4.0 cm播深、7.9 cm株距播种21-42。"
            )
        else:
            briefing_text = "请复核条件后，只播种黑农84中播区21-42。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_mid_plant_weather_check"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_mid_plant_forecast_check"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_mid_plant_soil_check"
            ).depends_on(o_forecast, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_mid_plant_seed_inventory_check"
            ).depends_on(o_soil, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_tractor_before_mid_planting"
            ).depends_on(o_inventory, delay_seconds=1)
            plant_events, o_plant = apply_hn84_planting_blocks(
                tractor, o_tractor, "o_mid_zone", MID_START, MID_END
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_mid_zone_planting"
            ).depends_on(o_plant, delay_seconds=1)
            o_recheck = farm_world.get_ridge_range_state(MID_START, MID_END).oracle().with_id(
                "o_recheck_mid_zone_planted"
            ).depends_on(o_commit, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑农84中播区21-42播种。"
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
        return validate_native_workflow(self, env, "HN84 mid-zone planting L1 split")

