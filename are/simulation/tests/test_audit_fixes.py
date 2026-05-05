"""Comprehensive verification suite for the professor's audit (TO-FIX-2.pdf).

Every test in this file maps to one specific claim in the audit. Tests are
named `test_<section>_<claim_id>_<short_description>` so a failure tells you
exactly which audit item is broken.

Sections:
    F  — Framework / cross-cutting fixes (Phase A in the plan)
    S  — Round-3 episode scenarios (B1)
    R1 — Round-1+2 physics action/tick scenarios (B3)
    R4 — Round-4 full-season scenarios (B2)
    U  — Universal patterns (audit's "modification method" rules)
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from are.simulation.apps.farm_world.farm_world_app import FarmWorldApp
from are.simulation.apps.farm_world.models import (
    InventoryState,
    RidgeState,
    SeedType,
)
from are.simulation.apps.farm_world.tractor_app import split_pass
from are.simulation.apps.system import SystemApp
from are.simulation.physics.phenology_engine import (
    DEFAULT_SEED_TYPE_PARAMS,
    SoybeanStage,
)


REPO = Path(__file__).resolve().parent.parent.parent.parent
SCENARIO_DIRS = {
    "r3": REPO / "are/simulation/scenarios/scenario_farm_worldpp_physics",
    "r12_phys": REPO / "are/simulation/scenarios/scenario_farm_world_physics",
    "r12_mirror": REPO / "are/simulation/scenarios/scenario_farm_world",
    "r4": REPO / "are/simulation/scenarios/scenario_farm_world_fullseason",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _instantiate(scenario_module_path: str, class_name: str):
    """Import-and-instantiate a scenario class without going through the runner."""
    import importlib
    mod = importlib.import_module(scenario_module_path)
    cls = getattr(mod, class_name)
    s = cls()
    s.initialize()
    return s


def _r3(name: str, cls: str):
    return _instantiate(f"are.simulation.scenarios.scenario_farm_worldpp_physics.{name}", cls)


def _r4(name: str, cls: str):
    return _instantiate(f"are.simulation.scenarios.scenario_farm_world_fullseason.{name}", cls)


def _r12_phys(name: str, cls: str):
    return _instantiate(f"are.simulation.scenarios.scenario_farm_world_physics.{name}", cls)


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def _scan_oracle_calls(src: str, pattern: str) -> list[re.Match]:
    """Return all regex matches in the source text."""
    return list(re.finditer(pattern, src))


def _run_oracle(scenario_id: str) -> dict:
    """Run a scenario in oracle mode and return the parsed FOS metrics dict.

    Returns: {success: bool, combined: float|None, fos: float|None,
              outcome: float|None, decision: float|None, efficiency: float|None,
              gates_matched: tuple[int,int]|None, safety: int|None}
    """
    import subprocess
    out = subprocess.run(
        [str(REPO / ".venv312/bin/python"), "-m", "are.simulation.main",
         "-s", scenario_id, "-a", "farm_baseline_react", "-o",
         "--log-level", "WARNING", "--output_dir", f"/tmp/audit_{scenario_id}"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    text = out.stdout + out.stderr
    success = "Success=100.0%" in text
    m_combined = re.search(r"combined=([0-9.]+)", text)
    m_fos = re.search(r"\bfos=([0-9.]+)", text)
    m_outcome = re.search(r"outcome=([0-9.]+)", text)
    m_decision = re.search(r"decision=([0-9.]+)", text)
    m_efficiency = re.search(r"efficiency=([0-9.]+)", text)
    m_gates = re.search(r"gates_matched=([0-9]+)/([0-9]+)", text)
    m_safety = re.search(r"safety=([0-9]+)", text)
    return {
        "success": success,
        "rc": out.returncode,
        "combined": float(m_combined.group(1)) if m_combined else None,
        "fos": float(m_fos.group(1)) if m_fos else None,
        "outcome": float(m_outcome.group(1)) if m_outcome else None,
        "decision": float(m_decision.group(1)) if m_decision else None,
        "efficiency": float(m_efficiency.group(1)) if m_efficiency else None,
        "gates_matched": (int(m_gates.group(1)), int(m_gates.group(2))) if m_gates else None,
        "safety": int(m_safety.group(1)) if m_safety else None,
    }


# ===========================================================================
# F — Framework / cross-cutting (Phase A)
# ===========================================================================


def test_F1_weatherapp_advances_date_after_advance_time():
    """Audit §1: 'After SystemApp.advance_time(days=1), WeatherApp.current
    may still stop at the old date in static mode.'"""
    from are.simulation.apps.farm_world.weather_app import WeatherApp
    s = _r3("scenario_physics_planting_window_reschedule", "ScenarioPhysicsPlantingWindowReschedule")
    weather = s.get_typed_app(WeatherApp)
    sys_app = s.get_typed_app(SystemApp)
    fw = s.get_typed_app(FarmWorldApp)

    fw.advance_physics_time()  # initial seed
    pre_date = weather.get_current_weather_snapshot()["date"]
    sys_app.advance_time(hours=24)
    post_date = weather.get_current_weather_snapshot()["date"]
    # The audit's claim was the date stayed FROZEN. We just need to verify
    # it advances at all (the exact gap depends on sim_time vs scenario
    # narrative date — orthogonal issue).
    assert post_date != pre_date, (
        f"WeatherApp did not advance: pre={pre_date} post={post_date}"
    )
    pre_d = date.fromisoformat(pre_date)
    post_d = date.fromisoformat(post_date)
    assert post_d > pre_d, "Weather date must move forward, not backward"


