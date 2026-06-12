from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_wetcold_high_residue_split_common import (
    CHECKPOINT_BEFORE_PLANTING,
    SOURCE_L3_SCENARIO_ID,
    apply_heinong84_planting_blocks,
    checkpoint_sim_time,
    populate_hb_wetcold_apps,
    restore_hb_wetcold_checkpoint,
    sync_field_prep_complete,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_wetcold_high_residue_planting_execution"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBWetcoldHighResiduePlantingExecution(Scenario):
    """L1 split: action-ready HEINONG84 whole-field planting."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_PLANTING)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_PLANTING
    source_checkpoint_date = "2026-05-11"
    source_growth_stage = "NOT_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_wetcold_apps(self)
        restore_hb_wetcold_checkpoint(self, CHECKPOINT_BEFORE_PLANTING)
        sync_field_prep_complete(self.get_typed_app(TractorApp))

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行湿冷高残茬田黑农84播种。整地、基肥和起垄已完成，当前是action-ready播种任务。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气和3天天气预报，确认播种窗口避开湿冷风险。\n"
                "2. 读取土壤传感器，确认种床土温/墒情和通行性。\n"
                "3. 查看种子库存和播种机/拖拉机状态。\n"
                "4. 装载HEINONG84种子。\n"
                "5. 按播深4.0cm、株距7.9cm完成0-63垄播种；中途种子不足时先补装。\n"
                "6. 播种后提交当天物理状态并查看全田概览。\n"
                "7. 向我汇报播种范围、窗口判断和全田是否进入建苗阶段。"
            )
        else:
            briefing_text = "请复核湿冷高残茬田播种窗口；条件合适时完成0-63垄黑农84播种并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_weather_before_planting"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_forecast_before_planting"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_seedbed_soil_before_planting"
            ).depends_on(o_forecast, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_seed_inventory_before_planting"
            ).depends_on(o_soil, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_planter_status_before_planting"
            ).depends_on(o_inventory, delay_seconds=1)
            plant_events, o_after_planting = apply_heinong84_planting_blocks(
                tractor, o_tractor, "o_whole_field_residue"
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_whole_field_planting"
            ).depends_on(o_after_planting, delay_seconds=1)
            o_recheck = farm_world.get_farm_overview().oracle().with_id(
                "o_recheck_planted_field"
            ).depends_on(o_commit, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成湿冷高残茬黑农84全田播种和播后复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
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
        return validate_native_workflow(self, env, "wet-cold high-residue planting L1")
