from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    harvest_range,
)
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_replant_after_crusting_short_season_split_common import (
    CHECKPOINT_HARVEST_READY,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_replant_apps,
    restore_hb_replant_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_replant_short_season_harvest_store_ready"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBReplantShortSeasonHarvestStoreReady(Scenario):
    """L1 split: execute ready staged harvest and direct storage."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_READY)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_READY
    source_checkpoint_date = "2026-09-11"
    source_dap = 128
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_replant_apps(self)
        restore_hb_replant_checkpoint(self, CHECKPOINT_HARVEST_READY)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行短季补种田块的收获入库复核。主体区和补种区都需要按成熟、水分和容量分别确认。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气和3天天气预报，确认收获窗口。\n"
                "2. 读取土壤传感器，确认通行性。\n"
                "3. 先读取主体区状态，确认R8、harvest_allowed和grain_moisture。\n"
                "4. 查看拖车和仓储容量。\n"
                "5. 水分安全时完成主体区收获闭环并直接入库。\n"
                "6. 再读取补种区状态，确认是否也达到安全直接入库条件。\n"
                "7. 条件支持时完成补种区收获闭环并直接入库。\n"
                "8. 若任何步骤失败，不要继续后续卸粮或入库。\n"
                "9. 向我分别汇报主体区和补种区 recovered yield 入库闭环。"
            )
        else:
            briefing_text = "请复核主体区和补种区收获条件与仓储容量；水分安全时完成收获闭环并直接入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_harvest_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_confirm_harvest_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_harvest_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_reference_state = farm_world.get_ridge_range_state(12, 63).oracle().with_id(
                "o_confirm_reference_ready_12_63"
            ).depends_on(o_soil, delay_seconds=1)
            o_capacity = farm_world.get_inventory().oracle().with_id(
                "o_confirm_storage_capacity"
            ).depends_on(o_reference_state, delay_seconds=1)
            o_reference_done = harvest_range(
                tractor,
                farm_world,
                o_capacity,
                start_ridge=12,
                end_ridge=63,
                id_prefix="o_reference_ready_12_63",
                dry_after_harvest=False,
            )
            o_replanted_state = farm_world.get_ridge_range_state(0, 11).oracle().with_id(
                "o_confirm_replanted_ready_0_11"
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
                "o_recheck_stored_grain"
            ).depends_on(o_replanted_done, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成主体区域和补种区域收获、卸粮与直接入库复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_reference_state,
            o_capacity,
            o_reference_done,
            o_replanted_state,
            o_replanted_done,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 ready harvest-store split"
        )
