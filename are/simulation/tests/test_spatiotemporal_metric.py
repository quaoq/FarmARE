"""Unit tests for FARM-FOS, the spatiotemporally-weighted path metric.

Tests run on synthetic workflow dicts (the same shape produced by
``workflow_from_oracle_events`` / ``workflow_from_event_log``), so they are
fast, deterministic, and need no LLM traces. Each test maps to a property the
paper claims.
"""

from __future__ import annotations

import inspect

import pytest

from are.simulation.scenarios.fos import calibration as calib_mod
from are.simulation.scenarios.fos.calibration import (
    FROZEN_CALIBRATION,
    category_for_tool,
    temporal_kernel,
)
from are.simulation.scenarios.fos.spatiotemporal import (
    baseline_bfcl,
    compute_farm_fos,
    compute_farm_fos_v2,
    extract_ridges,
    spatial_overlap,
)

DAY = 86400.0


def _step(tool, t_days, **args):
    return {"tool_name": tool, "tool_args": args or {}, "op_type": "WRITE", "time": t_days * DAY}


def _step_with_content(tool, t_days, content, **args):
    step = _step(tool, t_days, **args)
    step["content"] = content
    return step


def _wf(steps):
    return {f"step{i}": s for i, s in enumerate(steps)}


# A minimal but representative oracle: plant (day 0), irrigate R5 (day 90),
# fungicide (day 60), harvest (day 130). Targets the whole field except
# irrigation which targets a dry strip 20-43.
def _oracle():
    return _wf([
        _step("TractorApp__plant_seeds", 0, start_ridge=0, end_ridge=63),
        _step("TractorApp__apply_fungicide", 60, start_ridge=33, end_ridge=43),
        _step("FieldOpsApp__irrigate", 90, start_ridge=20, end_ridge=43),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
    ])


def _oracle_with_postharvest():
    return _wf([
        _step("TractorApp__form_ridges", 0, ridge_width_m=1.1),
        _step("TractorApp__base_fertilize", 0),
        _step("TractorApp__plant_seeds", 1, start_ridge=0, end_ridge=63, depth_cm=4.0, seed_spacing_cm=7.9),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
        _step("TractorApp__unload_grain", 130),
        _step("TractorApp__store_grain", 130),
    ])


def _oracle_with_management_and_postharvest():
    return _wf([
        _step("TractorApp__form_ridges", 0, ridge_width_m=1.1),
        _step("TractorApp__base_fertilize", 0),
        _step("TractorApp__plant_seeds", 1, start_ridge=0, end_ridge=63, depth_cm=4.0, seed_spacing_cm=7.9),
        _step("TractorApp__apply_fungicide", 64, start_ridge=20, end_ridge=43, liters_per_ridge=3.4),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
        _step("TractorApp__unload_grain", 130),
        _step("TractorApp__store_grain", 130),
    ])


# ---------------------------------------------------------------------------
# Anti-circularity firewall
# ---------------------------------------------------------------------------
def test_calibration_is_yield_independent():
    """The calibration module must never read yield/correlation/run output in
    *code* (docstrings/comments may discuss it). Guards the central
    reviewer-defense claim: weights are a-priori agronomic priors, not fit.

    We tokenize and inspect only NAME/identifier tokens, so prose mentions of
    'yield' in the module docstring are ignored while any actual code access to
    yield/correlation data structures is caught."""
    import io
    import tokenize

    src = inspect.getsource(calib_mod)
    code_names: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NAME:
            code_names.append(tok.string.lower())
    code_blob = " ".join(code_names)

    banned = ["crop_loss", "pearson", "spearman", "recovered_yield",
              "biological_kg", "yield_preserved", "yield_ratio", "yield_loss",
              "read_excel", "read_csv", "evaluate_fos", "evaluate_workflows"]
    for token in banned:
        assert token not in code_blob, (
            f"calibration.py uses '{token}' in code — calibration must be a "
            f"frozen agronomic prior, independent of any yield/outcome data."
        )
    # and it must not import from the evaluation / metrics / outcome modules
    assert "import" in src  # sanity
    for mod in ["evaluation", "metrics", "pandas", "openpyxl"]:
        assert f"import {mod}" not in src and f"from are.simulation.scenarios.fos.{mod}" not in src, (
            f"calibration.py must not import '{mod}'."
        )