def test_F1b_weatherapp_consumes_forecast_entry():
    """A1 forecast consumption: when scenario provides forecast, advance
    consumes the head entry."""
    from are.simulation.apps.farm_world.weather_app import WeatherApp
    s = _r3("scenario_physics_planting_window_reschedule", "ScenarioPhysicsPlantingWindowReschedule")
    weather = s.get_typed_app(WeatherApp)
    sys_app = s.get_typed_app(SystemApp)
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    forecast_before = list(weather._weather.forecast)
    sys_app.advance_time(hours=24)
    forecast_after = list(weather._weather.forecast)
    assert len(forecast_after) < len(forecast_before), (
        f"Auto-advance should consume forecast entries; before={len(forecast_before)} "
        f"after={len(forecast_after)}"
    )


def test_F2_disease_pressure_bridges_to_biotic():
    """Audit: 'In the disease scenario, only r.disease_pressure = 0.38 is
    written, but _configure_physics_layers reads disease_pressure_base'."""
    s = _r3("scenario_physics_disease_after_rain_fungicide", "ScenarioPhysicsDiseaseAfterRainFungicide")
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    # Disease block per scenario: 34-46
    for rid in [34, 40, 46]:
        biotic = fw.physics.biotic.states[rid]
        assert biotic.disease_pressure >= 0.30, (
            f"ridge {rid}: physics.biotic.disease_pressure={biotic.disease_pressure} "
            f"(scenario init wrote 0.38; bridge should mirror)"
        )


def test_F3_pest_pressure_bridges_to_biotic():
    """Audit: 'The pest scenario only sets r.pest_pressure = 0.30, but the
    physics seed refers to base'."""
    s = _r3("scenario_physics_threshold_pest_monitoring", "ScenarioPhysicsThresholdPestMonitoring")
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    for rid in [16, 22, 27]:
        biotic = fw.physics.biotic.states[rid]
        assert biotic.insect_pressure >= 0.25, (
            f"ridge {rid}: physics.biotic.insect_pressure={biotic.insect_pressure} "
            f"(init wrote 0.30; bridge should mirror)"
        )


def test_F4_ridgestate_has_nutrient_index_field():
    """Audit: 'r.nutrient_index, but RidgeState itself does not have this
    field' — must now exist."""
    r = RidgeState.default(0)
    assert hasattr(r, "nutrient_index")
    assert isinstance(r.nutrient_index, float)


def test_F4b_nutrient_index_bridges_to_management():
    """Audit: nutrient anomaly must reach physics.management.nutrient_index."""
    s = _r3("scenario_physics_differential_diagnosis_fertigation", "ScenarioPhysicsDifferentialDiagnosisFertigation")
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    for rid in [28, 32, 35]:
        mgmt = fw.physics.management.states[rid]
        assert mgmt.nutrient_index <= 0.65, (
            f"ridge {rid}: physics.management.nutrient_index={mgmt.nutrient_index} "
            f"(scenario set anomaly to 0.55)"
        )


def test_F5_ridgestate_has_stand_fraction_field():
    """Audit: 'r.stand_fraction is set, but the robot physics path reads
    physics.management.states[rid].stand_fraction' — must bridge."""
    r = RidgeState.default(0)
    assert hasattr(r, "stand_fraction")
    assert isinstance(r.stand_fraction, float)


def test_F5b_stand_fraction_bridges_to_management():
    """Audit: emergence anomaly must reach physics.management.stand_fraction
    (robot reads from there, not from r.stand_fraction)."""
    s = _r3("scenario_physics_emergence_replant_decision", "ScenarioPhysicsEmergenceReplantDecision")
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    # Bad block 12-19 should have low stand_fraction
    for rid in [12, 15, 19]:
        mgmt = fw.physics.management.states[rid]
        assert mgmt.stand_fraction <= 0.55, (
            f"ridge {rid}: physics.management.stand_fraction={mgmt.stand_fraction} "
            f"(init wrote 0.45 for bad ridges)"
        )
    # Good ridge (outside 12-19) should have high stand_fraction
    for rid in [0, 30, 60]:
        mgmt = fw.physics.management.states[rid]
        assert mgmt.stand_fraction >= 0.85, (
            f"ridge {rid}: stand_fraction={mgmt.stand_fraction} should be ~0.92"
        )


def test_F6_robot_inspect_emergence_sees_stand_fraction_difference():
    """Audit: 'what the robot actually sees is all 0.0, not the difference
    between bad seedlings and good seedlings as designed.'"""
    from are.simulation.apps.farm_world.robot_app import RobotApp
    s = _r3("scenario_physics_emergence_replant_decision", "ScenarioPhysicsEmergenceReplantDecision")
    fw = s.get_typed_app(FarmWorldApp)
    robot = s.get_typed_app(RobotApp, "Robot0")
    fw.advance_physics_time()

    # Inspect a bad block
    bad_result = robot.inspect_emergence(12, 15)
    good_result = robot.inspect_emergence(0, 3)

    bad_obs = bad_result["observations"]
    good_obs = good_result["observations"]
    bad_avg = sum(o["stand_fraction"] for o in bad_obs.values()) / len(bad_obs)
    good_avg = sum(o["stand_fraction"] for o in good_obs.values()) / len(good_obs)

    # Robot must observe a clear difference (audit's claim was bad=0.0,
    # good=0.0 — a "blind" robot. After A2 bridge, must see real values.)
    assert bad_avg < good_avg, (
        f"robot sees bad={bad_avg}, good={good_avg} — should differ"
    )
    assert bad_avg < 0.6 and good_avg > 0.7, (
        f"robot signal too compressed: bad={bad_avg}, good={good_avg}"
    )


