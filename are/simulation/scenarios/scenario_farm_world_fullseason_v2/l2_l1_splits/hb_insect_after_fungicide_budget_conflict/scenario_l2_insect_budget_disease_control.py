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

from ._hb_insect_after_fungicide_budget_conflict_split_common import (
    CHECKPOINT_AFTER_MID_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    apply_l3_fungicide_blocks,
    checkpoint_sim_time,
    populate_hb_insect_budget_apps,
    restore_hb_insect_budget_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_insect_budget_disease_control"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBInsectBudgetDiseaseControl(Scenario):
    """L2 split: confirm the disease block, spray, and recheck while preserving budget."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_MID_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_MID_ROUTINE
    source_checkpoint_date = "2026-07-04"
    source_dap = 61
    source_growth_stage = "R1_BEGINNING_BLOOM"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_insect_budget_apps(self)
        restore_hb_insect_budget_checkpoint(self, CHECKPOINT_AFTER_MID_ROUTINE)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-07-04，黑农84田块处于R1初花期，且有总有效成分预算上限。"
                "例行巡查已经看到中部冠层异常，但还不能直接把预算花光。"
                "请先复核天气、短期预报、土壤和canopy概览，再用无人机定位异常区，并用地面机器人确认叶片病斑/病害压力。"
                "同时对健康参照区做对照，确认不是水、肥、草或虫害主导。"
                "若证据支持病害且喷药窗口合适，等待合适窗口后只对确认病害区定向杀菌，并保留后续虫害可能需要的药剂预算。"
                "喷后复查目标区和库存/预算，不要扩展处理到参照区。"
            )
        else:
            briefing_text = (
                "R1初花期中部冠层异常且喷药预算有限。请完成观察、定位、地面诊断、定向杀菌、预算复查和目标区复查。"
            )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_spray_forecast"
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
                "o_map_midseason_canopy_anomaly"
            ).depends_on(o_overview, delay_seconds=2)
            o_target = farm_world.get_ridge_range_state(18, 39).oracle().with_id(
                "o_read_disease_block_state"
            ).depends_on(o_survey, delay_seconds=1)
            o_reference = farm_world.get_ridge_range_state(0, 15).oracle().with_id(
                "o_read_reference_block_state"
            ).depends_on(o_target, delay_seconds=1)
            o_ground = robot.inspect_crop_health(18, 39).oracle().with_id(
                "o_ground_confirm_disease_block"
            ).depends_on(o_reference, delay_seconds=2)
            o_wait = system.advance_time(days=6).oracle().with_id(
                "o_wait_to_l3_spray_window"
            ).depends_on(o_ground, delay_seconds=1)
            o_weather_ready = weather.get_current_weather().oracle().with_id(
                "o_recheck_spray_weather"
            ).depends_on(o_wait, delay_seconds=1)
            o_state_ready = farm_world.get_ridge_range_state(18, 39).oracle().with_id(
                "o_recheck_disease_block_before_spray"
            ).depends_on(o_weather_ready, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_chemical_budget_before_spray"
            ).depends_on(o_state_ready, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_check_sprayer_status"
            ).depends_on(o_inventory, delay_seconds=1)
            o_load = tractor.load_fungicide(76.3).oracle().with_id(
                "o_load_l3_fungicide_volume"
            ).depends_on(o_status, delay_seconds=2)
            spray_events, o_after_spray = apply_l3_fungicide_blocks(
                tractor, o_load, "o_apply_fungicide_disease_block"
            )
            o_budget = farm_world.get_inventory().oracle().with_id(
                "o_recheck_budget_after_fungicide"
            ).depends_on(o_after_spray, delay_seconds=1)
            o_recheck = farm_world.get_ridge_range_state(18, 39).oracle().with_id(
                "o_recheck_disease_block_after_spray"
            ).depends_on(o_budget, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成病害证据链、定向杀菌、预算复核和喷后复查。"
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
            o_status,
            o_load,
            *spray_events,
            o_budget,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 disease-control split"
        )
