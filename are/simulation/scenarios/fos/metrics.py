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

    `yield_ratio` is the headline number. `crop_loss_count` and
    `safety_violations` are penalty terms.
    """
    yield_ratio: float
    recovered_yield_kg: float
    scenario_potential_kg: float
    crop_loss_count: int
    crop_loss_fraction: float
    safety_violations: int
    safety_violation_details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "yield_ratio": round(self.yield_ratio, 4),
            "recovered_yield_kg": round(self.recovered_yield_kg, 2),
            "scenario_potential_kg": round(self.scenario_potential_kg, 2),
            "crop_loss_count": self.crop_loss_count,
            "crop_loss_fraction": round(self.crop_loss_fraction, 4),
            "safety_violations": self.safety_violations,
            "safety_violation_details": self.safety_violation_details,
        }


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
