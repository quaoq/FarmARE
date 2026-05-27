from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_replant_after_crusting_short_season_split_common import (
    CHECKPOINT_BEFORE_PLANTING,
    SOURCE_L3_SCENARIO_ID,
    apply_heinong84_planting_blocks,
    checkpoint_sim_time,
    populate_hb_replant_apps,
    restore_hb_replant_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_replant_heinong84_planting_execution"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBReplantHeinong84PlantingExecution(Scenario):
    """L1 split: execute HEINONG84 planting after field prep is complete."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_PLANTING)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_PLANTING
    source_checkpoint_date = "2026-05-05"
    source_dap = 0
    source_growth_stage = "NOT_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_replant_apps(self)
        restore_hb_replant_checkpoint(self, CHECKPOINT_BEFORE_PLANTING)
        tractor = self.get_typed_app(TractorApp)
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-05，黑农84全田播前整地、基肥和起垄已经完成，64条垄尚未播种。"
                "请做播种前最小复核：当前天气、三日预报、种床土壤、黑农84种子/燃油库存和播种设备状态。"
                "若条件合适，按4.0 cm播深和7.9 cm株距完成全田播种，并提交一次日物理复查，"
                "确认已播垄数、资源余量和早期出苗风险。"
            )
        else:
            briefing_text = "请完成黑农84全田播种前复核，条件合适时按标准密度播种并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_planting_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_confirm_planting_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_seedbed_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_confirm_seed_and_fuel"
            ).depends_on(o_soil, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_confirm_planter_status"
            ).depends_on(o_inventory, delay_seconds=1)
            plant_events, o_after_planting = apply_heinong84_planting_blocks(
                tractor, o_status, "o_whole_field_heinong84_standard_density"
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_whole_field_planting"
            ).depends_on(o_after_planting, delay_seconds=2)
            o_recheck = farm_world.get_farm_overview().oracle().with_id(
                "o_recheck_planted_field"
            ).depends_on(o_commit, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成黑农84标准密度播种执行和播后复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_inventory,
            o_status,
            *plant_events,
            o_commit,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 HEINONG84 planting execution split"
        )