def test_calibration_table_frozen_values():
    """Spot-check a few frozen priors so accidental edits are caught."""
    assert FROZEN_CALIBRATION["plant"].weight == 1.00
    assert FROZEN_CALIBRATION["harvest"].kernel == "harvest"
    assert FROZEN_CALIBRATION["irrigate"].sigma_days == 7.0
    # ordering reflects agronomy: plant >= harvest >= irrigate >= fungicide
    w = FROZEN_CALIBRATION
    assert w["plant"].weight >= w["harvest"].weight >= w["irrigate"].weight >= w["fungicide"].weight


# ---------------------------------------------------------------------------
# Perfect replay -> ~1.0
# ---------------------------------------------------------------------------
def test_perfect_match_scores_one():
    oracle = _oracle()
    rep = compute_farm_fos(oracle, oracle, harm_lambda=0.25)
    assert rep.farm_fos == pytest.approx(1.0, abs=1e-9)
    assert rep.n_matched == rep.n_oracle_decisions == 4


# ---------------------------------------------------------------------------
# Temporal: monotonic decay with offset
# ---------------------------------------------------------------------------
def test_timing_offset_decays_monotonically():
    oracle = _oracle()
    scores = []
    for off in [0, 2, 4, 6, 8]:
        agent = _wf([
            _step("TractorApp__plant_seeds", 0, start_ridge=0, end_ridge=63),
            _step("TractorApp__apply_fungicide", 60, start_ridge=33, end_ridge=43),
            _step("FieldOpsApp__irrigate", 90 + off, start_ridge=20, end_ridge=43),
            _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
        ])
        scores.append(compute_farm_fos(agent_workflow=agent, oracle_workflow=oracle).farm_fos)
    # strictly non-increasing as the irrigation drifts off its window
    for a, b in zip(scores, scores[1:]):
        assert b <= a + 1e-9
    assert scores[0] > scores[-1]  # clear total drop


def test_irrigation_beyond_window_zero_temporal_credit():
    oracle = _oracle()
    # irrigate 20 days late >> sigma=7 -> kernel 0 for that action
    agent = _wf([
        _step("TractorApp__plant_seeds", 0, start_ridge=0, end_ridge=63),
        _step("TractorApp__apply_fungicide", 60, start_ridge=33, end_ridge=43),
        _step("FieldOpsApp__irrigate", 110, start_ridge=20, end_ridge=43),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
    ])
    rep = compute_farm_fos(oracle, agent)
    irr = [r for r in rep.per_action if r.category == "irrigate"][0]
    assert irr.temporal_credit == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Spatial: wrong ridges penalised
# ---------------------------------------------------------------------------
def test_spatial_miss_penalised():
    oracle = _oracle()
    # irrigate the wrong half of the field (0-11) instead of 20-43
    agent = _wf([
        _step("TractorApp__plant_seeds", 0, start_ridge=0, end_ridge=63),
        _step("TractorApp__apply_fungicide", 60, start_ridge=33, end_ridge=43),
        _step("FieldOpsApp__irrigate", 90, start_ridge=0, end_ridge=11),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
    ])
    rep = compute_farm_fos(oracle, agent)
    irr = [r for r in rep.per_action if r.category == "irrigate"][0]
    assert irr.spatial_overlap == pytest.approx(0.0)
    assert rep.farm_fos < 1.0


def test_partial_spatial_overlap():
    # oracle targets 20-43 (24 ridges); agent hits 20-31 (12) -> 0.5 overlap
    assert spatial_overlap(set(range(20, 32)), set(range(20, 44))) == pytest.approx(0.5)
    # whole-field agent action covers any oracle target
    assert spatial_overlap(None, set(range(20, 44))) == 1.0


# ---------------------------------------------------------------------------
# Omission: missing a high-weight action hurts more than a low-weight one
# ---------------------------------------------------------------------------
def test_missing_high_weight_action_drops_more():
    oracle = _oracle()
    drop_harvest = _wf([
        _step("TractorApp__plant_seeds", 0, start_ridge=0, end_ridge=63),
        _step("TractorApp__apply_fungicide", 60, start_ridge=33, end_ridge=43),
        _step("FieldOpsApp__irrigate", 90, start_ridge=20, end_ridge=43),
    ])  # harvest omitted (weight 0.90)
    drop_fungicide = _wf([
        _step("TractorApp__plant_seeds", 0, start_ridge=0, end_ridge=63),
        _step("FieldOpsApp__irrigate", 90, start_ridge=20, end_ridge=43),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
    ])  # fungicide omitted (weight 0.70)
    s_no_harvest = compute_farm_fos(oracle, drop_harvest).farm_fos
    s_no_fungicide = compute_farm_fos(oracle, drop_fungicide).farm_fos
    assert s_no_harvest < s_no_fungicide  # losing the heavier action hurts more


