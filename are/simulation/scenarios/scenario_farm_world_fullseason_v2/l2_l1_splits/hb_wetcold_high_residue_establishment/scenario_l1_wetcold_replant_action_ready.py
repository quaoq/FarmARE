from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, RobotApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_wetcold_high_residue_split_common import (
    CHECKPOINT_BEFORE_REPLANT,
    SOURCE_L3_SCENARIO_ID,
    apply_replant_blocks,
    checkpoint_sim_time,
    populate_hb_wetcold_apps,
    restore_hb_wetcold_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_hb_wetcold_high_residue_replant_action_ready"


@register_scenario(SCENARIO_ID)
class ScenarioL1HBWetcoldHighResidueReplantActionReady(Scenario):
    """L1 split: action-ready replanting for ridges 0-15."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_REPLANT)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_REPLANT
    source_checkpoint_date = "2026-06-04"
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_wetcold_apps(self)
        restore_hb_wetcold_checkpoint(self, CHECKPOINT_BEFORE_REPLANT)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "本L1从机器人确认后的补种前 checkpoint 开始。"
                "请重新复核天气、土壤、0-15目标区状态、机器人/拖拉机状态和种子库存，"
                "然后对 0-15 执行黑农84补种并复查。不要扩大到参考区。"
            )
        else:
            briefing_text = "请复核补种前状态后，对0-15执行补种并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_weather_before_replant"
            ).depends_on(briefing, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_soil_before_replant"
            ).depends_on(o_weather, delay_seconds=1)
            o_range = farm_world.get_ridge_range_state(0, 15).oracle().with_id(
                "o_target_range_before_replant"
            ).depends_on(o_soil, delay_seconds=1)
            o_ground = robot.inspect_emergence(0, 15).oracle().with_id(
                "o_ground_reconfirm_before_replant"
            ).depends_on(o_range, delay_seconds=2)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_seed_inventory_before_replant"
            ).depends_on(o_ground, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id(
                "o_tractor_before_replant"
            ).depends_on(o_inventory, delay_seconds=1)
            replant_events, o_after_replant = apply_replant_blocks(
                tractor, o_tractor, "o_replant_slow_emergence_0_15"
            )
            o_recheck = farm_world.get_ridge_range_state(0, 15).oracle().with_id(
                "o_recheck_replanted_range"
            ).depends_on(o_after_replant, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成0-15补种动作和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=1)

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_range,
            o_ground,
            o_inventory,
            o_tractor,
            *replant_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "wet-cold high-residue replant L1")

