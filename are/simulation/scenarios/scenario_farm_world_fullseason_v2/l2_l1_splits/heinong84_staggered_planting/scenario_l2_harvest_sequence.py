from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._heinong84_staggered_split_common import (
    CHECKPOINT_EARLY_HARVEST_R8_START,
    EARLY_END,
    EARLY_START,
    LATE_END,
    LATE_START,
    MID_END,
    MID_START,
    SOURCE_L3_SCENARIO_ID,
    apply_hn84_harvest_zone,
    checkpoint_sim_time,
    populate_hn84_staggered_apps,
    restore_hn84_staggered_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hn84_staggered_harvest_sequence"


@register_scenario(SCENARIO_ID)
class ScenarioL2HN84StaggeredHarvestSequence(Scenario):
    """L2 split: decide staggered harvest windows and handle each batch."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_EARLY_HARVEST_R8_START)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_EARLY_HARVEST_R8_START
    source_checkpoint_date = "2026-08-26"
    source_dap = 95
    source_growth_stage = "R8_WINDOW_BEGINNING"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hn84_staggered_apps(self)
        restore_hn84_staggered_checkpoint(self, CHECKPOINT_EARLY_HARVEST_R8_START)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "黑农84错期播种田块已进入收获窗口起点。早播、中播、晚播区成熟和籽粒水分不同，"
                "不能全田固定同一天收。请从R8/harvest_allowed、天气、预报、土壤通行性、"
                "目标分区籽粒水分和仓储/烘干能力判断窗口；每批必须按 harvest -> unload -> dry -> store 处理。"
            )
        else:
            briefing_text = "请为黑农84三区寻找收获窗口，分批收获、卸粮、烘干和入库。"

        events = []
        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            current = briefing
            events.append(briefing)
            for day in range(1, 12):
                current = system.advance_time(days=1).oracle().with_id(
                    f"o_wait_early_harvest_window_advance_day_{day:03d}"
                ).depends_on(current, delay_seconds=1)
                events.append(current)
            o_early_weather = weather.get_current_weather().oracle().with_id(
                "o_early_harvest_weather_check"
            ).depends_on(current, delay_seconds=1)
            o_early_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_early_harvest_forecast_check"
            ).depends_on(o_early_weather, delay_seconds=1)
            o_early_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_early_harvest_soil_check"
            ).depends_on(o_early_forecast, delay_seconds=1)
            o_early_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_early_harvest_farm_overview"
            ).depends_on(o_early_soil, delay_seconds=1)
            o_early_range = farm_world.get_ridge_range_state(EARLY_START, EARLY_END).oracle().with_id(
                "o_early_harvest_range_state"
            ).depends_on(o_early_overview, delay_seconds=1)
            o_attach = tractor.attach_implement("harvester").oracle().with_id(
                "o_attach_harvester"
            ).depends_on(o_early_range, delay_seconds=1)
            early_events, o_early_done = apply_hn84_harvest_zone(
                tractor, farm_world, o_attach, "o_early_zone", EARLY_START, EARLY_END
            )
            current = o_early_done
            for day in range(1, 2):
                current = system.advance_time(days=1).oracle().with_id(
                    f"o_wait_mid_harvest_window_advance_day_{day:03d}"
                ).depends_on(current, delay_seconds=1)
                events.append(current)
            o_mid_weather = weather.get_current_weather().oracle().with_id(
                "o_mid_harvest_weather_check"
            ).depends_on(current, delay_seconds=1)
            o_mid_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_mid_harvest_forecast_check"
            ).depends_on(o_mid_weather, delay_seconds=1)
            o_mid_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_mid_harvest_soil_check"
            ).depends_on(o_mid_forecast, delay_seconds=1)
            o_mid_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_mid_harvest_farm_overview"
            ).depends_on(o_mid_soil, delay_seconds=1)
            o_mid_range = farm_world.get_ridge_range_state(MID_START, MID_END).oracle().with_id(
                "o_mid_harvest_range_state"
            ).depends_on(o_mid_overview, delay_seconds=1)
            mid_events, o_mid_done = apply_hn84_harvest_zone(
                tractor, farm_world, o_mid_range, "o_mid_zone", MID_START, MID_END
            )
            current = o_mid_done
            for day in range(1, 15):
                current = system.advance_time(days=1).oracle().with_id(
                    f"o_wait_late_harvest_window_advance_day_{day:03d}"
                ).depends_on(current, delay_seconds=1)
                events.append(current)
            o_late_weather = weather.get_current_weather().oracle().with_id(
                "o_late_harvest_weather_check"
            ).depends_on(current, delay_seconds=1)
            o_late_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_late_harvest_forecast_check"
            ).depends_on(o_late_weather, delay_seconds=1)
            o_late_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_late_harvest_soil_check"
            ).depends_on(o_late_forecast, delay_seconds=1)
            o_late_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_late_harvest_farm_overview"
            ).depends_on(o_late_soil, delay_seconds=1)
            o_late_range = farm_world.get_ridge_range_state(LATE_START, LATE_END).oracle().with_id(
                "o_late_harvest_range_state"
            ).depends_on(o_late_overview, delay_seconds=1)
            late_events, o_late_done = apply_hn84_harvest_zone(
                tractor, farm_world, o_late_range, "o_late_zone", LATE_START, LATE_END
            )
            o_commit = farm_world.commit_daily_physics().oracle().with_id(
                "o_commit_final_harvest_state"
            ).depends_on(o_late_done, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_recheck_stored_grain"
            ).depends_on(o_commit, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成黑农84早/中/晚三区分批收获、卸粮、烘干和入库。"
            ).oracle().with_id("o_report").depends_on(o_inventory, delay_seconds=1)

        self.events = [
            *events,
            o_early_weather,
            o_early_forecast,
            o_early_soil,
            o_early_overview,
            o_early_range,
            o_attach,
            *early_events,
            o_mid_weather,
            o_mid_forecast,
            o_mid_soil,
            o_mid_overview,
            o_mid_range,
            *mid_events,
            o_late_weather,
            o_late_forecast,
            o_late_soil,
            o_late_overview,
            o_late_range,
            *late_events,
            o_commit,
            o_inventory,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HN84 staggered harvest-sequence L2 split")
