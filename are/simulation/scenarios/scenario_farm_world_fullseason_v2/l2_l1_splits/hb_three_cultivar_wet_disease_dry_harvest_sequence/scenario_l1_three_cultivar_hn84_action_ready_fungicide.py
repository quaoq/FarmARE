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

from ._hb_three_cultivar_split_common import (
    CHECKPOINT_BEFORE_MID_FUNGICIDE,
    SOURCE_L3_SCENARIO_ID,
    apply_hn84_fungicide_blocks,
    cst_timestamp,
    populate_three_cultivar_apps,
    restore_three_cultivar_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_three_cultivar_hn84_action_ready_fungicide"


@register_scenario(SCENARIO_ID)
class ScenarioL1ThreeCultivarHN84ActionReadyFungicide(Scenario):
    """L1 split: execute the confirmed HEINONG84 fungicide operation."""

    start_time: float | None = cst_timestamp(2026, 7, 9, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_MID_FUNGICIDE
    source_checkpoint_date = "2026-07-09"
    source_dap = 65
    source_growth_stage = "R1_BEGINNING_BLOOM"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_three_cultivar_apps(self)
        restore_three_cultivar_checkpoint(self, CHECKPOINT_BEFORE_MID_FUNGICIDE)

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
                "已知本田块按品种分为三个可见管理分区：HEIHE50早熟分区为ridges 0-20，HEINONG84分区为ridges 21-42，HEINONG58分区为ridges 43-63。"
                "截至2026-07-09，HEINONG84分区进入喷药前复核状态："
                "7月4日雨天例行巡查和5天等待窗口已经完成，当前仍需做最小复核。"
                "请确认当前无雨、风速可喷、目标HEINONG84区病害证据仍存在，并复核药剂库存和喷雾设备。"
                "若证据和资源支持，只对HEINONG84目标区定向杀菌；不要扩展到HEIHE50或HEINONG58区。"
                "如果喷药工具返回失败，停止后续操作并报告失败原因。"
            )
        else:
            briefing_text = "请在HEINONG84喷药前窗口做最小复核，然后定向杀菌。"

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
                .with_id("o_confirm_soil_trafficability")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_confirm_canopy_signal")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(21, 42)
                .oracle()
                .with_id("o_confirm_hn84_target_state")
                .depends_on(o_canopy, delay_seconds=1)
            )
            o_survey = (
                mavic.fly_survey(21, 42)
                .oracle()
                .with_id("o_confirm_hn84_ndvi_map")
                .depends_on(o_state, delay_seconds=2)
            )
            o_ground = (
                robot.inspect_crop_health(21, 42)
                .oracle()
                .with_id("o_confirm_hn84_ground_symptoms")
                .depends_on(o_survey, delay_seconds=2)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_confirm_fungicide_inventory")
                .depends_on(o_ground, delay_seconds=1)
            )
            o_status = (
                tractor.get_status()
                .oracle()
                .with_id("o_confirm_sprayer_status")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_load = (
                tractor.load_fungicide(85.3)
                .oracle()
                .with_id("o_load_hn84_fungicide")
                .depends_on(o_status, delay_seconds=1)
            )
            spray_events, o_after_spray = apply_hn84_fungicide_blocks(
                tractor, o_load, "o_apply_hn84_fungicide"
            )
            o_recheck = (
                farm_world.get_ridge_range_state(21, 42)
                .oracle()
                .with_id("o_recheck_hn84_after_fungicide")
                .depends_on(o_after_spray, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(content="已完成HEINONG84区定向杀菌和复核。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_canopy,
            o_state,
            o_survey,
            o_ground,
            o_inventory,
            o_status,
            o_load,
            *spray_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 HEINONG84 fungicide split"
        )
