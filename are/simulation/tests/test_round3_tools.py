"""
Unit tests for the round-3 tool surface added in Phase 3a-tools.

Each new tool gets:
  - happy-path test (returns ok, expected state mutation visible)
  - guard test (errors on bad args / preconditions)

End-to-end scenario integration is covered by Phase 3b smokes; this file
only verifies tool semantics in isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from are.simulation.apps.farm_world import (
    FarmWorldApp,
    FieldOpsApp,
    RobotApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp


@pytest.fixture
def world():
    """Build a minimal physics-active world for tool tests."""
    fw = FarmWorldApp()
    weather = WeatherApp()
    tractor = TractorApp(farm_world_app=fw, weather_app=weather)
    field_ops = FieldOpsApp(farm_world_app=fw, weather_app=weather)
    robot = RobotApp(farm_world_app=fw, weather_app=weather, name="Robot0")
    system = SystemApp()
    fw.attach_system_app(system)

    fw.time_manager.reset(
        datetime(2026, 5, 20, 7, 0, 0, tzinfo=timezone.utc).timestamp()
    )
    weather.set_weather(
        date="2026-05-20",
        temp_c=22.0,
        humidity_pct=50.0,
        wind_speed_ms=2.0,
        rainfall_mm=0.0,
        solar_radiation=480.0,
    )
    fw.configure_physics_profile(profile_name="smoke", scenario_type="unit")
    return {
        "fw": fw,
        "weather": weather,
        "tractor": tractor,
        "field_ops": field_ops,
        "robot": robot,
        "system": system,
    }


# ---------------------------------------------------------------------------
# SystemApp.advance_time
# ---------------------------------------------------------------------------


def test_advance_time_advances_clock_and_fires_physics(world):
    fw = world["fw"]
    system = world["system"]
    t0 = fw.time_manager.time()
    fw.advance_physics_time()  # ensure physics is initialised pre-test

    res = system.advance_time(hours=2)
    assert res["status"] == "ok"
    assert res["advanced_seconds"] == 7200
    assert fw.time_manager.time() - t0 >= 7200


def test_advance_time_rejects_zero(world):
    res = world["system"].advance_time()
    assert "error" in res


def test_advance_time_compound_units(world):
    res = world["system"].advance_time(days=1, hours=2, minutes=30, seconds=15)
    assert res["status"] == "ok"
    assert res["advanced_seconds"] == 86400 + 7200 + 1800 + 15


@pytest.mark.parametrize("ridge_width_m", [1.0, 0.75])
def test_form_ridges_keeps_fixed_indexed_ridge_count(world, ridge_width_m):
    fw = world["fw"]
    tractor = world["tractor"]

    res = tractor.form_ridges(ridge_width_m=ridge_width_m)

    assert res["status"] == "ok"
    assert res["num_ridges"] == 64
    assert fw.num_ridges == 64
    assert len(fw._ridges) == 64
    assert fw.ridge_width_m == pytest.approx(ridge_width_m)

    fw.configure_physics_profile(profile_name="t", scenario_type="test")
    sync = fw.sync_initial_physics_state()
    assert sync["status"] == "initialized"


# ---------------------------------------------------------------------------
# FarmWorldApp.commit_daily_physics / apply_fertigation / dry_grain / store_grain
# ---------------------------------------------------------------------------


def test_commit_daily_physics_runs_one_day(world):
    fw = world["fw"]
    fw.advance_physics_time()
    res = fw.commit_daily_physics()
    assert res["status"] == "ok"
    assert res["tick_result"]["day_ticks_run"] >= 1


def test_apply_fertigation_records_action(world):
    fw = world["fw"]
    fertilizer_before = fw.get_inventory()["fertilizer_kg"]
    res = fw.apply_fertigation(
        start_ridge=10, end_ridge=15, nutrient_amount=1.0, water_mm=8.0
    )
    assert res["status"] == "ok"
    assert res["fertigated_ridges"] == [10, 11, 12, 13, 14, 15]
    assert res["carrier_water_mm"] == pytest.approx(8.0)
    assert res["fertilizer_kg_used"] == pytest.approx(180.0)
    assert res["field_equivalent_irrigation_mm"] == pytest.approx(0.75)
    assert fw.get_inventory()["fertilizer_kg"] == pytest.approx(
        fertilizer_before - 180.0
    )
    assert res["management_regime"]["irrigation_used_mm_total"] == pytest.approx(0.75)
    # The action should have been logged + management state updated.
    log = fw.physics.action_log
    assert any(a.action_type == "fertigation" for a in log)
    # Fertigation raises nutrient state and registers its carrier water.
    assert fw.physics.management.states[12].cumulative_fertigation_amount > 0.0
    assert fw.physics.management.states[12].recent_irrigation_mm == pytest.approx(8.0)
    assert fw.physics.management.states[12].cumulative_irrigation_mm == pytest.approx(
        8.0
    )


def test_apply_fertigation_rejects_bad_args(world):
    fw = world["fw"]
    assert "error" in fw.apply_fertigation(0, 5, nutrient_amount=0.0, water_mm=8.0)
    assert (
        fw.apply_fertigation(0, 5, nutrient_amount=1.0, water_mm=0.0)["status"] == "ok"
    )
    assert "error" in fw.apply_fertigation(0, 5, nutrient_amount=1.0, water_mm=-0.1)
    assert "error" in fw.apply_fertigation(-1, 5, nutrient_amount=1.0, water_mm=8.0)


def test_dry_grain_sets_moisture_for_harvested_ridges(world):
    fw = world["fw"]
    # Mark a ridge as harvested with high moisture AND register it as part
    # of the current trailer batch — upstream dry_grain() now only operates
    # on the trailer batch (set by harvest), not on all harvested ridges.
    state = fw.physics.yield_recovery.states[0]
    state.harvested = True
    state.grain_moisture_frac = 0.18
    fw._trailer_grain_ridge_ids = {0}
    fw._trailer_grain_moistures_pct = [18.0]
    res = fw.dry_grain(target_moisture_pct=13.5)
    assert res["status"] == "ok"
    assert res["ridges_dried"] >= 1
    assert state.grain_moisture_frac == pytest.approx(0.135)


def test_dry_grain_rejects_out_of_range_target(world):
    fw = world["fw"]
    assert "error" in fw.dry_grain(target_moisture_pct=20.0)
    assert "error" in fw.dry_grain(target_moisture_pct=8.0)


def test_dry_grain_respects_capacity_and_storage_moisture(world):
    fw = world["fw"]
    fw.configure_postharvest_market(
        max_storage_moisture_pct=13.5,
        drying_capacity_kg_per_day=100.0,
    )
    fw._inventory.harvest_grain_kg = 150.0

    assert "error" in fw.dry_grain(target_moisture_pct=14.0)
    assert "error" in fw.dry_grain(target_moisture_pct=13.0)


def test_store_grain_records_action(world):
    fw = world["fw"]
    fw._inventory.harvest_grain_kg = 1234.0
    res = fw.store_grain()
    assert res["status"] == "ok"
    assert res["warehouse_grain_kg"] == 1234.0
    assert any(a.action_type == "store_grain" for a in fw.physics.action_log)


def test_store_grain_respects_storage_capacity(world):
    fw = world["fw"]
    fw.configure_postharvest_market(storage_capacity_kg=100.0)
    fw._inventory.harvest_grain_kg = 120.0

    assert "error" in fw.store_grain()


# ---------------------------------------------------------------------------
# TractorApp.load_fungicide / apply_fungicide / replant_seeds / incorporate_residue
# ---------------------------------------------------------------------------


def test_load_fungicide_consumes_warehouse_pool(world):
    fw = world["fw"]
    tractor = world["tractor"]
    fw._inventory.pesticide_liters = 500.0
    res = tractor.load_fungicide(liters=120.0)
    assert res["status"] == "ok"
    assert res["fungicide_tank_l"] == 120.0
    assert fw._inventory.pesticide_liters == 380.0


def test_load_fungicide_rejects_zero_or_negative(world):
    tractor = world["tractor"]
    assert "error" in tractor.load_fungicide(liters=0.0)
    assert "error" in tractor.load_fungicide(liters=-5.0)


def test_apply_fungicide_opens_residual_window(world):
    fw = world["fw"]
    tractor = world["tractor"]
    fw._inventory.pesticide_liters = 500.0
    tractor._fuel_tank_l = 100.0
    tractor.load_fungicide(liters=120.0)
    res = tractor.apply_fungicide(start_ridge=0, end_ridge=4, liters_per_ridge=8.0)
    assert res["status"] == "ok"
    # Biotic engine fungicide residual window should be open.
    assert fw.physics.biotic.states[0].fungicide_residual_days_left > 0
    # Management engine residual too.
    assert fw.physics.management.states[0].fungicide_residual_days_left > 0


def test_apply_fungicide_blocked_when_no_tank(world):
    tractor = world["tractor"]
    tractor._fuel_tank_l = 100.0
    res = tractor.apply_fungicide(start_ridge=0, end_ridge=4, liters_per_ridge=8.0)
    assert "error" in res


def test_fungicide_application_respects_management_regime(world):
    fw = world["fw"]
    tractor = world["tractor"]
    system = world["system"]
    fw.configure_management_regime(
        regime="low_chemical",
        max_fungicide_applications=1,
        active_ingredient_cap_kg=0.1,
    )
    fw._inventory.pesticide_liters = 500.0
    tractor._fuel_tank_l = 100.0
    tractor.load_fungicide(liters=120.0)

    first = tractor.apply_fungicide(start_ridge=0, end_ridge=4, liters_per_ridge=8.0)
    second = tractor.apply_fungicide(start_ridge=5, end_ridge=9, liters_per_ridge=8.0)
    system.advance_time(days=1)
    third = tractor.apply_fungicide(start_ridge=10, end_ridge=11, liters_per_ridge=8.0)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["management_regime"]["fungicide_applications_used"] == 1
    assert "error" in third
    assert "application cap" in third["error"]


def test_herbicide_blocked_when_active_ingredient_cap_is_zero(world):
    fw = world["fw"]
    tractor = world["tractor"]
    fw.configure_management_regime(
        regime="low_chemical",
        active_ingredient_cap_kg=0.0,
    )
    fw._inventory.pesticide_liters = 500.0
    tractor._fuel_tank_l = 100.0
    tractor.load_pesticide(liters=120.0)

    res = tractor.apply_herbicide(
        start_ridge=0, end_ridge=4, liters_per_ridge=3.0
    )

    assert "error" in res
    assert "active ingredient cap" in res["error"]


def test_irrigation_respects_water_quota(world):
    fw = world["fw"]
    field_ops = world["field_ops"]
    fw.configure_management_regime(
        regime="water_constrained", irrigation_quota_mm_total=0.1
    )
    for rid in range(2):
        fw.get_ridge(rid).soil_vwc = 0.20

    res = field_ops.irrigate(0, 1, hours=1.0)

    assert "error" in res
    assert "irrigation quota" in res["error"]


def test_mechanical_weed_control_respects_ridge_area_cap(world):
    fw = world["fw"]
    tractor = world["tractor"]
    fw.configure_management_regime(
        regime="low_chemical",
        max_machine_passes=3,
        max_mechanical_weed_ridges=12,
    )
    tractor._fuel_tank_l = 100.0

    first = tractor.mechanical_weed_control(0, 9)
    second = tractor.mechanical_weed_control(10, 11)
    third = tractor.mechanical_weed_control(12, 12)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["management_regime"]["mechanical_weed_ridges_treated"] == 12
    assert "error" in third
    assert "mechanical weed ridge cap" in third["error"]


def test_replant_seeds_gap_filling_preserves_established_phenology(world):
    fw = world["fw"]
    tractor = world["tractor"]
    tractor.seed_type = "STANDARD"
    tractor._seed_hopper = 100000
    tractor._fuel_tank_l = 100.0
    fw._ridges[10].planted = True
    fw._ridges[10].days_since_planted = 18
    fw._ridges[10].growth_stage = "V2"
    fw._ridges[10].stand_fraction = 0.64
    fw._ridges[10].soil_vwc = 0.25
    fw._ridges[10].soil_temp_c = 12.0

    from are.simulation.physics import SoybeanStage

    fw.advance_physics_time()
    fw.physics.phenology.states[10].stage = SoybeanStage.V2
    fw.physics.phenology.states[10].emerged = True
    fw.physics.phenology.states[10].accumulated_gdd = 100.0
    fw.physics.phenology.states[10].effective_development_gdd = 90.0
    fw.physics.phenology.states[10].days_after_planting = 18

    res = tractor.replant_seeds(
        start_ridge=10, end_ridge=10, depth_cm=4.0, spacing_cm=5.0
    )
    assert res["status"] == "ok"
    assert res["phenology_reset_ridges"] == []
    assert res["stand_fraction_before"][10] == 0.64
    assert res["stand_fraction_after"][10] > res["stand_fraction_before"][10]
    # Gap filling should improve stand without restarting the established crop.
    assert fw.physics.phenology.states[10].stage == SoybeanStage.V2
    assert fw.physics.phenology.states[10].emerged is True
    assert fw.physics.phenology.states[10].accumulated_gdd == 100.0
    assert fw.physics.phenology.states[10].days_after_planting == 18
    assert fw._ridges[10].days_since_planted == 18
    assert fw._ridges[10].growth_stage == "V2"


def test_replant_seeds_fresh_replant_resets_unestablished_phenology(world):
    fw = world["fw"]
    tractor = world["tractor"]
    tractor.seed_type = "STANDARD"
    tractor._seed_hopper = 100000
    tractor._fuel_tank_l = 100.0
    fw._ridges[11].stand_fraction = 0.35
    fw._ridges[11].soil_vwc = 0.25
    fw._ridges[11].soil_temp_c = 12.0

    from are.simulation.physics import SoybeanStage

    fw.advance_physics_time()
    fw.physics.phenology.states[11].stage = SoybeanStage.NOT_PLANTED
    fw.physics.phenology.states[11].emerged = False

    res = tractor.replant_seeds(
        start_ridge=11, end_ridge=11, depth_cm=4.0, spacing_cm=5.0
    )
    assert res["status"] == "ok"
    assert res["phenology_reset_ridges"] == [11]
    assert fw.physics.phenology.states[11].stage == SoybeanStage.PLANTED_PRE_EMERGENCE
    assert fw.physics.phenology.states[11].emerged is False
    assert fw.physics.phenology.states[11].accumulated_gdd == 0.0
    assert fw._ridges[11].planted is True
    assert fw._ridges[11].growth_stage == "VE"


def test_incorporate_residue_logs_action(world):
    fw = world["fw"]
    tractor = world["tractor"]
    tractor._fuel_tank_l = 100.0
    res = tractor.incorporate_residue(start_ridge=0, end_ridge=5)
    assert res["status"] == "ok"
    # Management engine should have applied INCORPORATE_RESIDUE; tag visible
    # on management state after a daily tick.
    fw.commit_daily_physics()
    tags = fw.physics.management.states[0].tags
    assert "residue_incorporated" in tags or any(
        a.action_type == "incorporate_residue" for a in fw.physics.action_log
    )


# ---------------------------------------------------------------------------
# RobotApp range-inspect tools
# ---------------------------------------------------------------------------


def test_inspect_pests_returns_per_ridge_observations(world):
    robot = world["robot"]
    res = robot.inspect_pests(start_ridge=0, end_ridge=5)
    assert res["status"] == "ok"
    assert len(res["covered_ridges"]) > 0
    for rid in res["covered_ridges"]:
        obs = res["observations"][rid]
        # Observation model returns confidences; we should see them.
        assert "pest_confidence" in obs
        assert "disease_confidence" in obs


def test_inspect_crop_health_returns_weed_observation_fields(world):
    fw = world["fw"]
    robot = world["robot"]

    fw._ridges[0].planted = True
    fw.advance_physics_time()
    fw.physics.management.states[0].planted = True
    fw.physics.biotic.states[0].weed_pressure = 0.6
    fw.physics.observation_model.params.weed_detection_sensitivity = 1.0

    res = robot.inspect_crop_health(start_ridge=0, end_ridge=0)

    assert res["status"] == "ok"
    obs = res["observations"][0]
    assert obs["weed_present"] is True
    assert obs["weed_pressure_band"] == "medium"
    assert "weed_confidence" in obs


def test_inspect_emergence_returns_stand_fraction(world):
    fw = world["fw"]
    robot = world["robot"]
    # Mark a ridge as planted so management has stand_fraction
    fw._ridges[2].planted = True
    fw._ridges[2].soil_vwc = 0.25
    fw._ridges[2].soil_temp_c = 12.0
    fw.advance_physics_time()
    fw.physics.management.states[2].planted = True
    fw.physics.management.states[2].stand_fraction = 0.85

    res = robot.inspect_emergence(start_ridge=0, end_ridge=5)
    assert res["status"] == "ok"
    assert res["covered_ridges"]
    for rid in res["covered_ridges"]:
        assert "stand_fraction" in res["observations"][rid]


def test_inspect_blocked_when_low_battery(world):
    robot = world["robot"]
    robot._battery_pct = 5.0
    res = robot.inspect_pests(start_ridge=0, end_ridge=5)
    assert "error" in res


def test_inspect_range_caps_coverage(world):
    """Robot can't physically walk all 64 ridges in one call."""
    robot = world["robot"]
    res = robot.inspect_pests(start_ridge=0, end_ridge=63)
    assert res["status"] == "ok"
    # Coverage cap is 8 — more ridges should be uncovered.
    assert len(res["covered_ridges"]) <= 8
    assert len(res["uncovered_ridges"]) >= 64 - 8 - 1
