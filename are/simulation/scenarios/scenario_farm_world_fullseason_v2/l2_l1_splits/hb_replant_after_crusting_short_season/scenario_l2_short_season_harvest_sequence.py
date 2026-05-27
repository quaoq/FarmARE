from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    harvest_range,
)
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_replant_after_crusting_short_season_split_common import (
    CHECKPOINT_HARVEST_R8_STAGGERED,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_replant_apps,
    restore_hb_replant_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_replant_short_season_harvest_sequence"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBReplantShortSeasonHarvestSequence(Scenario):
    """L2 split: wait through staggered maturity, then harvest/store by readiness."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_R8_STAGGERED)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_R8_STAGGERED
    source_checkpoint_date = "2026-08-23"
    source_dap = 108
    source_growth_stage = "R6/R8_MIXED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_replant_apps(self)
        restore_hb_replant_checkpoint(self, CHECKPOINT_HARVEST_R8_STAGGERED)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-08-23，田块进入成熟复核阶段，但前期局部补种使不同区域成熟和籽粒水分不同步。"
                "不要因为一部分达到R8就立即全田收获。请连续复查天气、预报、土壤通行性、"
                "各区域R8/harvest_allowed、grain moisture、拖车和仓储容量；水分过高或仍未成熟时等待。"
                "当条件合适且籽粒水分进入可直接安全入库范围时，先处理已成熟的主体区域，"
                "再处理补种后成熟较晚的区域；按harvest -> unload -> store顺序完成，不要为8%-10%水分过度等待。"
            )
        else:
            briefing_text = "请从成熟错位状态开始复查天气、通行性和籽粒水分，等待合适窗口后分区收获并直接入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_staggered_maturity_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_staggered_harvest_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_initial_harvest_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_check_initial_maturity_and_moisture"
            ).depends_on(o_soil, delay_seconds=1)
            o_capacity = farm_world.get_inventory().oracle().with_id(
                "o_check_initial_storage_capacity"
            ).depends_on(o_state, delay_seconds=1)
            o_wait_1 = system.advance_time(days=8).oracle().with_id(
                "o_wait_first_staggered_drydown_interval"
            ).depends_on(o_capacity, delay_seconds=1)
            o_state_1 = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_maturity_after_first_wait"
            ).depends_on(o_wait_1, delay_seconds=1)
            o_wait_2 = system.advance_time(days=6).oracle().with_id(
                "o_wait_second_staggered_drydown_interval"
            ).depends_on(o_state_1, delay_seconds=1)
            o_state_2 = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_safe_moisture_after_second_wait"
            ).depends_on(o_wait_2, delay_seconds=1)
            o_wait_3 = system.advance_time(days=5).oracle().with_id(
                "o_wait_to_source_harvest_window"
            ).depends_on(o_state_2, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_harvest_weather_ready"
            ).depends_on(o_wait_3, delay_seconds=1)
            o_soil_ready = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_harvest_soil_ready"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_reference_state = farm_world.get_ridge_range_state(12, 63).oracle().with_id(
                "o_reference_ready_12_63_harvest_range_state"
            ).depends_on(o_soil_ready, delay_seconds=1)
            o_reference_done = harvest_range(
                tractor,
                farm_world,
                o_reference_state,
                start_ridge=12,
                end_ridge=63,
                id_prefix="o_reference_ready_12_63",
                dry_after_harvest=False,
            )
            o_replanted_state = farm_world.get_ridge_range_state(0, 11).oracle().with_id(
                "o_replanted_0_11_harvest_range_state"
            ).depends_on(o_reference_done, delay_seconds=1)
            o_replanted_done = harvest_range(
                tractor,
                farm_world,
                o_replanted_state,
                start_ridge=0,
                end_ridge=11,
                id_prefix="o_replanted_0_11",
                dry_after_harvest=False,
            )
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_warehouse_after_staged_harvest"
            ).depends_on(o_replanted_done, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成短季节成熟错位复查、分区收获、卸粮和直接入库。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_capacity,
            o_wait_1,
            o_state_1,
            o_wait_2,
            o_state_2,
            o_wait_3,
            o_weather_ready,
            o_soil_ready,
            o_reference_state,
            o_reference_done,
            o_replanted_state,
            o_replanted_done,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 short-season harvest sequence split"
        )
