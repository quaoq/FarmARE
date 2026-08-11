from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_coldspring_heihe50_split_common import (
    CHECKPOINT_HARVEST_DIRECT_STORE_READY,
    SOURCE_L3_SCENARIO_ID,
    apply_heihe50_harvest_direct_store,
    checkpoint_sim_time,
    populate_hb_coldspring_heihe50_apps,
    restore_hb_coldspring_heihe50_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_coldspring_heihe50_harvest_store_ready"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBColdspringHeihe50HarvestStoreReady(Scenario):
    """L1 split: harvest HEIHE50 when moisture is safe for direct storage."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_DIRECT_STORE_READY)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_DIRECT_STORE_READY
    source_checkpoint_date = "2026-09-06"
    source_dap = 114
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_coldspring_heihe50_apps(self)
        restore_hb_coldspring_heihe50_checkpoint(
            self, CHECKPOINT_HARVEST_DIRECT_STORE_READY
        )

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行黑河50全田直接入库收获。当前是收获前直接入库窗口，目标是确认水分安全后，"
                "完成0-63垄harvest -> unload -> store闭环。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气和3天天气预报，确认没有会影响收割和运输的降雨风险。\n"
                "2. 读取土壤传感器，确认田间通行性。\n"
                "3. 读取0-63垄状态，重点确认R8、harvest_allowed和grain_moisture。\n"
                "4. 查看库存/仓储容量，确认直接入库空间足够。\n"
                "5. 若籽粒水分确认<=13.5%，按每趟4垄完成0-63垄收获；每趟收获后及时卸粮。\n"
                "6. 将卸下的粮食直接入库；不要烘干，也不要继续等待到8%-10%水分。\n"
                "7. 入库后复查库存和已存粮状态。\n"
                "8. 向我汇报 recovered yield、入库状态和是否完成全田直接入库闭环。"
            )
        else:
            briefing_text = "请复核黑河50收获条件；水分安全时完成0-63垄收获、卸粮和直接入库。"

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
            o_state = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_confirm_safe_moisture"
            ).depends_on(o_soil, delay_seconds=1)
            o_capacity = farm_world.get_inventory().oracle().with_id(
                "o_confirm_storage_capacity"
            ).depends_on(o_state, delay_seconds=1)
            harvest_events, o_store = apply_heihe50_harvest_direct_store(
                tractor, farm_world, o_capacity, "o_whole_field_heihe50"
            )
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_stored_grain"
            ).depends_on(o_store, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑河50全田收获、卸粮和安全直接入库。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_capacity,
            *harvest_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HEIHE50 harvest-store ready L1")
