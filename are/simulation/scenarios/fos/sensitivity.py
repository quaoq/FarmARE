"""
Sensitivity-analysis utility for FOS reports.

The composite FOS = w_O · O + w_D · D + w_E · E. Reviewers reasonably ask
"why these weights?". We answer by re-weighting existing reports without
re-running the simulation — the per-component scores are all that's needed.

Use:
    from are.simulation.scenarios.fos import re_weight_fos

    new_report = re_weight_fos(report, {"outcome": 0.4, "decision": 0.4, "efficiency": 0.2})

Bulk usage in `scripts/fos_sensitivity_analysis.py`:
    for w in weight_grid:
        for report in load_reports(suite_output_dir):
            new = re_weight_fos(report, w)
            ...

Re-weighting is idempotent and preserves all breakdowns; only the composite
`fos` field and the `weights` dict change.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from are.simulation.scenarios.fos.metrics import FOSComponents, FOSReport


def re_weight_fos(report: FOSReport, new_weights: dict[str, float]) -> FOSReport:
    """Recompute FOS composite with new weights, preserving component scores.

    Weights are normalised internally so they sum to 1.
    """
    weights = _normalise(new_weights)
    components = report.components
    fos = (
        weights["outcome"] * components.outcome
        + weights["decision"] * components.decision
        + weights["efficiency"] * components.efficiency
    )
    new_components = FOSComponents(
        outcome=components.outcome,
        decision=components.decision,
        efficiency=components.efficiency,
        fos=max(0.0, min(1.0, fos)),
    )
    return replace(report, components=new_components, weights=dict(weights))


def weight_grid(
    outcome_range: Iterable[float] = (0.4, 0.5, 0.6),
    decision_range: Iterable[float] = (0.2, 0.3, 0.4),
) -> list[dict[str, float]]:
    """Cartesian product of plausible weight assignments for an appendix sweep.

    `efficiency` is set so the three weights sum to 1 for each cell.
    """
    grid: list[dict[str, float]] = []
    for w_o in outcome_range:
        for w_d in decision_range:
            w_e = 1.0 - w_o - w_d
            if w_e < 0:
                continue
            grid.append({"outcome": w_o, "decision": w_d, "efficiency": w_e})
    return grid


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    keys = ("outcome", "decision", "efficiency")
    coerced = {k: float(weights.get(k, 0.0)) for k in keys}
    total = sum(coerced.values())
    if total <= 0.0:
        # fall back to defaults if caller passed all zeros
        return {"outcome": 0.5, "decision": 0.3, "efficiency": 0.2}
    return {k: v / total for k, v in coerced.items()}
