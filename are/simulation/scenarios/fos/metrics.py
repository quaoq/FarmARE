"""
FOS metric dataclasses.

Mirrors the "decompose, then compose" approach from the integration guide and
the rounds-3-4 paper plan. Every report captures both the composite FOS score
and the per-component breakdowns so reviewers can audit the metric and
post-hoc sensitivity analyses can re-weight without re-running.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OutcomeBreakdown:
    """How the Outcome (O) component was computed.

    Headline metric:
    - crop_loss_pct: fraction of yield lost vs. an oracle baseline,
      computed as `1 - agent_biological_kg / oracle_biological_kg`.
      None when no oracle baseline is available; in that case the
      legacy `yield_ratio` carries the signal.
    - yield_preserved_ratio: the inverse, `agent / oracle`, capped at
      [0, 1] for FOS Outcome score arithmetic.

    Both numbers compare *biological* yields (the latent yield that the
    crop achieved by R8) so that mid-season episodes — where the agent
    isn't expected to actually run the harvest — can still be scored
    on a like-for-like basis: did the agent preserve the field's yield
    potential vs. an oracle's choices?

    Yield-pool fields (always populated):
    - agent_biological_kg: sum of `biological_yield_g_m2 * area / 1000`
      across planted ridges in the (replayed + post-extrapolation) run.
    - oracle_biological_kg: same, from the cached oracle baseline JSON
      under `oracle_baselines/<scenario_id>.json`. None when no baseline
      file is available for this scenario.

    Three crop-loss buckets (mutually exclusive, count semantics):
    - growing_loss_count: ridges whose biological collapsed below
      `crop_loss_threshold * field-median` mid-season.
    - harvest_loss_count: harvested ridges with recovered/biological <
      `crop_loss_threshold` (machine loss, quality discount).
    - unharvested_mature_count: ridges at R8 the agent never harvested.

    Modulator:
    - expects_agent_harvest: True for scenarios where harvesting is
      part of the agent's mandate (round-1+2 harvest, round-4 fullseason,
      post-harvest drying). For False-mandate scenarios (mid-season
      episodes), unharvested_mature is NOT penalised on Outcome score,
      because the agent wasn't asked to harvest in the first place.

    Backwards-compat aliases:
    - crop_loss_count == harvest_loss_count (legacy CSV column).
    - yield_ratio: the pre-baseline metric; still computed (mostly for
      audit / fallback when no baseline is present).
    """
    yield_ratio: float
    recovered_yield_kg: float
    scenario_potential_kg: float
    agent_biological_kg: float
    oracle_biological_kg: float | None
    yield_preserved_ratio: float | None
    crop_loss_pct: float | None
    growing_loss_count: int
    harvest_loss_count: int
    unharvested_mature_count: int
    crop_loss_count: int
    crop_loss_fraction: float
    safety_violations: int
    expects_agent_harvest: bool = True
    safety_violation_details: list[dict[str, Any]] = field(default_factory=list)
    extrapolation: dict[str, Any] | None = None
    # Do-nothing baseline: yield with zero management (lower bound).
    donothing_biological_kg: float | None = None
    # Normalized yield: (agent - donothing) / (oracle - donothing).
    # 0 = no improvement over doing nothing; 1 = matches oracle.
    normalized_yield_score: float | None = None
    # Focus-ridge subset: per-scenario "ridges that matter most".
    focus_ridge_ids: list[int] | None = None
    focus_agent_biological_kg: float | None = None
    focus_oracle_biological_kg: float | None = None
    focus_donothing_biological_kg: float | None = None
    focus_yield_preserved_ratio: float | None = None
    focus_normalized_yield_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        def _r(v: float | None, n: int = 4) -> float | None:
            return None if v is None else round(v, n)

        out: dict[str, Any] = {
            "yield_ratio": round(self.yield_ratio, 4),
            "recovered_yield_kg": round(self.recovered_yield_kg, 2),
            "scenario_potential_kg": round(self.scenario_potential_kg, 2),
            "agent_biological_kg": round(self.agent_biological_kg, 2),
            "oracle_biological_kg": _r(self.oracle_biological_kg, 2),
            "yield_preserved_ratio": _r(self.yield_preserved_ratio, 4),
            "crop_loss_pct": _r(self.crop_loss_pct, 4),
            "donothing_biological_kg": _r(self.donothing_biological_kg, 2),
            "normalized_yield_score": _r(self.normalized_yield_score, 4),
            "growing_loss_count": self.growing_loss_count,
            "harvest_loss_count": self.harvest_loss_count,
            "unharvested_mature_count": self.unharvested_mature_count,
            "crop_loss_count": self.crop_loss_count,
            "crop_loss_fraction": round(self.crop_loss_fraction, 4),
            "safety_violations": self.safety_violations,
            "expects_agent_harvest": self.expects_agent_harvest,
            "safety_violation_details": self.safety_violation_details,
        }
        if self.extrapolation is not None:
            out["extrapolation"] = self.extrapolation
        if self.focus_ridge_ids is not None:
            out["focus_ridge_ids"] = self.focus_ridge_ids
            out["focus_agent_biological_kg"] = _r(self.focus_agent_biological_kg, 2)
            out["focus_oracle_biological_kg"] = _r(self.focus_oracle_biological_kg, 2)
            out["focus_donothing_biological_kg"] = _r(self.focus_donothing_biological_kg, 2)
            out["focus_yield_preserved_ratio"] = _r(self.focus_yield_preserved_ratio, 4)
            out["focus_normalized_yield_score"] = _r(self.focus_normalized_yield_score, 4)
        return out


@dataclass
class EfficiencyBreakdown:
    """How the Efficiency (E) component was computed."""
    agent_tool_calls: int
    oracle_tool_calls: int
    tool_inflation: float  # agent / oracle, capped at 3.0
    redundant_reads: int
    redundant_fraction: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_tool_calls": self.agent_tool_calls,
            "oracle_tool_calls": self.oracle_tool_calls,
            "tool_inflation": round(self.tool_inflation, 4),
            "redundant_reads": self.redundant_reads,
            "redundant_fraction": round(self.redundant_fraction, 4),
        }


@dataclass
class FOSComponents:
    """The three composable scores plus the weighted composite."""
    outcome: float  # ∈ [0, 1]
    decision: float  # ∈ [0, 1]
    efficiency: float  # ∈ [0, 1]
    fos: float  # weighted composite, ∈ [0, 1]

    def to_dict(self) -> dict[str, float]:
        return {
            "outcome": round(self.outcome, 4),
            "decision": round(self.decision, 4),
            "efficiency": round(self.efficiency, 4),
            "fos": round(self.fos, 4),
        }


@dataclass
class FOSReport:
    """Full FOS evaluation result for a single scenario run."""
    scenario_id: str
    components: FOSComponents
    outcome_breakdown: OutcomeBreakdown
    decision_breakdown: list[Any]  # list[GateResult]; type loosened to avoid circular import
    efficiency_breakdown: EfficiencyBreakdown
    weights: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "components": self.components.to_dict(),
            "outcome_breakdown": self.outcome_breakdown.to_dict(),
            "decision_breakdown": [
                gate_result.to_dict()
                if hasattr(gate_result, "to_dict")
                else gate_result
                for gate_result in self.decision_breakdown
            ],
            "efficiency_breakdown": self.efficiency_breakdown.to_dict(),
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
        }
