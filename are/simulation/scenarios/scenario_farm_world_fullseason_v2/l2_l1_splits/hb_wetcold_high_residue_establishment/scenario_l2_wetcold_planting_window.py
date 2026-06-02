from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_wetcold_high_residue_split_common import (
    BASE_FERTILIZER_KG,
    CHECKPOINT_INITIAL_BEFORE_FIELD_PREP,
    RIDGE_WIDTH_M,
    SOURCE_L3_SCENARIO_ID,
    apply_heinong84_planting_blocks,
    checkpoint_sim_time,
    populate_hb_wetcold_apps,
    restore_hb_wetcold_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_wetcold_high_residue_planting_window"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBWetcoldHighResiduePlantingWindow(Scenario):
    """L2 split: prep, wait for wet-cold residue seedbed, then plant."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_INITIAL_BEFORE_FIELD_PREP
    source_checkpoint_date = "2026-05-08"
    source_growth_stage = "NOT_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_wetcold_apps(self)
        restore_hb_wetcold_checkpoint(self, CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "湿冷春高残茬黑农84田块尚未播种。核心是找播种窗口："
                "先复核天气、预报、种床土壤和设备，完成整地/基肥/起垄；"
                "不要在湿冷种床上硬播，等待约3天后重新复核天气、预报、土温/墒情和播种机状态，"
                "条件满足再按4.0 cm播深、7.9 cm株距全田播种。"
            )
        else:
            briefing_text = "请完成湿冷高残茬田块播种窗口判断、等待复查、全田播种和播后复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather_initial = weather.get_current_weather().oracle().with_id(
                "o_initial_weather_check"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast_initial = weather.get_forecast(days=5).oracle().with_id(
                "o_initial_forecast_check"
            ).depends_on(o_weather_initial, delay_seconds=1)
            o_soil_initial = sensor.read_soil_sensors().oracle().with_id(
                "o_initial_seedbed_soil_check"
            ).depends_on(o_forecast_initial, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_seed_fertilizer_fuel"
            ).depends_on(o_soil_initial, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_check_tractor_before_prep"
            ).depends_on(o_inventory, delay_seconds=1)
            o_attach_grader = tractor.attach_implement("grader").oracle().with_id(
                "o_attach_grader"
            ).depends_on(o_tractor, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(
                o_attach_grader, delay_seconds=2
            )
            o_detach_grader = tractor.detach_implement().oracle().with_id(
                "o_detach_grader"
            ).depends_on(o_level, delay_seconds=1)
            o_load_base = tractor.load_fertilizer(BASE_FERTILIZER_KG).oracle().with_id(
                "o_load_base_fertilizer"
            ).depends_on(o_detach_grader, delay_seconds=1)
            o_base = tractor.base_fertilize().oracle().with_id(
                "o_apply_base_fertilizer"
            ).depends_on(o_load_base, delay_seconds=2)
            o_attach_furrower = tractor.attach_implement("furrower").oracle().with_id(
                "o_attach_furrower"
            ).depends_on(o_base, delay_seconds=1)
            o_ridge = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id(
                "o_form_1p1m_ridges"
            ).depends_on(o_attach_furrower, delay_seconds=2)
            o_detach_furrower = tractor.detach_implement().oracle().with_id(
                "o_detach_furrower"
            ).depends_on(o_ridge, delay_seconds=1)
            o_wait = system.advance_time(days=3).oracle().with_id(
                "o_wait_seedbed_warming_window"
            ).depends_on(o_detach_furrower, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_confirm_planting_weather_ready"
            ).depends_on(o_wait, delay_seconds=1)
            o_forecast_ready = weather.get_forecast(days=3).oracle().with_id(
                "o_confirm_planting_forecast_ready"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_soil_ready = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_seedbed_ready"
            ).depends_on(o_forecast_ready, delay_seconds=1)
            o_status_ready = tractor.get_status().oracle().with_id(
                "o_confirm_planter_ready"
            ).depends_on(o_soil_ready, delay_seconds=1)
            plant_events, o_after_planting = apply_heinong84_planting_blocks(
                tractor, o_status_ready, "o_whole_field_residue"
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_whole_field_planting"
            ).depends_on(o_after_planting, delay_seconds=1)
            o_recheck = farm_world.get_farm_overview().oracle().with_id(
                "o_recheck_planted_field"
            ).depends_on(o_commit, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成湿冷高残茬田块播种窗口等待、全田播种和播后复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather_initial,
            o_forecast_initial,
            o_soil_initial,
            o_inventory,
            o_tractor,
            o_attach_grader,
            o_level,
            o_detach_grader,
            o_load_base,
            o_base,
            o_attach_furrower,
            o_ridge,
            o_detach_furrower,
            o_wait,
            o_weather_ready,
            o_forecast_ready,
            o_soil_ready,
            o_status_ready,
            *plant_events,
            o_commit,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "wet-cold high-residue planting-window L2"
        )

