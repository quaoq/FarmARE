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
    after_any_of,
    after_observation,
    and_,
    arg_equals,
    max_arg,
    min_arg,
    or_,
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

_HOTSPOT_START = 16
_HOTSPOT_END = 27

@register_scenario("scenario_physics_threshold_pest_monitoring")
class ScenarioPhysicsThresholdPestMonitoring(Scenario):
    """
    L2 episode: thresholded pest response, not immediate spraying.

    Objective:
        The oracle monitors a suspected aphid-like outbreak over two days. It
        does not spray on weak evidence. After ground inspection crosses a
        threshold-like condition and weather remains sprayable, it treats only
        the affected block.
    """

    start_time: float | None = (
        datetime(2026, 6, 20, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 3 * 24 * 3600
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
        tractor = self.get_typed_app(TractorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")

        weather.set_weather(
            date="2026-06-20",
            temp_c=25.0,
            humidity_pct=55.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=520.0,
            forecast=[
                {"date": "2026-06-21", "temp_c": 27.0, "humidity_pct": 50.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 540.0},
                {"date": "2026-06-22", "temp_c": 28.0, "humidity_pct": 48.0, "wind_speed_ms": 2.5, "rainfall_mm": 0.0, "solar_radiation": 550.0},
            ],
            avg_soil_vwc=0.24,
        )
        farm_world.set_season_phase("growing")
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 60.0
        tractor._pesticide_tank_l = 0.0
        mavic._battery_pct = 90.0

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.days_since_planted = 50
            r.growth_stage = "V4"
            r.soil_vwc = 0.24
            if _HOTSPOT_START <= i <= _HOTSPOT_END:
                # Write to the engine-truth field; the legacy r.pest_pressure
                # is reset on each tick from biotic state via the orchestrator's
                # compatibility shadow. Below treatment threshold on day 0;
                # physics grows it past threshold on day 1.
                r.pest_pressure_base = 0.30
                r.pest_pressure = 0.30
                r.ndvi = 0.62
                r.ndvi_proxy = 0.62
                r.canopy_temp_c = 25.5
            else:
                r.pest_pressure_base = 0.02
                r.pest_pressure = 0.02
                r.ndvi = 0.72
                r.ndvi_proxy = 0.72
                r.canopy_temp_c = 24.5

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        system = self.get_typed_app(SystemApp)

        briefing_text = (
            "传感器显示有轻微虫害迹象。请不要见到异常就喷药；先判断是否达到阈值，"
            "若未达到则监测一天。若虫口压力继续上升并经地面确认，再按受影响范围喷药。"
        )

        with EventRegisterer.capture_mode():
            # Suspect zones are C2 (11-21) and C3 (22-32). Survey covers full
            # zones rather than the narrower hotspot 16-27 — that's the
            # observation pattern the sensor → drone → robot pipeline expects.
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id("o_day0_weather").depends_on(briefing, delay_seconds=2)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id("o_day0_canopy_weak_signal").depends_on(o_weather, delay_seconds=1)
            o_drone = mavic.check_status().oracle().with_id("o_day0_check_drone").depends_on(o_canopy, delay_seconds=1)
            o_survey = mavic.fly_survey(11, 32).oracle().with_id("o_day0_survey").depends_on(o_drone, delay_seconds=2)
            o_robot_status0 = robot.check_status().oracle().with_id("o_day0_robot_status").depends_on(o_survey, delay_seconds=1)
            o_ground0 = robot.inspect_pests(_HOTSPOT_START + 4, _HOTSPOT_START + 6).oracle().with_id("o_day0_ground_below_threshold").depends_on(o_robot_status0, delay_seconds=2)

            # advance one daily physics step so biotic pressure can evolve.
            o_wait = system.advance_time(hours=24).oracle().with_id("o_wait_one_day_for_pressure_trend").depends_on(o_ground0, delay_seconds=1)
            o_weather1 = weather.get_current_weather().oracle().with_id("o_day1_weather_sprayable").depends_on(o_wait, delay_seconds=1)
            o_survey1 = mavic.fly_survey(11, 32).oracle().with_id("o_day1_survey_worse").depends_on(o_weather1, delay_seconds=2)
            o_robot_status1 = robot.check_status().oracle().with_id("o_day1_robot_status").depends_on(o_survey1, delay_seconds=1)
            o_ground1 = robot.inspect_pests(_HOTSPOT_START + 4, _HOTSPOT_START + 6).oracle().with_id("o_day1_ground_threshold_met").depends_on(o_robot_status1, delay_seconds=2)
            o_tractor = tractor.get_status().oracle().with_id("o_check_tractor").depends_on(o_ground1, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id("o_check_pesticide_inventory").depends_on(o_tractor, delay_seconds=1)
            o_refill = tractor.load_pesticide(120.0).oracle().with_id("o_load_insecticide").depends_on(o_inventory, delay_seconds=2)
            # spray_pesticide max_width = 10; hotspot 16-27 is 12 ridges. Split.
            o_spray_a = tractor.spray_pesticide(_HOTSPOT_START, _HOTSPOT_START + 9, liters_per_ridge=6.0).oracle().with_id("o_spray_threshold_block_a").depends_on(o_refill, delay_seconds=2)
            o_spray_b = tractor.spray_pesticide(_HOTSPOT_START + 10, _HOTSPOT_END, liters_per_ridge=6.0).oracle().with_id("o_spray_threshold_block_b").depends_on(o_spray_a, delay_seconds=2)
            o_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_insecticide_effect").depends_on(o_spray_b, delay_seconds=1)
            o_report = aui.send_message_to_user(content="已等待趋势确认，达到阈值后对16-27垄完成虫害处理。").oracle().with_id("o_report").depends_on(o_commit, delay_seconds=2)

        self.events = [briefing, o_weather, o_canopy, o_drone, o_survey, o_robot_status0, o_ground0, o_wait, o_weather1, o_survey1, o_robot_status1, o_ground1, o_tractor, o_inventory, o_refill, o_spray_a, o_spray_b, o_commit, o_report]

    def _configure_physics_layers(self) -> None:
        """Activate physics for this round-3 episode."""
        farm_world = self.get_typed_app(FarmWorldApp)
        farm_world.configure_physics_profile(
            profile_name="physics_threshold_pest",
            location="Harbin/Heilongjiang",
            scenario_type="threshold_pest_monitoring",
        )
        # Pest pressure on hotspot block; ground-truth grows over time.
        physics = farm_world.physics
        for i in range(64):
            soil = physics.soil.states[i]
            ridge = farm_world._ridges[i]
            soil.top_vwc = float(ridge.soil_vwc)
            soil.root_vwc = float(ridge.soil_vwc)
            biotic = physics.biotic.states[i]
            biotic.insect_pressure = max(
                biotic.insect_pressure,
                float(getattr(ridge, "pest_pressure_base", 0.0)),
            )

    def _gates(self) -> list[GateSpec]:
        """FOS Decision-component gates for this episode."""
        return [
            GateSpec(
                name="G1_initial_pest_observation",
                intent="agent observes pest pressure (drone+sensor)",
                window_days=(0.0, 0.5),
                eligible_tools=[("Mavic3M", "fly_survey"), ("Matrice4T", "fly_survey")],
            ),
            GateSpec(
                name="G2_advance_time_for_trend",
                intent="agent waits ~1 day to observe trend before deciding",
                window_days=(0.0, 2.0),
                eligible_tools=[("SystemApp", "advance_time")],
            ),
            GateSpec(
                name="G3_re_observe_after_wait",
                intent="re-observe pest pressure after the trend wait",
                window_days=(0.5, 3.0),
                eligible_tools=[("Mavic3M", "fly_survey"), ("Matrice4T", "fly_survey")],
                requires=after_observation("SystemApp", "advance_time"),
            ),
            GateSpec(
                name="G4_robot_threshold_confirm",
                intent="robot ground-confirms pest before spray",
                window_days=(0.5, 3.0),
                eligible_tools=[("Robot0", "inspect_pests")],
            ),
            GateSpec(
                name="G5_targeted_spray",
                intent="spray only confirmed hotspot, not the whole field",
                window_days=(0.5, 3.0),
                eligible_tools=[("TractorApp", "spray_pesticide"), ("TractorApp", "apply_pesticide")],
                requires=after_observation("Robot0", "inspect_pests"),
            ),
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="round-3 episode")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result
