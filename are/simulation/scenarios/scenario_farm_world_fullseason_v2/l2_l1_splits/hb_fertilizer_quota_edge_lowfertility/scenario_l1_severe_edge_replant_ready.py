from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_fertilizer_quota_edge_lowfertility_split_common import (
    CHECKPOINT_BEFORE_SEVERE_EDGE_REPLANT,
    SOURCE_L3_SCENARIO_ID,
    apply_heilong84_replant_block,
    checkpoint_sim_time,
    populate_hb_fertilizer_quota_apps,
    restore_hb_fertilizer_quota_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_fertilizer_quota_severe_edge_replant_ready"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBFertilizerQuotaSevereEdgeReplantReady(Scenario):
    """L1 split: execute a confirmed small replant pass on the severe edge."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_SEVERE_EDGE_REPLANT)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_SEVERE_EDGE_REPLANT
    source_checkpoint_date = "2026-05-30"
    source_dap = 26
    source_growth_stage = "V1"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_fertilizer_quota_apps(self)
        restore_hb_fertilizer_quota_checkpoint(
            self, CHECKPOINT_BEFORE_SEVERE_EDGE_REPLANT
        )

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-30，重度边缘缺株补苗范围已经地面确认，当前是补苗执行前复核。"
                "请复核天气、土壤温度/水分、目标小块stand、种子余量和播种机状态；"
                "若条件支持，只对已确认缺株小块补苗，随后复查stand和种子使用。"
            )
        else:
            briefing_text = "请完成重度边缘补苗前复核，窗口合适时执行已确认小块补苗并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_recheck_replant_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_recheck_seedbed"
            ).depends_on(o_weather, delay_seconds=1)
            o_state = farm_world.get_ridge_range_state(0, 3).oracle().with_id(
                "o_recheck_confirmed_gap_block"
            ).depends_on(o_soil, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_seed_before_replant"
            ).depends_on(o_state, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_check_planter_status"
            ).depends_on(o_inventory, delay_seconds=1)
            replant_events, o_after_replant = apply_heilong84_replant_block(
                tractor, o_status, "o_apply_gap_replant"
            )
            o_recheck = farm_world.get_ridge_range_state(0, 3).oracle().with_id(
                "o_recheck_gap_block_after_replant"
            ).depends_on(o_after_replant, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成确认小块补苗和stand复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_state,
            o_inventory,
            o_status,
            *replant_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "fertilizer quota severe-edge replant L1 split"
        )
