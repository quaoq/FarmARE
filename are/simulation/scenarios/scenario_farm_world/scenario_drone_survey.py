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

# Anomaly zone: ridges 15-22 have pest pressure
_ANOMALY_START = 18
_ANOMALY_END = 18


@register_scenario("scenario_farm_world_drone_survey")
class ScenarioFarmWorldDroneSurvey(Scenario):
    """
    Routine drone survey for crop health monitoring.

    Crops are at V4-V5 stage. The agent must check weather conditions,
    verify drone battery levels, fly systematic surveys across all 64 ridges,
    charge the drone mid-way if needed, identify anomalies (low NDVI zones),
    and dispatch a robot for ground-truth inspection of suspicious areas.
    """

    # 2026-06-05 08:00 CST — about 5 weeks after planting
    start_time: float | None = (
        datetime(2026, 6, 5, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
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
            description="DJI Mavic 3 Multispectral — multispectral imaging drone for NDVI vegetation index mapping",
            speed_ms=5.0,
            effective_ridges_per_pass=7,
            battery_pct_per_ridge=1.0,
        )
        matrice = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Matrice4T",
            description="DJI Matrice 4T — thermal imaging drone for canopy temperature and stress detection",
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
        mavic = self.get_typed_app(DroneApp, "Mavic3M")

        weather.set_weather(
            date="2026-06-05",
            temp_c=24.0,
            humidity_pct=55.0,
            wind_speed_ms=3.0,
            rainfall_mm=0.0,
            solar_radiation=520.0,
            forecast=[
                {
                    "date": "2026-06-06",
                    "temp_c": 25.0,
                    "humidity_pct": 50.0,
                    "wind_speed_ms": 2.5,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 530.0,
                },
                {
                    "date": "2026-06-07",
                    "temp_c": 22.0,
                    "humidity_pct": 65.0,
                    "wind_speed_ms": 4.0,
                    "rainfall_mm": 3.0,
                    "solar_radiation": 350.0,
                },
            ],
            avg_soil_vwc=0.23,
        )

        farm_world.set_season_phase("growing")

        # All 64 ridges planted, V4-V5 stage
        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.days_since_planted = 42
            r.growth_stage = "V4"
            r.soil_vwc = 0.22 + (i % 4) * 0.01
            r.soil_temp_c = 20.0 + (i % 3) * 0.3
            # Anomaly zone: pest pressure on ridges 15-22
            if _ANOMALY_START <= i <= _ANOMALY_END:
                r.pest_pressure_base = 0.3 + (i % 3) * 0.1
                r.pest_pressure = r.pest_pressure_base
            else:
                r.pest_pressure_base = 0.02
                r.pest_pressure = 0.02

        # Mavic starts at 65% battery — not full, farmer must notice
        mavic._battery_pct = 65.0

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot_0 = self.get_typed_app(RobotApp, "Robot0")
        system = self.get_typed_app(SystemApp)

        # --- Two briefing versions ---
        if self.detailed_briefing:
            briefing_text = (
                """
                作物已进入V4生长阶段（播种后约42天），需要进行例行无人机巡查监测作物健康。
                请按以下步骤操作：
                1. 查看天气，确认无雨且风速<12m/s（无人机飞行条件）。
                2. Mavic3M 检查无人机电量（当前约65%，不满）。
                3. 飞行巡查全部64垄。飞到电量不足时会自动返航，返回部分结果。
                4. 返航后，检查无人机状态，给无人机充电（约30分钟）。
                5. 充电完成后用 继续飞剩余未覆盖的区域。
                6. 全部飞完后，如果发现NDVI偏低的区域，然后派机器狗到异常垄做地面巡检确认病虫害，执行前先检查机器狗Robot0电量，。
                7. 全部完成后立即结束任务向我汇报巡查结果。
                """
            )
        else:
            briefing_text = (
                "作物进入V4阶段了，飞一圈无人机看看长势。"
                "发现问题的话派机器狗去确认一下。完成后告诉我。"
            )

        with EventRegisterer.capture_mode():
            # --- Briefing ---
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("survey_briefing")
                .depends_on(None, delay_seconds=5)
            )

            # --- Pre-flight checks ---
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_check_weather")
                .depends_on(briefing, delay_seconds=2)
            )
            o_drone_status = (
                mavic.check_status()
                .oracle()
                .with_id("o_check_drone")
                .depends_on(o_weather, delay_seconds=1)
            )

            # --- First survey attempt: 0-63 (will return partial due to low battery) ---
            # Mavic starts at 65%, each ridge costs 1%, safe threshold 20%
            # Can fly ~45 ridges before returning (65% - 20% = 45%)
            o_survey1 = (
                mavic.fly_survey(0, 63)
                .oracle()
                .with_id("o_survey_first_batch")
                .depends_on(o_drone_status, delay_seconds=2)
            )

            # --- Check battery after partial return ---
            o_check_battery = (
                mavic.check_status()
                .oracle()
                .with_id("o_check_battery_after_partial")
                .depends_on(o_survey1, delay_seconds=1)
            )

            # --- Charge drone ---
            o_charge = (
                mavic.charge()
                .oracle()
                .with_id("o_charge_drone")
                .depends_on(o_check_battery, delay_seconds=2)
            )

            o_wait_charge = (
                system.wait_for_notification(timeout=30 * 60)
                .oracle()
                .with_id("o_wait_for_charge_notification")
                .depends_on(o_charge, delay_seconds=1)
            )

            # --- Continue survey: remaining ridges (agent should figure out which ones) ---
            # Mavic flies in 7-ridge passes costing 7% each; from 65% with 20% safe
            # threshold, it completes 6 passes (42 ridges, 0-41) before aborting.
            # Ridges 42-63 are uncovered.
            o_survey2 = (
                mavic.fly_survey(42, 63)
                .oracle()
                .with_id("o_survey_remaining")
                .depends_on(o_wait_charge, delay_seconds=1)
            )



            # --- Ground-truth with robot ---
            o_robot_status = (
                robot_0.check_status()
                .oracle()
                .with_id("o_check_robot")
                .depends_on(o_survey2, delay_seconds=1)
            )
            o_inspect = (
                robot_0.inspect_ridge(18)
                .oracle()
                .with_id("o_robot_inspect")
                .depends_on(o_robot_status, delay_seconds=2)
            )

            # --- Report ---
            o_report = (
                aui.send_message_to_user(
                    content="无人机巡查完成。发现ridges 18区域NDVI偏低，机器狗确认有虫害。"
                )
                .oracle()
                .with_id("o_report")
                .depends_on(o_inspect, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_drone_status,
            o_survey1,
            o_check_battery,
            o_charge,
            o_wait_charge,
            o_survey2,
            o_robot_status,
            o_inspect,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="no validation")
        return append_workflow_evaluation(self, env, result)
