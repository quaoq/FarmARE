"""
Observation-level checks for FarmWorld++ physics scenario intents.

These tests verify the closed-loop claim used by the farm_worldpp_physics
oracles: after a management action and a short physics delay, agent-visible
observations move in the expected direction.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from are.simulation.apps.farm_world import (
    FarmWorldApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp, DroneApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.physics import SoybeanStage
from are.simulation.physics.canopy_biomass_engine import SeedType as CanopySeedType


def _build_v4_physics_world() -> tuple[
    FarmWorldApp,
    WeatherApp,
    SensorApp,
    TractorApp,
    RobotApp,
    SystemApp,
]:
    farm_world = FarmWorldApp()
    weather = WeatherApp()
    sensor = SensorApp(farm_world_app=farm_world)
    tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
    robot = RobotApp(farm_world_app=farm_world, weather_app=weather, name="Robot0")
    system = SystemApp()
    farm_world.attach_system_app(system)

    start = datetime(2026, 6, 20, 7, 0, tzinfo=timezone.utc).timestamp()
    for app in (farm_world, weather, sensor, tractor, robot, system):
        app.time_manager.reset(start)

    weather.set_weather(
        date="2026-06-20",
        temp_c=27.0,
        humidity_pct=50.0,
        wind_speed_ms=2.0,
        rainfall_mm=0.0,
        solar_radiation=540.0,
        forecast=[
            {
                "date": "2026-06-21",
                "temp_c": 27.0,
                "humidity_pct": 50.0,
                "wind_speed_ms": 2.0,
                "rainfall_mm": 0.0,
                "solar_radiation": 540.0,
            },
            {
                "date": "2026-06-22",
                "temp_c": 27.0,
                "humidity_pct": 50.0,
                "wind_speed_ms": 2.0,
                "rainfall_mm": 0.0,
                "solar_radiation": 540.0,
            },
        ],
        avg_soil_vwc=0.24,
    )
    farm_world.configure_physics_profile(
        profile_name="test_farm_worldpp_observation_outcomes",
        location="Harbin/Heilongjiang",
        scenario_type="test",
    )

    for ridge in farm_world._ridges:
        ridge.planted = True
        ridge.seed_type = "STANDARD"
        ridge.days_since_planted = 50
        ridge.growth_stage = "V4"
        ridge.soil_vwc = 0.24
        ridge.soil_temp_c = 20.0

    farm_world.advance_physics_time()

    for ridge_id in range(farm_world.num_ridges):
        phenology = farm_world.physics.phenology.states[ridge_id]
        phenology.planted = True
        phenology.stage = SoybeanStage.V4_PLUS
        phenology.emerged = True
        phenology.days_after_planting = 50

        canopy = farm_world.physics.canopy.states[ridge_id]
        if not canopy.initialized:
            farm_world.physics.canopy.initialize_ridges(
                [ridge_id],
                seed_type=CanopySeedType.STANDARD,
                initial_stand_fraction=1.0,
            )
        canopy.lai = 1.2
        canopy.ndvi_proxy = 0.55

        management = farm_world.physics.management.states[ridge_id]
        management.planted = True
        management.stand_fraction = 1.0
        management.nutrient_index = 0.95
        management.nutrient_stress = 1.0

        farm_world.physics.biotic.states[ridge_id].insect_pressure = 0.05

    return farm_world, weather, sensor, tractor, robot, system


def _pest_present_count(observation: dict) -> int:
    return sum(
        1
        for item in observation["observations"].values()
        if item.get("pest_present") is True
    )


def test_fertigation_followup_canopy_observation_matches_oracle_design():
    farm_world, _weather, sensor, _tractor, _robot, system = _build_v4_physics_world()

    # C3 covers ridges 22-32. Make that block visibly nutrient-limited.
    for ridge_id in range(22, 33):
        management = farm_world.physics.management.states[ridge_id]
        management.nutrient_index = 0.45
        management.nutrient_stress = 0.65
        farm_world.physics.canopy.states[ridge_id].ndvi_proxy = 0.42

    before = sensor.read_canopy_sensor("C3")
    before_nutrient = farm_world.physics.management.states[25].nutrient_index

    result = farm_world.apply_fertigation(
        22,
        32,
        nutrient_amount=1.5,
        water_mm=6.0,
    )
    assert result["status"] == "ok"

    system.advance_time(days=2)
    after = sensor.read_canopy_sensor("C3")
    after_nutrient = farm_world.physics.management.states[25].nutrient_index

    assert after_nutrient > before_nutrient
    assert after["ndvi_proxy"] > before["ndvi_proxy"] + 0.04


def test_insecticide_followup_robot_observation_matches_oracle_design():
    farm_world, _weather, _sensor, tractor, robot, system = _build_v4_physics_world()

    for ridge_id in range(16, 20):
        farm_world.physics.biotic.states[ridge_id].insect_pressure = 0.65

    before_truth = farm_world.physics.biotic.states[18].insect_pressure
    before_observation = robot.inspect_pests(16, 19)

    load_result = tractor.load_pesticide(50.0)
    assert load_result["status"] == "ok"
    spray_result = tractor.spray_pesticide(16, 19, liters_per_ridge=6.0)
    assert spray_result["status"] == "ok"

    system.advance_time(days=2)
    after_observation = robot.inspect_pests(16, 19)
    after_truth = farm_world.physics.biotic.states[18].insect_pressure

    assert after_truth < before_truth * 0.5
    assert _pest_present_count(after_observation) < _pest_present_count(
        before_observation
    )


def test_disease_pressure_base_propagates_to_engine_and_robot_observation():
    """Prove that setting disease_pressure_base on RidgeState correctly flows
    through _seed_physics_from_ridges into the biotic engine, and that the
    robot's ground inspection detects disease in the affected zone."""
    farm_world = FarmWorldApp()
    weather = WeatherApp()

    robot = RobotApp(farm_world_app=farm_world, weather_app=weather, name="Robot0")
    sensor = SensorApp(farm_world_app=farm_world)

    system = SystemApp()
    farm_world.attach_system_app(system)

    start = datetime(2026, 7, 12, 7, 0, tzinfo=timezone.utc).timestamp()
    for app in (farm_world, weather, robot, system):
        app.time_manager.reset(start)

    weather.set_weather(
        date="2026-07-12",
        temp_c=22.0,
        humidity_pct=70.0,
        wind_speed_ms=2.0,
        rainfall_mm=0.0,
        solar_radiation=400.0,
        forecast=[],
    )
    farm_world.configure_physics_profile(
        profile_name="test_disease_base_propagation",
        location="Harbin/Heilongjiang",
        scenario_type="test",
    )

    disease_zone = range(34, 47)
    for ridge in farm_world._ridges:
        ridge.planted = True
        ridge.seed_type = "STANDARD"
        ridge.days_since_planted = 72
        ridge.growth_stage = "R1"
        ridge.soil_vwc = 0.28
        ridge.soil_temp_c = 20.0
        if ridge.ridge_id in disease_zone:
            ridge.disease_pressure_base = 0.38
        else:
            ridge.disease_pressure_base = 0.03

    farm_world.advance_physics_time()

    # Engine state reflects the base values.
    assert farm_world.physics.biotic.states[38].disease_pressure >= 0.38
    assert farm_world.physics.biotic.states[10].disease_pressure <= 0.05
    canopy_result = sensor.read_canopy_sensors()
    # Robot detects disease in the affected zone.
    result = robot.inspect_crop_health(38, 40)
    detected = [
        rid
        for rid, obs in result["observations"].items()
        if obs["disease_present"]
    ]
    assert len(detected) >= 2

    # Normal zone has few or no false positives.
    result_normal = robot.inspect_crop_health(10, 12)
    false_positives = [
        rid
        for rid, obs in result_normal["observations"].items()
        if obs["disease_present"]
    ]
    assert len(false_positives) <= 1


