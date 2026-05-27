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

from ._wetjune_recheck_common import (
    CHECKPOINT_HARVEST_R8_START,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_wetjune_recheck_apps,
    restore_wetjune_recheck_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_wetjune_recheck_harvest_postharvest"


@register_scenario(SCENARIO_ID)
class ScenarioL2WetjuneRecheckHarvestPostharvest(Scenario):
    """L2 split: verify harvestability, harvest, dry, store, and recheck inventory."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_R8_START)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_R8_START
    source_checkpoint_date = "2026-08-24"
    source_dap = 112
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_wetjune_recheck_apps(self)
        restore_wetjune_recheck_checkpoint(self, CHECKPOINT_HARVEST_R8_START)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "同一田块进入R8初期收获决策窗口。作物已达到R8，但这不等于今天就能收；"
                "你需要自己根据天气、土壤通行性、籽粒水分和后处理资源决定哪一天开始收获。"
                "先检查当天和短期天气、土壤传感器、全田概览和全田ridge状态；如果grain moisture仍明显偏高，应该等待自然降水分并复查。"
                "每天或隔几天复查时仍要确认R8/harvest_allowed、天气可作业、grain moisture不超过收获上限且后处理资源可用。"
                "当水分进入可收但高于安全直接入库目标时，收获后必须按顺序卸粮、烘干到安全水分，再入库。"
                "不要在收获失败后继续假定有粮可卸、可烘干或可入库。最后复查库存和入库状态。"
            )
        else:
            briefing_text = "R8刚开始。请持续复查天气、土壤和籽粒水分，自行选择合适收获日，再完成收获、卸粮、烘干和入库。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_check_harvest_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_forecast = (
                weather.get_forecast(days=3)
                .oracle()
                .with_id("o_check_harvest_forecast")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_check_harvest_soil")
                .depends_on(o_forecast, delay_seconds=1)
            )
            o_overview = (
                farm_world.get_farm_overview()
                .oracle()
                .with_id("o_check_harvest_overview")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(0, 63)
                .oracle()
                .with_id("o_check_whole_field_harvestability")
                .depends_on(o_overview, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_initial_check_postharvest_capacity")
                .depends_on(o_state, delay_seconds=1)
            )
            o_wait_1 = (
                system.advance_time(days=3)
                .oracle()
                .with_id("o_wait_three_days_for_natural_drydown")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_weather_1 = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_recheck_weather_after_first_wait")
                .depends_on(o_wait_1, delay_seconds=1)
            )
            o_state_1 = (
                farm_world.get_ridge_range_state(0, 63)
                .oracle()
                .with_id("o_recheck_grain_moisture_after_first_wait")
                .depends_on(o_weather_1, delay_seconds=1)
            )
            o_wait_2 = (
                system.advance_time(days=3)
                .oracle()
                .with_id("o_wait_second_drydown_interval")
                .depends_on(o_state_1, delay_seconds=1)
            )
            o_weather_2 = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_recheck_weather_after_second_wait")
                .depends_on(o_wait_2, delay_seconds=1)
            )
            o_state_2 = (
                farm_world.get_ridge_range_state(0, 63)
                .oracle()
                .with_id("o_recheck_grain_moisture_after_second_wait")
                .depends_on(o_weather_2, delay_seconds=1)
            )
            o_wait_3 = (
                system.advance_time(days=3)
                .oracle()
                .with_id("o_wait_to_l3_harvestable_window")
                .depends_on(o_state_2, delay_seconds=1)
            )
            o_weather_ready = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_recheck_harvest_weather_ready")
                .depends_on(o_wait_3, delay_seconds=1)
            )
            o_state_ready = (
                farm_world.get_ridge_range_state(0, 63)
                .oracle()
                .with_id("o_recheck_whole_field_harvestability_ready")
                .depends_on(o_weather_ready, delay_seconds=1)
            )
            o_capacity_ready = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_recheck_postharvest_capacity_ready")
                .depends_on(o_state_ready, delay_seconds=1)
            )
            o_harvest_done = harvest_range(
                tractor,
                farm_world,
                o_capacity_ready,
                start_ridge=0,
                end_ridge=63,
                id_prefix="o_whole_field",
                dry_after_harvest=True,
            )
            o_recheck = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_recheck_storage_inventory")
                .depends_on(o_harvest_done, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(
                    content="已确认收获条件并完成全田收获、卸粮、烘干和入库复查。"
                )
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_overview,
            o_state,
            o_inventory,
            o_wait_1,
            o_weather_1,
            o_state_1,
            o_wait_2,
            o_weather_2,
            o_state_2,
            o_wait_3,
            o_weather_ready,
            o_state_ready,
            o_capacity_ready,
            o_harvest_done,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 harvest postharvest split"
        )
