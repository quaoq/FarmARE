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
from are.simulation.apps.system import SystemApp
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

SCENARIO_ID = "scenario_l2_wetjune_recheck_r5_respray"


@register_scenario(SCENARIO_ID)
class ScenarioL2WetjuneRecheckR5Respray(Scenario):
    """L2 split: R5 disease recheck, second fungicide decision, and response check."""

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
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "同一黑农84田块进入R5始粒期复查窗口。"
                "前期湿六月病害已经处理过一次，但连续湿度和结荚后冠层郁闭可能让病害重新发展。"
                "先检查天气、全田概览和传感器，再用无人机重新定位异常冠层；不要把自然R5叶色变化直接当作病害。"
                "对异常区做地面机器人复查，并与健康参照区比较病斑、病害压力和虫害压力。"
                "如果复查证据支持病害仍在上升，等到合适喷药窗口，确认库存和喷雾设备后做第二次低剂量定向杀菌。"
                "处理后复查目标区，确认病害压力下降或至少被压住。"
            )
        else:
            briefing_text = "R5复查发现湿六月病害可能再发展。请观察、确认、必要时第二次定向杀菌，并复查。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_check_r5_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_overview = (
                farm_world.get_farm_overview()
                .oracle()
                .with_id("o_check_r5_field_overview")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_check_r5_soil_context")
                .depends_on(o_overview, delay_seconds=1)
            )
            o_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_check_r5_canopy_context")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_survey = (
                mavic.fly_survey(0, 63)
                .oracle()
                .with_id("o_map_r5_canopy_anomaly")
                .depends_on(o_canopy, delay_seconds=2)
            )
            o_block = (
                farm_world.get_ridge_range_state(20, 43)
                .oracle()
                .with_id("o_read_r5_anomaly_block")
                .depends_on(o_survey, delay_seconds=1)
            )
            o_reference = (
                farm_world.get_ridge_range_state(0, 15)
                .oracle()
                .with_id("o_read_r5_reference_block")
                .depends_on(o_block, delay_seconds=1)
            )
            o_ground = (
                robot.inspect_crop_health(20, 43)
                .oracle()
                .with_id("o_ground_confirm_r5_disease")
                .depends_on(o_reference, delay_seconds=2)
            )
            o_wait = (
                system.advance_time(days=3)
                .oracle()
                .with_id("o_wait_to_r5_spray_window")
                .depends_on(o_ground, delay_seconds=1)
            )
            o_weather_ready = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_recheck_r5_spray_weather")
                .depends_on(o_wait, delay_seconds=1)
            )
            o_state_ready = (
                farm_world.get_ridge_range_state(20, 43)
                .oracle()
                .with_id("o_recheck_r5_block_before_spray")
                .depends_on(o_weather_ready, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_check_r5_fungicide_inventory")
                .depends_on(o_state_ready, delay_seconds=1)
            )
            o_status = (
                tractor.get_status()
                .oracle()
                .with_id("o_check_r5_sprayer_status")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_load = (
                tractor.load_fungicide(70.0)
                .oracle()
                .with_id("o_load_r5_fungicide")
                .depends_on(o_status, delay_seconds=2)
            )
            spray_events, o_after_spray = apply_disease_block_fungicide_events(
                tractor,
                o_load,
                "o_apply_r5_fungicide_block",
                liters_per_ridge=2.8,
            )
            o_recheck = (
                farm_world.get_ridge_range_state(20, 43)
                .oracle()
                .with_id("o_recheck_r5_block_after_spray")
                .depends_on(o_after_spray, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(
                    content="已完成R5病害复查、第二次定向杀菌和复查。"
                )
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_overview,
            o_soil,
            o_canopy,
            o_survey,
            o_block,
            o_reference,
            o_ground,
            o_wait,
            o_weather_ready,
            o_state_ready,
            o_inventory,
            o_status,
            o_load,
            *spray_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 R5 wet-June disease recheck split"
        )