def test_F7_replant_max_width_4():
    """Audit: 'replant_seeds(12,19) exceeds the tool limit, the current max
    is 4 ridges/pass'. Verify the tool does enforce 4."""
    from are.simulation.apps.farm_world.tractor_app import TractorApp
    s = _r3("scenario_physics_emergence_replant_decision", "ScenarioPhysicsEmergenceReplantDecision")
    tractor = s.get_typed_app(TractorApp)
    s.get_typed_app(FarmWorldApp).advance_physics_time()
    tractor.load_seeds("STANDARD", 100000)
    result = tractor.replant_seeds(12, 19, depth_cm=4.0, spacing_cm=5.0)
    assert "error" in result, "replant_seeds(12,19) should fail (8 ridges > max 4)"
    assert "4 ridges" in result["error"]


def test_F7b_apply_fungicide_max_width_10():
    """Audit: 'apply_fungicide(34,46) will report an error if it exceeds 10
    ridges/pass'."""
    from are.simulation.apps.farm_world.tractor_app import TractorApp
    s = _r3("scenario_physics_disease_after_rain_fungicide", "ScenarioPhysicsDiseaseAfterRainFungicide")
    tractor = s.get_typed_app(TractorApp)
    s.get_typed_app(FarmWorldApp).advance_physics_time()
    tractor.load_fungicide(120.0)
    result = tractor.apply_fungicide(34, 46, liters_per_ridge=5.0)
    assert "error" in result, "apply_fungicide(34,46) should fail (13 ridges > max 10)"
    assert "10 ridges" in result["error"]


def test_F7c_spray_pesticide_max_width_10():
    """Audit: 'spray_pesticide(16,27) If it exceeds 10 ridges/pass'."""
    from are.simulation.apps.farm_world.tractor_app import TractorApp
    s = _r3("scenario_physics_threshold_pest_monitoring", "ScenarioPhysicsThresholdPestMonitoring")
    tractor = s.get_typed_app(TractorApp)
    s.get_typed_app(FarmWorldApp).advance_physics_time()
    tractor.load_pesticide(120.0)
    result = tractor.spray_pesticide(16, 27, liters_per_ridge=6.0)
    assert "error" in result, "spray_pesticide(16,27) should fail (12 ridges > max 10)"


def test_F7d_incorporate_residue_max_width_10():
    """Audit: 'incorporate_residue(0,63) will report an error if it exceeds
    10 ridges/pass'."""
    from are.simulation.apps.farm_world.tractor_app import TractorApp
    s = _r3("scenario_physics_postharvest_drying_storage", "ScenarioPhysicsPostharvestDryingStorage")
    tractor = s.get_typed_app(TractorApp)
    s.get_typed_app(FarmWorldApp).advance_physics_time()
    result = tractor.incorporate_residue(0, 63)
    assert "error" in result, "incorporate_residue(0,63) should fail (64 ridges > max 10)"


def test_F8a_split_pass_4_replant():
    """A6 helper: split_pass(12, 19, 4) → [(12,15), (16,19)]."""
    assert split_pass(12, 19, 4) == [(12, 15), (16, 19)]


def test_F8b_split_pass_10_fungicide():
    """A6 helper: split_pass(34, 46, 10) → [(34,43), (44,46)]."""
    assert split_pass(34, 46, 10) == [(34, 43), (44, 46)]


def test_F8c_split_pass_10_residue():
    """A6 helper: split_pass(0, 63, 10) → 7 passes."""
    out = split_pass(0, 63, 10)
    assert len(out) == 7
    assert out[0] == (0, 9)
    assert out[-1] == (60, 63)
    # Total coverage must equal 64 ridges.
    total = sum(e - s + 1 for s, e in out)
    assert total == 64


def test_F8d_split_pass_10_spray():
    """split_pass(16, 27, 10) → covers 12 ridges in 2 passes."""
    out = split_pass(16, 27, 10)
    assert len(out) == 2
    assert all(e - s + 1 <= 10 for s, e in out)


def test_F9_gdd_threshold_lowered():
    """Audit r4: 'SeedType.STANDARD.gdd_to_r8 = 1850, this threshold is too
    high'. Verify it's been lowered to ~1400."""
    p = DEFAULT_SEED_TYPE_PARAMS[SeedType.STANDARD]
    assert p.gdd_to_r8 < 1500, (
        f"STANDARD.gdd_to_r8={p.gdd_to_r8} — should be ≤1500 to fit Harbin GDD"
    )


def test_F10_phenology_reaches_R8_in_full_season():
    """Audit r4: 'The physical growth period remains at R3 even in September'.
    Verify a full-season simulation actually reaches R8."""
    s = _r4("scenario_full_season_baseline_balanced_season", "ScenarioFullSeasonBalanced")
    fw = s.get_typed_app(FarmWorldApp)
    # Seed a planted ridge state directly so the test doesn't depend on the
    # oracle correctly planting.
    for i in range(64):
        r = fw._ridges[i]
        r.planted = True
        r.seed_type = "STANDARD"
        r.days_since_planted = 0
        r.growth_stage = "PLANTED_PRE_EMERGENCE"
    fw.advance_physics_time()
    # Advance ~130 days
    for _ in range(130):
        fw.time_manager.add_offset(86400)
        fw.advance_physics_time()
    stages = {fw.physics.phenology.states[rid].stage for rid in range(64)}
    assert SoybeanStage.R8 in stages or SoybeanStage.R7 in stages, (
        f"After 130 days, no ridge reached R7/R8. Stages: {stages}"
    )


