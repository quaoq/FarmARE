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
                "准备诊断黑河43 VC期早期营养弱势。重点关注8-19垄，并用24-35垄作为健康参照；"
                "目标是确认偏弱是否主要由营养不足造成。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气和3天天气预报，确认是否存在可执行水肥恢复的窗口。\n"
                "2. 读取土壤传感器和冠层传感器，先排除明显的全田性缺水或冠层异常。\n"
                "3. 查看全田概览，并用无人机巡查0-63垄，定位早期长势差异。\n"
                "4. 读取目标区和参照区状态，比较stand、长势和胁迫指标。\n"
                "5. 用地面机器人检查目标区，确认主要风险是营养不足，而不是草害、病虫害或单纯缺水。\n"
                "6. 等待到水肥恢复窗口后，重新查看天气和目标区状态。\n"
                "7. 查看库存和投入品条件。\n"
                "8. 证据支持时只对目标区执行一次小水量水肥恢复。\n"
                "9. 完成后复查目标区状态。\n"
                "10. 向我汇报营养诊断依据、处理范围、资源消耗，以及该动作如何保护早期产量潜力。"
            )
        else:
            briefing_text = "请诊断黑河43 VC期8-19垄早期营养弱势；证据支持时定向水肥恢复并复查。"

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