def test_disease_after_rain_scenario_engine_state_matches_intent():
    """Using ScenarioPhysicsDiseaseAfterRainFungicide directly, verify that
    disease_pressure propagates to the biotic engine and robot can detect it."""
    from are.simulation.scenarios.scenario_farm_worldpp_physics.scenario_physics_disease_after_rain_fungicide import (
        ScenarioPhysicsDiseaseAfterRainFungicide,
    )

    scenario = ScenarioPhysicsDiseaseAfterRainFungicide()
    scenario.init_and_populate_apps()

    farm_world = scenario.get_typed_app(FarmWorldApp)
    robot = scenario.get_typed_app(RobotApp, "Robot0")
    weather = scenario.get_typed_app(WeatherApp)
    system = scenario.get_typed_app(SystemApp)

    sensor = scenario.get_typed_app(SensorApp)
    mavic = scenario.get_typed_app(DroneApp, "Mavic3M")
    matrice = scenario.get_typed_app(DroneApp, "Matrice4T")
    robot = scenario.get_typed_app(RobotApp, "Robot0")

    o_soil = sensor.read_soil_sensors()
    o_mavic = mavic.check_status()
    o_ndvi = mavic.fly_survey(32, 48)
    o_thermal = matrice.fly_survey(32, 48)
    o_canopy = sensor.read_canopy_sensors()
    o_weather = weather.get_current_weather()
    o_forecast = weather.get_forecast(3)

    # Sanity: scenario set up day-0 narrative weather and a 2-day forecast.
    assert o_weather["date"] == "2026-07-12"
    assert o_weather["rainfall_mm"] == pytest.approx(3.0)
    assert [f["date"] for f in o_forecast["forecast"]] == [
        "2026-07-13",
        "2026-07-14",
    ]

    system.advance_time(hours=24)

    o_soil_1 = sensor.read_soil_sensors()
    o_mavic_1 = mavic.check_status()
    o_ndvi_1 = mavic.fly_survey(32, 48)
    o_thermal_1 = matrice.fly_survey(32, 48)
    o_canopy_1 = sensor.read_canopy_sensors()
    o_weather_1 = weather.get_current_weather()
    o_forecast_1 = weather.get_forecast(3)

    # After advance_time(24h), the WeatherApp's narrative date must roll
    # forward by exactly one day and pick up the matching forecast entry —
    # confirming the agent sees a sprayable window (rain 0, low wind), not
    # a stale day-0 readout or a wall-clock-derived date drift.
    assert o_weather_1["date"] == "2026-07-13"
    assert o_weather_1["rainfall_mm"] == pytest.approx(0.0)
    assert o_weather_1["wind_speed_ms"] == pytest.approx(3.0)
    assert [f["date"] for f in o_forecast_1["forecast"]] == ["2026-07-14"]

    # Robot should detect disease in the affected zone.
    result = robot.inspect_crop_health(38, 40)
    detected = [
        rid for rid, obs in result["observations"].items() if obs["disease_present"]
    ]
    assert len(detected) >= 2, f"Expected disease detection, got {detected}"
