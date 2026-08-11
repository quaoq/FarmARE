from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._heinong84_staggered_split_common import (
    CHECKPOINT_AFTER_EARLY_PLANTING,
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

SCENARIO_ID = "scenario_l2_hn84_staggered_mid_planting_window"


@register_scenario(SCENARIO_ID)
class ScenarioL2HN84StaggeredMidPlantingWindow(Scenario):
    """L2 split: find the mid-zone planting window and plant ridges 21-42."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_EARLY_PLANTING)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_EARLY_PLANTING
    source_checkpoint_date = "2026-05-07"
    source_dap = 0
    source_growth_stage = "EARLY_ZONE_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hn84_staggered_apps(self)
        restore_hn84_staggered_checkpoint(self, CHECKPOINT_AFTER_EARLY_PLANTING)
        sync_field_prep_complete(self.get_typed_app(TractorApp))

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "早播区0-20已按黑农84参数播完。现在需要寻找中播区21-42的合法播种窗口："
                "中播区最早2026-05-12以后才能播，不能立即提前播。等待期间到窗口后必须重新检查"
                "天气、预报、土壤墒情、种子和设备状态，然后按7.9 cm株距播种21-42。"
            )
        else:
            briefing_text = "请等待并确认中播区21-42窗口，条件合适后完成播种和复查。"

        events = []
        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            current = briefing
            events.append(briefing)
            for day in range(1, 8):
                current = system.advance_time(days=1).oracle().with_id(
                    f"o_wait_mid_planting_window_advance_day_{day:03d}"
                ).depends_on(current, delay_seconds=1)
                events.append(current)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_mid_plant_weather_check"
            ).depends_on(current, delay_seconds=1)
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
                content="已完成黑农84中播区21-42窗口确认、播种和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            *events,
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
        return validate_native_workflow(self, env, "HN84 mid-zone planting-window L2 split")

