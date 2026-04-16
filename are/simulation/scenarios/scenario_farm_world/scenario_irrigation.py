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
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

# Dry zone: ridges 20-39 have low VWC (simulating uneven irrigation / sandy patch)
_DRY_START = 20
_DRY_END = 39
_IRRIGATION_HOURS = 1.5


@register_scenario("scenario_farm_world_irrigation")
class ScenarioFarmWorldIrrigation(Scenario):
    """
    Mid-season irrigation decision-making.

    Crops are at V2-V3 stage. A dry spell has left ridges 20-39 with low
    soil moisture. The agent must identify the dry zone via sensors and
    drone survey, check the forecast (no rain coming), then irrigate the
    affected ridges. After irrigation, re-read sensors to confirm.
    """

    # 2026-05-20 07:00 CST — about 3 weeks after planting
    start_time: float | None = (
        datetime(2026, 5, 20, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 50000
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
        sensor = self.get_typed_app(SensorApp)

        # Dry spell weather — no rain, warm
        weather.set_weather(
            date="2026-05-20",
            temp_c=26.0,
            humidity_pct=35.0,
            wind_speed_ms=1.5,
            rainfall_mm=0.0,
            solar_radiation=550.0,
            forecast=[
                {
                    "date": "2026-05-21",
                    "temp_c": 27.0,
                    "humidity_pct": 30.0,
                    "wind_speed_ms": 2.0,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 560.0,
                },
                {
                    "date": "2026-05-22",
                    "temp_c": 28.0,
                    "humidity_pct": 28.0,
                    "wind_speed_ms": 1.0,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 570.0,
                },
                {
                    "date": "2026-05-23",
                    "temp_c": 25.0,
                    "humidity_pct": 40.0,
                    "wind_speed_ms": 3.0,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 500.0,
                },
            ],
            avg_soil_vwc=0.19,
        )

        farm_world.set_season_phase("growing")

        # All 64 ridges planted, V2-V3 stage
        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.days_since_planted = 22
            r.growth_stage = "V2"
            r.soil_temp_c = 18.0 + (i % 3) * 0.5
            if _DRY_START <= i <= _DRY_END:
                # Dry zone — VWC below stress threshold
                r.soil_vwc = 0.14 + (i % 3) * 0.01
            else:
                # Normal zone
                r.soil_vwc = 0.22 + (i % 4) * 0.01

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        field_ops = self.get_typed_app(FieldOpsApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")

        # --- Two briefing versions ---
        if self.detailed_briefing:
            briefing_text = (
                "作物已进入V2生长阶段（播种后约22天），最近持续干旱无雨。\n"
                "请按以下步骤操作：\n"
                "1.  查看今天天气。\n"
                "2.  查看未来几天预报，"
                "如果近期有雨就不用灌溉了，让天然降雨补充水分。\n"
                "3. 读取6个土壤传感器，"
                "找出VWC < 0.20的干旱区域（正常应在0.20-0.30之间）。\n"
                "4. 用 Mavic3M  检查无人机电量。\n"
                "5. 用 飞行巡查干旱区域，"
                "通过冠层温度和NDVI确认水分胁迫情况。\n"
                "6. 对干旱区域灌溉1.5小时。"
                "灌溉会使土壤VWC增加约0.08。\n"
                "7. 灌溉完成后 再次读取传感器，"
                "确认土壤湿度已恢复到正常范围。\n"
                "8. 向我汇报灌溉完成情况。"
            )
        else:
            briefing_text = (
                "最近一直没下雨，地有点干了。"
                "查查哪些地方缺水，灌溉一下。完成后告诉我。"
            )

        with EventRegisterer.capture_mode():
            # --- Briefing ---
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("irrigation_briefing")
                .depends_on(None, delay_seconds=5)
            )

            # --- Check conditions ---
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
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_read_soil")
                .depends_on(o_forecast, delay_seconds=1)
            )

            # --- Drone survey to confirm dry zone ---
            o_drone_status = (
                mavic.check_status()
                .oracle()
                .with_id("o_check_drone")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_survey = (
                mavic.fly_survey(22, 43)
                .oracle()
                .with_id("o_survey_dry_zone")
                .depends_on(o_drone_status, delay_seconds=2)
            )

            # --- Irrigate dry zone ---
            o_irrigate = (
                field_ops.irrigate_range(_DRY_START, _DRY_END, _IRRIGATION_HOURS)
                .oracle()
                .with_id("o_irrigate")
                .depends_on(o_survey, delay_seconds=2)
            )

            # --- Verify with sensors ---
            o_verify = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_verify_soil")
                .depends_on(o_irrigate, delay_seconds=2)
            )

            # --- Report ---
            o_report = (
                aui.send_message_to_user(
                    content="干旱区域灌溉完成，土壤湿度已恢复正常。"
                )
                .oracle()
                .with_id("o_report")
                .depends_on(o_verify, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_forecast,
            o_soil,
            o_drone_status,
            o_survey,
            o_irrigate,
            o_verify,
            o_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        return ScenarioValidationResult(success=True, rationale="no validation")
