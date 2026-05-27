from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._wetjune_recheck_common import (
    CHECKPOINT_BEFORE_MID_FUNGICIDE,
    SOURCE_L3_SCENARIO_ID,
    apply_disease_block_fungicide_events,
    checkpoint_sim_time,
    populate_wetjune_recheck_apps,
    restore_wetjune_recheck_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_wetjune_recheck_apply_fungicide"


@register_scenario(SCENARIO_ID)
class ScenarioL1WetjuneRecheckApplyFungicide(Scenario):
    """L1 split: execute the confirmed wet-June fungicide operation."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_BEFORE_MID_FUNGICIDE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_MID_FUNGICIDE
    source_checkpoint_date = "2026-07-10"
    source_dap = 67
    source_growth_stage = "R1_BEGINNING_BLOOM"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_wetjune_recheck_apps(self)
        restore_wetjune_recheck_checkpoint(self, CHECKPOINT_BEFORE_MID_FUNGICIDE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-07-10，湿六月后的病害区进入喷药前复核窗口，但仍需你重新确认作业证据。"
                "先复核当前天气、土壤传感器、canopy传感器和目标区ridge状态，再用无人机重新查看冠层异常，并用地面机器人确认叶片症状和病害压力。"
                "同时检查健康参照区，确认这不是水分、肥力或虫害主导的问题。"
                "若天气、库存和设备均支持作业，就对确认的病害区完成定向杀菌；不要扩展到健康参照区，也不要用水肥或杀虫剂替代杀菌剂。"
                "喷药返回失败时先停止并处理失败原因。完成后向用户汇报。"
            )
        else:
            briefing_text = "请在喷药前窗口重新复核天气、土壤、canopy、无人机和地面证据，然后只对确认的湿六月病害区定向杀菌。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_confirm_spray_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_confirm_soil_status")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_confirm_canopy_status")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(20, 43)
                .oracle()
                .with_id("o_confirm_marked_disease_block")
                .depends_on(o_canopy, delay_seconds=1)
            )
            o_survey = (
                mavic.fly_survey(20, 43)
                .oracle()
                .with_id("o_recheck_marked_block_ndvi")
                .depends_on(o_state, delay_seconds=2)
            )
            o_ground = (
                robot.inspect_crop_health(20, 43)
                .oracle()
                .with_id("o_ground_confirm_marked_block")
                .depends_on(o_survey, delay_seconds=2)
            )
            o_reference = (
                farm_world.get_ridge_range_state(0, 15)
                .oracle()
                .with_id("o_confirm_reference_block_not_target")
                .depends_on(o_ground, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_confirm_fungicide_inventory")
                .depends_on(o_reference, delay_seconds=1)
            )
            o_status = (
                tractor.get_status()
                .oracle()
                .with_id("o_confirm_sprayer_status")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_load = (
                tractor.load_fungicide(95.0)
                .oracle()
                .with_id("o_load_fungicide_for_marked_block")
                .depends_on(o_status, delay_seconds=2)
            )
            spray_events, o_after_spray = apply_disease_block_fungicide_events(
                tractor, o_load, "o_apply_fungicide_marked_block"
            )
            o_report = (
                aui.send_message_to_user(content="已完成湿六月病害区定向杀菌。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_after_spray, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_canopy,
            o_state,
            o_survey,
            o_ground,
            o_reference,
            o_inventory,
            o_status,
            o_load,
            *spray_events,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 wet-June fungicide split"
        )
