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
from are.simulation.types import EventRegisterer

_BASE_FERTILIZER_LOAD_KG = 200.0


@register_scenario("scenario_farm_world_field_prep")
class ScenarioFarmWorldFieldPrep(Scenario):
    """
    Pre-planting field preparation.

    A realistic field-prep shift: the agent must first check weather and soil
    conditions to confirm the field is workable, then complete three preparation
    steps in the correct agronomic order:
      1. level          — attach grader, rotary till and level the soil surface
      2. base_fertilize — load fertilizer from warehouse, apply across field
      3. form_ridges    — form the 64 ridge rows (1.1 m width)

    The oracle sequence includes the ground-condition checks a real farmer
    would perform before driving onto the field.
    """

    # 2026-04-25 08:00 CST (UTC+8)
    start_time: float | None = (
        datetime(2026, 4, 25, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 25000
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True  # Set to True for detailed instructions

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

        weather.set_weather(
            date="2026-04-25",
            temp_c=15.0,
            humidity_pct=55.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=420.0,
            forecast=[
                {
                    "date": "2026-04-26",
                    "temp_c": 16.5,
                    "humidity_pct": 50.0,
                    "wind_speed_ms": 3.0,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 450.0,
                },
                {
                    "date": "2026-04-27",
                    "temp_c": 14.0,
                    "humidity_pct": 70.0,
                    "wind_speed_ms": 5.0,
                    "rainfall_mm": 8.0,
                    "solar_radiation": 200.0,
                },
            ],
            avg_soil_vwc=0.22,
        )
        farm_world.set_season_phase("prep")
        for i in range(64):
            r = farm_world.get_ridge(i)
            r.soil_vwc = 0.22 + ((i % 4) - 1.5) * 0.002
            r.soil_temp_c = 10.0 + (i % 3) * 0.3

        self._sync_sensors(farm_world, sensor)

    def _sync_sensors(self, farm_world: FarmWorldApp, sensor: SensorApp) -> None:
        """Push ground-truth ridge data into sensor caches."""
        for s in sensor.get_state()["soil_sensors"]:
            sid, rs, re = s["sensor_id"], s["ridge_start"], s["ridge_end"]
            ridges = [farm_world.get_ridge(r) for r in range(rs, re + 1)]
            avg_vwc = sum(r.soil_vwc for r in ridges) / len(ridges)
            avg_temp = sum(r.soil_temp_c for r in ridges) / len(ridges)
            sensor.update_soil_sensor(sid, avg_vwc, avg_temp)

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)

        # --- Two briefing versions ---
        if self.detailed_briefing:
            briefing_text = (
                "农场进入种植前准备阶段，今天需要完成全部整地工作。\n"
                "请按以下步骤操作：\n"
                "1.  查看今天天气，确认无雨可以下地；"
                "2. 读取土壤传感器，确认土壤VWC < 0.35（拖拉机可通行）。\n"
                "3. 检查拖拉机油量和挂接状态。\n"
                "4. 平整地面：先 挂接平地机，"
                "然后 旋耕平整全田（平地机宽3m，速度3km/h），"
                "完成后卸下平地机。\n"
                "5. 施基肥：从仓库装载200kg化肥到施肥机，"
                "然后全田撒施（撒播机宽6m，速度6km/h）。\n"
                "6. 起垄：用挂接开沟机，"
                "然后 起垄（垄宽1.1m，4垄/趟，速度4km/h）。\n"
                "7. 全部完成后向我汇报。"
            )
        else:
            briefing_text = (
                "要种地了，请开始种植前的准备处理。"
                "完成后告诉我。"
            )

        with EventRegisterer.capture_mode():
            # --- Briefing ---
            briefing = aui.send_message_to_agent(
                content=briefing_text
            ).depends_on(None, delay_seconds=5)

            # --- Ground checks ---
            oracle_check_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("oracle_check_weather")
                .depends_on(briefing, delay_seconds=2)
            )


            oracle_read_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("oracle_read_soil")
                .depends_on(oracle_check_weather, delay_seconds=1)
            )

            oracle_check_tractor = (
                tractor.get_status()
                .oracle()
                .with_id("oracle_check_tractor")
                .depends_on(oracle_read_soil, delay_seconds=1)
            )

            # --- Step 1: Level ---
            oracle_attach = (
                tractor.attach_implement("grader")
                .oracle()
                .with_id("oracle_attach_grader")
                .depends_on(oracle_check_tractor, delay_seconds=2)
            )

            oracle_level = (
                tractor.level()
                .oracle()
                .with_id("oracle_level")
                .depends_on(oracle_attach, delay_seconds=2)
            )

            oracle_detach = (
                tractor.detach_implement()
                .oracle()
                .with_id("oracle_detach_implement")
                .depends_on(oracle_level, delay_seconds=1)
            )

            # --- Step 2: Base fertilize ---
            oracle_load_fertilizer = (
                tractor.load_fertilizer(_BASE_FERTILIZER_LOAD_KG)
                .oracle()
                .with_id("oracle_load_fertilizer")
                .depends_on(oracle_detach, delay_seconds=1)
            )

            oracle_fertilize = (
                tractor.base_fertilize()
                .oracle()
                .with_id("oracle_base_fertilize")
                .depends_on(oracle_load_fertilizer, delay_seconds=2)
            )

            # --- Step 3: Form ridges ---
            oracle_attach_furrower = (
                tractor.attach_implement("furrower")
                .oracle()
                .with_id("oracle_attach_furrower")
                .depends_on(oracle_fertilize, delay_seconds=2)
            )
            oracle_ridges = (
                tractor.form_ridges(1.1)
                .oracle()
                .with_id("oracle_form_ridges")
                .depends_on(oracle_attach_furrower, delay_seconds=2)
            )

            # --- Report ---
            oracle_report = (
                aui.send_message_to_user(content="种植前准备完成，可以进入播种阶段。")
                .oracle()
                .with_id("oracle_report_completion")
                .depends_on(oracle_ridges, delay_seconds=2)
            )

        self.events = [
            briefing,
            oracle_check_weather,
            oracle_read_soil,
            oracle_check_tractor,
            oracle_attach,
            oracle_level,
            oracle_detach,
            oracle_load_fertilizer,
            oracle_fertilize,
            oracle_attach_furrower,
            oracle_ridges,
            oracle_report,
        ]

