from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._wetjune_recheck_common import (
    CHECKPOINT_AFTER_R5_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    apply_disease_block_fungicide_events,
    checkpoint_sim_time,
    populate_wetjune_recheck_apps,
    restore_wetjune_recheck_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_wetjune_recheck_r5_fungicide"


@register_scenario(SCENARIO_ID)
class ScenarioL1WetjuneRecheckR5Fungicide(Scenario):
    """L1 split: execute the R5 second fungicide pass after recheck evidence."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_R5_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_R5_ROUTINE
    source_checkpoint_date = "2026-07-26"
    source_dap = 83
    source_growth_stage = "R5_BEGINNING_SEED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_wetjune_recheck_apps(self)
        restore_wetjune_recheck_checkpoint(self, CHECKPOINT_AFTER_R5_ROUTINE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "前序R5复查已提示同一湿六月病害区仍有处理价值。"
                "这个L1只考察第二次低剂量杀菌作业：先复核天气、目标区状态、健康参照区、库存和喷雾设备。"
                "若条件支持，就对已确认的病害区完成一次定向杀菌；不要把R5自然叶色、虫害或水分问题当作杀菌理由。"
                "不要扩展到健康参照区。若任一喷药段失败，应停止并处理失败原因。"
            )
        else:
            briefing_text = "请复核R5喷药条件，并只对已确认仍有病害压力的区域完成第二次定向杀菌。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_confirm_r5_spray_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(20, 43)
                .oracle()
                .with_id("o_confirm_r5_disease_block")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_reference = (
                farm_world.get_ridge_range_state(0, 15)
                .oracle()
                .with_id("o_confirm_r5_reference_block")
                .depends_on(o_state, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_confirm_r5_fungicide_inventory")
                .depends_on(o_reference, delay_seconds=1)
            )
            o_status = (
                tractor.get_status()
                .oracle()
                .with_id("o_confirm_r5_sprayer_status")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_load = (
                tractor.load_fungicide(70.0)
                .oracle()
                .with_id("o_load_r5_fungicide_for_marked_block")
                .depends_on(o_status, delay_seconds=2)
            )
            spray_events, o_after_spray = apply_disease_block_fungicide_events(
                tractor,
                o_load,
                "o_apply_r5_fungicide_marked_block",
                liters_per_ridge=2.8,
            )
            o_report = (
                aui.send_message_to_user(content="已完成R5病害区第二次定向杀菌。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_after_spray, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_state,
            o_reference,
            o_inventory,
            o_status,
            o_load,
            *spray_events,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 R5 fungicide split"
        )
