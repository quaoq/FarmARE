from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._heinong84_staggered_split_common import (
    CHECKPOINT_INITIAL_BEFORE_FIELD_PREP,
    EARLY_END,
    EARLY_START,
    RIDGE_WIDTH_M,
    SOURCE_L3_SCENARIO_ID,
    apply_hn84_planting_blocks,
    checkpoint_sim_time,
    populate_hn84_staggered_apps,
    restore_hn84_staggered_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hn84_staggered_field_prep_early_planting"


@register_scenario(SCENARIO_ID)
class ScenarioL2HN84StaggeredFieldPrepEarlyPlanting(Scenario):
    """L2 split: prep the field and plant the early Heinong84 zone."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_INITIAL_BEFORE_FIELD_PREP
    source_checkpoint_date = "2026-05-05"
    source_dap = 0
    source_growth_stage = "NOT_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hn84_staggered_apps(self)
        restore_hn84_staggered_checkpoint(self, CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "这是黑农84错期播种的开局窗口：全田64条垄分为早播0-20、"
                "中播21-42、晚播43-63。当前只允许完成播前整地、基肥、1.1 m起垄，"
                "并在天气、土壤和设备合适时播种早播区0-20；中播和晚播区不能提前播。"
                "播种使用HEINONG84、4.0 cm播深、7.9 cm株距。"
            )
        else:
            briefing_text = "请完成黑农84错期播种开局窗口：整地基肥起垄，并只播种早播区0-20。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather_0 = weather.get_current_weather().oracle().with_id(
                "o_weather_before_prep"
            ).depends_on(briefing, delay_seconds=2)
            o_forecast_0 = weather.get_forecast(days=5).oracle().with_id(
                "o_forecast_before_prep"
            ).depends_on(o_weather_0, delay_seconds=1)
            o_soil_0 = sensor.read_soil_sensors().oracle().with_id(
                "o_soil_before_prep"
            ).depends_on(o_forecast_0, delay_seconds=1)
            o_inventory_0 = farm_world.get_inventory().oracle().with_id(
                "o_inventory_before_prep"
            ).depends_on(o_soil_0, delay_seconds=1)
            o_tractor_0 = tractor.get_status().oracle().with_id(
                "o_tractor_before_prep"
            ).depends_on(o_inventory_0, delay_seconds=1)
            o_attach_grader = tractor.attach_implement("grader").oracle().with_id(
                "o_attach_grader"
            ).depends_on(o_tractor_0, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(
                o_attach_grader, delay_seconds=2
            )
            o_load_base = tractor.load_fertilizer(250.0).oracle().with_id(
                "o_load_base_fertilizer"
            ).depends_on(o_level, delay_seconds=1)
            o_base = tractor.base_fertilize().oracle().with_id(
                "o_apply_base_fertilizer"
            ).depends_on(o_load_base, delay_seconds=2)
            o_ridge = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id(
                "o_form_1p1m_ridges"
            ).depends_on(o_base, delay_seconds=2)
            o_commit_prep = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_prep_physics"
            ).depends_on(o_ridge, delay_seconds=1)
            o_early_weather = weather.get_current_weather().oracle().with_id(
                "o_early_plant_weather_check"
            ).depends_on(o_commit_prep, delay_seconds=1)
            o_early_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_early_plant_forecast_check"
            ).depends_on(o_early_weather, delay_seconds=1)
            o_early_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_early_plant_soil_check"
            ).depends_on(o_early_forecast, delay_seconds=1)
            o_early_tractor = tractor.get_status().oracle().with_id(
                "o_tractor_before_early_planting"
            ).depends_on(o_early_soil, delay_seconds=1)
            plant_events, o_early_plant = apply_hn84_planting_blocks(
                tractor,
                o_early_tractor,
                "o_early_zone",
                EARLY_START,
                EARLY_END,
            )
            o_commit_early = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_early_zone_planting"
            ).depends_on(o_early_plant, delay_seconds=1)
            o_recheck = farm_world.get_ridge_range_state(EARLY_START, EARLY_END).oracle().with_id(
                "o_recheck_early_zone_planted"
            ).depends_on(o_commit_early, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑农84早播区0-20的开局播种闭环。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather_0,
            o_forecast_0,
            o_soil_0,
            o_inventory_0,
            o_tractor_0,
            o_attach_grader,
            o_level,
            o_load_base,
            o_base,
            o_ridge,
            o_commit_prep,
            o_early_weather,
            o_early_forecast,
            o_early_soil,
            o_early_tractor,
            *plant_events,
            o_commit_early,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "HN84 field-prep plus early-zone planting L2 split"
        )

