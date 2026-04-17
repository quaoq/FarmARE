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


@register_scenario("scenario_farm_world_harvest")
class ScenarioFarmWorldHarvest(Scenario):
    """
    Soybean harvest scenario.

    Late September, Harbin. All 64 ridges are at R8 maturity with grain
    moisture in the 13-18% harvest window. The agent must check weather
    (rain forecast creates urgency), confirm field conditions, survey crop
    maturity, then systematically harvest all ridges before the weather
    window closes.

    A realistic harvest shift: the farmer checks weather and soil, reads
    canopy sensors to confirm the field has senesced, flies a drone survey
    to verify uniform maturity, checks tractor fuel (low — must refuel
    before the 16-pass job), tops up, then harvests ridge-by-ridge in
    4-ridge passes (16 passes total for 64 ridges).
    """

    # 2026-09-25 08:00 CST (UTC+8)
    start_time: float | None = (
        datetime(2026, 9, 25, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 30000
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        aui = AgentUserInterface()
        farm_world = FarmWorldApp()
        weather = WeatherApp()
        sensor = SensorApp(farm_world_app=farm_world)
        drone = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Mavic3M",
            description="DJI Mavic 3M multispectral drone",
            speed_ms=5.0,
            effective_ridges_per_pass=7,
            takeoff_overhead_s=30,
            min_battery_pct=20.0,
            battery_pct_per_ridge=1.0,
        )
        robot = RobotApp(farm_world_app=farm_world, weather_app=weather)
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()

        self.apps = [
            aui,
            farm_world,
            weather,
            sensor,
            drone,
            robot,
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

        # Late September harvest weather: clear today, rain forecast day after tomorrow
        weather.set_weather(
            date="2026-09-25",
            temp_c=18.0,
            humidity_pct=45.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=380.0,
            forecast=[
                {
                    "date": "2026-09-26",
                    "temp_c": 19.0,
                    "humidity_pct": 50.0,
                    "wind_speed_ms": 2.5,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 400.0,
                },
                {
                    "date": "2026-09-27",
                    "temp_c": 16.0,
                    "humidity_pct": 75.0,
                    "wind_speed_ms": 6.0,
                    "rainfall_mm": 12.0,
                    "solar_radiation": 150.0,
                },
            ],
            avg_soil_vwc=0.24,
        )

        farm_world.set_season_phase("harvest")

        # All 64 ridges planted ~125 days ago, now at R8 maturity
        # Grain moisture ~15% (within 13-18% harvest window)
        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.seed_spacing_cm = 12.0
            r.seeds_planted = 4467  # typical for 12cm spacing
            r.days_since_planted = 125
            r.growth_stage = "R8"
            r.grain_moisture_pct = 15.0 + ((i % 5) - 2) * 0.3  # 14.4-15.6%
            r.soil_vwc = 0.24 + ((i % 4) - 1.5) * 0.01
            r.soil_temp_c = 12.0 + (i % 3) * 0.2
            r.yield_potential = 0.93 + (i % 7) * 0.01  # slight variation 0.93-0.99
            r.ndvi = 0.35 + (i % 5) * 0.02  # R8 stage, senescing
            r.canopy_temp_c = 20.0 + (i % 3) * 0.5

        # Tractor fuel: 20 L — NOT enough for 16 passes × 2 L = 32 L.
        # Farmer must refuel before going out.
        tractor._fuel_tank_l = 20.0
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]


    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        drone = self.get_typed_app(DroneApp, app_name="Mavic3M")
        tractor = self.get_typed_app(TractorApp)

        with EventRegisterer.capture_mode():
            # --- Briefing ---
            briefing = aui.send_message_to_agent(
                content=(
                    "大豆已成熟，进入收获阶段。请按真实农民流程操作：\n"
                    "1. 查看今天天气（rainfall=0，可收割）和 3 天预报"
                     "2. 读土壤传感器，确认 VWC<0.35、地面可通行。\n"
                    "3. 读冠层传感器，看 NDVI 是否全田枯黄（R8 应该偏低）。\n"
                    "4. 确认所有 64 条垄都到 R8 且籽粒含水 13-18%。\n"
                    "5. 用 Mavic3M 飞一圈（fly_survey 0-63）验证均匀成熟。\n"
                    "6. 检查拖拉机：油只有 20 L → 先 refuel 到 100 L。\n"
                    "7. 一趟 4 垄，共 16 趟，从 0-3 开始到 60-63。\n"
                    "   每趟约 980 kg 粮食进入储罐（容量 2000 kg），\n"
                    "   所以每 2 趟就要 unload_grain 一次把粮食卸到仓库。\n"
                    "8. 全部收割完 + 卸完粮后，看 inventory 汇报总产量。"
                )
            ).depends_on(None, delay_seconds=5)

            # --- Pre-harvest checks ---
            oracle_check_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("oracle_check_weather")
                .depends_on(briefing, delay_seconds=2)
            )

            oracle_check_forecast = (
                weather.get_forecast()
                .oracle()
                .with_id("oracle_check_forecast")
                .depends_on(oracle_check_weather, delay_seconds=1)
            )

            oracle_read_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("oracle_read_soil")
                .depends_on(oracle_check_forecast, delay_seconds=1)
            )

            oracle_read_canopy = (
                sensor.read_canopy_sensors()
                .oracle()
                .with_id("oracle_read_canopy")
                .depends_on(oracle_read_soil, delay_seconds=1)
            )

            oracle_farm_overview = (
                farm_world.get_farm_overview()
                .oracle()
                .with_id("oracle_farm_overview")
                .depends_on(oracle_read_canopy, delay_seconds=1)
            )

            # --- Drone survey to confirm uniform maturity ---
            oracle_drone_survey = (
                drone.fly_survey(start_ridge=0, end_ridge=63)
                .oracle()
                .with_id("oracle_drone_survey")
                .depends_on(oracle_farm_overview, delay_seconds=2)
            )

            # --- Check tractor — fuel is low, must refuel ---
            oracle_check_tractor = (
                tractor.get_status()
                .oracle()
                .with_id("oracle_check_tractor")
                .depends_on(oracle_drone_survey, delay_seconds=1)
            )

            oracle_refuel = (
                tractor.refuel(80.0)
                .oracle()
                .with_id("oracle_refuel")
                .depends_on(oracle_check_tractor, delay_seconds=2)
            )

            # --- Harvest all 64 ridges in 16 passes (4 ridges per pass).
            # Grain bin holds 2000 kg ≈ 2 passes; unload after every 2 passes. ---
            prev = oracle_refuel
            field_events: list = []  # harvest + unload interleaved in dep order
            for pass_idx in range(16):
                start_ridge = pass_idx * 4
                end_ridge = start_ridge + 3
                oracle_harvest = (
                    tractor.harvest(start_ridge=start_ridge, end_ridge=end_ridge)
                    .oracle()
                    .with_id(f"oracle_harvest_pass_{pass_idx + 1}")
                    .depends_on(prev, delay_seconds=2)
                )
                field_events.append(oracle_harvest)
                prev = oracle_harvest

                # After every 2nd pass (indices 1, 3, 5, ..., 15) the bin is
                # near-full and must be unloaded before the next pass.
                if pass_idx % 2 == 1:
                    oracle_unload = (
                        tractor.unload_grain()
                        .oracle()
                        .with_id(f"oracle_unload_{(pass_idx + 1) // 2}")
                        .depends_on(prev, delay_seconds=2)
                    )
                    field_events.append(oracle_unload)
                    prev = oracle_unload

            # --- Check inventory ---
            oracle_check_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("oracle_check_inventory")
                .depends_on(prev, delay_seconds=2)
            )

            # --- Report completion ---
            oracle_report = (
                aui.send_message_to_user(content="收获完成，全部64条垄已收割入库。")
                .oracle()
                .with_id("oracle_report_completion")
                .depends_on(oracle_check_inventory, delay_seconds=2)
            )

        self.events = [
            briefing,
            oracle_check_weather,
            oracle_check_forecast,
            oracle_read_soil,
            oracle_read_canopy,
            oracle_farm_overview,
            oracle_drone_survey,
            oracle_check_tractor,
            oracle_refuel,
            *field_events,
            oracle_check_inventory,
            oracle_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        step_specs = [
            OracleStepSpec(function_name="get_current_weather", class_name="WeatherApp"),
            OracleStepSpec(function_name="get_forecast", class_name="WeatherApp"),
            OracleStepSpec(function_name="read_soil_sensors", class_name="SensorApp"),
            OracleStepSpec(function_name="read_canopy_sensors", class_name="SensorApp"),
            OracleStepSpec(function_name="get_farm_overview", class_name="FarmWorldApp"),
            OracleStepSpec(
                function_name="fly_survey",
                class_name="DroneApp",
                penalty_if_repeated=0.03,
            ),
            OracleStepSpec(function_name="get_status", class_name="TractorApp"),
            OracleStepSpec(
                function_name="refuel",
                class_name="TractorApp",
                penalty_if_repeated=0.05,
            ),
        ]

        # 16 harvest passes + unload after every 2 passes (8 unloads).
        for pass_idx in range(16):
            step_specs.append(
                OracleStepSpec(
                    function_name="harvest",
                    class_name="TractorApp",
                    penalty_if_repeated=0.1,  # harvesting same ridge twice is bad
                )
            )
            if pass_idx % 2 == 1:
                step_specs.append(
                    OracleStepSpec(
                        function_name="unload_grain",
                        class_name="TractorApp",
                        penalty_if_repeated=0.05,
                    )
                )

        step_specs.extend([
            OracleStepSpec(function_name="get_inventory", class_name="FarmWorldApp"),
            OracleStepSpec(
                function_name="send_message_to_user",
                class_name="AgentUserInterface",
                penalty_if_repeated=0.05,
            ),
        ])

        return oracle_validate(
            scenario=self,
            env=env,
            step_specs=step_specs,
            success_threshold=0.85,
            harmless_extra_penalty=0.02,
        )
