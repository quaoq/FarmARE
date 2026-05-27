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

from ._hb_heihe43_split_common import (
    CHECKPOINT_HARVEST_R7_START,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_heihe43_apps,
    restore_hb_heihe43_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_heihe43_r7_harvest_drydown_store"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBHeihe43R7HarvestDrydownStore(Scenario):
    """L2 split: wait from R7/R8 transition into harvest + drying moisture."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_R7_START)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_R7_START
    source_checkpoint_date = "2026-08-08"
    source_dap = 96
    source_growth_stage = "R7_BEGINNING_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_heihe43_apps(self)
        restore_hb_heihe43_checkpoint(self, CHECKPOINT_HARVEST_R7_START)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-08-08，黑河43全田进入成熟后期复核阶段，但还不能只凭接近成熟就立即收获。"
                "请连续检查天气、预报、土壤通行性、R8/harvest_allowed、籽粒水分和仓储资源；"
                "水分过高时等待自然降水分。当天气、通行性和水分进入可收但仍需烘干窗口时，"
                "按harvest -> unload -> dry_grain(target_moisture_pct=13.0) -> store顺序完成全田收获，"
                "不要把高于13.5%的湿粮直接入库，也不要为8%-10%水分过度等待。"
            )
        else:
            briefing_text = "请从成熟后期开始复查天气、通行性和籽粒水分，等待合适窗口后完成全田收获、烘干和入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_maturity_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_maturity_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_initial_harvest_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_check_initial_maturity_and_moisture"
            ).depends_on(o_soil, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_storage_capacity_initial"
            ).depends_on(o_state, delay_seconds=1)
            o_wait_1 = system.advance_time(days=4).oracle().with_id(
                "o_wait_first_drydown_interval"
            ).depends_on(o_inventory, delay_seconds=1)
            o_state_1 = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_moisture_after_first_wait"
            ).depends_on(o_wait_1, delay_seconds=1)
            o_wait_2 = system.advance_time(days=4).oracle().with_id(
                "o_wait_second_drydown_interval"
            ).depends_on(o_state_1, delay_seconds=1)
            o_state_2 = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_moisture_after_second_wait"
            ).depends_on(o_wait_2, delay_seconds=1)
            o_wait_3 = system.advance_time(days=4).oracle().with_id(
                "o_wait_to_harvest_window"
            ).depends_on(o_state_2, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_harvest_weather_ready"
            ).depends_on(o_wait_3, delay_seconds=1)
            o_soil_ready = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_harvest_soil_ready"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_state_ready = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_safe_moisture_and_harvestability"
            ).depends_on(o_soil_ready, delay_seconds=1)
            o_capacity_ready = farm_world.get_inventory().oracle().with_id(
                "o_recheck_storage_capacity_ready"
            ).depends_on(o_state_ready, delay_seconds=1)
            o_harvest_done = harvest_range(
                tractor,
                farm_world,
                o_capacity_ready,
                start_ridge=0,
                end_ridge=63,
                id_prefix="o_whole_field",
                dry_after_harvest=True,
            )
            o_recheck = farm_world.get_inventory().oracle().with_id(
                "o_recheck_stored_grain"
            ).depends_on(o_harvest_done, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成成熟后期水分等待、全田收获、卸粮、烘干至13.0%并入库。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_state,
            o_inventory,
            o_wait_1,
            o_state_1,
            o_wait_2,
            o_state_2,
            o_wait_3,
            o_weather_ready,
            o_soil_ready,
            o_state_ready,
            o_capacity_ready,
            o_harvest_done,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "HEIHE43 R7/R8 harvest drydown L2 split"
        )
