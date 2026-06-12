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

from ._hb_fertilizer_quota_edge_lowfertility_split_common import (
    CHECKPOINT_AFTER_EMERGENCE_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    checkpoint_sim_time,
    populate_hb_fertilizer_quota_apps,
    restore_hb_fertilizer_quota_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_fertilizer_quota_severe_edge_nutrient_recovery"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBFertilizerQuotaSevereEdgeNutrientRecovery(Scenario):
    """L2 split: diagnose severe edge nutrition and spend quota on the highest priority block."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_EMERGENCE_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_EMERGENCE_ROUTINE
    source_checkpoint_date = "2026-05-21"
    source_dap = 17
    source_growth_stage = "PLANTED_PRE_EMERGENCE"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_fertilizer_quota_apps(self)
        restore_hb_fertilizer_quota_checkpoint(
            self, CHECKPOINT_AFTER_EMERGENCE_ROUTINE
        )

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
                "准备诊断黑农84早期边缘弱苗和低肥力恢复。全田已播种，肥料配额有限，"
                "目标是只处理证据最强的重度边缘，不要把配额平均撒到全田。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气和3天天气预报，确认近期有可执行水肥恢复的窗口。\n"
                "2. 读取土壤传感器和冠层传感器，先排除明显的全田性水分或冠层异常。\n"
                "3. 查看全田概览，确认当前处于早期出苗/苗期恢复判断阶段。\n"
                "4. 用无人机完成0-63垄巡查，定位边缘弱势程度和健康参照区差异。\n"
                "5. 分别读取重度边缘和健康参照区状态，比较stand、长势和胁迫指标。\n"
                "6. 用地面机器人检查重度边缘，确认主要风险是低肥力，而不是草害、病虫害或单纯缺水。\n"
                "7. 等待到水肥恢复窗口后，重新查看天气和重度边缘状态。\n"
                "8. 查看库存和肥料配额，确认只够优先处理最高风险区域。\n"
                "9. 对重度边缘执行一次小水量水肥恢复，水肥量要保持在配额内。\n"
                "10. 复查重度边缘状态和剩余肥料配额。\n"
                "11. 向我汇报为何优先重度边缘、处理范围、配额消耗和对产量潜力的保护。"
            )
        else:
            briefing_text = "请诊断边缘弱苗成因；证据支持低肥力时优先处理重度边缘并复查肥料配额。"

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
                "o_check_soil_context"
            ).depends_on(o_forecast, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id(
                "o_check_canopy_context"
            ).depends_on(o_soil, delay_seconds=1)
            o_overview = farm_world.get_farm_overview().oracle().with_id(
                "o_check_whole_field_overview"
            ).depends_on(o_canopy, delay_seconds=1)
            o_survey = mavic.fly_survey(0, 63).oracle().with_id(
                "o_map_edge_vigor"
            ).depends_on(o_overview, delay_seconds=2)
            o_target = farm_world.get_ridge_range_state(0, 7).oracle().with_id(
                "o_read_severe_edge_state"
            ).depends_on(o_survey, delay_seconds=1)
            o_reference = farm_world.get_ridge_range_state(20, 31).oracle().with_id(
                "o_read_healthy_reference_state"
            ).depends_on(o_target, delay_seconds=1)
            o_ground = robot.inspect_crop_health(0, 7).oracle().with_id(
                "o_ground_confirm_severe_edge_low_fertility"
            ).depends_on(o_reference, delay_seconds=2)
            o_wait = system.advance_time(days=3).oracle().with_id(
                "o_wait_to_fertigation_window"
            ).depends_on(o_ground, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_fertigation_weather"
            ).depends_on(o_wait, delay_seconds=1)
            o_state_ready = farm_world.get_ridge_range_state(0, 7).oracle().with_id(
                "o_recheck_severe_edge_before_fertigation"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_fertilizer_quota_before_recovery"
            ).depends_on(o_state_ready, delay_seconds=1)
            o_fertigate = farm_world.apply_fertigation(0, 7, 0.32, 1.2).oracle().with_id(
                "o_apply_severe_edge_fertigation_0_7"
            ).depends_on(o_inventory, delay_seconds=2)
            o_recheck = farm_world.get_ridge_range_state(0, 7).oracle().with_id(
                "o_recheck_severe_edge_after_fertigation"
            ).depends_on(o_fertigate, delay_seconds=2)
            o_budget = farm_world.get_inventory().oracle().with_id(
                "o_recheck_fertilizer_quota_after_recovery"
            ).depends_on(o_recheck, delay_seconds=1)
            o_report = aui.send_message_to_user(
                content="已完成重度边缘低肥力诊断、水肥恢复和配额复查。"
            ).oracle().with_id("o_report").depends_on(o_budget, delay_seconds=2)

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
            o_budget,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "fertilizer quota severe-edge nutrient L2 split"
        )
