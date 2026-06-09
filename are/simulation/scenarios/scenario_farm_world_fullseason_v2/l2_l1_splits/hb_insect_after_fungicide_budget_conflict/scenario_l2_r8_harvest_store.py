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

from ._hb_insect_after_fungicide_budget_conflict_split_common import (
    CHECKPOINT_HARVEST_R8_START,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_insect_budget_apps,
    restore_hb_insect_budget_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_insect_budget_r8_harvest_store"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBInsectBudgetR8HarvestStore(Scenario):
    """L2 split: start at R8, wait for harvest window, harvest, dry, and store."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_R8_START)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_R8_START
    source_checkpoint_date = "2026-08-25"
    source_dap = 113
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_insect_budget_apps(self)
        restore_hb_insect_budget_checkpoint(self, CHECKPOINT_HARVEST_R8_START)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-08-25，作物已达到R8_FULL_MATURITY。"
                "作物已经成熟，但籽粒水分仍明显高于可收上限，不能因为达到R8就立即收获。"
                "请连续复查天气、土壤通行性、全田状态、grain moisture和仓储/拖车资源；水分过高时等待自然降水分。"
                "当天气和土壤可作业且籽粒水分进入可收范围时，按源L3路径完成全田收获、卸粮、必要干燥和入库。"
                "本任务不要为8%-10%水分过度等待；收后按实际粮食水分和仓储要求干燥到安全目标再入库。"
            )
        else:
            briefing_text = "从R8开始复查天气、通行性和籽粒水分，选择合适日期完成全田收获、卸粮、按需干燥和入库。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_r8_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_r8_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_r8_soil_trafficability"
            ).depends_on(o_forecast, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_check_r8_whole_field_moisture"
            ).depends_on(o_soil, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_storage_capacity_initial"
            ).depends_on(o_state, delay_seconds=1)
            o_wait_1 = system.advance_time(days=5).oracle().with_id(
                "o_wait_first_natural_drydown_interval"
            ).depends_on(o_inventory, delay_seconds=1)
            o_state_1 = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_moisture_after_first_wait"
            ).depends_on(o_wait_1, delay_seconds=1)
            o_wait_2 = system.advance_time(days=5).oracle().with_id(
                "o_wait_second_natural_drydown_interval"
            ).depends_on(o_state_1, delay_seconds=1)
            o_state_2 = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_moisture_after_second_wait"
            ).depends_on(o_wait_2, delay_seconds=1)
            o_wait_3 = system.advance_time(days=1).oracle().with_id(
                "o_wait_to_source_l3_harvest_window"
            ).depends_on(o_state_2, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_harvest_weather_ready"
            ).depends_on(o_wait_3, delay_seconds=1)
            o_soil_ready = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_harvest_soil_ready"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_state_ready = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
                "o_recheck_harvestable_whole_field"
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
                "o_recheck_storage_after_harvest"
            ).depends_on(o_harvest_done, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成R8后水分等待、全田收获、卸粮、干燥和入库复查。"
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
            self, env, "full-checkpoint L2 R8 harvest-store split"
        )
