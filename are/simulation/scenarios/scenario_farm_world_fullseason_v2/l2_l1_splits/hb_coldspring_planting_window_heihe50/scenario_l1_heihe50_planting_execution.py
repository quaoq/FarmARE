from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_coldspring_heihe50_split_common import (
    CHECKPOINT_BEFORE_PLANTING,
    SOURCE_L3_SCENARIO_ID,
    apply_heihe50_planting_blocks,
    checkpoint_sim_time,
    populate_hb_coldspring_heihe50_apps,
    restore_hb_coldspring_heihe50_checkpoint,
    sync_field_prep_complete,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_coldspring_heihe50_planting_execution"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBColdspringHeihe50PlantingExecution(Scenario):
    """L1 split: execute HEIHE50 planting from the action-ready window."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_PLANTING)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_PLANTING
    source_checkpoint_date = "2026-05-16"
    source_dap = 0
    source_growth_stage = "NOT_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_coldspring_heihe50_apps(self)
        restore_hb_coldspring_heihe50_checkpoint(self, CHECKPOINT_BEFORE_PLANTING)
        sync_field_prep_complete(self.get_typed_app(TractorApp))

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行冷春黑河50播种。田块已完成整地、基肥和起垄，当前任务是复核窗口后完成0-63共64垄播种。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气，确认无雨且可以下地作业。\n"
                "2. 查看3天天气预报，确认播种后短期天气不会明显破坏出苗窗口。\n"
                "3. 读取土壤传感器，确认种床土温、墒情和通行性适合播种。\n"
                "4. 查看仓库库存，确认HEIHE50种子和燃油充足。\n"
                "5. 检查拖拉机和播种设备状态，确认可以直接进入播种作业。\n"
                "6. 装载HEIHE50种子。\n"
                "7. 按每趟4垄、播深4.0cm、株距8.2cm完成0-63垄播种；中途种子不足时先补装再继续。\n"
                "8. 播种完成后查看田块总览，复查已播垄数、播深/株距记录和剩余库存。\n"
                "9. 向我汇报冷春窗口依据、64垄播种是否完成，以及后续建苗风险。"
            )
        else:
            briefing_text = "请复核冷春黑河50播种窗口，条件合适时完成0-63垄播种和复查。"

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
            plant_events, o_after_planting = apply_heihe50_planting_blocks(
                tractor, o_status, "o_whole_field_heihe50_window_ready"
            )
            o_recheck = farm_world.get_farm_overview().oracle().with_id(
                "o_recheck_planted_field"
            ).depends_on(o_after_planting, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑河50冷春窗口全田播种执行和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_inventory,
            o_status,
            *plant_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HEIHE50 planting execution L1")
