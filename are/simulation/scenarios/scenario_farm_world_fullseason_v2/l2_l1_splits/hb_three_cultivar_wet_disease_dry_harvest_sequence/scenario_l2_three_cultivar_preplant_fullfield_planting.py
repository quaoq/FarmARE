from __future__ import annotations

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import FarmWorldApp, SensorApp, TractorApp, WeatherApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    RIDGE_WIDTH_M,
    plant_range,
)
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

from ._hb_three_cultivar_split_common import (
    CHECKPOINT_INITIAL_BEFORE_FIELD_PREP,
    SOURCE_L3_SCENARIO_ID,
    cst_timestamp,
    populate_three_cultivar_apps,
    restore_three_cultivar_checkpoint,
    validate_native_workflow,
)

SCENARIO_ID = "scenario_l2_three_cultivar_preplant_fullfield_planting"


@register_scenario(SCENARIO_ID)
class ScenarioL2ThreeCultivarPreplantFullfieldPlanting(Scenario):
    """L2 split: prepare and plant the three-cultivar field from the L3 start."""

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
        populate_three_cultivar_apps(self)
        restore_three_cultivar_checkpoint(self, CHECKPOINT_INITIAL_BEFORE_FIELD_PREP)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                "截至2026-05-05，三品种大豆田处于田间准备和播种前复核阶段。"
                "请先检查当前天气、3天天气预报、土壤传感器、种肥燃油库存和拖拉机状态。"
                "若天气和种床条件允许，按三品种分区计划完成整地、基肥、起垄和播种："
                "HEIHE50为0-20垄、HEINONG84为21-42垄、HEINONG58为43-63垄。"
                "播种参数必须使用对应品种和株距；完成后复查全田，确认64垄均已按分区播种。"
            )
        else:
            briefing_text = "请从5月5日播种前状态完成三品种田整地、基肥、起垄、分区播种和复查。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("briefing")
                .depends_on(None, delay_seconds=5)
            )
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_check_planting_weather")
                .depends_on(briefing, delay_seconds=1)
            )
            o_forecast = (
                weather.get_forecast(days=3)
                .oracle()
                .with_id("o_check_planting_forecast")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_check_seedbed_soil")
                .depends_on(o_forecast, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_check_seed_fertilizer_inventory")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_status = (
                tractor.get_status()
                .oracle()
                .with_id("o_check_tractor_before_prep")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_attach_grader = (
                tractor.attach_implement("grader")
                .oracle()
                .with_id("o_attach_grader")
                .depends_on(o_status, delay_seconds=1)
            )
            o_level = (
                tractor.level()
                .oracle()
                .with_id("o_level_field")
                .depends_on(o_attach_grader, delay_seconds=2)
            )
            o_detach_grader = (
                tractor.detach_implement()
                .oracle()
                .with_id("o_detach_grader")
                .depends_on(o_level, delay_seconds=1)
            )
            o_load_fert = (
                tractor.load_fertilizer(360.0)
                .oracle()
                .with_id("o_load_base_fertilizer")
                .depends_on(o_detach_grader, delay_seconds=1)
            )
            o_base_fert = (
                tractor.base_fertilize()
                .oracle()
                .with_id("o_apply_base_fertilizer")
                .depends_on(o_load_fert, delay_seconds=2)
            )
            o_attach_furrower = (
                tractor.attach_implement("furrower")
                .oracle()
                .with_id("o_attach_furrower")
                .depends_on(o_base_fert, delay_seconds=1)
            )
            o_form = (
                tractor.form_ridges(RIDGE_WIDTH_M)
                .oracle()
                .with_id("o_form_ridges")
                .depends_on(o_attach_furrower, delay_seconds=2)
            )
            o_detach_furrower = (
                tractor.detach_implement()
                .oracle()
                .with_id("o_detach_furrower")
                .depends_on(o_form, delay_seconds=1)
            )
            o_plant_a = plant_range(
                tractor,
                o_detach_furrower,
                start_ridge=0,
                end_ridge=20,
                seed_type="HEIHE50",
                spacing_cm=8.2,
                id_prefix="o_zone_a_heihe50",
            )
            o_plant_b = plant_range(
                tractor,
                o_plant_a,
                start_ridge=21,
                end_ridge=42,
                seed_type="HEINONG84",
                spacing_cm=7.9,
                id_prefix="o_zone_b_heinong84",
            )
            o_plant_c = plant_range(
                tractor,
                o_plant_b,
                start_ridge=43,
                end_ridge=63,
                seed_type="HEINONG58",
                spacing_cm=8.4,
                id_prefix="o_zone_c_heinong58",
            )
            o_recheck = (
                farm_world.get_ridge_range_state(0, 63)
                .oracle()
                .with_id("o_recheck_all_ridges_planted")
                .depends_on(o_plant_c, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(content="已完成三品种全田整地、基肥、起垄、分区播种和复查。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_recheck, delay_seconds=2)
            )

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
            o_form,
            o_detach_furrower,
            o_plant_a,
            o_plant_b,
            o_plant_c,
            o_recheck,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(
            self, env, "full-checkpoint L2 three-cultivar preplant planting split"
        )
