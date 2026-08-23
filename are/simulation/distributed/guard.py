"""Three-valued causal prerequisite enforcement."""

from __future__ import annotations

from collections.abc import Iterable

from are.simulation.distributed.knowledge import KnowledgeStore
from are.simulation.distributed.models import (
    ActorSpec,
    FactRequirement,
    GuardResult,
    GuardVerdict,
    RequirementVerdict,
)


def _scope_covers(
    actual: tuple[int, int] | str | None, required: tuple[int, int] | str | None
) -> bool:
    if required is None:
        return True
    if actual is None:
        return False
    if isinstance(actual, tuple) and isinstance(required, tuple):
        return actual[0] <= required[0] and actual[1] >= required[1]
    return actual == required


class CausalGuard:
    def evaluate(
        self,
        *,
        actor: ActorSpec,
        action: str,
        requirements: Iterable[FactRequirement],
        knowledge: KnowledgeStore,
        logical_time: float,
        evidence_ids: set[str],
        transport_closed: bool,
        transport_gap: bool = False,
        unresolved_fact_keys: frozenset[str] = frozenset(),
    ) -> GuardResult:
        if action not in actor.permitted_actions:
            return GuardResult(
                verdict=GuardVerdict.BLOCK,
                reasons=(f"actor {actor.actor_id} does not own action {action}",),
            )
        verdicts: dict[str, RequirementVerdict] = {}
        reasons: list[str] = []
        supporting: list[str] = []
        unknown = False
        false = False
        for requirement in requirements:
            if requirement.actor_id != actor.actor_id or requirement.action != action:
                continue
            if requirement.fact_key in unresolved_fact_keys:
                verdicts[requirement.requirement_id] = RequirementVerdict.FALSE
                reasons.append(f"fact {requirement.fact_key} is explicitly unresolved")
                false = True
                continue
            item = knowledge.latest(requirement.fact_key)
            if item is None:
                verdicts[requirement.requirement_id] = RequirementVerdict.UNKNOWN
                reasons.append(f"missing fact {requirement.fact_key}")
                unknown = True
                continue
            supporting.append(item.item_id)
            item_false = False
            operations = {
                "eq": lambda: item.value == requirement.expected_value,
                "ne": lambda: item.value != requirement.expected_value,
                "ge": lambda: item.value >= requirement.expected_value,
                "gt": lambda: item.value > requirement.expected_value,
                "le": lambda: item.value <= requirement.expected_value,
                "lt": lambda: item.value < requirement.expected_value,
                "in": lambda: item.value in requirement.expected_value,
            }
            try:
                value_matches = bool(operations[requirement.operator]())
            except (TypeError, ValueError):
                value_matches = False
            if not value_matches:
                reasons.append(
                    f"fact {requirement.fact_key} contradicts the requirement"
                )
                item_false = True
            if not _scope_covers(item.scope, requirement.scope):
                reasons.append(f"fact {requirement.fact_key} has insufficient scope")
                item_false = True
            if item.valid_until is not None and logical_time > item.valid_until:
                reasons.append(f"fact {requirement.fact_key} is stale")
                item_false = True
            if (
                requirement.max_age is not None
                and logical_time - item.observed_at > requirement.max_age
            ):
                reasons.append(f"fact {requirement.fact_key} exceeds max_age")
                item_false = True
            if requirement.require_evidence and (
                not item.evidence_ids or not set(item.evidence_ids) <= evidence_ids
            ):
                reasons.append(f"fact {requirement.fact_key} has unsupported evidence")
                item_false = True
            if item_false:
                verdicts[requirement.requirement_id] = RequirementVerdict.FALSE
                false = True
            else:
                verdicts[requirement.requirement_id] = RequirementVerdict.TRUE
        relevant = [
            requirement
            for requirement in requirements
            if requirement.actor_id == actor.actor_id and requirement.action == action
        ]
        if transport_gap and relevant:
            verdict = GuardVerdict.BLOCK
            reasons.append("closed delivery stream contains a missing message sequence")
        elif false:
            verdict = GuardVerdict.BLOCK
        elif unknown:
            deadline_passed = any(
                requirement.deadline is not None
                and logical_time >= requirement.deadline
                for requirement in relevant
            )
            verdict = (
                GuardVerdict.BLOCK
                if transport_closed or deadline_passed
                else GuardVerdict.DEFER
            )
        elif relevant and not transport_closed:
            verdict = GuardVerdict.DEFER
            reasons.append("incoming channel has not reached a delivery watermark")
        else:
            verdict = GuardVerdict.ALLOW
        return GuardResult(
            verdict=verdict,
            reasons=tuple(reasons),
            supporting_item_ids=tuple(dict.fromkeys(supporting)),
            requirement_verdicts=verdicts,
        )
