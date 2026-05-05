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
from are.simulation.scenarios.oracle_matching import OracleStepSpec, oracle_validate
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.scenarios.fos.evaluation import append_fos_evaluation
from are.simulation.scenarios.fos.gates import GateSpec
from are.simulation.scenarios.fos.predicates import after_observation
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.types import EventRegisterer

# Outbreak zone: 10 contiguous ridges — suitable for the tractor spray boom
_BOOM_START = 15
_BOOM_END = 24

# Isolated hot-spot single ridge — suitable for the backpack sprayer
_MANUAL_RIDGE = 25

# Resource sizing
_PESTICIDE_LOAD_L = 100.0   # 10 ridges × 8 L + spare
_REFUEL_L = 80.0            # top-up amount


@register_scenario("scenario_farm_world_pesticide")
class ScenarioFarmWorldPesticide(Scenario):
    """
    Mid-season pesticide response after drone+robot confirmation.

    Yesterday's Mavic survey flagged ridges 15-25 for low NDVI / elevated canopy
    temperature — consistent with an aphid outbreak. The agent must act like a
    real farmer this morning:

      1. Weather + 3-day forecast (spray needs wind < 5 m/s, no rain today or
         in the next few hours; rain is coming in 48 h — creates urgency).
      2. Soil sensors to confirm the tractor can drive onto the field.
      3. Canopy sensors / drone re-survey over the suspect block to re-check
         the anomaly is still there.
      4. Robot dog ground-truth on one representative ridge — confirm aphids.
      5. Tractor + inventory status. Fuel is low (10 L), pesticide tank is
         empty → must refuel AND refill before going out.
      6. Apply:
           - Tractor boom over ridges 15-24 (10 ridges in one pass).
           - Backpack sprayer on ridge 25 (isolated severe spot).
      7. Report completion.

    Initial state is designed so every oracle step succeeds:
      - avg VWC 0.23 (< 0.35, trafficable & sprayable)
      - wind 2 m/s (< 5 m/s sprayable)
      - 10 outbreak ridges pest_pressure_base 0.35–0.55
      - 1 hot-spot ridge pest_pressure_base 0.65
      - 53 background ridges pest_pressure_base 0.02
      - Mavic battery 80 %, Robot0 100 %
      - Tractor fuel 10 L, pesticide tank 0 L, prep already completed
      - Warehouse pesticide 2000 L, fuel 1000 L — both sufficient
    """

    # 2026-06-06 09:00 CST (UTC+8) — day after the drone survey
    start_time: float | None = (
        datetime(2026, 6, 6, 9, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 30000
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
            description="Zhiyuan D1 Max #1 — ground-level pest/disease inspection robot",
        )
        robot_1 = RobotApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Robot1",
            description="Zhiyuan D1 Max #2 — ground-level pest/disease inspection robot",
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
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")

        # Clear, sprayable today + tomorrow; rain arriving in 48 h → urgency
        weather.set_weather(
            date="2026-06-06",
            temp_c=23.0,
            humidity_pct=50.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=500.0,
            forecast=[
                {
                    "date": "2026-06-07",
                    "temp_c": 24.0,
                    "humidity_pct": 48.0,
                    "wind_speed_ms": 1.5,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 520.0,
                },
                {
                    "date": "2026-06-08",
                    "temp_c": 21.0,
                    "humidity_pct": 72.0,
                    "wind_speed_ms": 5.5,
                    "rainfall_mm": 9.0,
                    "solar_radiation": 220.0,
                },
                {
                    "date": "2026-06-09",
                    "temp_c": 20.0,
                    "humidity_pct": 80.0,
                    "wind_speed_ms": 4.0,
                    "rainfall_mm": 4.0,
                    "solar_radiation": 260.0,
                },
            ],
            avg_soil_vwc=0.23,
        )

        farm_world.set_season_phase("growing")

        # All 64 ridges planted in early May, now at V4 (≈43 days)
        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.seed_spacing_cm = 12.0
            r.seeds_planted = 4467
            r.days_since_planted = 43
            r.growth_stage = "V4"
            r.soil_vwc = 0.22 + (i % 4) * 0.01          # 0.22–0.25, trafficable
            r.soil_temp_c = 20.0 + (i % 3) * 0.3
            r.yield_potential = 0.95

            if i == _MANUAL_RIDGE:
                # Isolated severe hot-spot → manual backpack job
                r.pest_pressure_base = 0.65
            else:
                # Background noise
                r.pest_pressure_base = 0.02
            r.pest_pressure = r.pest_pressure_base
            r.disease_pressure_base = 0.02
            r.disease_pressure = 0.02

        # Tractor: prep already done, fuel low, pesticide tank empty
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 10.0
        tractor._pesticide_tank_l = 0.0

        # Mavic at 80 % — comfortable margin for an 11-ridge re-survey
        mavic._battery_pct = 80.0


    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot_0 = self.get_typed_app(RobotApp, "Robot0")
        field_ops = self.get_typed_app(FieldOpsApp)

        if self.detailed_briefing:
            briefing_text = (
                """
                昨天 Mavic3M 巡查发现 ridges 15-25 区域 NDVI 偏低，怀疑蚜虫爆发。
                今天请按如下流程处理：
                1. 查看当前天气，确认风速<5 m/s、无雨（喷药条件）。
                2. 看 3 天预报，确认今天和明天的喷药窗口。
                3. 读冠层传感器，再看一下 NDVI 分布。
                4. 先使用Mavic3M核查异常区，使用前先检查机器状态。
                5. 读土壤传感器，确认 VWC<0.35（机器狗可下地）。
                6. 再使用Robot0地面复核Mavic3M报告的异常区域，使用前先检查机器状态。
                7. 人工对异常点施药。
                8. 全部完成立即结束任务后汇报。
                """
            )
        else:
            briefing_text = (
                "昨天无人机发现 ridges 15-25 有蚜虫迹象。"
                "请核实后用合适的方式喷药处理，完成后汇报。"
            )

        with EventRegisterer.capture_mode():
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("pesticide_briefing")
                .depends_on(None, delay_seconds=5)
            )

            # --- Step 1-2: weather + forecast ---
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

            # --- Step 3-4: soil + canopy sensors ---

            o_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("o_read_canopy")
                .depends_on(o_forecast, delay_seconds=1)
            )

            # --- Step 5: drone re-survey of the suspect block ---
            o_drone_status = (
                mavic.check_status()
                .oracle()
                .with_id("o_check_drone")
                .depends_on(o_canopy, delay_seconds=1)
            )
            o_drone_survey = (
                mavic.fly_survey(_BOOM_START, _MANUAL_RIDGE)
                .oracle()
                .with_id("o_survey_suspect")
                .depends_on(o_drone_status, delay_seconds=2)
            )

            # --- Step 6: robot ground-truth on one ridge in the outbreak ---
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_read_soil")
                .depends_on(o_drone_survey, delay_seconds=1)
            )
            o_robot_status = (
                robot_0.check_status()
                .oracle()
                .with_id("o_check_robot")
                .depends_on(o_drone_survey, delay_seconds=1)
            )
            o_robot_inspect = (
                robot_0.inspect_ridge(25)
                .oracle()
                .with_id("o_robot_inspect")
                .depends_on(o_robot_status, delay_seconds=2)
            )
            o_manual = (
                field_ops.apply_pesticide_manual(_MANUAL_RIDGE)
                .oracle()
                .with_id("o_spray_manual")
                .depends_on(o_robot_inspect, delay_seconds=2)
            )

            # --- Step 11: report ---
            o_report = (
                aui.send_message_to_user(
                    content=(
                        "蚜虫防治完成：ridges 15-24 拖拉机喷药，ridge 25 手持补刀。"
                        "药效 2-3 天后显现，届时会再次巡查。"
                    )
                )
                .oracle()
                .with_id("o_report")
                .depends_on(o_manual, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_canopy,
            o_drone_status,
            o_drone_survey,
            o_soil,
            o_robot_status,
            o_robot_inspect,
            o_manual,
            o_report,
        ]

    def _gates(self) -> list[GateSpec]:
        return [
            GateSpec(name="G1_observe_pest", intent="agent observes pest",
                window_days=(0.0, 1.0),
                eligible_tools=[("Mavic3M", "fly_survey"), ("Robot0", "inspect_pests")]),
            GateSpec(name="G2_load_pesticide", intent="load pesticide",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "load_pesticide"), ("TractorApp", "refill_pesticide_tank")]),
            GateSpec(name="G3_spray", intent="apply pesticide",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "spray_pesticide"), ("TractorApp", "apply_pesticide")],
                requires=after_observation("TractorApp", "load_pesticide")),
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="round-1+2 mirror")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result