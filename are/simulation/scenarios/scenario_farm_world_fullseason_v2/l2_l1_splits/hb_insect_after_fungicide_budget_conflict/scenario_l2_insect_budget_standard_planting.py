from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_insect_after_fungicide_budget_conflict_split_common import (
    BASE_FERTILIZER_KG,
    CHECKPOINT_INITIAL_BEFORE_FIELD_PREP,
    PLANT_DEPTH_CM,
    RIDGE_WIDTH_M,
    SEED_SPACING_CM,
    SEED_TYPE,
    SOURCE_L3_SCENARIO_ID,
    apply_hb_insect_budget_planting_blocks,
    checkpoint_sim_time,
    populate_hb_insect_budget_apps,
    restore_hb_insect_budget_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_insect_budget_standard_planting"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBInsectBudgetStandardPlanting(Scenario):
    """L2 split: standard HEINONG84 establishment for the budget-conflict L3."""

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
        populate_hb_insect_budget_apps(self)
        restore_hb_insect_budget_checkpoint(self, CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-05，黑农84标准密度田块尚未播种，后续存在先病害后虫害的喷药预算冲突风险。"
                "请先复核当前天气、3天预报、种床土壤、库存和拖拉机状态；条件合适后完成整地、"
                f"{BASE_FERTILIZER_KG:.0f} kg基肥、{RIDGE_WIDTH_M:.1f} m起垄，再装载{SEED_TYPE}种子，"
                f"按{PLANT_DEPTH_CM:.1f} cm播深、seed_spacing_cm={SEED_SPACING_CM:.1f}全田播种0-63垄。"
                "播后提交物理更新并复查全田概览、库存和已播状态。"
            )
        else:
            briefing_text = (
                "请完成黑农84标准密度田的播前检查、整地、基肥、起垄、全田播种和播后复查。"
            )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_planting_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_seedbed_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_seed_fertilizer_fuel"
            ).depends_on(o_soil, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_check_tractor_before_field_prep"
            ).depends_on(o_inventory, delay_seconds=1)
            o_attach_grader = tractor.attach_implement("grader").oracle().with_id(
                "o_attach_grader"
            ).depends_on(o_status, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(
                o_attach_grader, delay_seconds=2
            )
            o_detach_grader = tractor.detach_implement().oracle().with_id(
                "o_detach_grader"
            ).depends_on(o_level, delay_seconds=1)
            o_load_fertilizer = tractor.load_fertilizer(BASE_FERTILIZER_KG).oracle().with_id(
                "o_load_base_fertilizer"
            ).depends_on(o_detach_grader, delay_seconds=1)
            o_base_fertilize = tractor.base_fertilize().oracle().with_id(
                "o_apply_base_fertilizer"
            ).depends_on(o_load_fertilizer, delay_seconds=2)
            o_attach_furrower = tractor.attach_implement("furrower").oracle().with_id(
                "o_attach_furrower"
            ).depends_on(o_base_fertilize, delay_seconds=1)
            o_form_ridges = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id(
                "o_form_1p1m_ridges"
            ).depends_on(o_attach_furrower, delay_seconds=2)
            o_detach_furrower = tractor.detach_implement().oracle().with_id(
                "o_detach_furrower"
            ).depends_on(o_form_ridges, delay_seconds=1)
            o_plant_weather = weather.get_current_weather().oracle().with_id(
                "o_whole_field_plant_weather_check"
            ).depends_on(o_detach_furrower, delay_seconds=1)
            o_plant_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_whole_field_plant_forecast_check"
            ).depends_on(o_plant_weather, delay_seconds=1)
            o_plant_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_whole_field_plant_soil_check"
            ).depends_on(o_plant_forecast, delay_seconds=1)
            o_plant_status = tractor.get_status().oracle().with_id(
                "o_whole_field_tractor_before_planting"
            ).depends_on(o_plant_soil, delay_seconds=1)
            plant_events, o_after_planting = apply_hb_insect_budget_planting_blocks(
                tractor, o_plant_status, "o_whole_field"
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_whole_field_planting"
            ).depends_on(o_after_planting, delay_seconds=1)
            o_recheck = farm_world.get_farm_overview().oracle().with_id(
                "o_recheck_planted_field"
            ).depends_on(o_commit, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑农84标准密度建植、播种物理更新和播后复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_inventory,
            o_status,
            o_attach_grader,
            o_level,
            o_detach_grader,
            o_load_fertilizer,
            o_base_fertilize,
            o_attach_furrower,
            o_form_ridges,
            o_detach_furrower,
            o_plant_weather,
            o_plant_forecast,
            o_plant_soil,
            o_plant_status,
            *plant_events,
            o_commit,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 standard-establishment split"
        )
