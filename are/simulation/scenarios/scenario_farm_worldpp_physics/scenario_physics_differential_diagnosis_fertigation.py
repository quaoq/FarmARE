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

# NOTE:
# These are physics-aware scenario scaffolds. They intentionally reference
# several tools that may not exist yet in the current FarmWorld apps.
# Assumed tools are marked inline. The goal is to define oracle structure
# and scenario logic first, then update the apps/tools to support them.

_ANOMALY_START = 28
_ANOMALY_END = 35

@register_scenario("scenario_physics_differential_diagnosis_fertigation")
class ScenarioPhysicsDifferentialDiagnosisFertigation(Scenario):
    """
    L2 episode: diagnose low NDVI as nutrient stress rather than drought/pest.

    Objective:
        A block has reduced NDVI. The oracle checks soil moisture, thermal
        imagery, SPAD/ground inspection, and pest evidence before choosing
        fertigation instead of irrigation or pesticide.
    """

    start_time: float | None = (
        datetime(2026, 6, 8, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 24000
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

    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        tractor = self.get_typed_app(TractorApp)

        weather.set_weather(
            date="2026-06-08",
            temp_c=24.0,
            humidity_pct=50.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=520.0,
            forecast=[
                {"date": "2026-06-09", "temp_c": 25.0, "humidity_pct": 48.0, "wind_speed_ms": 2.0, "rainfall_mm": 0.0, "solar_radiation": 530.0},
                {"date": "2026-06-10", "temp_c": 24.0, "humidity_pct": 55.0, "wind_speed_ms": 3.0, "rainfall_mm": 2.0, "solar_radiation": 460.0},
            ],
            avg_soil_vwc=0.24,
        )
        farm_world.set_season_phase("growing")
        mavic._battery_pct = 85.0
        matrice._battery_pct = 75.0
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 70.0

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.days_since_planted = 38
            r.growth_stage = "V4"
            r.soil_vwc = 0.24
            r.soil_temp_c = 20.0
            r.pest_pressure = 0.02
            r.disease_pressure = 0.02
            if _ANOMALY_START <= i <= _ANOMALY_END:
                r.ndvi = 0.48
                r.canopy_temp_c = 25.0  # not thermally stressed
                r.nutrient_index = 0.55
                r.yield_potential = 0.78
            else:
                r.ndvi = 0.68
                r.canopy_temp_c = 24.8
                r.nutrient_index = 0.95
                r.yield_potential = 0.95

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        farm_world = self.get_typed_app(FarmWorldApp)

        briefing_text = (
            "V4阶段出现一块低NDVI区域。请诊断原因，不要直接默认是缺水或虫害。"
            "需要区分水分胁迫、虫害/病害和营养缺乏；如果确认是营养问题，用垄级肥水系统处理。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id("o_weather").depends_on(briefing, delay_seconds=2)
            o_forecast = weather.get_forecast(days=3).oracle().with_id("o_forecast").depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id("o_soil_not_dry").depends_on(o_forecast, delay_seconds=1)
            o_canopy = sensor.read_canopy_sensors().oracle().with_id("o_canopy_low_ndvi").depends_on(o_soil, delay_seconds=1)
            o_mavic = mavic.check_status().oracle().with_id("o_check_mavic").depends_on(o_canopy, delay_seconds=1)
            o_ndvi = mavic.fly_survey(_ANOMALY_START - 2, _ANOMALY_END + 2).oracle().with_id("o_uav_ndvi_map").depends_on(o_mavic, delay_seconds=2)
            o_matrice = matrice.check_status().oracle().with_id("o_check_thermal_drone").depends_on(o_ndvi, delay_seconds=1)
            o_thermal = matrice.fly_survey(_ANOMALY_START - 2, _ANOMALY_END + 2).oracle().with_id("o_thermal_no_hotspot").depends_on(o_matrice, delay_seconds=2)

            # ASSUMED TOOL: ground inspection includes SPAD/nutrient status and pest absence.
            o_ground = robot.inspect_crop_health(_ANOMALY_START, _ANOMALY_END).oracle().with_id("o_ground_spad_confirm_nutrient").depends_on(o_thermal, delay_seconds=2)
            o_inventory = farm_world.get_inventory().oracle().with_id("o_check_fertilizer_inventory").depends_on(o_ground, delay_seconds=1)

            # ASSUMED TOOL: ridge-level liquid nutrient delivery/fertigation rack.
            o_fertigate = farm_world.apply_fertigation(_ANOMALY_START, _ANOMALY_END, nutrient_amount=0.8, water_mm=6.0).oracle().with_id("o_apply_ridge_fertigation").depends_on(o_inventory, delay_seconds=2)
            o_wait = self.get_typed_app(SystemApp).advance_time(hours=48).oracle().with_id("o_wait_for_delayed_response").depends_on(o_fertigate, delay_seconds=1)
            o_followup = sensor.read_canopy_sensors().oracle().with_id("o_followup_canopy_response").depends_on(o_wait, delay_seconds=1)
            o_report = aui.send_message_to_user(content="已确认低NDVI主要来自营养胁迫，并完成垄级肥水处理与延迟复查。").oracle().with_id("o_report").depends_on(o_followup, delay_seconds=2)

        self.events = [briefing, o_weather, o_forecast, o_soil, o_canopy, o_mavic, o_ndvi, o_matrice, o_thermal, o_ground, o_inventory, o_fertigate, o_wait, o_followup, o_report]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(
            success=True,
            rationale="scaffold scenario: oracle/evaluation hooks to be implemented after tool integration",
        )
        return append_workflow_evaluation(self, env, result)