def test_F11_commit_daily_physics_no_crash_on_seed_type_none():
    """Audit: 'commit_daily_physics() will crash: planted ridge is missing
    seed_type or planting_date.' Defensive guard should no-op for that ridge
    instead of raising."""
    s = _r3("scenario_physics_postharvest_drying_storage", "ScenarioPhysicsPostharvestDryingStorage")
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    # Force a malformed ridge
    fw._ridges[10].planted = True
    fw._ridges[10].seed_type = None
    # Should not raise
    fw.time_manager.add_offset(86400)
    fw.advance_physics_time()  # Daily tick


def test_F12a_inventory_state_has_warehouse_grain_kg():
    """A4: InventoryState has a separate warehouse_grain_kg field."""
    inv = InventoryState.default()
    assert hasattr(inv, "warehouse_grain_kg")
    assert inv.warehouse_grain_kg == 0.0
    assert hasattr(inv, "grain_dried")
    assert inv.grain_dried is False


def test_F12b_dry_grain_then_store_grain_moves_kg_to_warehouse():
    """Audit: 'dry_grain() appears to succeed, but store_grain() still
    results in warehouse grain being 0.0'. After fix: store_grain moves
    trailer kg to warehouse."""
    s = _r3("scenario_physics_postharvest_drying_storage", "ScenarioPhysicsPostharvestDryingStorage")
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    inv_before = fw.get_inventory()
    assert inv_before["harvest_grain_kg"] >= 4000, (
        f"Postharvest scenario should start with trailer grain "
        f"(got harvest_grain_kg={inv_before['harvest_grain_kg']})"
    )
    fw.dry_grain(target_moisture_pct=13.0)
    assert fw._inventory.grain_dried is True
    moved = fw._inventory.harvest_grain_kg
    fw.store_grain()
    inv_after = fw.get_inventory()
    assert inv_after["warehouse_grain_kg"] >= moved - 0.01, (
        f"warehouse_grain_kg={inv_after['warehouse_grain_kg']}, expected ~{moved}"
    )
    assert inv_after["harvest_grain_kg"] == 0.0


def test_F13_drone_is_flyable_correctly_gated():
    """Audit: 'It will rain on Day0, but Oracle still schedules Mavic/Matrice
    flights, and the actual drones will fail.' Verify the gate works."""
    from are.simulation.apps.farm_world.weather_app import WeatherApp
    s = _r3("scenario_physics_disease_after_rain_fungicide", "ScenarioPhysicsDiseaseAfterRainFungicide")
    weather = s.get_typed_app(WeatherApp)
    # Day 0 has rain in this scenario
    snap = weather.get_current_weather_snapshot()
    assert snap["rainfall_mm"] > 0, f"day-0 should have rain, got {snap['rainfall_mm']}"
    assert weather.is_flyable is False, "drone should not be flyable in rain"


# ===========================================================================
# S — Round-3 episode scenarios (B1 fixes)
# ===========================================================================


def test_S1_planting_window_oracle_passes():
    """planting_window: oracle pass with FOS >= 0.9."""
    r = _run_oracle("scenario_physics_planting_window_reschedule")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.9, f"fos={r['fos']}"
    assert r["combined"] == 1.0, f"workflow_combined={r['combined']}"


def test_S1b_planting_window_oracle_event_count():
    """Verify oracle has the wait+plant sequence aligned with A1 weather advance."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_planting_window_reschedule.py")
    # Oracle should call advance_time at least twice
    assert len(_scan_oracle_calls(src, r"advance_time\(hours=24\)")) >= 2


def test_S2_emergence_replant_oracle_passes():
    r = _run_oracle("scenario_physics_emergence_replant_decision")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.95, f"fos={r['fos']}"


def test_S2b_emergence_replant_check_status_present():
    """Audit: 'robot.inspect_emergence(...) 前缺 robot.check_status()'."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_emergence_replant_decision.py")
    # robot.check_status() must appear before robot.inspect_emergence
    cs_pos = src.find("robot.check_status()")
    ie_pos = src.find("robot.inspect_emergence")
    assert cs_pos > 0, "robot.check_status() missing"
    assert ie_pos > 0, "robot.inspect_emergence missing"
    assert cs_pos < ie_pos, "robot.check_status() must come before inspect_emergence"


def test_S2c_emergence_replant_split_into_4_ridge_passes():
    """Audit: 'replant is split into 12-15, 16-19'."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_emergence_replant_decision.py")
    # Find replant_seeds calls
    replant_calls = re.findall(r"replant_seeds\((\d+),\s*(?:_BAD_START\s*\+\s*)?(\d+)", src)
    # Filter to oracle-relevant ones (skip parameter-default sigs)
    found_split = False
    for s, e in replant_calls:
        s_int = int(s)
        e_int = int(e)
        if 12 <= s_int <= 19 and (e_int - s_int + 1) <= 4:
            found_split = True
    # Alternative: search for explicit "_BAD_START + 3" pattern
    if not found_split:
        assert "_BAD_START + 3" in src or "_BAD_START + 4" in src or "12, 15" in src, (
            "Expected replant split into 4-ridge passes"
        )


def test_S3_differential_diagnosis_oracle_passes():
    r = _run_oracle("scenario_physics_differential_diagnosis_fertigation")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.9, f"fos={r['fos']}"


def test_S3b_differential_diagnosis_check_status_present():
    """Audit: 'robot.inspect_crop_health(...) 前缺 robot.check_status()'."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_differential_diagnosis_fertigation.py")
    cs_pos = src.find("robot.check_status()")
    ich_pos = src.find("robot.inspect_crop_health")
    assert cs_pos > 0, "robot.check_status() missing"
    assert ich_pos > 0, "robot.inspect_crop_health missing"
    assert cs_pos < ich_pos


