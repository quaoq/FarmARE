from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, FieldOpsApp, SensorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_three_cultivar_split_common import (
    CHECKPOINT_BEFORE_R5_IRRIGATION,
    SOURCE_L3_SCENARIO_ID,
    cst_timestamp,
    populate_three_cultivar_apps,
    restore_three_cultivar_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l1_three_cultivar_hn58_action_ready_irrigation"


@register_scenario(SCENARIO_ID)
class ScenarioL1ThreeCultivarHN58ActionReadyIrrigation(Scenario):
    """L1 split: execute confirmed HEINONG58-zone irrigation."""

    start_time: float | None = cst_timestamp(2026, 8, 6, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_BEFORE_R5_IRRIGATION
    source_checkpoint_date = "2026-08-06"
    source_dap = 93
    source_growth_stage = "R5_BEGINNING_SEED/R6_FULL_SEED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_three_cultivar_apps(self)
        restore_three_cultivar_checkpoint(self, CHECKPOINT_BEFORE_R5_IRRIGATION)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        field_ops = self.get_typed_app(FieldOpsApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "已知本田块按品种分为三个可见管理分区：HEIHE50早熟分区为ridges 0-20，HEINONG84分区为ridges 21-42，HEINONG58分区为ridges 43-63。"
                "截至2026-08-06，HEINONG58区进入灌溉前复核状态："
                "前序传感器、无人机热成像和地面确认已经显示43-63垄的后期干旱压力。"
                "请做最小复核：当前天气、土壤水分、目标区状态和剩余灌溉配额。"
                "若目标区仍低水分且配额允许，只对HEINONG58区执行0.8小时灌溉，并等待响应后复查。"
                "不要给病虫草或健康分区灌水，也不要扩大到HEIHE50/HEINONG84区。"
            )
        else:
            briefing_text = "请在HEINONG58灌溉前窗口复核天气、水分和配额，然后只灌溉43-63垄。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_confirm_irrigation_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_confirm_soil_water_deficit")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_confirm_canopy_water_stress")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_state = (
                farm_world.get_ridge_range_state(43, 63)
                .oracle()
                .with_id("o_confirm_hn58_target_state")
                .depends_on(o_canopy, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_confirm_irrigation_quota")
                .depends_on(o_state, delay_seconds=1)
            )
            o_irrigate = (
                field_ops.irrigate(43, 63, 0.8)
                .oracle()
                .with_id("o_irrigate_hn58_43_63")
                .depends_on(o_inventory, delay_seconds=2)
            )
            o_wait = (
                system.advance_time(hours=6)
                .oracle()
                .with_id("o_wait_irrigation_response")
                .depends_on(o_irrigate, delay_seconds=1)
            )
            o_recheck = (
                farm_world.get_ridge_range_state(43, 63)
                .oracle()
                .with_id("o_recheck_hn58_after_irrigation")
                .depends_on(o_wait, delay_seconds=1)
            )
            o_report = (
                aui.send_message_to_user(content="已完成HEINONG58区精准灌溉和复查。")
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
            o_inventory,
            o_irrigate,
            o_wait,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L1 HEINONG58 irrigation split"
        )