# ---------------------------------------------------------------------------
# Harm: spurious harmless observation actions don't change the score;
#       a spurious *decision* action does (when lambda>0)
# ---------------------------------------------------------------------------
def test_spurious_observation_actions_are_free():
    oracle = _oracle()
    agent = _oracle()
    # add a bunch of reads / checks / waits — none are decision actions
    extra = dict(agent)
    extra["x1"] = _step("SensorApp__read_soil_sensors", 5)
    extra["x2"] = _step("WeatherApp__get_forecast", 6)
    extra["x3"] = _step("SystemApp__advance_time", 7, days=1)
    extra["x4"] = _step("RobotApp__check_status", 8)
    assert compute_farm_fos(oracle, extra).farm_fos == pytest.approx(1.0, abs=1e-9)


def test_spurious_decision_action_incurs_harm():
    oracle = _oracle()
    agent = dict(_oracle())
    # an unprovoked extra fungicide spray (no matching oracle action left)
    agent["x1"] = _step("TractorApp__apply_fungicide", 75, start_ridge=0, end_ridge=10)
    s_with_harm = compute_farm_fos(oracle, agent, harm_lambda=0.25).farm_fos
    s_no_harm = compute_farm_fos(oracle, agent, harm_lambda=0.0).farm_fos
    assert s_with_harm < s_no_harm == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Baselines: the degeneracy relationships
# ---------------------------------------------------------------------------
def test_bfcl_is_timing_blind():
    """BFCL gives full credit even when timing is wrong, which is exactly why
    it fails to track yield. We distort the *schedule* (not a pure global
    translation): planting stays on day 0 but the mid/late-season actions are
    badly mistimed relative to their windows."""
    oracle = _oracle()
    agent = _wf([
        _step("TractorApp__plant_seeds", 0, start_ridge=0, end_ridge=63),     # on time
        _step("TractorApp__apply_fungicide", 100, start_ridge=33, end_ridge=43),  # 40d late
        _step("FieldOpsApp__irrigate", 130, start_ridge=20, end_ridge=43),    # 40d late
        _step("TractorApp__harvest", 170, start_ridge=0, end_ridge=63),       # 40d late
    ])
    assert baseline_bfcl(oracle, agent) == pytest.approx(1.0)  # blind to timing
    assert compute_farm_fos(oracle, agent).farm_fos < 0.5      # FARM-FOS punishes it


def test_extract_ridges_shapes():
    assert extract_ridges({"start_ridge": 20, "end_ridge": 23}) == {20, 21, 22, 23}
    assert extract_ridges({"ridge_ids": [1, 2, 3]}) == {1, 2, 3}
    assert extract_ridges({"ridge_id": 7}) == {7}
    assert extract_ridges({}) is None
    assert extract_ridges(None) is None


def test_harvest_asymmetric_kernel():
    h = FROZEN_CALIBRATION["harvest"]
    assert temporal_kernel(0.0, h) == pytest.approx(1.0)
    assert temporal_kernel(5.0, h) == pytest.approx(1.0)    # within grace
    assert temporal_kernel(-3.0, h) < 1.0                   # early penalised
    # early is harsher than late by the same offset magnitude
    assert temporal_kernel(-6.0, h) < temporal_kernel(13.0, h)


# ---------------------------------------------------------------------------
# FARM-FOS-v2: plant/harvest gates and agronomic parameters
# ---------------------------------------------------------------------------
def test_v2_perfect_postharvest_path_is_not_penalised():
    oracle = _oracle_with_postharvest()
    rep = compute_farm_fos_v2(oracle, oracle)
    assert rep.farm_fos_v2_path == pytest.approx(1.0)
    assert rep.farm_fos_v2_param == pytest.approx(1.0)
    assert rep.farm_fos_v2_terminal == pytest.approx(1.0)
    assert rep.farm_fos_v2_total == pytest.approx(1.0)


