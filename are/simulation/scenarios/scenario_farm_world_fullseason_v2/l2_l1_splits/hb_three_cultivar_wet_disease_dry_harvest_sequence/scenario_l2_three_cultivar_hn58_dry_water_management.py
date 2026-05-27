from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    FieldOpsApp,
    RobotApp,
    SensorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_three_cultivar_split_common import (
    CHECKPOINT_AFTER_R5_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    cst_timestamp,
    populate_three_cultivar_apps,
    restore_three_cultivar_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_three_cultivar_hn58_dry_water_management"


@register_scenario(SCENARIO_ID)
class ScenarioL2ThreeCultivarHN58DryWaterManagement(Scenario):
    """L2 split: diagnose late dry stress, irrigate HEINONG58, and recheck."""

    start_time: float | None = cst_timestamp(2026, 8, 2, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_R5_ROUTINE
    source_checkpoint_date = "2026-08-02"
    source_dap = 89
    source_growth_stage = "R5_BEGINNING_SEED/R6_FULL_SEED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_three_cultivar_apps(self)
        restore_three_cultivar_checkpoint(self, CHECKPOINT_AFTER_R5_ROUTINE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        robot = self.get_typed_app(RobotApp, "Robot0")
        farm_world = self.get_typed_app(FarmWorldApp)
        field_ops = self.get_typed_app(FieldOpsApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "已知本田块按品种分为三个可见管理分区：HEIHE50早熟分区为ridges 0-20，HEINONG84分区为ridges 21-42，HEINONG58分区为ridges 43-63。"
                "截至2026-08-02，田块处于R5/R6分区水分复查阶段。"
                "后期转干风险主要在HEINONG58区，但必须先从天气、土壤、canopy、全田概览和分区状态建立证据链。"
                "等待后期干旱窗口后，用多光谱和热成像定位水分胁迫，再由地面机器人确认不是病虫草或肥力问题。"
                "若目标区水分胁迫和灌溉配额都支持，只对43-63垄灌溉0.8小时，随后等待响应并复查。"
            )
        else:
            briefing_text = "请完成HEINONG58区干旱诊断、精准灌溉和复查，不要扩大到其他分区。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_check_current_dry_window")
                .depends_on(briefing, delay_seconds=1)
            )
            o_forecast = (
                weather.get_forecast(days=3)
                .oracle()
                .with_id("o_check_dry_window_forecast")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_check_soil_water_status")
                .depends_on(o_forecast, delay_seconds=1)
            )
            o_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_check_canopy_water_signal")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_overview = (
                farm_world.get_farm_overview()
                .oracle()
                .with_id("o_check_three_zone_overview")
                .depends_on(o_canopy, delay_seconds=1)
            )
            o_wait = (
                system.advance_time(days=4)
                .oracle()
                .with_id("o_wait_to_source_l3_irrigation_window")
                .depends_on(o_overview, delay_seconds=1)
            )
            o_weather_ready = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_recheck_irrigation_weather")
                .depends_on(o_wait, delay_seconds=1)
            )
            o_soil_ready = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_recheck_soil_water_deficit")
                .depends_on(o_weather_ready, delay_seconds=1)
            )
            o_canopy_ready = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_recheck_canopy_water_signal")
                .depends_on(o_soil_ready, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(43, 63)
                .oracle()
                .with_id("o_read_hn58_target_state")
                .depends_on(o_canopy_ready, delay_seconds=1)
            )
            o_ndvi = (
                mavic.fly_survey(43, 63)
                .oracle()
                .with_id("o_map_hn58_canopy")
                .depends_on(o_state, delay_seconds=2)
            )
            o_thermal = (
                matrice.fly_survey(43, 63)
                .oracle()
                .with_id("o_map_hn58_thermal_stress")
                .depends_on(o_ndvi, delay_seconds=2)
            )
            o_ground = (
                robot.inspect_crop_health(43, 63)
                .oracle()
                .with_id("o_ground_confirm_hn58_water_stress")
                .depends_on(o_thermal, delay_seconds=2)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_check_irrigation_quota")
                .depends_on(o_ground, delay_seconds=1)
            )
            o_irrigate = (
                field_ops.irrigate(43, 63, 0.8)
                .oracle()
                .with_id("o_irrigate_hn58_43_63")
                .depends_on(o_inventory, delay_seconds=2)
            )
            o_wait_response = (
                system.advance_time(hours=6)
                .oracle()
                .with_id("o_wait_irrigation_response")
                .depends_on(o_irrigate, delay_seconds=1)
            )
            o_recheck = (
                farm_world.get_ridge_range_state(43, 63)
                .oracle()
                .with_id("o_recheck_hn58_after_irrigation")
                .depends_on(o_wait_response, delay_seconds=1)
            )
            o_report = (
                aui.send_message_to_user(content="已完成HEINONG58区干旱诊断、精准灌溉和复查。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_canopy,
            o_overview,
            o_wait,
            o_weather_ready,
            o_soil_ready,
            o_canopy_ready,
            o_state,
            o_ndvi,
            o_thermal,
            o_ground,
            o_inventory,
            o_irrigate,
            o_wait_response,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 HEINONG58 water-management split"
        )
