from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    FarmWorldApp,
    FieldOpsApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._heinong84_staggered_split_common import (
    CHECKPOINT_LATE_PLANTING_READY,
    LATE_END,
    LATE_START,
    SOURCE_L3_SCENARIO_ID,
    apply_hn84_planting_blocks,
    checkpoint_sim_time,
    populate_hn84_staggered_apps,
    restore_hn84_staggered_checkpoint,
    sync_field_prep_complete,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hn84_staggered_late_seedbed_planting"


@register_scenario(SCENARIO_ID)
class ScenarioL1HN84StaggeredLateSeedbedPlanting(Scenario):
    """L1 split: irrigate the late seedbed and plant the action-ready late zone."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_LATE_PLANTING_READY)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_LATE_PLANTING_READY
    source_checkpoint_date = "2026-05-22"
    source_dap = 15
    source_growth_stage = "EARLY_AND_MID_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hn84_staggered_apps(self)
        restore_hn84_staggered_checkpoint(self, CHECKPOINT_LATE_PLANTING_READY)
        sync_field_prep_complete(self.get_typed_app(TractorApp))

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        field_ops = self.get_typed_app(FieldOpsApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "当前已到晚播区43-63的可执行窗口，早播和中播区已播。"
                "本L1只处理43-63：复核天气、预报和土壤，按证据做1.2小时种床灌溉，"
                "等待6小时入渗后复查土壤，再用HEINONG84、4.0 cm播深、7.9 cm株距播种43-63。"
            )
        else:
            briefing_text = "请复核晚播区43-63条件，种床灌溉、等待入渗后完成播种。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_late_plant_weather_check"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_late_plant_forecast_check"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_late_plant_soil_check"
            ).depends_on(o_forecast, delay_seconds=1)
            o_irrigate = field_ops.irrigate(LATE_START, LATE_END, hours=1.2).oracle().with_id(
                "o_late_zone_seedbed_irrigation"
            ).depends_on(o_soil, delay_seconds=1)
            o_wait = system.advance_time(hours=6).oracle().with_id(
                "o_wait_late_seedbed_infiltration"
            ).depends_on(o_irrigate, delay_seconds=1)
            o_soil_recheck = sensor.read_soil_sensors().oracle().with_id(
                "o_late_seedbed_soil_recheck"
            ).depends_on(o_wait, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_late_plant_seed_inventory_check"
            ).depends_on(o_soil_recheck, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_tractor_before_late_planting"
            ).depends_on(o_inventory, delay_seconds=1)
            plant_events, o_plant = apply_hn84_planting_blocks(
                tractor, o_tractor, "o_late_zone", LATE_START, LATE_END
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_late_zone_planting"
            ).depends_on(o_plant, delay_seconds=1)
            o_recheck = farm_world.get_ridge_range_state(LATE_START, LATE_END).oracle().with_id(
                "o_recheck_late_zone_planted"
            ).depends_on(o_commit, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑农84晚播区43-63种床灌溉和播种。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_irrigate,
            o_wait,
            o_soil_recheck,
            o_inventory,
            o_tractor,
            *plant_events,
            o_commit,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HN84 late seedbed planting L1 split")

