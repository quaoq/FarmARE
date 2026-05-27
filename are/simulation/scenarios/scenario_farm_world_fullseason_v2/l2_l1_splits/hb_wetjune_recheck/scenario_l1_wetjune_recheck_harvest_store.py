from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    harvest_range,
)
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._wetjune_recheck_common import (
    CHECKPOINT_HARVEST_READY,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_wetjune_recheck_apps,
    restore_wetjune_recheck_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_wetjune_recheck_harvest_store"


@register_scenario(SCENARIO_ID)
class ScenarioL1WetjuneRecheckHarvestStore(Scenario):
    """L1 split: execute the harvest and safe-storage sequence for a ready field."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_HARVEST_READY)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_HARVEST_READY
    source_checkpoint_date = "2026-09-02"
    source_dap = 121
    source_growth_stage = "R8_FULL_MATURITY"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_wetjune_recheck_apps(self)
        restore_wetjune_recheck_checkpoint(self, CHECKPOINT_HARVEST_READY)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "前序检查已经把本田块推进到可收获窗口。"
                "这个L1只考察收获到安全入库的执行顺序：先快速复核天气、全田R8/籽粒水分和库存容量。"
                "若条件支持，按粮箱容量分批收获并及时卸粮；水分高于安全入库目标时，必须先烘干再入库。"
                "不要跳过卸粮，不要把高水分粮直接入库，也不要在任何收获段失败后继续烘干或入库。"
            )
        else:
            briefing_text = "请复核可收条件，然后按收获、卸粮、烘干、入库顺序完成安全入库。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_confirm_harvest_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(0, 63)
                .oracle()
                .with_id("o_confirm_whole_field_ready")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_confirm_storage_capacity")
                .depends_on(o_state, delay_seconds=1)
            )
            o_harvest_done = harvest_range(
                tractor,
                farm_world,
                o_inventory,
                start_ridge=0,
                end_ridge=63,
                id_prefix="o_whole_field",
                dry_after_harvest=True,
            )
            o_report = (
                aui.send_message_to_user(content="已完成全田收获、卸粮、烘干和安全入库。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_harvest_done, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_state,
            o_inventory,
            o_harvest_done,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 harvest-store split"
        )
