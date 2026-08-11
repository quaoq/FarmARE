"""Unit tests for path_correctness_v2 — the relaxed-numeric variant of
the legacy ``workflow_validation.evaluate_workflows`` path_correctness
metric.

These tests pin the contract the user asked for:
  * binary numeric matching with tol_ratio (default 2.0)
  * AND across multiple numeric args in the same step
  * non-numeric args still require exact equality
  * when the oracle issues the same tool with several numeric values,
    the agent step is mapped to the *closest* in-window oracle anchor
"""
from __future__ import annotations

from are.simulation.scenarios.fos.path_correctness_v2 import (
    DEFAULT_TOL_RATIO,
    evaluate_path_correctness_v2,
)


def _step(name: str, args: dict | None = None, op: str = "WRITE") -> dict:
    return {"tool_name": name, "tool_args": args or {}, "op_type": op}


# ---------------------------------------------------------------------------
# Sanity: identical sequences score 1.0
# ---------------------------------------------------------------------------


def test_identical_sequences_score_one():
    oracle = [
        _step("FarmApp__plant_seeds", {"start_ridge": 0, "end_ridge": 31}),
        _step("FarmApp__irrigate", {"amount_mm": 25.0}),
    ]
    agent = [
        _step("FarmApp__plant_seeds", {"start_ridge": 0, "end_ridge": 31}),
        _step("FarmApp__irrigate", {"amount_mm": 25.0}),
    ]
    out = evaluate_path_correctness_v2(oracle, agent)
    assert out["path_correctness_v2"] == 1.0
    assert out["n_agent_reused"] == 2
    assert out["n_agent_fresh"] == 0


# ---------------------------------------------------------------------------
# Single numeric arg: tol=2.0 binary window
# ---------------------------------------------------------------------------


def test_numeric_window_binary_at_default_tol():
    oracle = [_step("Irrigation__apply", {"amount": 1.0})]

    # In window [0.5, 2.0] → match.
    for v in (0.5, 0.9, 1.0, 1.5, 2.0):
        out = evaluate_path_correctness_v2(
            oracle, [_step("Irrigation__apply", {"amount": v})]
        )
        assert out["path_correctness_v2"] == 1.0, f"agent={v} should match"
        assert out["n_agent_reused"] == 1

    # Out of window → no match (substitution cost 1, len=1, pc2 = 0.0).
    for v in (0.49, 0.4, 2.01, 2.5, 5.0):
        out = evaluate_path_correctness_v2(
            oracle, [_step("Irrigation__apply", {"amount": v})]
        )
        assert out["path_correctness_v2"] == 0.0, f"agent={v} should NOT match"
        assert out["n_agent_fresh"] == 1


# ---------------------------------------------------------------------------
# AND across multiple numeric args
# ---------------------------------------------------------------------------


def test_multiple_numeric_args_require_all_in_window():
    oracle = [
        _step("Tractor__apply", {"amount": 50.0, "duration_min": 30.0}),
    ]
    # Both in window → match.
    out = evaluate_path_correctness_v2(
        oracle,
        [_step("Tractor__apply", {"amount": 80.0, "duration_min": 40.0})],
    )
    assert out["path_correctness_v2"] == 1.0

    # amount in window (1.6×), duration out (3.33×) → no match.
    out = evaluate_path_correctness_v2(
        oracle,
        [_step("Tractor__apply", {"amount": 80.0, "duration_min": 100.0})],
    )
    assert out["path_correctness_v2"] == 0.0


# ---------------------------------------------------------------------------
# Non-numeric args still demand exact equality
# ---------------------------------------------------------------------------


def test_categorical_args_require_exact_match():
    oracle = [_step("Tractor__attach", {"implement": "sprayer"})]
    # Same string → match.
    out = evaluate_path_correctness_v2(
        oracle, [_step("Tractor__attach", {"implement": "sprayer"})]
    )
    assert out["path_correctness_v2"] == 1.0
    # Different string → no match.
    out = evaluate_path_correctness_v2(
        oracle, [_step("Tractor__attach", {"implement": "harvester"})]
    )
    assert out["path_correctness_v2"] == 0.0


