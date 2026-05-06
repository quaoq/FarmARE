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
from are.simulation.scenarios.fos.evaluation import append_fos_evaluation
from are.simulation.scenarios.fos.gates import GateSpec
from are.simulation.scenarios.fos.predicates import after_observation
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

_DEFICIENT_START = 22
_DEFICIENT_END = 27
_SURVEY_START = 22
_SURVEY_END = 32
_FERTILIZER_KG_PER_RIDGE = 10.0
_FERTILIZER_LOAD_KG = 100.0


@register_scenario("scenario_farm_world_fertilizer")
class ScenarioFarmWorldFertilizer(Scenario):
    """
    Mid-season nutrient management triggered by canopy deficiency.

    Crops are at V3-V4 stage. Ridges 22-27 show low NDVI and reduced yield
    potential, indicating nutrient deficiency. The agent must diagnose the
    issue via canopy sensors and a drone survey, then apply targeted fertilizer.
    """

    start_time: float | None = (
        datetime(2026, 6, 2, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )

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
            description="Zhiyuan D1 Max #1 — ground-level inspection robot",
        )
        robot_1 = RobotApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Robot1",
            description="Zhiyuan D1 Max #2 — ground-level inspection robot",
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

    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        tractor = self.get_typed_app(TractorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")

        weather.set_weather(
            date="2026-06-02",
            temp_c=24.0,
            humidity_pct=45.0,
            wind_speed_ms=1.5,
            rainfall_mm=0.0,
            solar_radiation=520.0,
            forecast=[
                {
                    "date": "2026-06-03",
                    "temp_c": 25.0,
                    "humidity_pct": 42.0,
                    "wind_speed_ms": 2.0,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 530.0,
                },
                {
                    "date": "2026-06-04",
                    "temp_c": 23.0,
                    "humidity_pct": 55.0,
                    "wind_speed_ms": 2.5,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 480.0,
                },
                {
                    "date": "2026-06-05",
                    "temp_c": 22.0,
                    "humidity_pct": 70.0,
                    "wind_speed_ms": 3.0,
                    "rainfall_mm": 5.0,
                    "solar_radiation": 300.0,
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
            r.days_since_planted = 35
            r.growth_stage = "V3"
            r.soil_vwc = 0.23 + (i % 4) * 0.01
            r.soil_temp_c = 20.0 + (i % 3) * 0.3
            r.pest_pressure_base = 0.02
            r.pest_pressure = 0.02
            r.disease_pressure_base = 0.02
            r.disease_pressure = 0.02

            if _DEFICIENT_START <= i <= _DEFICIENT_END:
                r.ndvi = 0.45 + (i % 3) * 0.02
                r.yield_potential = 0.75 + (i % 3) * 0.02
                r.canopy_temp_c = 28.0 + (i % 2) * 0.5
            else:
                r.ndvi = 0.65 + (i % 4) * 0.03
                r.yield_potential = 0.95
                r.canopy_temp_c = 25.0 + (i % 3) * 0.3

        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 80.0
        tractor._fertilizer_spreader_kg = 0.0
        mavic._battery_pct = 85.0

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        tractor = self.get_typed_app(TractorApp)

        if self.detailed_briefing:
            briefing_text = (
                """
                作物已进入V3-V4生长阶段（播种后约35天），冠层传感器显示部分区域NDVI偏低，怀疑营养缺乏。
                由于大豆固氮作用，氮肥需求较低，但磷钾平衡仍影响产量。
                请按以下步骤操作：
                1. 查看当前天气，确认适合下地作业（无雨）。
                2. 查看未来3天预报，确认作业窗口。
                3. 读取冠层传感器，找出NDVI偏低的区域。
                4. 检查Mavic3M状态，飞行巡查异常区域确认缺肥情况。
                5. 检查拖拉机状态（油量、施肥机）和仓库肥料库存。
                6. 装载肥料100kg，对缺肥区（NDVI小于0.45）域追施肥料（每垄约10kg）。
                7. 全部完成后立即结束任务向我汇报。
                """
            )
        else:
            briefing_text = "作物进入V3阶段，部分区域长势偏弱。检查后追肥，完成后汇报。"

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("fertilizer_briefing")
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
                mavic.fly_survey(_SURVEY_START, _SURVEY_END)
                .oracle()
                .with_id("o_survey_deficient_zone")
                .depends_on(o_drone_status, delay_seconds=2)
            )
            o_tractor = (
                tractor.get_status()
                .oracle()
                .with_id("o_check_tractor")
                .depends_on(o_survey, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_check_inventory")
                .depends_on(o_tractor, delay_seconds=1)
            )
            o_load = (
                tractor.load_fertilizer(_FERTILIZER_LOAD_KG)
                .oracle()
                .with_id("o_load_fertilizer")
                .depends_on(o_inventory, delay_seconds=2)
            )
            o_apply = (
                tractor.apply_fertilizer(
                    _DEFICIENT_START, _DEFICIENT_END, _FERTILIZER_KG_PER_RIDGE
                )
                .oracle()
                .with_id("o_apply_fertilizer")
                .depends_on(o_load, delay_seconds=2)
            )
            o_report = (
                aui.send_message_to_user(content="缺肥区域已完成追肥处理。")
                .oracle()
                .with_id("o_report")
                .depends_on(o_apply, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_canopy,
            o_drone_status,
            o_survey,
            o_tractor,
            o_inventory,
            o_load,
            o_apply,
            o_report,
        ]

    def _gates(self) -> list[GateSpec]:
        return [
            GateSpec(name="G1_observe", intent="agent observes ridges",
                window_days=(0.0, 1.0),
                eligible_tools=[("SensorApp", "read_canopy_sensors")]),
            GateSpec(name="G2_load", intent="load fertilizer",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "load_fertilizer")]),
            GateSpec(name="G3_apply", intent="apply fertilizer",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "apply_fertilizer"), ("FarmWorldApp", "apply_fertigation")],
                requires=after_observation("TractorApp", "load_fertilizer")),
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="round-1+2 mirror")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result