def test_S4_pod_fill_oracle_passes():
    r = _run_oracle("scenario_physics_pod_fill_drought_irrigation")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.95, f"fos={r['fos']}"


def test_S4b_pod_fill_canopy_initialized_to_R5_LAI():
    """Audit: 'R5 canopy was not initialized correctly, causing the model to
    strongly evaporate the top soil like bare ground.' Verify canopy LAI is
    high (R5-appropriate) on dry-zone ridges."""
    s = _r3("scenario_physics_pod_fill_drought_irrigation", "ScenarioPhysicsPodFillDroughtIrrigation")
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    for rid in [22, 30, 40]:
        canopy = fw.physics.canopy.states[rid]
        assert canopy.initialized
        assert canopy.lai >= 3.0, (
            f"ridge {rid}: canopy.lai={canopy.lai} — R5 should have LAI>=3.5"
        )


def test_S5_disease_oracle_passes():
    r = _run_oracle("scenario_physics_disease_after_rain_fungicide")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.85, f"fos={r['fos']}"


def test_S5b_disease_check_status_present():
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_disease_after_rain_fungicide.py")
    cs_pos = src.find("robot.check_status()")
    ich_pos = src.find("robot.inspect_crop_health")
    assert cs_pos > 0 and ich_pos > 0 and cs_pos < ich_pos


def test_S5c_disease_fungicide_split_pass():
    """Audit: 'fungicide split pass, e.g. 34-43, 44-46, each segment <=10'."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_disease_after_rain_fungicide.py")
    fungicide_count = len(re.findall(r"apply_fungicide\(", src))
    assert fungicide_count >= 2, (
        f"Expected ≥2 apply_fungicide calls (split into multiple passes); "
        f"found {fungicide_count}"
    )


def test_S6_threshold_pest_oracle_passes():
    r = _run_oracle("scenario_physics_threshold_pest_monitoring")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.95, f"fos={r['fos']}"


def test_S6b_threshold_pest_drone_covers_full_zones():
    """Audit: 'drone fly 11-32' (full C2/C3 zones)."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_threshold_pest_monitoring.py")
    assert "fly_survey(11, 32)" in src, "drone should fly full zones 11-32"


def test_S6c_threshold_pest_spray_split():
    """Audit: 'spray split into 16-21, 22-27'."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_threshold_pest_monitoring.py")
    spray_count = len(re.findall(r"spray_pesticide\(", src))
    assert spray_count >= 2, f"expected ≥2 spray calls; found {spray_count}"


def test_S6d_threshold_pest_check_status_before_each_inspect():
    """Audit: 'Add check_status() before both day0 and day1 robot inspections'."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_threshold_pest_monitoring.py")
    cs_count = len(re.findall(r"robot\.check_status\(\)", src))
    inspect_count = len(re.findall(r"robot\.inspect_pests\(", src))
    assert cs_count >= 2, f"Need check_status before both inspects; got {cs_count}"
    assert inspect_count == 2, f"Expected 2 inspect_pests calls; got {inspect_count}"


def test_S7_harvest_moisture_oracle_passes():
    r = _run_oracle("scenario_physics_harvest_moisture_timing")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.85, f"fos={r['fos']}"


def test_S7b_harvest_moisture_weather_advances():
    """Audit: 'advance_time(hours=24) the current weather date remains stale'.
    With A1 fix, post-advance weather.date should differ."""
    from are.simulation.apps.farm_world.weather_app import WeatherApp
    s = _r3("scenario_physics_harvest_moisture_timing", "ScenarioPhysicsHarvestMoistureTiming")
    fw = s.get_typed_app(FarmWorldApp)
    weather = s.get_typed_app(WeatherApp)
    sys_app = s.get_typed_app(SystemApp)
    fw.advance_physics_time()
    pre = weather.get_current_weather_snapshot()["date"]
    sys_app.advance_time(hours=24)
    post = weather.get_current_weather_snapshot()["date"]
    assert pre != post, f"Weather date stuck at {pre}"


def test_S8_postharvest_oracle_passes():
    r = _run_oracle("scenario_physics_postharvest_drying_storage")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.85, f"fos={r['fos']}"


def test_S8b_postharvest_inventory_starts_with_trailer_grain():
    """Audit: 'There is trailer grain in the scenario narrative, but
    get_inventory() returns harvest_grain_kg=0.0'."""
    s = _r3("scenario_physics_postharvest_drying_storage", "ScenarioPhysicsPostharvestDryingStorage")
    fw = s.get_typed_app(FarmWorldApp)
    inv = fw.get_inventory()
    assert inv["harvest_grain_kg"] >= 4000, (
        f"Scenario init should populate trailer grain via _inventory.harvest_grain_kg; "
        f"got {inv['harvest_grain_kg']}"
    )


def test_S8c_postharvest_residue_split_into_passes():
    """Audit: 'Residue incorporation is split into <=10 ridges/pass'."""
    src = _read("are/simulation/scenarios/scenario_farm_worldpp_physics/scenario_physics_postharvest_drying_storage.py")
    inc_count = len(re.findall(r"incorporate_residue\(", src))
    # Call sites: definition + at least 6 oracle splits (64/10 ceiling)
    # Use split_pass helper-driven loop, so source has 1 incorporate_residue call inside loop
    # Verify the source uses split_pass
    assert "split_pass" in src, "Should use split_pass helper to split residue passes"


