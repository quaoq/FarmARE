from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    RobotApp,
    SensorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_heihe43_split_common import (
    CHECKPOINT_AFTER_EMERGENCE_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    cst_timestamp,
    populate_hb_heihe43_apps,
    restore_hb_heihe43_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_heihe43_nutrient_recovery"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBHeihe43NutrientRecovery(Scenario):
    """L2 split: diagnose and recover the early HEIHE43 nutrient-weak block."""

    start_time: float | None = cst_timestamp(2026, 5, 21, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_EMERGENCE_ROUTINE
    source_checkpoint_date = "2026-05-21"
    source_dap = 17
    source_growth_stage = "VC"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_heihe43_apps(self)
        restore_hb_heihe43_checkpoint(self, CHECKPOINT_AFTER_EMERGENCE_ROUTINE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-21，黑河43全田已出苗并处于VC期，早期巡查看到局部长势偏弱。"
                "请用天气、土壤水分、stand/密度、canopy、无人机NDVI和地面检查判断偏弱区域是否主要由营养不足造成，"
                "并与健康参照区对比，排除缺水、草害、病虫害主导。"
                "若证据支持营养弱势，等待合适作业窗口后只对确认区域做小水量水肥恢复，并复查该区域、参照区和投入品使用。"
            )
        else:
            briefing_text = "请诊断黑河43早期长势偏弱原因；证据支持营养不足时执行定向水肥恢复并复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_recovery_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_soil_and_water_context"
            ).depends_on(o_forecast, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_check_canopy_context"
            ).depends_on(o_soil, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_check_whole_field_overview"
            ).depends_on(o_canopy, delay_seconds=1)
            o_survey = mavic.fly_survey(0, 63).oracle().with_id(
                "o_map_early_growth_variability"
            ).depends_on(o_overview, delay_seconds=2)
            o_target = farm_world.get_ridge_range_state(8, 19).oracle().with_id(
                "o_read_lower_vigor_block"
            ).depends_on(o_survey, delay_seconds=1)
            o_reference = farm_world.get_ridge_range_state(24, 35).oracle().with_id(
                "o_read_reference_block"
            ).depends_on(o_target, delay_seconds=1)
            o_ground = robot.inspect_crop_health(8, 19).oracle().with_id(
                "o_ground_confirm_nutrient_stress"
            ).depends_on(o_reference, delay_seconds=2)
            o_wait = system.advance_time(days=4).oracle().with_id(
                "o_wait_to_fertigation_window"
            ).depends_on(o_ground, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_fertigation_weather"
            ).depends_on(o_wait, delay_seconds=1)
            o_state_ready = farm_world.get_ridge_range_state(8, 19).oracle().with_id(
                "o_recheck_nutrient_block_before_fertigation"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_fertigation_inputs"
            ).depends_on(o_state_ready, delay_seconds=1)
            o_fertigate = farm_world.apply_fertigation(8, 19, 0.24, 1.2).oracle().with_id(
                "o_apply_targeted_fertigation_8_19"
            ).depends_on(o_inventory, delay_seconds=2)
            o_recheck = farm_world.get_ridge_range_state(8, 19).oracle().with_id(
                "o_recheck_nutrient_block_after_recovery"
            ).depends_on(o_fertigate, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成营养弱势区域诊断、水肥恢复和复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_canopy,
            o_overview,
            o_survey,
            o_target,
            o_reference,
            o_ground,
            o_wait,
            o_weather_ready,
            o_state_ready,
            o_inventory,
            o_fertigate,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, "HEIHE43 nutrient recovery L2 split")

