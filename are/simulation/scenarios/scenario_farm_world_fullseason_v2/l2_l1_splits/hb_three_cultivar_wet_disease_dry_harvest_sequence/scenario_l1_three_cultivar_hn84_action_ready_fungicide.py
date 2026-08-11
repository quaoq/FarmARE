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
                "任务：承接三品种田块中HEINONG84分区的杀菌剂 action-ready 状态。"
                "本田块有三个可见管理分区：HEIHE50早熟分区为0-20垄，HEINONG84分区为21-42垄，HEINONG58分区为43-63垄。"
                "截至2026-07-09，前期雨天巡查、等待窗口和初步病害定位已经完成；现在只需要做喷药前最小复核并闭环执行。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气，确认无雨、风速适合喷药。\n"
                "2. 读取土壤传感器，确认田间可通行；读取冠层传感器，确认HEINONG84区仍有异常冠层信号。\n"
                "3. 查看21-42垄状态，并用无人机巡查HEINONG84区，确认病害压力仍集中在该分区。\n"
                "4. 用地面机器人检查21-42垄，确认症状符合病害处理，而不是虫害、水分或肥力问题主导。\n"
                "5. 查看库存和喷雾设备状态，确认杀菌剂和设备都能支持本次作业。\n"
                "6. 证据和资源都支持时，装载约85.3 L杀菌剂，并对21-42垄分块完成一次定向杀菌。\n"
                "7. 喷药后复查HEINONG84区状态，并向我报告已完成复核、定向杀菌和复查。"
            )
        else:
            briefing_text = (
                "请复核HEINONG84分区的喷药条件和病害证据，完成一次定向杀菌并复查。"
            )

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
                aui.send_message_to_user(
                    content="已完成HEINONG84区喷药前复核、定向杀菌和处理后复查。"
                )
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