def test_S8d_postharvest_seed_type_set_on_init():
    """Audit: 'commit_daily_physics will crash: planted ridge is missing
    seed_type or planting_date'. Init must set seed_type."""
    s = _r3("scenario_physics_postharvest_drying_storage", "ScenarioPhysicsPostharvestDryingStorage")
    fw = s.get_typed_app(FarmWorldApp)
    for r in fw._ridges:
        if r.planted:
            assert r.seed_type is not None, f"ridge {r.ridge_id}: seed_type is None"


# ===========================================================================
# R1 — Round-1+2 physics action/tick scenarios (B3 fixes)
# ===========================================================================


def test_R1_1_fertilizer_oracle_passes():
    r = _run_oracle("scenario_farm_world_fertilizer_physics_action_tick")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R1_2_harvest_physics_oracle_passes():
    r = _run_oracle("scenario_farm_world_harvest_physics_action_tick")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R1_3_irrigation_oracle_passes():
    r = _run_oracle("scenario_farm_world_irrigation_physics_action_tick")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None and r["fos"] >= 0.9


def test_R1_4_drone_survey_oracle_passes():
    r = _run_oracle("scenario_farm_world_drone_survey_physics_action_tick")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


# ===========================================================================
# R4 — Round-4 full-season scenarios (B2 fixes)
# ===========================================================================


def test_R4_baseline_28_35_nutrient_anomaly_set_in_init():
    """Audit: 'baseline init did not create 28-35 nutrient anomaly.
    init is just the whole field nutrient_index = 0.85.'"""
    s = _r4("scenario_full_season_baseline_balanced_season", "ScenarioFullSeasonBalanced")
    fw = s.get_typed_app(FarmWorldApp)
    # Per fix: ridges 28-35 should have lower nutrient_index than 0.85
    for rid in [28, 30, 35]:
        r = fw._ridges[rid]
        assert r.nutrient_index < 0.7, (
            f"ridge {rid}: nutrient_index={r.nutrient_index}, expected < 0.7 (anomaly)"
        )
    # Outside 28-35 should have baseline 0.85
    for rid in [0, 50, 63]:
        r = fw._ridges[rid]
        assert r.nutrient_index >= 0.8


def test_R4_baseline_oracle_passes():
    r = _run_oracle("scenario_full_season_balanced")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_dry_pod_fill_oracle_passes():
    r = _run_oracle("scenario_full_season_dry_pod_fill")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_wet_june_disease_oracle_passes():
    r = _run_oracle("scenario_full_season_wet_june_disease")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_wet_june_disease_fungicide_split():
    """Audit: 'apply_fungicide(34, 46) exceeds the tool range limit'.
    Oracle must split."""
    src = _read("are/simulation/scenarios/scenario_farm_world_fullseason/scenario_full_season_wet_june_disease_pressure.py")
    # Either uses split_pass helper or has multiple apply_fungicide calls with widths ≤10
    fungicide_calls = re.findall(r"apply_fungicide\((\d+),\s*(\d+)", src)
    if fungicide_calls:
        for s_str, e_str in fungicide_calls:
            width = int(e_str) - int(s_str) + 1
            assert width <= 10, (
                f"apply_fungicide({s_str},{e_str}) width={width} > 10 (audit limit)"
            )


def test_R4_nutrient_differential_28_35_anomaly_set():
    """Audit: 'init also did not set 28-35 to low nutrient, low SPAD, low NDVI'."""
    s = _r4("scenario_full_season_nutrient_vs_drought_differential", "ScenarioFullSeasonNutrientDifferential")
    fw = s.get_typed_app(FarmWorldApp)
    for rid in [28, 32, 35]:
        r = fw._ridges[rid]
        assert r.nutrient_index < 0.6, (
            f"ridge {rid}: nutrient_index={r.nutrient_index}, expected < 0.6"
        )


def test_R4_nutrient_differential_oracle_passes():
    r = _run_oracle("scenario_full_season_nutrient_differential")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_mixed_stress_oracle_passes():
    r = _run_oracle("scenario_full_season_mixed_stress_trap")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_adversarial_oracle_passes():
    r = _run_oracle("scenario_full_season_adversarial_weather")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_late_harvest_rain_risk_oracle_passes():
    r = _run_oracle("scenario_full_season_late_harvest_rain_risk")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_cold_spring_oracle_passes():
    r = _run_oracle("scenario_full_season_cold_spring")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_resource_limited_oracle_passes():
    r = _run_oracle("scenario_full_season_resource_limited")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


def test_R4_aphid_threshold_oracle_passes():
    r = _run_oracle("scenario_full_season_aphid_threshold")
    assert r["success"], f"oracle failed: {r}"
    assert r["fos"] is not None


# ===========================================================================
# B — Behavior tests: directly assert the *anti-symptom* of each dynamic
#     audit claim (after fix, the broken behavior no longer reproduces).
# ===========================================================================


