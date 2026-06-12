from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_heihe43_split_common import (
    CHECKPOINT_INITIAL_BEFORE_FIELD_PREP,
    SOURCE_L3_SCENARIO_ID,
    apply_heihe43_planting_blocks,
    cst_timestamp,
    populate_hb_heihe43_apps,
    restore_hb_heihe43_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_hb_heihe43_recommended_density_planting"


@register_scenario(SCENARIO_ID)
class ScenarioL2HBHeihe43RecommendedDensityPlanting(Scenario):
    """L2 split: prepare and plant HEIHE43 at its recommended density."""

    start_time: float | None = cst_timestamp(2026, 5, 5, 8)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_INITIAL_BEFORE_FIELD_PREP
    source_checkpoint_date = "2026-05-05"
    source_dap = 0
    source_growth_stage = "NOT_PLANTED"

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        populate_hb_heihe43_apps(self)
        restore_hb_heihe43_checkpoint(self, CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "准备播种大豆。目标是在可播窗口内完成黑河43全田（0-63，共64垄）的播前准备、播种和复查。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气，确认无雨、风速和田间条件支持下地作业。\n"
                "2. 查看3天天气预报；如果后续降雨会影响窗口，请优先在今天完成关键田间作业。\n"
                "3. 读取土壤传感器，确认土壤VWC低于0.4，拖拉机可以通行。\n"
                "4. 检查拖拉机油量、当前挂接状态和可用农具。\n"
                "5. 查看仓库库存，确认黑河43种子、360kg基肥、柴油和作业资源充足。\n"
                "6. 平整地面：挂接平地机，平整全田，完成后卸下平地机。\n"
                "7. 施基肥：装载360kg化肥，然后完成全田基肥撒施。\n"
                "8. 起垄：挂接开沟机，按1.1m垄宽完成起垄，做完后卸下开沟机。\n"
                "9. 条件合适后，重新检查拖拉机状态和种子库存，装载HEIHE43种子。\n"
                "10. 按每趟4垄、播深4.0cm、株距8.1cm完成0-63垄播种；中途种子不足时先补装再继续。\n"
                "11. 播种完成后复查田块总览、已播垄数、剩余库存和关键作业记录，并汇报64垄播种闭环已完成。"
            )
        else:
            briefing_text = "请在可播窗口完成黑河43全田（0-63，共64垄）的播前准备、播种和复查。"

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id(
                "briefing"
            ).depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id(
                "o_check_current_weather"
            ).depends_on(briefing, delay_seconds=1)
            o_forecast = weather.get_forecast(days=3).oracle().with_id(
                "o_check_planting_forecast"
            ).depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id(
                "o_check_seedbed_soil"
            ).depends_on(o_forecast, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id(
                "o_check_seed_fertilizer_fuel"
            ).depends_on(o_soil, delay_seconds=1)
            o_status = tractor.get_status().oracle().with_id(
                "o_check_tractor_before_field_prep"
            ).depends_on(o_inventory, delay_seconds=1)
            o_attach_grader = tractor.attach_implement("grader").oracle().with_id(
                "o_attach_grader"
            ).depends_on(o_status, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(
                o_attach_grader, delay_seconds=2
            )
            o_detach_grader = tractor.detach_implement().oracle().with_id(
                "o_detach_grader"
            ).depends_on(o_level, delay_seconds=1)
            o_load_fert = tractor.load_fertilizer(360.0).oracle().with_id(
                "o_load_base_fertilizer"
            ).depends_on(o_detach_grader, delay_seconds=1)
            o_base_fert = tractor.base_fertilize().oracle().with_id(
                "o_apply_base_fertilizer"
            ).depends_on(o_load_fert, delay_seconds=2)
            o_attach_furrower = tractor.attach_implement("furrower").oracle().with_id(
                "o_attach_furrower"
            ).depends_on(o_base_fert, delay_seconds=1)
            o_ridges = tractor.form_ridges(ridge_width_m=1.1).oracle().with_id(
                "o_form_ridges"
            ).depends_on(o_attach_furrower, delay_seconds=2)
            o_detach_furrower = tractor.detach_implement().oracle().with_id(
                "o_detach_furrower"
            ).depends_on(o_ridges, delay_seconds=1)
            plant_events, o_after_planting = apply_heihe43_planting_blocks(
                tractor, o_detach_furrower, "o_whole_field_heihe43_recommended_density"
            )
            o_recheck = farm_world.get_farm_overview().oracle().with_id(
                "o_recheck_planted_field"
            ).depends_on(o_after_planting, delay_seconds=2)
            o_report = aui.send_message_to_user(
                content="已完成黑河43推荐密度全田播种和播后复查。"
            ).oracle().with_id("o_report").depends_on(o_recheck, delay_seconds=2)

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_inventory,
            o_status,
            o_attach_grader,
            o_level,
            o_detach_grader,
            o_load_fert,
            o_base_fert,
            o_attach_furrower,
            o_ridges,
            o_detach_furrower,
            *plant_events,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "HEIHE43 recommended-density planting L2 split"
        )
