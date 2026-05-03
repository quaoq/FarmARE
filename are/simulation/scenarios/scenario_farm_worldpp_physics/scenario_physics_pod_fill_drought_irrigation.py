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
    after_any_of,
    and_,
    min_arg,
    targets_ridges_overlap,
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

# NOTE:
# These are physics-aware scenario scaffolds. They intentionally reference
# several tools that may not exist yet in the current FarmWorld apps.
# Assumed tools are marked inline. The goal is to define oracle structure
# and scenario logic first, then update the apps/tools to support them.

_DRY_START = 20
_DRY_END = 43

@register_scenario("scenario_physics_pod_fill_drought_irrigation")
class ScenarioPhysicsPodFillDroughtIrrigation(Scenario):
    """
    L2 episode: R5/R6 pod-fill drought irrigation.

    Objective:
        Manage water stress during the sensitive seed-fill window. The oracle
        uses soil sensors, forecast, thermal stress, and crop stage to irrigate
        before yield potential is penalized further.
    """

    start_time: float | None = (
        datetime(2026, 8, 5, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 4 * 24 * 3600
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
            description="Zhiyuan D1 Max #1 — ground-level inspection robot",
        )
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()

        self.apps = [aui, farm_world, weather, sensor, mavic, matrice, robot_0, tractor, field_ops, system]
        self._configure_initial_state()
        farm_world.attach_system_app(system)
        self._configure_physics_layers()

    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        matrice = self.get_typed_app(DroneApp, "Matrice4T")

        weather.set_weather(
            date="2026-08-05",
            temp_c=28.0,
            humidity_pct=35.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=570.0,
            forecast=[
                {"date": "2026-08-06", "temp_c": 29.0, "humidity_pct": 32.0, "wind_speed_ms": 2.5, "rainfall_mm": 0.0, "solar_radiation": 580.0},
                {"date": "2026-08-07", "temp_c": 30.0, "humidity_pct": 30.0, "wind_speed_ms": 3.0, "rainfall_mm": 0.0, "solar_radiation": 590.0},
                {"date": "2026-08-08", "temp_c": 27.0, "humidity_pct": 45.0, "wind_speed_ms": 3.5, "rainfall_mm": 1.0, "solar_radiation": 500.0},
            ],
            avg_soil_vwc=0.17,
        )
        farm_world.set_season_phase("seed_fill")
        matrice._battery_pct = 90.0

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.days_since_planted = 92
            r.growth_stage = "R5"
            if _DRY_START <= i <= _DRY_END:
                r.soil_vwc = 0.15
                r.canopy_temp_c = 31.0
                r.ndvi = 0.70
                r.yield_potential = 0.82
            else:
                r.soil_vwc = 0.22
                r.canopy_temp_c = 27.0
                r.ndvi = 0.76
                r.yield_potential = 0.95

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        field_ops = self.get_typed_app(FieldOpsApp)
        system = self.get_typed_app(SystemApp)

        briefing_text = (
            "大豆进入R5/R6灌浆敏感期，最近持续干旱。请判断是否需要灌溉，"
            "重点是避免灌浆期水分胁迫继续降低产量潜力。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id("o_weather_hot_dry").depends_on(briefing, delay_seconds=2)
            o_forecast = weather.get_forecast(days=4).oracle().with_id("o_forecast_no_rain").depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id("o_read_soil_stress").depends_on(o_forecast, delay_seconds=1)
            o_drone = matrice.check_status().oracle().with_id("o_check_thermal_drone").depends_on(o_soil, delay_seconds=1)
            o_thermal = matrice.fly_survey(_DRY_START, _DRY_END).oracle().with_id("o_thermal_confirm_water_stress").depends_on(o_drone, delay_seconds=2)
            o_irrigate = field_ops.irrigate(_DRY_START, _DRY_END, hours=2.0).oracle().with_id("o_irrigate_dry_seed_fill_block").depends_on(o_thermal, delay_seconds=2)

            # ASSUMED TOOL: update soil bucket and growth stress after irrigation delay.
            o_wait = system.advance_time(hours=6).oracle().with_id("o_wait_for_soil_response").depends_on(o_irrigate, delay_seconds=1)
            o_recheck = sensor.read_soil_sensors().oracle().with_id("o_recheck_soil_after_irrigation").depends_on(o_wait, delay_seconds=1)
            o_commit = self.get_typed_app(FarmWorldApp).commit_daily_physics().oracle().with_id("o_commit_growth_response").depends_on(o_recheck, delay_seconds=1)
            o_report = aui.send_message_to_user(content="已在R5灌浆期对干旱区进行灌溉，并完成土壤复查。").oracle().with_id("o_report").depends_on(o_commit, delay_seconds=2)

        self.events = [briefing, o_weather, o_forecast, o_soil, o_drone, o_thermal, o_irrigate, o_wait, o_recheck, o_commit, o_report]

    def _configure_physics_layers(self) -> None:
        """Activate physics for the pod-fill drought irrigation episode.

        Pushes the dry-zone (ridges 20-43) initial conditions directly to
        the engine state so the orchestrator's seed step doesn't drift
        from the scenario's intent (low VWC + elevated canopy temp at R5/R6).
        """
        farm_world = self.get_typed_app(FarmWorldApp)
        farm_world.configure_physics_profile(
            profile_name="physics_pod_fill_drought",
            location="Harbin/Heilongjiang",
            scenario_type="pod_fill_drought_irrigation",
        )
        physics = farm_world.physics
        for i in range(64):
            soil = physics.soil.states[i]
            ridge = farm_world._ridges[i]
            soil.top_vwc = float(ridge.soil_vwc)
            soil.root_vwc = float(ridge.soil_vwc)

    def _gates(self) -> list[GateSpec]:
        """FOS Decision-component gates for pod-fill drought response.

        The agent must (G1) recognise the dry hot conditions, (G2) verify
        no rain incoming, (G3) detect the dry zone via sensors, (G4) confirm
        with thermal imagery, (G5) irrigate the dry zone, and (G6) verify.
        """
        return [
            GateSpec(
                name="G1_check_weather",
                intent="confirm hot/dry conditions before acting",
                window_days=(0.0, 1.0),
                eligible_tools=[("WeatherApp", "get_current_weather")],
            ),
            GateSpec(
                name="G2_check_forecast",
                intent="rule out incoming rain",
                window_days=(0.0, 1.0),
                eligible_tools=[("WeatherApp", "get_forecast")],
            ),
            GateSpec(
                name="G3_observe_soil",
                intent="quantify soil-moisture stress in pod-fill zone",
                window_days=(0.0, 1.0),
                eligible_tools=[("SensorApp", "read_soil_sensors")],
            ),
            GateSpec(
                name="G4_thermal_confirm",
                intent="thermal drone confirms canopy water stress",
                window_days=(0.0, 1.5),
                eligible_tools=[("Matrice4T", "fly_survey")],
                requires=after_observation("SensorApp", "read_soil_sensors"),
            ),
            GateSpec(
                name="G5_irrigate_dry_zone",
                intent="irrigate pod-fill dry zone (20-43) for >=1.5h",
                window_days=(0.0, 2.0),
                eligible_tools=[
                    ("FieldOpsApp", "irrigate"),
                    ("FieldOpsApp", "irrigate_range"),
                ],
                requires=and_(
                    after_observation("SensorApp", "read_soil_sensors"),
                    targets_ridges_overlap(_DRY_START, _DRY_END),
                    min_arg("hours", 1.0),
                ),
            ),
            GateSpec(
                name="G6_verify_irrigation",
                intent="re-read sensors after the soil-response wait",
                window_days=(0.0, 3.0),
                eligible_tools=[("SensorApp", "read_soil_sensors")],
                requires=after_any_of([
                    ("FieldOpsApp", "irrigate"),
                    ("FieldOpsApp", "irrigate_range"),
                ]),
            ),
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="round-3 episode")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result