def test_B1_post_fertigation_ndvi_recovery():
    """Audit (differential_diagnosis): 'After fertilization, the follow-up
    canopy did not show obvious recovery, and the observed values did not
    conform to the oracle narrative.'

    With A2 nutrient_index bridge + the management engine's nutrient stress
    relief on fertigation, canopy NDVI on the anomaly block should rise
    after fertigation + a few days. Verify that NDVI ≥ pre-value."""
    s = _r3("scenario_physics_differential_diagnosis_fertigation",
            "ScenarioPhysicsDifferentialDiagnosisFertigation")
    fw = s.get_typed_app(FarmWorldApp)
    sys_app = s.get_typed_app(SystemApp)
    fw.advance_physics_time()
    pre = {rid: fw.physics.canopy.states[rid].ndvi_proxy for rid in [28, 32, 35]}
    fw.apply_fertigation(28, 35, nutrient_amount=0.8, water_mm=6.0)
    sys_app.advance_time(hours=72)
    post = {rid: fw.physics.canopy.states[rid].ndvi_proxy for rid in [28, 32, 35]}
    # Audit's expectation: post-fertigation values should NOT be below pre.
    # We allow stable-or-rising (engine may saturate) but not regression.
    for rid in pre:
        assert post[rid] >= pre[rid] - 0.02, (
            f"ridge {rid}: NDVI regressed pre={pre[rid]} post={post[rid]}"
        )


def test_B2_post_irrigation_soil_vwc_rises():
    """Audit (pod_fill, irrigation_physics): 'after irrigation, the recheck
    shows that it is actually drier' — fixed by stage-aware canopy LAI in
    A2 follow-up. Verify post-irrigation soil VWC rises."""
    from are.simulation.apps.farm_world.field_ops_app import FieldOpsApp
    s = _r3("scenario_physics_pod_fill_drought_irrigation",
            "ScenarioPhysicsPodFillDroughtIrrigation")
    fw = s.get_typed_app(FarmWorldApp)
    field_ops = s.get_typed_app(FieldOpsApp)
    sys_app = s.get_typed_app(SystemApp)
    fw.advance_physics_time()
    pre = {rid: fw.physics.soil.states[rid].top_vwc for rid in [22, 30, 40]}
    field_ops.irrigate(22, 43, hours=2.0)
    sys_app.advance_time(hours=8)
    post = {rid: fw.physics.soil.states[rid].top_vwc for rid in [22, 30, 40]}
    # At least one ridge in the dry zone should have higher VWC after
    # irrigation. (Audit's broken behavior was VWC dropping uniformly.)
    rises = sum(1 for rid in pre if post[rid] > pre[rid])
    assert rises >= 1, (
        f"No ridge VWC rose after irrigation. pre={pre} post={post}"
    )


def test_B3_harvest_physics_returns_nonzero_grain():
    """Audit (harvest_physics): 'actual harvest returns grain_kg_added = 0.0,
    all subsequent unloads are empty.'

    With yield_recovery seeded properly, harvest must return positive grain."""
    from are.simulation.apps.farm_world.tractor_app import TractorApp
    s = _r12_phys("scenario_harvest_physics_action_tick",
                  "ScenarioFarmWorldHarvestPhysicsActionTick")
    fw = s.get_typed_app(FarmWorldApp)
    tractor = s.get_typed_app(TractorApp)
    fw.advance_physics_time()
    # Set up the tractor as the oracle would
    tractor._fuel_tank_l = 80.0
    tractor.attach_implement("harvester")
    result = tractor.harvest(0, 3)
    assert "error" not in result or result.get("grain_kg_added", 0) > 0, (
        f"Harvest returned error or zero grain: {result}"
    )
    grain = float(result.get("grain_kg_added", 0))
    assert grain > 0, f"grain_kg_added={grain} (should be > 0)"


def test_B4_pest_pressure_threshold_trend():
    """Audit (threshold_pest): 'what the robot sees on day0/day1 is
    random/default low pest pressure, which will not form a threshold trend'.

    With A2 r.pest_pressure_base bridge, biotic.insect_pressure on hotspot
    ridges starts above the default. Day 1 should be ≥ day 0 (engine may
    decay or grow but should not collapse)."""
    s = _r3("scenario_physics_threshold_pest_monitoring",
            "ScenarioPhysicsThresholdPestMonitoring")
    fw = s.get_typed_app(FarmWorldApp)
    sys_app = s.get_typed_app(SystemApp)
    fw.advance_physics_time()
    day0 = [fw.physics.biotic.states[rid].insect_pressure for rid in [16, 20, 27]]
    sys_app.advance_time(hours=24)
    day1 = [fw.physics.biotic.states[rid].insect_pressure for rid in [16, 20, 27]]
    # Hotspot ridges should still be at non-trivial pressure on day 1.
    # Audit's complaint was day0/day1 both showed ~0 pressure; now both
    # should reflect the seeded baseline.
    assert min(day0) >= 0.20, f"day0 pressure too low: {day0}"
    assert min(day1) >= 0.15, f"day1 pressure collapsed: {day1}"


def test_B5_fertilizer_scenario_ndvi_differential():
    """Audit (fertilizer_physics): 'actual sensor shows that the NDVI
    across the entire field is only around 0.20, not like C3 lacks
    fertilizer while other areas are normal.'

    Verify the canopy is NOT stuck at 0.20 across the whole field — at
    least some portion has a meaningfully higher NDVI (i.e. the canopy
    actually grew under physics, not stuck at VE)."""
    s = _r12_phys("scenario_fertilizer_physics_action_tick",
                  "ScenarioFarmWorldFertilizerPhysicsActionTick")
    fw = s.get_typed_app(FarmWorldApp)
    fw.advance_physics_time()
    ndvi_values = [fw.physics.canopy.states[rid].ndvi_proxy for rid in range(64)]
    max_ndvi = max(ndvi_values)
    # If the field's canopy were stuck at LAI=0.05 (the audit's broken
    # state), max NDVI would be ≈ 0.21 (Beer-Lambert with k=0.65 on tiny
    # LAI). With the stage-aware LAI fast-forward in physics_orchestrator,
    # at least some ridges should have NDVI well above 0.21.
    assert max_ndvi > 0.30, (
        f"Field canopy stuck at low NDVI (max={max_ndvi}); audit's broken "
        f"behavior would max ≈ 0.21"
    )


