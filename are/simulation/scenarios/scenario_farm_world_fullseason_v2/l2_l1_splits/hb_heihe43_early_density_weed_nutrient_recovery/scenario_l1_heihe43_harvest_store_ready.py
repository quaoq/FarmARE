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

from ._hb_heihe43_split_common import (
    CHECKPOINT_HARVEST_READY,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_heihe43_apps,
    restore_hb_heihe43_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_heihe43_harvest_store_ready"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBHeihe43HarvestStoreReady(Scenario):
    """L1 split: harvest the ready HEIHE43 field, dry grain, then store."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_READY)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_READY
    source_checkpoint_date = "2026-08-20"
    source_dap = 108
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_heihe43_apps(self)
        restore_hb_heihe43_checkpoint(self, CHECKPOINT_HARVEST_READY)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行黑河43全田收获入库。目标是在条件满足时完成0-63共64垄的harvest、unload、dry和store闭环。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气，确认没有影响收获和通行的降雨风险。\n"
                "2. 读取土壤传感器，确认田间通行性满足拖拉机收获作业。\n"
                "3. 查看0-63垄田块状态，确认R8成熟、harvest_allowed和籽粒水分。\n"
                "4. 查看库存和仓储/烘干容量，确认可以接收并处理本次收获粮食。\n"
                "5. 如果籽粒水分高于13.5%，不要直接入库；应收获后烘干到13.0%目标水分再入库。\n"
                "6. 按每趟约4垄收获0-63垄；每趟收获后先卸粮，再继续下一趟。\n"
                "7. 全田收获和卸粮完成后，执行dry_grain(target_moisture_pct=13.0)。\n"
                "8. 烘干成功后执行store_grain入库。\n"
                "9. 如果任何收获、卸粮或烘干步骤失败，不要继续后续入库动作。\n"
                "10. 最后复查库存/入库状态，并向我汇报黑河43全田recovered yield闭环是否完成。"
            )
        else:
            briefing_text = "请复核天气、通行性、水分和容量；条件合适时完成黑河43全田收获、烘干和入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_confirm_harvest_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_confirm_harvest_soil"
            ).depends_on(o_weather, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_confirm_whole_field_harvestability"
            ).depends_on(o_soil, delay_seconds=1)
            o_capacity = farm_world.get_inventory().oracle().with_id(
                "o_confirm_storage_capacity"
            ).depends_on(o_state, delay_seconds=1)
            o_harvest_done = harvest_range(
                tractor,
                farm_world,
                o_capacity,
                start_ridge=0,
                end_ridge=63,
                id_prefix="o_whole_field",
                dry_after_harvest=True,
            )
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_stored_grain"
            ).depends_on(o_harvest_done, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成黑河43全田收获、卸粮、烘干至13.0%和入库复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_state,
            o_capacity,
            o_harvest_done,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "HEIHE43 harvest-store ready L1 split"
        )
