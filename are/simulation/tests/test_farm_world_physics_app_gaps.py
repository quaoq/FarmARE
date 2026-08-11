"""
Known integration gaps between FarmWorldApp and the physics engine.

These tests are intentionally xfail: they encode the behaviour the scenario
runner will eventually need from the app/engine boundary, while documenting
the current failure mode without changing production code.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from are.simulation.apps.farm_world import FarmWorldApp, FieldOpsApp, WeatherApp
from are.simulation.apps.system import SystemApp


def _build_physics_world() -> tuple[FarmWorldApp, WeatherApp, FieldOpsApp, SystemApp]:
    farm_world = FarmWorldApp()
    weather = WeatherApp()
    field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
    system = SystemApp()
    farm_world.attach_system_app(system)

    start = datetime(2026, 5, 20, 7, 0, tzinfo=timezone.utc).timestamp()
    for app in (farm_world, weather, field_ops, system):
        app.time_manager.reset(start)

    weather.set_weather(
        date="2026-05-20",
        temp_c=24.0,
        humidity_pct=40.0,
        wind_speed_ms=2.0,
        rainfall_mm=0.0,
        solar_radiation=520.0,
        forecast=[
            {
                "date": "2026-05-21",
                "temp_c": 12.0,
                "humidity_pct": 85.0,
                "wind_speed_ms": 4.0,
                "rainfall_mm": 20.0,
                "solar_radiation": 120.0,
            }
        ],
        avg_soil_vwc=0.18,
    )
    farm_world.configure_physics_profile(
        profile_name="test_static_weather_gap",
        location="Harbin/Heilongjiang",
        scenario_type="test",
    )
    farm_world.advance_physics_time()
    return farm_world, weather, field_ops, system


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FarmWorldApp.get_state/load_state serialize only legacy ridge/inventory "
        "fields; FarmPhysicsState engine state and action queues are dropped."
    ),
)
def test_physics_state_survives_farm_world_state_roundtrip():
    farm_world, _weather, _field_ops, _system = _build_physics_world()
    farm_world.physics.soil.states[7].top_vwc = 0.41
    farm_world.physics.soil.states[7].root_vwc = 0.39
    farm_world.advance_physics_time()

    restored = FarmWorldApp()
    restored.load_state(farm_world.get_state())

    assert restored.physics_active is True
    assert restored.physics.soil.states[7].top_vwc == pytest.approx(0.41)
    assert restored.physics.soil.states[7].root_vwc == pytest.approx(0.39)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FarmWorldApp.reset resets legacy fields but leaves the lazily-created "
        "FarmPhysicsState attached and active."
    ),
)
def test_farm_world_reset_clears_active_physics_state():
    farm_world, _weather, _field_ops, _system = _build_physics_world()

    assert farm_world.physics_active is True
    farm_world.reset()

    assert farm_world.physics_active is False
    assert farm_world._physics is None


def test_system_time_advance_promotes_static_weather_forecast_for_physics_ticks():
    """After SystemApp.advance_time crosses a UTC day boundary, the WeatherApp's
    narrative date and weather values must roll forward via the matching
    forecast entry. The orchestrator advances the WeatherApp's narrative date
    by one day per daily tick, anchored on the snapshot's own date — see
    ``_build_weather_inputs`` in ``physics_orchestrator.py``."""
    _farm_world, weather, _field_ops, system = _build_physics_world()
    current = weather.get_current_weather()

    system.advance_time(days=1)

    current = weather.get_current_weather()
    assert current["date"] == "2026-05-21"
    assert current["rainfall_mm"] == pytest.approx(20.0)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Physics-mode irrigation queues are consumed immediately by "
        "FieldOpsApp._register_irrigation_with_physics, while the tool response "
        "still tells the agent to wait for a delayed soil response."
    ),
)
def test_physics_irrigation_response_respects_reported_effect_ready_at_delay():
    farm_world, _weather, field_ops, _system = _build_physics_world()
    farm_world.physics.soil.states[25].top_vwc = 0.14
    farm_world.physics.soil.states[25].root_vwc = 0.14
    farm_world.advance_physics_time()

    before = farm_world.physics.soil.states[25].top_vwc
    result = field_ops.irrigate(25, 25, hours=1.5)
    after = farm_world.physics.soil.states[25].top_vwc

    assert result["status"] == "irrigation_started"
    assert result["effect_ready_at"] > farm_world.time_manager.time()
    assert after == pytest.approx(before)
