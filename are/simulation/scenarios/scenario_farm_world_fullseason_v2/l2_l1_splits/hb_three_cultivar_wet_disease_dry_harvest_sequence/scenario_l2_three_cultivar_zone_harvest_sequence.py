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
                "已知本田块按品种分为三个可见管理分区：HEIHE50早熟分区为ridges 0-20，HEINONG84分区为ridges 21-42，HEINONG58分区为ridges 43-63。"
                "截至2026-08-23，田块进入三品种分区收获窗口。"
                "请按分区成熟度、籽粒水分、天气和土壤通行性决定收获顺序。"
                "HEIHE50早熟分区已到可收后烘干窗口；HEINONG84和HEINONG58分区需要继续等待各自R8和水分窗口。"
                "每个分区都必须按harvest -> unload -> dry -> store顺序完成；不要在收获失败后继续卸粮、烘干或入库。"
                "水分13.5%-18%可收后烘干，超过18%正常不要收。"
            )
        else:
            briefing_text = "请按三品种分区成熟和水分窗口，依次完成收获、卸粮、烘干和入库。"

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