def test_list_arg_requires_exact_equality():
    oracle = [_step("Drone__fly", {"ridge_ids": [1, 2, 3]})]
    # Exactly equal → match.
    out = evaluate_path_correctness_v2(
        oracle, [_step("Drone__fly", {"ridge_ids": [1, 2, 3]})]
    )
    assert out["path_correctness_v2"] == 1.0
    # Different list (even partial overlap) → no match.
    out = evaluate_path_correctness_v2(
        oracle, [_step("Drone__fly", {"ridge_ids": [1, 2, 99]})]
    )
    assert out["path_correctness_v2"] == 0.0


# ---------------------------------------------------------------------------
# Closest-anchor selection: A(1.5) → A(2.8); agent A(2) prefers 1.5 over 2.8
# ---------------------------------------------------------------------------


def test_closest_anchor_wins_when_oracle_repeats_tool():
    oracle = [
        _step("Irrigation__apply", {"amount": 1.5}),
        _step("Irrigation__apply", {"amount": 2.8}),
        _step("Tractor__attach", {"implement": "sprayer"}),
    ]
    # Agent calls irrigate with amount=2.0. ratio(2.0, 1.5)=1.33,
    # ratio(2.0, 2.8)=1.4 — both inside tol=2.0, closest is 1.5.
    agent = [_step("Irrigation__apply", {"amount": 2.0})]
    out = evaluate_path_correctness_v2(oracle, agent)
    # Symbols: oracle = "ABC", agent = "A" (the 1.5 anchor).
    assert out["oracle_symbols"] == "ABC"
    assert out["agent_symbols"] == "A"
    # Levenshtein("A", "ABC") = 2 (delete B, delete C). pc2 = 1 - 2/3.
    assert out["path_correctness_v2"] == round(1.0 - 2.0 / 3.0, 4)


def test_two_agent_calls_split_across_two_oracle_anchors():
    oracle = [
        _step("Irrigation__apply", {"amount": 1.5}),
        _step("Irrigation__apply", {"amount": 2.8}),
    ]
    # Agent calls in oracle order; both inside tol=2.0 window of their
    # respective anchors.
    agent = [
        _step("Irrigation__apply", {"amount": 1.4}),  # closer to 1.5
        _step("Irrigation__apply", {"amount": 2.6}),  # closer to 2.8
    ]
    out = evaluate_path_correctness_v2(oracle, agent)
    # Both anchors reused, in correct order → perfect score.
    assert out["path_correctness_v2"] == 1.0
    assert out["n_agent_reused"] == 2


def test_two_agent_calls_unclaimed_preference_breaks_distance_tie():
    """If two agent calls each look equally close to one anchor, the
    second one should prefer an un-claimed anchor over re-claiming."""
    oracle = [
        _step("Irrigation__apply", {"amount": 1.0}),
        _step("Irrigation__apply", {"amount": 1.0}),  # same anchor distance as the first
    ]
    agent = [
        _step("Irrigation__apply", {"amount": 1.0}),
        _step("Irrigation__apply", {"amount": 1.0}),
    ]
    out = evaluate_path_correctness_v2(oracle, agent)
    # Each agent call lands on a different oracle anchor (claim_count tie-break).
    assert out["path_correctness_v2"] == 1.0


# ---------------------------------------------------------------------------
# Default tol & out-of-registry tools fallback
# ---------------------------------------------------------------------------


def test_default_tol_ratio_is_two():
    assert DEFAULT_TOL_RATIO == 2.0


def test_empty_workflows_score_one_by_convention():
    """Both sides empty → no penalty, by Levenshtein convention (0/1 = 0)."""
    out = evaluate_path_correctness_v2([], [])
    assert out["path_correctness_v2"] == 1.0
    assert out["n_oracle_steps"] == 0
    assert out["n_agent_steps"] == 0