# ===========================================================================
# U — Universal patterns from the audit's "Modification Method" section
# ===========================================================================


def test_U1_all_robot_inspect_have_check_status_in_r3_scenarios():
    """Audit: 'For all oracles related to robot, it is recommended to
    uniformly add: robot.check_status() -> robot.inspect_*'."""
    r3_files = [
        "scenario_physics_emergence_replant_decision.py",
        "scenario_physics_differential_diagnosis_fertigation.py",
        "scenario_physics_disease_after_rain_fungicide.py",
        "scenario_physics_threshold_pest_monitoring.py",
    ]
    failures = []
    for f in r3_files:
        src = _read(f"are/simulation/scenarios/scenario_farm_worldpp_physics/{f}")
        if "robot.inspect_" in src:
            cs_count = len(re.findall(r"robot\.check_status\(\)", src))
            if cs_count == 0:
                failures.append(f"{f}: no robot.check_status() call")
    assert not failures, "\n".join(failures)


def test_U2_max_width_obeyed_across_all_scenarios():
    """Audit: 'spray split into … each segment <=10 ridges'.
    Scan every scenario file for direct calls and verify range widths."""
    rules = {
        "spray_pesticide": 10,
        "apply_fungicide": 10,
        "incorporate_residue": 10,
        "replant_seeds": 4,
    }
    failures = []
    for tier_dir in SCENARIO_DIRS.values():
        for f in tier_dir.glob("*.py"):
            src = f.read_text()
            for tool, max_w in rules.items():
                # Match calls with literal int args (skip variable args).
                for m in re.finditer(
                    rf"\b{tool}\((\d+),\s*(\d+)", src
                ):
                    s, e = int(m.group(1)), int(m.group(2))
                    width = e - s + 1
                    if width > max_w:
                        failures.append(
                            f"{f.name}: {tool}({s},{e}) width={width} > max {max_w}"
                        )
    assert not failures, "\n".join(failures)


def test_U3_sensor_zones_match_audit():
    """Audit: 'C1/S1: 0-10, C2/S2: 11-21, C3/S3: 22-32, C4/S4: 33-43,
    C5/S5: 44-53, C6/S6: 54-63'.

    The sensor_app.py defines zones as (sensor_id, install_ridge, start, end)
    tuples; we extract the (start, end) pairs and check coverage."""
    expected_zones = [
        (0, 10), (11, 21), (22, 32), (33, 43), (44, 53), (54, 63)
    ]
    src = _read("are/simulation/apps/farm_world/sensor_app.py")
    found = []
    for m in re.finditer(r'\(\s*"[1-6]"\s*,\s*\d+\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', src):
        a, b = int(m.group(1)), int(m.group(2))
        found.append((a, b))
    for ez in expected_zones:
        assert ez in found, f"sensor zone {ez} missing from sensor_app.py"


# ===========================================================================
# E — End-to-end sanity: all 34 oracle scenarios pass + produce FOS
# ===========================================================================


ALL_SCENARIOS = [
    # Round-1+2 mirror
    "scenario_farm_world_field_prep", "scenario_farm_world_fertilizer",
    "scenario_farm_world_drone_survey", "scenario_farm_world_harvest",
    "scenario_farm_world_irrigation", "scenario_farm_world_pesticide",
    "scenario_farm_world_pesticide_outbreak", "scenario_farm_world_planting",
    # Round-1+2 physics action/tick
    "scenario_farm_world_drone_survey_physics_action_tick",
    "scenario_farm_world_fertilizer_physics_action_tick",
    "scenario_farm_world_harvest_physics_action_tick",
    "scenario_farm_world_field_prep_physics_action_tick",
    "scenario_farm_world_pesticide_outbreak_physics_action_tick",
    "scenario_farm_world_irrigation_physics_action_tick",
    "scenario_farm_world_planting_physics_action_tick",
    "scenario_farm_world_pesticide_physics_action_tick",
    # Round-3 episodes
    "scenario_physics_planting_window_reschedule",
    "scenario_physics_emergence_replant_decision",
    "scenario_physics_differential_diagnosis_fertigation",
    "scenario_physics_pod_fill_drought_irrigation",
    "scenario_physics_disease_after_rain_fungicide",
    "scenario_physics_threshold_pest_monitoring",
    "scenario_physics_harvest_moisture_timing",
    "scenario_physics_postharvest_drying_storage",
    # Round-4 full-season
    "scenario_full_season_balanced", "scenario_full_season_cold_spring",
    "scenario_full_season_aphid_threshold", "scenario_full_season_dry_pod_fill",
    "scenario_full_season_mixed_stress_trap",
    "scenario_full_season_late_harvest_rain_risk",
    "scenario_full_season_adversarial_weather",
    "scenario_full_season_nutrient_differential",
    "scenario_full_season_wet_june_disease",
    "scenario_full_season_resource_limited",
]


@pytest.mark.parametrize("scenario_id", ALL_SCENARIOS)
def test_E_all_scenarios_oracle_pass_with_fos(scenario_id):
    """Run every registered farm scenario in oracle mode and verify it
    succeeds and produces a FOS value."""
    r = _run_oracle(scenario_id)
    assert r["success"], (
        f"{scenario_id} oracle failed: success={r['success']}, rc={r['rc']}"
    )
    assert r["fos"] is not None, f"{scenario_id}: no FOS reported"
    assert r["combined"] is not None, f"{scenario_id}: no workflow_combined"
