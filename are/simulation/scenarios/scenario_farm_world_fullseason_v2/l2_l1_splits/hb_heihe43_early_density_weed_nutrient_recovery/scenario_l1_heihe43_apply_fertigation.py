from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_heihe43_split_common import (
    CHECKPOINT_BEFORE_FERTIGATION,
    SOURCE_L3_SCENARIO_ID,
    cst_timestamp,
    populate_hb_heihe43_apps,
    restore_hb_heihe43_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_heihe43_apply_nutrient_fertigation"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBHeihe43ApplyNutrientFertigation(Scenario):
    """L1 split: apply targeted fertigation to the confirmed nutrient-weak block."""

    start_time: float | None = cst_timestamp(2026, 5, 25, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_FERTIGATION
    source_checkpoint_date = "2026-05-25"
    source_dap = 21
    source_growth_stage = "V1"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_heihe43_apps(self)
        restore_hb_heihe43_checkpoint(self, CHECKPOINT_BEFORE_FERTIGATION)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-25，黑河43 V1期的早期长势诊断已经完成，"
                "一处营养弱势区域需要小水量水肥恢复。"
                "请先复核当前天气、短期预报、土壤水分、目标区域状态和投入品余量；"
                "若条件适合，只对已确认的营养弱势区域执行水肥恢复，并复查恢复区和库存。"
            )
        else:
            briefing_text = "请复核天气、土壤、目标状态和库存，然后对已确认营养弱势区域执行一次水肥恢复。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_fertigation_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_confirm_fertigation_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_soil_before_fertigation"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(8, 19).oracle().with_id(
                "o_confirm_nutrient_block"
            ).depends_on(o_soil, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_confirm_fertigation_inputs"
            ).depends_on(o_state, delay_seconds=1)
            o_action = farm_world.apply_fertigation(8, 19, 0.24, 1.2).oracle().with_id(
                "o_apply_targeted_fertigation_8_19"
            ).depends_on(o_inventory, delay_seconds=2)
            o_recheck = farm_world.get_ridge_range_state(8, 19).oracle().with_id(
                "o_recheck_nutrient_block_after_fertigation"
            ).depends_on(o_action, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成营养弱势区域水肥恢复和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_inventory,
            o_action,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "HEIHE43 nutrient fertigation L1 split"
        )