def test_v2_returns_full_metric_set():
    """v2 must emit coverage_v2 / ktc_raw_v2 / ktc_adjusted_v2 / combined_v2
    alongside path_correctness_v2 — the same set as legacy
    workflow_validation.evaluate_workflows but suffixed _v2."""
    out = evaluate_path_correctness_v2(
        [_step("X__a"), _step("X__b")], [_step("X__a"), _step("X__b")]
    )
    for key in (
        "path_correctness_v2",
        "coverage_v2",
        "ktc_raw_v2",
        "ktc_adjusted_v2",
        "combined_v2",
    ):
        assert key in out, f"missing {key} in v2 output"
    assert out["path_correctness_v2"] == 1.0
    assert out["coverage_v2"] == 1.0
    assert out["combined_v2"] == 1.0


def test_v2_collapses_to_v1_when_tol_one():
    """tol=1.0 forces ratio<=1 (i.e. exact equality), so the v2 alphabet
    must coincide with the original v1 alphabet — and every metric in
    v2 must equal its v1 counterpart byte-for-byte."""
    from are.simulation.scenarios.workflow_validation import evaluate_workflows

    oracle = [
        _step("Irrigation__apply", {"amount": 1.5}),
        _step("Irrigation__apply", {"amount": 2.8}),
        _step("Tractor__attach", {"implement": "sprayer"}),
    ]
    agent = [
        _step("Irrigation__apply", {"amount": 1.5}),
        _step("Tractor__attach", {"implement": "sprayer"}),
        _step("Irrigation__apply", {"amount": 2.8}),
    ]
    v1 = evaluate_workflows(oracle, agent)
    v2 = evaluate_path_correctness_v2(oracle, agent, tol_ratio=1.0)
    assert v2["path_correctness_v2"] == v1["path_correctness"]
    assert v2["coverage_v2"] == v1["coverage"]
    assert v2["ktc_raw_v2"] == v1["ktc_raw"]
    assert v2["ktc_adjusted_v2"] == v1["ktc_adjusted"]
    assert v2["combined_v2"] == v1["combined"]


def test_v2_lifts_above_v1_when_numbers_within_tol():
    """At tol=2.0, agent values within the [0.5×, 2×] window of the oracle
    share a symbol — so coverage_v2 >= coverage and pc2 >= path_correctness
    on a sequence the v1 metric mis-handles."""
    from are.simulation.scenarios.workflow_validation import evaluate_workflows

    oracle = [
        _step("Irrigation__apply", {"amount": 1.0}),
        _step("Irrigation__apply", {"amount": 2.0}),
    ]
    agent = [
        _step("Irrigation__apply", {"amount": 1.4}),  # in window of 1.0
        _step("Irrigation__apply", {"amount": 2.5}),  # in window of 2.0
    ]
    v1 = evaluate_workflows(oracle, agent)
    v2 = evaluate_path_correctness_v2(oracle, agent, tol_ratio=2.0)
    # v1 sees four distinct symbols → coverage = 0.0; pc = 0.0.
    assert v1["path_correctness"] == 0.0
    assert v1["coverage"] == 0.0
    # v2 with tol=2.0 maps each agent call to the closest in-window oracle
    # anchor, recovering full credit.
    assert v2["path_correctness_v2"] == 1.0
    assert v2["coverage_v2"] == 1.0
    assert v2["combined_v2"] == 1.0


def test_user_op_steps_are_ignored():
    """Same as the legacy metric — USER op_type rows are stripped."""
    oracle = [
        _step("FarmApp__plant_seeds", {"start_ridge": 0}),
        _step("AgentUserInterface__send_message_to_user", {"text": "done"}, op="USER"),
    ]
    agent = [_step("FarmApp__plant_seeds", {"start_ridge": 0})]
    out = evaluate_path_correctness_v2(oracle, agent)
    assert out["n_oracle_steps"] == 1
    assert out["path_correctness_v2"] == 1.0
