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

from ._hb_three_cultivar_split_common import (
    CHECKPOINT_ZONE_A_HARVEST_READY,
    SOURCE_L3_SCENARIO_ID,
    cst_timestamp,
    populate_three_cultivar_apps,
    restore_three_cultivar_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_three_cultivar_zone_a_harvest_dry_store"


@register_scenario(SCENARIO_ID)
class ScenarioL1ThreeCultivarZoneAHarvestDryStore(Scenario):
    """L1 split: harvest ready HEIHE50 zone A, dry, and store."""

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

        if self.detailed_briefing:
            briefing_text = (
                "已知本田块按品种分为三个可见管理分区：HEIHE50早熟分区为ridges 0-20，HEINONG84分区为ridges 21-42，HEINONG58分区为ridges 43-63。"
                "截至2026-08-23，HEIHE50早熟分区进入收获前复核状态。"
                "请先确认该分区R8/harvest_allowed、天气、土壤通行性、籽粒水分和仓储/烘干资源。"
                "该分区水分在可收后烘干范围内时，按harvest -> unload -> dry -> store顺序处理ridges 0-20。"
                "不要收获尚未成熟的HEINONG84或HEINONG58区；如果收获失败，不能继续卸粮、烘干或入库。"
            )
        else:
            briefing_text = "请复核HEIHE50早熟分区天气、土壤、水分和资源；若可收，完成该分区收获、卸粮、烘干和入库。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_confirm_zone_a_harvest_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_forecast = (
                weather.get_forecast(days=3)
                .oracle()
                .with_id("o_confirm_zone_a_harvest_forecast")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_confirm_zone_a_soil_trafficability")
                .depends_on(o_forecast, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(0, 20)
                .oracle()
                .with_id("o_confirm_zone_a_harvestability")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_other = (
                farm_world.get_ridge_range_state(21, 63)
                .oracle()
                .with_id("o_confirm_other_zones_not_harvest_target")
                .depends_on(o_state, delay_seconds=1)
            )
            o_capacity = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_confirm_dryer_storage_capacity")
                .depends_on(o_other, delay_seconds=1)
            )
            o_harvest_done = harvest_range(
                tractor,
                farm_world,
                o_capacity,
                start_ridge=0,
                end_ridge=20,
                id_prefix="o_zone_a_heihe50_0_20",
                dry_after_harvest=True,
            )
            o_recheck = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_recheck_zone_a_stored_grain")
                .depends_on(o_harvest_done, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(content="已完成HEIHE50早熟分区收获、卸粮、烘干和入库。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_other,
            o_capacity,
            o_harvest_done,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 zone-A harvest dry-store split"
        )
