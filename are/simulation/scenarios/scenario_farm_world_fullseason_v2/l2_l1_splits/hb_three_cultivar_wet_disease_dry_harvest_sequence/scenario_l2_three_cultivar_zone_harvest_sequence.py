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

from ._hb_three_cultivar_split_common import (
    CHECKPOINT_ZONE_A_HARVEST_READY,
    SOURCE_L3_SCENARIO_ID,
    cst_timestamp,
    populate_three_cultivar_apps,
    restore_three_cultivar_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_three_cultivar_zone_harvest_sequence"


@register_scenario(SCENARIO_ID)
class ScenarioL2ThreeCultivarZoneHarvestSequence(Scenario):
    """L2 split: harvest and postharvest three cultivar zones by maturity."""

    start_time: float | None = cst_timestamp(2026, 8, 23, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_ZONE_A_HARVEST_READY
    source_checkpoint_date = "2026-08-23"
    source_dap = 110
    source_growth_stage = "R8_FULL_MATURITY in HEIHE50 zone"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_three_cultivar_apps(self)
        restore_three_cultivar_checkpoint(self, CHECKPOINT_ZONE_A_HARVEST_READY)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备执行三品种分区收获。田块分为HEIHE50早熟区0-20垄、HEINONG84中熟区21-42垄、"
                "HEINONG58晚熟区43-63垄。\n"
                "请按以下步骤操作：\n"
                "1. 先处理HEIHE50早熟区0-20垄：查看今天天气，读取土壤传感器，确认通行性。\n"
                "2. 查看0-20垄状态，确认R8成熟、harvest_allowed、籽粒水分和可收条件。\n"
                "3. 查看库存和仓储/烘干容量，确认可以完成收获后处理。\n"
                "4. 对0-20垄按每趟约4垄收获；每次收获后先卸粮，再继续下一趟。\n"
                "5. 0-20垄收完后，将粮食烘干到13.0%目标水分，然后入库。\n"
                "6. 等待HEINONG84分区进入合适窗口后，复查天气、土壤、21-42垄状态和仓储容量。\n"
                "7. 对21-42垄按harvest -> unload -> dry_grain(target_moisture_pct=13.0) -> store顺序完成闭环。\n"
                "8. 再等待HEINONG58分区进入合适窗口后，复查天气、土壤、43-63垄状态和仓储容量。\n"
                "9. 对43-63垄按harvest -> unload -> dry_grain(target_moisture_pct=13.0) -> store顺序完成闭环。\n"
                "10. 如果某一分区水分超过18%或通行性不满足，不要强行收获；如果收获或烘干失败，不要继续入库。\n"
                "11. 最后复查库存，并按三个分区分别汇报收获、卸粮、烘干、入库状态，以及recovered yield闭环是否完成。"
            )
        else:
            briefing_text = "请按三品种分区成熟和水分窗口，依次完成可收分区的收获、卸粮、烘干和入库。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather_a = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_zone_a_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_soil_a = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_zone_a_soil")
                .depends_on(o_weather_a, delay_seconds=1)
            )
            o_state_a = (
                farm_world.get_ridge_range_state(0, 20)
                .oracle()
                .with_id("o_zone_a_state")
                .depends_on(o_soil_a, delay_seconds=1)
            )
            o_capacity_a = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_zone_a_capacity")
                .depends_on(o_state_a, delay_seconds=1)
            )
            o_harvest_a = harvest_range(
                tractor,
                farm_world,
                o_capacity_a,
                start_ridge=0,
                end_ridge=20,
                id_prefix="o_zone_a_heihe50_0_20",
                dry_after_harvest=True,
            )
            o_wait_b = (
                system.advance_time(days=14)
                .oracle()
                .with_id("o_wait_zone_b_to_source_window")
                .depends_on(o_harvest_a, delay_seconds=1)
            )
            o_weather_b = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_zone_b_weather")
                .depends_on(o_wait_b, delay_seconds=1)
            )
            o_soil_b = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_zone_b_soil")
                .depends_on(o_weather_b, delay_seconds=1)
            )
            o_state_b = (
                farm_world.get_ridge_range_state(21, 42)
                .oracle()
                .with_id("o_zone_b_state")
                .depends_on(o_soil_b, delay_seconds=1)
            )
            o_capacity_b = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_zone_b_capacity")
                .depends_on(o_state_b, delay_seconds=1)
            )
            o_harvest_b = harvest_range(
                tractor,
                farm_world,
                o_capacity_b,
                start_ridge=21,
                end_ridge=42,
                id_prefix="o_zone_b_heinong84_21_42",
                dry_after_harvest=True,
            )
            o_wait_c = (
                system.advance_time(days=12)
                .oracle()
                .with_id("o_wait_zone_c_to_source_window")
                .depends_on(o_harvest_b, delay_seconds=1)
            )
            o_weather_c = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_zone_c_weather")
                .depends_on(o_wait_c, delay_seconds=1)
            )
            o_soil_c = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_zone_c_soil")
                .depends_on(o_weather_c, delay_seconds=1)
            )
            o_state_c = (
                farm_world.get_ridge_range_state(43, 63)
                .oracle()
                .with_id("o_zone_c_state")
                .depends_on(o_soil_c, delay_seconds=1)
            )
            o_capacity_c = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_zone_c_capacity")
                .depends_on(o_state_c, delay_seconds=1)
            )
            o_harvest_c = harvest_range(
                tractor,
                farm_world,
                o_capacity_c,
                start_ridge=43,
                end_ridge=63,
                id_prefix="o_zone_c_heinong58_43_63",
                dry_after_harvest=True,
            )
            o_recheck = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_recheck_all_zone_storage")
                .depends_on(o_harvest_c, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(content="已按三品种分区完成收获、卸粮、烘干和入库。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather_a,
            o_soil_a,
            o_state_a,
            o_capacity_a,
            o_harvest_a,
            o_wait_b,
            o_weather_b,
            o_soil_b,
            o_state_b,
            o_capacity_b,
            o_harvest_b,
            o_wait_c,
            o_weather_c,
            o_soil_c,
            o_state_c,
            o_capacity_c,
            o_harvest_c,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 three-zone harvest split"
        )
