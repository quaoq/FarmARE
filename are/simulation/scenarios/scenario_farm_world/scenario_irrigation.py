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
from are.simulation.scenarios.fos import GateSpec, append_fos_evaluation
from are.simulation.scenarios.fos.predicates import (
    after_observation,
    and_,
    min_arg,
    targets_ridges_overlap,
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

# Dry zone: ridges 22-32 have low VWC (simulating an uneven sandy patch)
_DRY_START = 22
_DRY_END = 32
_IRRIGATION_HOURS = 1.5


@register_scenario("scenario_farm_world_irrigation")
class ScenarioFarmWorldIrrigation(Scenario):
    """
    Mid-season irrigation decision-making.

    Crops are at V2-V3 stage. A dry spell has left ridges 22-32 with low
    soil moisture. The agent must identify the dry zone via sensors and
    drone survey, check the forecast (no rain coming), then irrigate the
    affected ridges. After the follow-up notification arrives, re-read
    sensors to confirm.
    """

    # 2026-05-20 07:00 CST — about 3 weeks after planting
    start_time: float | None = (
        datetime(2026, 5, 20, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
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
        system = self.get_typed_app(SystemApp)

        # --- Two briefing versions ---
        if self.detailed_briefing:
            briefing_text = (
                """
                作物已进入V2生长阶段（播种后约22天），最近持续干旱无雨。
                请按以下步骤操作：
                1. 查看今天天气。
                2. 查看未来3天预报，如果近期有雨就不用灌溉了，让天然降雨补充水分。
                3. 读取6个土壤传感器，找出VWC < 0.20的干旱区域（正常应在0.20-0.30之间）。
                4. 对干旱区域灌溉1.5小时。灌溉会使土壤VWC增加约0.08。
                5. 灌溉后等待系统在约2小时后发送通知，再次读取传感器，确认土壤湿度已恢复到正常范围。
                6. 全部完成后立即结束任务向我汇报灌溉完成情况。
                """

            )
        else:
            briefing_text = (
                "最近一直没下雨，地有点干了。"
                "查查哪些地方缺水，灌溉一下，灌溉后请再次检查。完成后告诉我。"
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

            # --- Irrigate dry zone ---
            o_irrigate = (
                field_ops.irrigate_range(_DRY_START, _DRY_END, _IRRIGATION_HOURS)
                .oracle()
                .with_id("o_irrigate")
                .depends_on(o_soil, delay_seconds=2)
            )

            o_wait_notification = (
                system.wait_for_notification(timeout=2 * 60 * 60)
                .oracle()
                .with_id("o_wait_for_irrigation_notification")
                .depends_on(o_irrigate, delay_seconds=1)
            )

            # --- Verify with sensors ---
            o_verify = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_verify_soil")
                .depends_on(o_wait_notification, delay_seconds=1)
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
            o_irrigate,
            o_wait_notification,
            o_verify,
            o_report,
        ]

    def _gates(self) -> list[GateSpec]:
        """FOS Decision-component gates for irrigation scenario.

        Five intent-based decision points; each is matched if the agent took
        any reasonable action of the right type within the time window. The
        full scenario fits in a few hours of sim time, so windows are tight.
        """
        return [
            GateSpec(
                name="G1_check_weather",
                intent="agent must check current weather to confirm dry conditions",
                window_days=(0.0, 0.5),
                eligible_tools=[("WeatherApp", "get_current_weather")],
            ),
            GateSpec(
                name="G2_check_forecast",
                intent="agent must consult forecast to rule out incoming rain",
                window_days=(0.0, 0.5),
                eligible_tools=[("WeatherApp", "get_forecast")],
            ),
            GateSpec(
                name="G3_observe_soil",
                intent="agent must read soil sensors to localise the dry zone",
                window_days=(0.0, 0.5),
                eligible_tools=[("SensorApp", "read_soil_sensors")],
            ),
            GateSpec(
                name="G4_irrigate_dry_zone",
                intent="agent must irrigate the dry zone (ridges 22-32) for >=1h after observing",
                window_days=(0.0, 1.0),
                eligible_tools=[
                    ("FieldOpsApp", "irrigate_range"),
                    ("FieldOpsApp", "irrigate_ridge"),
                ],
                requires=and_(
                    after_observation("SensorApp", "read_soil_sensors"),
                    targets_ridges_overlap(_DRY_START, _DRY_END),
                    min_arg("duration_hours", 1.0),
                ),
            ),
            GateSpec(
                name="G5_verify_after_irrigation",
                intent="agent must re-read sensors after the irrigation effect lands",
                window_days=(0.0, 1.0),
                eligible_tools=[("SensorApp", "read_soil_sensors")],
                requires=after_observation("FieldOpsApp", "irrigate_range"),
            ),
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="no validation")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result
