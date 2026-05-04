"""
Gate-predicate spec for the FOS Decision (D) component.

A `GateSpec` encodes one decision-point intent: "the agent should take an
action of one of these types within this time window, optionally satisfying a
precondition (e.g., observation X must have already happened, action must
target a specific ridge range)." A gate is matched by **any** eligible event;
multiple defensible orderings all satisfy the same gate.

Gates are scenario-specific. Each round-3 / round-4 scenario defines ~5 gates
in its `_gates(self)` method. They're authored as Python objects (rather than
e.g. a YAML config) because the `requires` predicate needs to inspect the
event log and physics state — that's hard to express declaratively.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# A precondition predicate is called with:
#   candidate_event:  the CompletedEvent being considered
#   prior_events:     all CompletedEvents strictly before candidate (in time order)
#   scenario:         the running scenario instance
#   env:              the simulation environment
# It returns True iff the candidate event satisfies the precondition.
PreconditionPredicate = Callable[[Any, list[Any], Any, Any], bool]


@dataclass
class GateSpec:
    """A single decision-point gate.

    Attributes:
        name:           Short identifier (e.g., "G1", "irrigate_dry_zone").
        intent:         Human-readable description; serves as the agronomic
                        anchor for the paper appendix.
        window_days:    (start_day, end_day) relative to scenario_start_time.
                        end_day is inclusive. Use (-inf, +inf) to disable.
        eligible_tools: List of (class_name, function_name) tuples; the gate
                        is matched if any agent event in the window has a
                        matching (class_name, function_name) pair.
        requires:       Optional precondition. If provided, the candidate
                        event matches only if requires(candidate, prior, scenario, env)
                        returns True.
    """
    name: str
    intent: str
    window_days: tuple[float, float]
    eligible_tools: list[tuple[str, str]]
    requires: Optional[PreconditionPredicate] = field(default=None)


@dataclass
class GateResult:
    """Outcome of evaluating one gate against an agent's event log."""
    gate: GateSpec
    matched: bool
    matched_event_id: Optional[str] = None
    matched_at_day: Optional[float] = None
    rejection_reason: Optional[str] = None  # if not matched, why

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate.name,
            "intent": self.gate.intent,
            "matched": self.matched,
            "matched_event_id": self.matched_event_id,
            "matched_at_day": (
                round(self.matched_at_day, 3) if self.matched_at_day is not None else None
            ),
            "rejection_reason": self.rejection_reason,
            "window_days": list(self.gate.window_days),
        }
