from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_replant_after_crusting_short_season_split_common import (
    CHECKPOINT_BEFORE_REPLANT,
    SOURCE_L3_SCENARIO_ID,
    apply_replant_blocks,
    checkpoint_sim_time,
    populate_hb_replant_apps,
    restore_hb_replant_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_replant_crusting_action_ready"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBReplantCrustingActionReady(Scenario):
    """L1 split: perform action-ready targeted replanting after confirmation."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_REPLANT)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_REPLANT
    source_checkpoint_date = "2026-05-25"
    source_dap = 21
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_replant_apps(self)
        restore_hb_replant_checkpoint(self, CHECKPOINT_BEFORE_REPLANT)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行播后板结导致的局部补种。目标区已完成地面确认，当前是action-ready补种任务。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气和3天天气预报，确认补种窗口。\n"
                "2. 读取土壤传感器，确认种床和通行性。\n"
                "3. 读取0-11垄状态，确认stand缺口仍存在。\n"
                "4. 查看黑农84种子库存和播种设备状态。\n"
                "5. 条件合适时只对确认缺苗区补种，播深4.0cm、株距7.9cm。\n"
                "6. 补种后复查目标区状态和资源余量。\n"
                "7. 向我汇报补种范围，以及健康垄没有被重复播种。"
            )
        else:
            briefing_text = "请完成0-11垄局部补种前复核；条件合适时只补种确认缺苗区并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_replant_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_confirm_replant_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_replant_seedbed"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 11).oracle().with_id(
                "o_confirm_replant_range_state"
            ).depends_on(o_soil, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_confirm_seed_stock"
            ).depends_on(o_state, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_confirm_replant_equipment"
            ).depends_on(o_inventory, delay_seconds=1)
            replant_events, o_after_replant = apply_replant_blocks(
                tractor, o_tractor, "o_action_ready_crusting"
            )
            o_recheck = farm_world.get_ridge_range_state(0, 11).oracle().with_id(
                "o_recheck_action_ready_replant"
            ).depends_on(o_after_replant, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成局部补种执行和补种后复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_inventory,
            o_tractor,
            *replant_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 action-ready crusting replant split"
        )
