from __future__ import annotations

from datetime import datetime, timezone

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    FieldOpsApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

# ---------------------------------------------------------------------------
# Farm-ARE action/tick/observation boundary
# ---------------------------------------------------------------------------
# This scenario follows the boundary we want in the implementation:
#
# 1. Direct farm actions update the relevant physical state immediately enough
#    for the operation to be meaningful. Examples:
#       plant_seeds      -> planting action, seed depth, stand/phenology init
#       irrigate         -> water delivery/action record/soil-water input
#       apply_fertilizer -> nutrient management effect
#       spray_pesticide  -> treatment action + residual treatment state
#       harvest          -> recovered-yield pass/inventory action
#
# 2. Elapsed time updates are not hidden in arbitrary tools. They should happen
#    through SystemApp time advancement, scheduled notification/wait events, or
#    the simulator clock/tick that ARE already uses between events.
#
# 3. Observations read physics state through the observation layer. Drone,
#    sensor, and robot tools should not expose hidden ground truth directly.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Physics version note
# ---------------------------------------------------------------------------
# This file is derived from the corresponding uploaded baseline scenario and
# intentionally preserves the original task scope, prompt style, and oracle
# event-flow shape.
#
# The main change is semantic and architectural: existing operation tools
# own their direct physical effects, while explicit clock/tick utilities own
# elapsed-time evolution. This keeps the scenario close to the baseline while
# making the physics contract clear.
#
# Boundary used here:
#   action tools  -> direct operational effects and action-history records
#   time/tick     -> weather, soil drying/wetting, GDD, growth, biotic evolution
#   observations -> noisy/sparse/latent-state readings exposed to the agent
# ---------------------------------------------------------------------------


_OUTBREAK_START = 15
_OUTBREAK_END = 39
_INSPECT_RIDGE = 27
_REFUEL_L = 80.0
_PESTICIDE_LOAD_L = 250.0


