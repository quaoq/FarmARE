from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_replant_after_crusting_short_season_split_common import (
    CHECKPOINT_INITIAL_BEFORE_FIELD_PREP,
    SOURCE_L3_SCENARIO_ID,
    apply_heinong84_planting_blocks,
    checkpoint_sim_time,
    populate_hb_replant_apps,
    restore_hb_replant_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_replant_heinong84_establishment_planting"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBReplantHeinong84EstablishmentPlanting(Scenario):
    """L2 split: establish HEINONG84 before a crusting-risk short season."""

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
        populate_hb_replant_apps(self)
        restore_hb_replant_checkpoint(self, CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-05，哈尔滨64条垄尚未播种，计划全田种植黑农84。"
                "本季重点是建立均匀群体并为播后强雨或表土板结风险保留清晰记录。"
                "请复核当前天气、5日预报、土壤温度/墒情、种子/肥料/燃油和拖拉机状态；"
                "条件合适时完成整地、基肥、起垄，再按4.0 cm播深和7.9 cm株距全田播种。"
                "播后请提交一次日物理复查，确认已播垄数、资源余量和早期出苗风险。"
            )
        else:
            briefing_text = "请完成黑农84全田播前检查、整地施基肥起垄、标准密度播种和播后复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=5).oracle().with_id(
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
            o_load_fert = tractor.load_fertilizer(360.0).oracle().with_id(
                "o_load_base_fertilizer"
            ).depends_on(o_detach_grader, delay_seconds=1)
            o_base_fert = tractor.base_fertilize().oracle().with_id(
                "o_apply_base_fertilizer"
            ).depends_on(o_load_fert, delay_seconds=2)
            o_attach_furrower = tractor.attach_implement("furrower").oracle().with_id(
                "o_attach_furrower"
            ).depends_on(o_base_fert, delay_seconds=1)
            o_ridges = tractor.form_ridges(ridge_width_m=1.1).oracle().with_id(
                "o_form_ridges"
            ).depends_on(o_attach_furrower, delay_seconds=2)
            o_detach_furrower = tractor.detach_implement().oracle().with_id(
                "o_detach_furrower"
            ).depends_on(o_ridges, delay_seconds=1)
            o_recheck_seedbed = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_seedbed_after_prep"
            ).depends_on(o_detach_furrower, delay_seconds=1)
            plant_events, o_after_planting = apply_heinong84_planting_blocks(
                tractor, o_recheck_seedbed, "o_whole_field_heinong84_standard_density"
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_whole_field_planting"
            ).depends_on(o_after_planting, delay_seconds=2)
            o_recheck = farm_world.get_farm_overview().oracle().with_id(
                "o_recheck_planted_field"
            ).depends_on(o_commit, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成黑农84全田整地、基肥、起垄、标准密度播种和播后复查。"
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
            o_load_fert,
            o_base_fert,
            o_attach_furrower,
            o_ridges,
            o_detach_furrower,
            o_recheck_seedbed,
            *plant_events,
            o_commit,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 HEINONG84 establishment planting split"
        )