def test_v2_bad_seed_spacing_lowers_parameter_score():
    oracle = _oracle_with_postharvest()
    agent = _wf([
        _step("TractorApp__form_ridges", 0, ridge_width_m=1.1),
        _step("TractorApp__base_fertilize", 0),
        # Same path and ridges, but extreme density: 2 cm in-row spacing.
        _step("TractorApp__plant_seeds", 1, start_ridge=0, end_ridge=63, depth_cm=4.0, seed_spacing_cm=2.0),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
        _step("TractorApp__unload_grain", 130),
        _step("TractorApp__store_grain", 130),
    ])
    rep = compute_farm_fos_v2(oracle, agent)
    assert rep.farm_fos_v2_path == pytest.approx(1.0)
    assert rep.farm_fos_v2_terminal == pytest.approx(1.0)
    assert rep.farm_fos_v2_param < 0.35
    assert rep.diagnosis.density_error_pct is not None
    assert rep.diagnosis.density_error_pct > 200.0


def test_v2_missing_harvest_breaks_recovered_chain():
    oracle = _oracle_with_postharvest()
    agent = _wf([
        _step("TractorApp__form_ridges", 0, ridge_width_m=1.1),
        _step("TractorApp__base_fertilize", 0),
        _step("TractorApp__plant_seeds", 1, start_ridge=0, end_ridge=63, depth_cm=4.0, seed_spacing_cm=7.9),
    ])
    rep = compute_farm_fos_v2(oracle, agent)
    assert rep.farm_fos_v2_terminal == pytest.approx(0.0)
    assert rep.farm_fos_v2_total == pytest.approx(0.0)
    assert rep.diagnosis.missing_harvest_ridges == 64
    assert "recovered chain broken" in rep.diagnosis.primary_issue


def test_v2_harvest_without_store_is_incomplete():
    oracle = _oracle_with_postharvest()
    agent = _wf([
        _step("TractorApp__form_ridges", 0, ridge_width_m=1.1),
        _step("TractorApp__base_fertilize", 0),
        _step("TractorApp__plant_seeds", 1, start_ridge=0, end_ridge=63, depth_cm=4.0, seed_spacing_cm=7.9),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
        _step("TractorApp__unload_grain", 130),
    ])
    rep = compute_farm_fos_v2(oracle, agent)
    assert rep.farm_fos_v2_terminal < 0.5
    assert rep.diagnosis.postharvest_incomplete is True
    assert rep.diagnosis.primary_issue == "postharvest chain incomplete after harvest"


def test_v2_store_warning_is_quality_warning_not_missing_store():
    oracle = _oracle_with_postharvest()
    agent = _wf([
        _step("TractorApp__form_ridges", 0, ridge_width_m=1.1),
        _step("TractorApp__base_fertilize", 0),
        _step("TractorApp__plant_seeds", 1, start_ridge=0, end_ridge=63, depth_cm=4.0, seed_spacing_cm=7.9),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
        _step("TractorApp__unload_grain", 130),
        _step_with_content(
            "TractorApp__store_grain",
            130,
            {"status": "ok", "warning": "grain stored without drying"},
        ),
    ])
    rep = compute_farm_fos_v2(oracle, agent)
    assert rep.farm_fos_v2_terminal == pytest.approx(0.9)
    assert rep.diagnosis.postharvest_incomplete is False
    assert rep.diagnosis.postharvest_warning is True
    assert rep.diagnosis.primary_issue == "postharvest warning after storage/drying"


def test_v2_missing_management_action_lowers_param_without_zeroing_terminal():
    oracle = _oracle_with_management_and_postharvest()
    agent = _wf([
        _step("TractorApp__form_ridges", 0, ridge_width_m=1.1),
        _step("TractorApp__base_fertilize", 0),
        _step("TractorApp__plant_seeds", 1, start_ridge=0, end_ridge=63, depth_cm=4.0, seed_spacing_cm=7.9),
        _step("TractorApp__harvest", 130, start_ridge=0, end_ridge=63),
        _step("TractorApp__unload_grain", 130),
        _step("TractorApp__store_grain", 130),
    ])
    rep = compute_farm_fos_v2(oracle, agent)
    assert rep.farm_fos_v2_terminal == pytest.approx(1.0)
    assert 0.0 < rep.farm_fos_v2_param < 1.0
    assert 0.0 < rep.farm_fos_v2_total < 1.0
    assert rep.diagnosis.missing_management_actions == 1
    assert rep.diagnosis.primary_issue == "missing or late management actions: 1"