@register_scenario("scenario_farm_world_pesticide_outbreak_physics_action_tick")
class ScenarioFarmWorldPesticideOutbreakPhysicsActionTick(Scenario):
    """
    Physics-aware action/tick variant of the baseline scenario. The oracle sequence stays close to the original; action tools apply direct physical effects, and elapsed-time effects are handled by explicit time/tick mechanisms.

    Large-scale pest outbreak response with tractor boom spraying.

    A major aphid outbreak spans ridges 15-39. The agent must diagnose the
    issue with canopy sensors, drone survey, and robot confirmation, then
    prepare the tractor and spray the whole area in multiple passes.
    """

    start_time: float | None = (
        datetime(2026, 6, 15, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 36000
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        aui = AgentUserInterface()
        farm_world = FarmWorldApp()
        weather = WeatherApp()
        sensor = SensorApp(farm_world_app=farm_world)
        mavic = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Mavic3M",
            description="DJI Mavic 3 Multispectral — multispectral NDVI mapping drone",
            speed_ms=5.0,
            effective_ridges_per_pass=7,
            battery_pct_per_ridge=1.0,
        )
        matrice = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Matrice4T",
            description="DJI Matrice 4T — thermal imaging drone",
            speed_ms=4.0,
            effective_ridges_per_pass=5,
            battery_pct_per_ridge=1.5,
        )
        robot_0 = RobotApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Robot0",
            description="Zhiyuan D1 Max #1 — ground-level pest inspection robot",
        )
        robot_1 = RobotApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Robot1",
            description="Zhiyuan D1 Max #2 — ground-level pest inspection robot",
        )
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()

        self.apps = [
            aui,
            farm_world,
            weather,
            sensor,
            mavic,
            matrice,
            robot_0,
            robot_1,
            tractor,
            field_ops,
            system,
        ]
        self._configure_initial_state()
        self._configure_physics_layers()

    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        tractor = self.get_typed_app(TractorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")

        weather.set_weather(
            date="2026-06-15",
            temp_c=22.0,
            humidity_pct=55.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=480.0,
            forecast=[
                {
                    "date": "2026-06-16",
                    "temp_c": 23.0,
                    "humidity_pct": 52.0,
                    "wind_speed_ms": 2.5,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 490.0,
                },
                {
                    "date": "2026-06-17",
                    "temp_c": 20.0,
                    "humidity_pct": 78.0,
                    "wind_speed_ms": 5.5,
                    "rainfall_mm": 12.0,
                    "solar_radiation": 200.0,
                },
                {
                    "date": "2026-06-18",
                    "temp_c": 19.0,
                    "humidity_pct": 82.0,
                    "wind_speed_ms": 4.0,
                    "rainfall_mm": 6.0,
                    "solar_radiation": 240.0,
                },
            ],
            avg_soil_vwc=0.24,
        )
        farm_world.set_season_phase("growing")

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.seed_spacing_cm = 12.0
            r.seeds_planted = 4467
            r.days_since_planted = 45
            r.growth_stage = "V4"
            r.soil_vwc = 0.23 + (i % 4) * 0.01
            r.soil_temp_c = 20.0 + (i % 3) * 0.3
            r.yield_potential = 0.95
            r.disease_pressure_base = 0.02
            r.disease_pressure = 0.02

            if _OUTBREAK_START <= i <= _OUTBREAK_END:
                center = (_OUTBREAK_START + _OUTBREAK_END) / 2.0
                dist = abs(i - center) / ((_OUTBREAK_END - _OUTBREAK_START) / 2.0)
                r.pest_pressure_base = round(0.50 - 0.20 * dist, 2)
                r.ndvi = round(0.65 - r.pest_pressure_base * 0.35, 3)
                r.canopy_temp_c = round(24.0 + r.pest_pressure_base * 4.0, 2)
            else:
                r.pest_pressure_base = 0.02
                r.ndvi = 0.65 + (i % 4) * 0.03
                r.canopy_temp_c = 24.0 + (i % 3) * 0.3
            r.pest_pressure = r.pest_pressure_base

        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 15.0
        tractor._pesticide_tank_l = 0.0
        mavic._battery_pct = 80.0

    def _configure_physics_layers(self) -> None:
        """Attach large-outbreak biotic-pressure physics to the original outbreak setup.

        Physics intent:
            Preserve the original task: diagnose a large aphid-like outbreak over
            ridges 15-39 and spray the whole affected block with the tractor boom
            before rain arrives.

        Implementation choice:
            No new oracle step is added for this direct action. The existing spray_pesticide() should
            update normalized insect pressure and residual treatment state. The
            oracle remains a same-day response because the outbreak is already
            large and above threshold at scenario start.
        """
        farm_world = self.get_typed_app(FarmWorldApp)
        try:
            farm_world.configure_physics_profile(
                profile_name="physics_large_aphid_outbreak",
                location="Harbin/Heilongjiang",
                scenario_type="pesticide_outbreak",
            )
        except AttributeError:
            pass

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.insect_pressure = getattr(r, "pest_pressure", getattr(r, "pest_pressure_base", 0.02))
            r.disease_pressure = getattr(r, "disease_pressure", 0.02)
            r.weed_pressure = 0.05
            r.aphid_equivalent_per_plant = 500.0 * r.insect_pressure
            r.biotic_stress_multiplier = max(0.35, 1.0 - 0.22 * r.insect_pressure)
            r.insecticide_residual_days_left = 0

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot_0 = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                """
                作物已进入V4-V5生长阶段（播种后约45天），固定传感器显示多个区域NDVI异常偏低，怀疑大面积蚜虫爆发。
                请按以下步骤操作：
                1. 查看当前天气，确认风速<5m/s、无雨（喷药条件）。
                2. 查看未来3天预报，确认喷药窗口（后天有雨，今天必须喷）。
                3. 读取冠层传感器，找出NDVI偏低的区域。
                4. 检查Mavic3M状态，飞行巡查异常区域确认虫害范围。
                5. 读取土壤传感器，确认VWC<0.35（拖拉机可下地）。
                6. 检查Robot0状态，派机器狗到虫害中心区域地面复核确认蚜虫。
                7. 检查拖拉机状态和仓库库存。
                8. 给拖拉机装载喷药器、 加80.0L油、装250.0L药。
                9. 用拖拉机喷杆分多趟喷药（每趟最多10垄），覆盖全部虫害区域。
                10. 全部完成后卸载喷药器、立即结束任务向我汇报。
                """
            )
        else:
            briefing_text = "传感器显示大面积虫害，核实后大规模喷药处理，完成后汇报。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("pesticide_outbreak_briefing")
                .depends_on(None, delay_seconds=5)
            )

            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_check_weather")
                .depends_on(briefing, delay_seconds=2)
            )
            o_forecast = (
                weather.get_forecast(days=3)
                .oracle()
                .with_id("o_check_forecast")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_read_canopy")
                .depends_on(o_forecast, delay_seconds=1)
            )
            o_drone_status = (
                mavic.check_status()
                .oracle()
                .with_id("o_check_drone")
                .depends_on(o_canopy, delay_seconds=1)
            )
            o_survey = (
                mavic.fly_survey(11, 43)
                .oracle()
                .with_id("o_survey_outbreak")
                .depends_on(o_drone_status, delay_seconds=2)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_read_soil")
                .depends_on(o_survey, delay_seconds=1)
            )
            o_robot_status = (
                robot_0.check_status()
                .oracle()
                .with_id("o_check_robot")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_robot_inspect = (
                robot_0.inspect_ridge(_INSPECT_RIDGE)
                .oracle()
                .with_id("o_robot_inspect")
                .depends_on(o_robot_status, delay_seconds=2)
            )
            o_tractor = (
                tractor.get_status()
                .oracle()
                .with_id("o_check_tractor")
                .depends_on(o_robot_inspect, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_check_inventory")
                .depends_on(o_tractor, delay_seconds=1)
            )
            o_attach = (
                tractor.attach_implement("sprayer")
                .oracle()
                .with_id("o_attach_sprayer")
                .depends_on(o_inventory, delay_seconds=1)
            )
            o_refuel = (
                tractor.refuel(_REFUEL_L)
                .oracle()
                .with_id("o_refuel")
                .depends_on(o_attach, delay_seconds=1)
            )
            o_load = (
                tractor.refill_pesticide_tank(_PESTICIDE_LOAD_L)
                .oracle()
                .with_id("o_load_pesticide")
                .depends_on(o_refuel, delay_seconds=1)
            )
            o_spray_1 = (
                tractor.apply_pesticide(15, 24)
                .oracle()
                .with_id("o_spray_pass_1")
                .depends_on(o_load, delay_seconds=2)
            )
            o_spray_2 = (
                tractor.apply_pesticide(25, 34)
                .oracle()
                .with_id("o_spray_pass_2")
                .depends_on(o_spray_1, delay_seconds=2)
            )
            o_spray_3 = (
                tractor.apply_pesticide(35, 39)
                .oracle()
                .with_id("o_spray_pass_3")
                .depends_on(o_spray_2, delay_seconds=2)
            )
            o_detach = (
                tractor.detach_implement()
                .oracle()
                .with_id("o_detach_sprayer")
                .depends_on(o_spray_3, delay_seconds=1)
            )
            o_report = (
                aui.send_message_to_user(content="大面积虫害喷药处理已完成。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_detach, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_canopy,
            o_drone_status,
            o_survey,
            o_soil,
            o_robot_status,
            o_robot_inspect,
            o_tractor,
            o_inventory,
            o_attach,
            o_refuel,
            o_load,
            o_spray_1,
            o_spray_2,
            o_spray_3,
            o_detach,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="no validation")
        return append_workflow_evaluation(self, env, result)
