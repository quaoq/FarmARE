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
    CHECKPOINT_AFTER_MID_ROUTINE,
    SOURCE_L3_SCENARIO_ID,
    apply_disease_block_fungicide_events,
    checkpoint_sim_time,
    populate_wetjune_recheck_apps,
    restore_wetjune_recheck_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_wetjune_recheck_disease_control"


@register_scenario(SCENARIO_ID)
class ScenarioL2WetjuneRecheckDiseaseControl(Scenario):
    """L2 split: diagnose the wet-June disease block, treat it, and recheck."""

    start_time: float | None = checkpoint_sim_time(CHECKPOINT_AFTER_MID_ROUTINE)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_AFTER_MID_ROUTINE
    source_checkpoint_date = "2026-07-07"
    source_dap = 64
    source_growth_stage = "R1_BEGINNING_BLOOM"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_wetjune_recheck_apps(self)
        restore_wetjune_recheck_checkpoint(self, CHECKPOINT_AFTER_MID_ROUTINE)

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
                "田块已播种并进入R1初花期，当前需要处理湿六月后的病害诊断和喷药窗口决策。"
                "6月持续偏湿后，全田例行巡查发现中部冠层存在异常，但最终原因和处理范围需要你用证据确认。"
                "先检查当天天气、土壤传感器和全田概览，再用无人机定位异常冠层区域；不要只凭NDVI异常直接用药。"
                "随后用地面机器人复查异常区叶片症状和病害压力，并与健康参照区比较。"
                "若证据支持湿六月后的病害压力，等待合适喷药窗口，确认库存和喷雾设备后只对确认的病害区定向杀菌。"
                "处理完成后复查该区状态，确认病害压力被压住且没有把水、肥、虫害问题误当病害处理。"
            )
        else:
            briefing_text = (
                "R1初花期湿六月后出现冠层异常。请完成观察、定位、地面诊断、定向杀菌和复查。"
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
                .with_id("o_check_current_spray_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_check_soil_wetness_context")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_overview = (
                farm_world.get_farm_overview()
                .oracle()
                .with_id("o_check_whole_field_overview")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_survey = (
                mavic.fly_survey(0, 63)
                .oracle()
                .with_id("o_map_canopy_anomaly")
                .depends_on(o_overview, delay_seconds=2)
            )
            o_disease_block = (
                farm_world.get_ridge_range_state(20, 43)
                .oracle()
                .with_id("o_read_anomaly_block_state")
                .depends_on(o_survey, delay_seconds=1)
            )
            o_reference = (
                farm_world.get_ridge_range_state(0, 15)
                .oracle()
                .with_id("o_read_reference_block_state")
                .depends_on(o_disease_block, delay_seconds=1)
            )
            o_ground = (
                robot.inspect_crop_health(20, 43)
                .oracle()
                .with_id("o_ground_confirm_disease_symptoms")
                .depends_on(o_reference, delay_seconds=2)
            )
            o_wait = (
                system.advance_time(days=3)
                .oracle()
                .with_id("o_wait_to_l3_spray_window")
                .depends_on(o_ground, delay_seconds=1)
            )
            o_weather_ready = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_recheck_spray_weather")
                .depends_on(o_wait, delay_seconds=1)
            )
            o_state_ready = (
                farm_world.get_ridge_range_state(20, 43)
                .oracle()
                .with_id("o_recheck_disease_block_before_spray")
                .depends_on(o_weather_ready, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_check_fungicide_inventory")
                .depends_on(o_state_ready, delay_seconds=1)
            )
            o_status = (
                tractor.get_status()
                .oracle()
                .with_id("o_check_sprayer_status")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_load = (
                tractor.load_fungicide(95.0)
                .oracle()
                .with_id("o_load_fungicide")
                .depends_on(o_status, delay_seconds=2)
            )
            spray_events, o_after_spray = apply_disease_block_fungicide_events(
                tractor, o_load, "o_apply_fungicide_disease_block"
            )
            o_recheck = (
                farm_world.get_ridge_range_state(20, 43)
                .oracle()
                .with_id("o_recheck_disease_block_after_spray")
                .depends_on(o_after_spray, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(
                    content="已完成湿六月病害区诊断、喷药窗口确认、定向杀菌和复查。"
                )
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_overview,
            o_survey,
            o_disease_block,
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
            self, env, "full-checkpoint L2 wet-June disease control split"
        )
