"""
Farm Operational Score (FOS) — composite long-horizon evaluation framework.

FOS = w_O · Outcome + w_D · Decision + w_E · Efficiency

Default weights: 0.5 / 0.3 / 0.2.

The three components measure orthogonal qualities of an agent's run:
  - **Outcome (O)** — what was the result? (yield, crop loss, safety violations)
  - **Decision (D)** — did the agent act at the right stages? (gate-predicate matching)
  - **Efficiency (E)** — did the agent waste resources? (tool inflation, redundant reads)

This module exists because at long horizon, workflow path-matching is brittle:
multiple correct paths exist, oracle depends on weather realisation, decision
penalties compound, action ≠ outcome, recovery is double-penalised, argument
granularity creates spurious mismatches. FOS's three-component decomposition
keeps the strengths of path-matching (still reported alongside) while adding
outcome-grounded and reasoning-aware signals that scale with horizon.

Public API:
  - FOSReport, FOSComponents, OutcomeBreakdown, EfficiencyBreakdown — dataclasses
  - GateSpec, GateResult — decision-gate spec
  - DEFAULT_WEIGHTS — the 0.5/0.3/0.2 dict
  - evaluate_fos(scenario, env, gates, weights=None) -> FOSReport
  - append_fos_evaluation(scenario, env, result, gates, ...) — mirror of
    workflow_validation.append_workflow_evaluation; appends a `fos_eval: ...`
    line to result.rationale so the suite-runner CSV picks it up.
  - re_weight_fos(report, new_weights) — sensitivity helper.
"""

from are.simulation.scenarios.fos.evaluation import (
    DEFAULT_WEIGHTS,
    append_fos_evaluation,
    evaluate_fos,
)
from are.simulation.scenarios.fos.gates import GateResult, GateSpec
from are.simulation.scenarios.fos.metrics import (
    EfficiencyBreakdown,
    FOSComponents,
    FOSReport,
    OutcomeBreakdown,
)
from are.simulation.scenarios.fos.sensitivity import re_weight_fos

__all__ = [
    "DEFAULT_WEIGHTS",
    "EfficiencyBreakdown",
    "FOSComponents",
    "FOSReport",
    "GateResult",
    "GateSpec",
    "OutcomeBreakdown",
    "append_fos_evaluation",
    "evaluate_fos",
    "re_weight_fos",
]
