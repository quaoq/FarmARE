"""
Integration tests for the physics orchestrator (`advance_physics_time`).

Verifies:
  - First call seeds engines from initial RidgeState; idempotent thereafter.
  - Sub-daily injection bumps soil VWC for queued irrigation actions.
  - Day-boundary crossings run one engine cycle per UTC date.
  - Compatibility-shadow fields on RidgeState track physics outputs.
  - Action queues drain correctly across mixed sub-daily / day-boundary calls.
"""
from __future__ import annotations

from datetime import datetime, timezone

from are.simulation.apps.farm_world import (
    FarmWorldApp,
    SensorApp,
    TractorApp,
    WeatherApp,
    FieldOpsApp,
)
from are.simulation.physics import (
    ManagementAction,
    ManagementActionType,
)
from are.simulation.apps.farm_world.physics_orchestrator import (
    _ridge_planting_density_plants_m2,
)


def _build_minimal_world(start_iso: str = "2026-05-20T07:00:00+00:00") -> tuple[FarmWorldApp, WeatherApp, TractorApp, FieldOpsApp]:
    fw = FarmWorldApp()
    weather = WeatherApp()
    weather.set_weather(
        date="2026-05-20",
        temp_c=22.0,
        humidity_pct=40.0,
        wind_speed_ms=2.0,
        rainfall_mm=0.0,
        solar_radiation=480.0,
    )
    tractor = TractorApp(farm_world_app=fw, weather_app=weather)
    field_ops = FieldOpsApp(farm_world_app=fw, weather_app=weather)
    fw.time_manager.reset(datetime.fromisoformat(start_iso).timestamp())
    return fw, weather, tractor, field_ops


def test_initial_advance_seeds_from_ridges_and_is_idempotent():
    fw, weather, _t, _fo = _build_minimal_world()
    fw._ridges[10].soil_vwc = 0.18
    fw.configure_physics_profile(profile_name="t", scenario_type="test")

    res1 = fw.advance_physics_time()
    assert res1["status"] == "initialized"
    assert fw.physics.soil.states[10].top_vwc == 0.18

    res2 = fw.advance_physics_time()
    # No simulated time has elapsed → status is noop and no day-ticks.
    assert res2["status"] in {"noop", "advanced"}
    assert res2["day_ticks_run"] == 0


def test_sync_initial_physics_state_seeds_without_day_tick():
    fw, weather, _t, _fo = _build_minimal_world()
    fw._ridges[10].soil_vwc = 0.18
    fw.configure_physics_profile(profile_name="t", scenario_type="test")

    res1 = fw.sync_initial_physics_state()
    assert res1["status"] == "initialized"
    assert res1["day_ticks_run"] == 0
    assert fw.physics.soil.states[10].top_vwc == 0.18

    before = fw.physics.last_physics_sim_time
    fw.time_manager.add_offset(86400)
    res2 = fw.sync_initial_physics_state()
    assert res2["status"] == "already_initialized"
    assert res2["day_ticks_run"] == 0
    assert fw.physics.last_physics_sim_time == before


def test_subdaily_irrigation_lifts_top_vwc_only_within_same_utc_date():
    fw, weather, _t, _fo = _build_minimal_world()
    for rid in range(64):
        fw._ridges[rid].soil_vwc = 0.15
    fw.configure_physics_profile(profile_name="t", scenario_type="test")
    fw.advance_physics_time()
    pre = fw.physics.soil.states[5].top_vwc

    fw.physics.queue_management_action(
        5,
        ManagementAction(action_type=ManagementActionType.IRRIGATION, amount=10.0),
    )
    # Same UTC date — sub-daily.
    fw.time_manager.add_offset(2 * 3600)
    res = fw.advance_physics_time()
    assert res["subdaily_irrigation"] is True
    assert res["day_ticks_run"] == 0
    assert fw.physics.soil.states[5].top_vwc > pre


def test_day_boundary_runs_full_daily_tick():
    fw, weather, _t, _fo = _build_minimal_world()
    fw.configure_physics_profile(profile_name="t", scenario_type="test")
    fw.advance_physics_time()

    fw.time_manager.add_offset(86400 + 3600)  # cross UTC midnight by 1 hour
    res = fw.advance_physics_time()
    assert res["day_ticks_run"] >= 1
    assert res["status"] == "advanced"


def test_compat_shadow_syncs_vwc_and_growth_stage():
    fw, weather, _t, _fo = _build_minimal_world()
    fw.configure_physics_profile(profile_name="t", scenario_type="test")
    fw.advance_physics_time()
    fw.physics.soil.states[3].top_vwc = 0.42
    fw.physics.soil.states[3].top_temp_c = 19.5

    fw.time_manager.add_offset(60)
    fw.advance_physics_time()
    # The compat sync writes top_vwc -> ridge.soil_vwc.
    # (Soil engine state was directly mutated above, no daily tick ran, so
    # the value just needs to round-trip through sync_compatibility_fields.)
    # advance_physics_time(60) is sub-daily and should still call sync.
    # Direct mutations bypass the sub-daily injection (no pending actions),
    # so we use the `noop` path which doesn't sync. To force a sync, queue
    # an action.
    fw.physics.queue_management_action(
        3, ManagementAction(action_type=ManagementActionType.IRRIGATION, amount=0.0001)
    )
    fw.advance_physics_time()
    assert fw._ridges[3].soil_vwc > 0.20


def test_queue_drains_after_consumption():
    fw, weather, _t, _fo = _build_minimal_world()
    fw.configure_physics_profile(profile_name="t", scenario_type="test")
    fw.advance_physics_time()

    fw.physics.queue_management_action(
        0, ManagementAction(action_type=ManagementActionType.IRRIGATION, amount=5.0)
    )
    assert fw.physics.pending_management_actions_by_ridge

    fw.time_manager.add_offset(3600)
    fw.advance_physics_time()
    # Sub-daily injection consumed the queue.
    assert not fw.physics.pending_management_actions_by_ridge


def test_ridge_planting_density_uses_ridge_width_and_seed_spacing():
    fw, _weather, _tractor, _field_ops = _build_minimal_world()
    fw.ridge_width_m = 1.1
    fw._ridges[0].seed_spacing_cm = 8.1
    fw._ridges[0].seeds_planted = 0

    density = _ridge_planting_density_plants_m2(fw, 0)

    assert round(density, 3) == 22.447
