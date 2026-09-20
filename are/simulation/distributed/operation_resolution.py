"""Resolve a proposal to one authored operation occurrence.

The resolver is deliberately side-effect free.  Runtime guards, diagnostic
packets, evaluators and repair studies use this module so that they cannot
silently disagree about which authored occurrence a proposal represents.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Literal

from pydantic import Field

from are.simulation.distributed.models import FrozenModel


class OperationResolution(FrozenModel):
    schema_version: Literal["operation_resolution_v1"] = "operation_resolution_v1"
    status: Literal["unique", "ambiguous", "unmatched"]
    transition_id: str | None = None
    candidate_transition_ids: tuple[str, ...] = ()
    phase: str | None = None
    policy_id: str | None = None
    obligation_ids: tuple[str, ...] = ()
    prerequisite_ids: tuple[str, ...] = ()
    deadline: float | None = None
    deadline_sources: tuple[dict[str, Any], ...] = ()
    match_basis: dict[str, Any] = Field(default_factory=dict)


def _plain(item: Any) -> Any:
    """Convert both released models and lightweight test/spec views.

    Historical callers pass ``SimpleNamespace`` projections of the process
    specification.  Keeping those readable is part of the v1/v2 artifact
    compatibility contract; the resolver must not require callers to rebuild
    a Pydantic model merely to identify an operation occurrence.
    """

    if isinstance(item, dict):
        return {str(key): _plain(value) for key, value in item.items()}
    if isinstance(item, (list, tuple, set)):
        return [_plain(value) for value in item]
    if isinstance(item, Enum):
        return item.value
    if hasattr(item, "model_dump"):
        return _plain(item.model_dump(mode="json"))
    if hasattr(item, "__dict__"):
        return {
            str(key): _plain(value)
            for key, value in vars(item).items()
            if not str(key).startswith("_")
        }
    return item


def _value(item: Any) -> dict[str, Any]:
    value = _plain(item)
    if isinstance(value, dict):
        return value
    raise TypeError(f"unsupported specification value {type(item)!r}")


def _process_value(process: Any) -> dict[str, Any]:
    return _value(process)


def _scope(value: Any) -> tuple[int, int] | str | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    if isinstance(value, str) or value is None:
        return value
    return None


def proposal_scope(arguments: dict[str, Any]) -> tuple[int, int] | str | None:
    start = arguments.get("start_ridge", arguments.get("ridge_start"))
    end = arguments.get("end_ridge", arguments.get("ridge_end"))
    if start is not None and end is not None:
        return int(start), int(end)
    return _scope(arguments.get("scope"))


def _scope_iou(left: Any, right: Any) -> float:
    left, right = _scope(left), _scope(right)
    if left is None or right is None:
        return 1.0 if left == right else 0.0
    if isinstance(left, tuple) and isinstance(right, tuple):
        overlap = max(0, min(left[1], right[1]) - max(left[0], right[0]) + 1)
        union = max(left[1], right[1]) - min(left[0], right[0]) + 1
        return overlap / union if union else 0.0
    return 1.0 if left == right else 0.0


def _constraint_accepts(constraint: dict[str, Any], arguments: dict[str, Any]) -> bool:
    name = str(constraint.get("name"))
    if name not in arguments:
        return not bool(constraint.get("critical", True))
    expected, actual = constraint.get("expected"), arguments.get(name)
    tolerance = constraint.get("tolerance")
    if tolerance is not None:
        if actual is None or expected is None:
            return False
        try:
            return abs(float(actual) - float(expected)) <= float(tolerance)
        except (TypeError, ValueError):
            return False
    return actual == expected


def _phase_at(process: dict[str, Any], world_time: float | None) -> str | None:
    if world_time is None:
        return None
    matches = []
    for item in process.get("phase_windows", ()):
        # Historical engineering fixtures used a non-empty sentinel to mean
        # that phase windows were frozen, without embedding their full bounds.
        # It should suppress runtime phase hints but cannot itself resolve a
        # phase from time.
        if not isinstance(item, dict):
            continue
        raw_start = item.get("start_world_time")
        raw_end = item.get("end_world_time")
        start = float(raw_start) if raw_start is not None else float("-inf")
        end = float(raw_end) if raw_end is not None else float("inf")
        if start <= world_time < end:
            matches.append(str(item.get("phase")))
    return matches[0] if len(matches) == 1 else None


def _event_arguments(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {})
    return dict(
        event.get("args") or payload.get("args") or payload.get("arguments") or {}
    )


def _prior_consumed(
    process: dict[str, Any],
    transitions: list[dict[str, Any]],
    prior_events: Iterable[dict[str, Any]],
) -> set[str]:
    """Conservatively match completed native actions to authored occurrences."""

    consumed: set[str] = set()
    for event in prior_events:
        if event.get("kind") != "action" or event.get("status") not in {
            "ok",
            "accepted",
        }:
            continue
        actor, action = event.get("actor_id"), event.get("action")
        args = _event_arguments(event)
        scope = proposal_scope(args) or _scope(event.get("scope"))
        payload = event.get("payload", {})
        recorded_transition = event.get("transition_id") or payload.get("transition_id")
        if recorded_transition is not None and any(
            item.get("transition_id") == recorded_transition
            and item.get("actor_id") == actor
            and item.get("action") == action
            for item in transitions
        ):
            consumed.add(str(recorded_transition))
            continue
        event_phase = event.get("season_phase") or payload.get("season_phase")
        if event_phase is None:
            event_phase = _phase_at(process, event.get("world_time"))
        candidates: list[tuple[int, str]] = []
        for transition in transitions:
            transition_id = str(transition.get("transition_id"))
            if transition_id in consumed:
                continue
            if (
                transition.get("actor_id") != actor
                or transition.get("action") != action
            ):
                continue
            if event_phase is not None and transition.get("phase") != event_phase:
                continue
            required_scope = _scope(transition.get("scope"))
            threshold = float(transition.get("scope_iou_threshold", 1.0))
            if (
                required_scope is not None
                and _scope_iou(scope, required_scope) < threshold
            ):
                continue
            constraints = list(transition.get("arguments", ()))
            accepted = sum(_constraint_accepts(item, args) for item in constraints)
            candidates.append((accepted, transition_id))
        if len(candidates) == 1:
            consumed.add(candidates[0][1])
        elif candidates:
            # Prefer the most argument-conforming occurrence, but do not require
            # every operational parameter to equal the authored reference.  A
            # native action with a wrong rate or spacing still consumed the
            # occurrence it targeted; argument correctness is evaluated later.
            best = max(score for score, _ in candidates)
            matches = [item for score, item in candidates if score == best]
            consumed.add(matches[0])
    return consumed


def _direct_transition_dependencies(
    process: dict[str, Any], transition_id: str, transition_ids: set[str]
) -> set[str]:
    """Find nearest upstream authored transitions through occurrence-net places."""

    incoming: dict[str, set[str]] = {}
    for arc in process.get("occurrence_net", {}).get("arcs", ()):
        incoming.setdefault(str(arc.get("target")), set()).add(str(arc.get("source")))
    dependencies: set[str] = set()
    frontier = list(incoming.get(transition_id, ()))
    visited: set[str] = set()
    while frontier:
        node = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        if node in transition_ids:
            dependencies.add(node)
            continue
        frontier.extend(incoming.get(node, ()))
    return dependencies


def _policy_matches(
    policy: dict[str, Any], actor: str, action: str, phase: str | None
) -> bool:
    return (
        policy.get("actor_id") == actor
        and action in set(policy.get("action_patterns", ()))
        and (phase is None or phase in set(policy.get("phases", ())))
    )


def resolve_operation_occurrence(
    process: Any,
    *,
    actor_id: str,
    action: str,
    arguments: dict[str, Any] | None = None,
    scope: tuple[int, int] | str | None = None,
    world_time: float | None = None,
    prior_events: Iterable[dict[str, Any]] = (),
) -> OperationResolution:
    """Bind a proposal to one occurrence or return an explicit unresolved state."""

    raw = _process_value(process)
    transitions = list(raw.get("occurrence_net", {}).get("transitions", ()))
    arguments = dict(arguments or {})
    scope = _scope(scope) or proposal_scope(arguments)
    phase = _phase_at(raw, world_time)
    consumed = _prior_consumed(raw, transitions, prior_events)
    actor_action = [
        item
        for item in transitions
        if item.get("actor_id") == actor_id and item.get("action") == action
    ]
    if not actor_action:
        return OperationResolution(
            status="unmatched",
            phase=phase,
            match_basis={
                "reason": "no_authored_actor_action",
                "actor_id": actor_id,
                "action": action,
            },
        )

    transition_ids = {str(item.get("transition_id")) for item in transitions}
    scored: list[tuple[tuple[int, float, int, int], dict[str, Any]]] = []
    eligible: list[tuple[tuple[int, float, int, int], dict[str, Any]]] = []
    dependency_evidence: dict[str, dict[str, Any]] = {}
    conformance_evidence: dict[str, dict[str, Any]] = {}
    for transition in actor_action:
        constraints = list(transition.get("arguments", ()))
        accepted = sum(_constraint_accepts(item, arguments) for item in constraints)
        critical = [item for item in constraints if item.get("critical", True)]
        critical_accepted = sum(
            _constraint_accepts(item, arguments) for item in critical
        )
        all_critical = all(_constraint_accepts(item, arguments) for item in critical)
        transition_scope = _scope(transition.get("scope"))
        iou = _scope_iou(scope, transition_scope)
        scope_ok = transition_scope is None or iou >= float(
            transition.get("scope_iou_threshold", 1.0)
        )
        phase_ok = phase is None or transition.get("phase") == phase
        transition_id = str(transition.get("transition_id"))
        unused = transition_id not in consumed
        dependencies = _direct_transition_dependencies(
            raw, transition_id, transition_ids
        )
        satisfied_dependencies = dependencies & consumed
        dependency_ratio = (
            len(satisfied_dependencies) / len(dependencies) if dependencies else 1.0
        )
        dependency_evidence[transition_id] = {
            "direct_predecessor_transition_ids": tuple(sorted(dependencies)),
            "satisfied_predecessor_transition_ids": tuple(
                sorted(satisfied_dependencies)
            ),
        }
        conformance_evidence[transition_id] = {
            "phase_conforming": phase_ok,
            "scope_conforming": scope_ok,
            "argument_conforming": all_critical,
            "accepted_argument_constraints": accepted,
            "total_argument_constraints": len(constraints),
            "accepted_critical_argument_constraints": critical_accepted,
            "total_critical_argument_constraints": len(critical),
            "scope_iou": iou,
        }
        score = (
            int(unused),
            dependency_ratio,
            critical_accepted,
            accepted,
        )
        scored.append((score, transition))
        if phase_ok and scope_ok:
            eligible.append((score, transition))

    # Phase and spatial scope identify which authored operation occurrence was
    # targeted.  Other arguments describe whether that occurrence was executed
    # correctly.  Requiring every rate/depth/spacing argument to match here
    # would turn a diagnosable bad action into an unresolved action and, in
    # enforce mode, could block a native-valid operation before its ordinary
    # acceptance checks run.
    if not eligible:
        best_score = max(score for score, _ in scored)
        nearest = [item for score, item in scored if score == best_score]
        return OperationResolution(
            status="unmatched",
            candidate_transition_ids=tuple(
                str(item.get("transition_id")) for item in nearest
            ),
            phase=phase,
            match_basis={
                "reason": "no_phase_scope_conforming_occurrence",
                "score": best_score,
                "candidate_conformance": {
                    str(item.get("transition_id")): conformance_evidence[
                        str(item.get("transition_id"))
                    ]
                    for item in nearest
                },
            },
        )
    best_score = max(score for score, _ in eligible)
    best = [item for score, item in eligible if score == best_score]
    if len(best) != 1:
        return OperationResolution(
            status="ambiguous",
            candidate_transition_ids=tuple(
                str(item.get("transition_id")) for item in best
            ),
            phase=phase,
            match_basis={"reason": "non_unique_best_occurrence", "score": best_score},
        )

    transition = best[0]
    transition_id = str(transition.get("transition_id"))
    policies = [
        item
        for item in raw.get("information_policies", ())
        if _policy_matches(item, actor_id, action, str(transition.get("phase")))
    ]
    policy = policies[0] if len(policies) == 1 else None
    obligations = [
        item
        for item in raw.get("causal_obligations", ())
        if transition_id in set(item.get("target_transition_ids", ()))
    ]
    prerequisites = [
        prerequisite
        for obligation in obligations
        for prerequisite in obligation.get("prerequisites", ())
    ]
    deadline_sources: list[dict[str, Any]] = []
    for source, identifier, value in (
        ("transition", transition_id, transition.get("window_end")),
        (
            "information_policy",
            (policy or {}).get("policy_id"),
            (policy or {}).get("deadline_world_time"),
        ),
    ):
        if value is not None:
            deadline_sources.append(
                {"source": source, "id": identifier, "deadline": float(value)}
            )
    acceptance = next(
        (
            item
            for item in raw.get("acceptance", ())
            if item.get("transition_id") == transition_id
        ),
        None,
    )
    if acceptance and acceptance.get("window_end") is not None:
        deadline_sources.append(
            {
                "source": "acceptance",
                "id": transition_id,
                "deadline": float(acceptance["window_end"]),
            }
        )
    scenario_horizon = raw.get("metadata", {}).get("scenario_horizon")
    if scenario_horizon is None:
        scenario_horizon = (
            raw.get("occurrence_net", {}).get("metadata", {}).get("scenario_horizon")
        )
    if scenario_horizon is not None:
        deadline_sources.append(
            {
                "source": "scenario_horizon",
                "id": raw.get("scenario_id"),
                "deadline": float(scenario_horizon),
            }
        )
    deadline = min((item["deadline"] for item in deadline_sources), default=None)
    return OperationResolution(
        status="unique",
        transition_id=transition_id,
        candidate_transition_ids=(transition_id,),
        phase=str(transition.get("phase"))
        if transition.get("phase") is not None
        else phase,
        policy_id=str(policy.get("policy_id")) if policy else None,
        obligation_ids=tuple(str(item.get("obligation_id")) for item in obligations),
        prerequisite_ids=tuple(
            str(item.get("prerequisite_id")) for item in prerequisites
        ),
        deadline=deadline,
        deadline_sources=tuple(deadline_sources),
        match_basis={
            "score": best_score,
            "consumed_occurrence_ids": tuple(sorted(consumed)),
            **dependency_evidence[transition_id],
            "arguments": arguments,
            "scope": scope,
            "world_time": world_time,
            **conformance_evidence[transition_id],
        },
    )


def resolved_components(
    process: Any, resolution: OperationResolution
) -> dict[str, Any]:
    """Return the exact transition, policy, obligations and prerequisites."""

    if resolution.status != "unique" or not resolution.transition_id:
        return {
            "transition": None,
            "policy": None,
            "obligations": (),
            "prerequisites": (),
        }
    raw = _process_value(process)
    transition = next(
        item
        for item in raw.get("occurrence_net", {}).get("transitions", ())
        if item.get("transition_id") == resolution.transition_id
    )
    policy = next(
        (
            item
            for item in raw.get("information_policies", ())
            if item.get("policy_id") == resolution.policy_id
        ),
        None,
    )
    obligations = tuple(
        item
        for item in raw.get("causal_obligations", ())
        if item.get("obligation_id") in set(resolution.obligation_ids)
    )
    prerequisites = tuple(
        {**prerequisite, "obligation_id": obligation.get("obligation_id")}
        for obligation in obligations
        for prerequisite in obligation.get("prerequisites", ())
        if prerequisite.get("prerequisite_id") in set(resolution.prerequisite_ids)
    )
    return {
        "transition": transition,
        "policy": policy,
        "obligations": obligations,
        "prerequisites": prerequisites,
    }


__all__ = [
    "OperationResolution",
    "proposal_scope",
    "resolve_operation_occurrence",
    "resolved_components",
]
