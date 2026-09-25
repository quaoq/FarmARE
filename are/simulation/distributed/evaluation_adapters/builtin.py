"""Inspectable same-packet comparison methods.

Only methods implemented by this module are registered as available results.
Historical names whose implementations overstated the executed method remain
typed, but return ``unavailable`` rather than a plausible mislabeled score.
"""

from __future__ import annotations

from typing import Any, Iterable

from are.simulation.distributed.knowledge import normalize_scope, scope_satisfies
from are.simulation.distributed.models import stable_digest

from .contracts import (
    ComparatorResult,
    DiagnosticPacket,
    DiagnosticWitness,
    RepairCandidate,
    RepairPrimitive,
)
from .registry import ComparatorAdapter, register_adapter
from .subprocess_adapter import external_adapter


def _metrics(packet: DiagnosticPacket) -> dict[str, Any]:
    return (
        packet.prefix_metrics if packet.prefix_decision_id else packet.existing_metrics
    )


def _score_result(
    packet: DiagnosticPacket, method: str, scores: dict[str, float | None]
) -> ComparatorResult:
    return ComparatorResult(
        method=method,
        capability="score",
        status="ok",
        packet_digest=packet.packet_digest,
        scores=scores,
        source_revision="farmare_local_adapter_v2",
    )


def _unavailable(
    packet: DiagnosticPacket, method: str, capability: str, reason: str
) -> ComparatorResult:
    return ComparatorResult(
        method=method,
        capability=capability,  # type: ignore[arg-type]
        status="unavailable",
        packet_digest=packet.packet_digest,
        error=reason,
        source_revision="retired_mislabeled_adapter_v2",
    )


def _core(packet: DiagnosticPacket) -> ComparatorResult:
    metrics = _metrics(packet)
    merged = metrics.get("merged_pc_ktc", {})
    return _score_result(
        packet,
        "core",
        {
            "combined": merged.get("combined"),
            "path_correctness": metrics.get("core_path_correctness"),
        },
    )


def _dcore_event_timing(packet: DiagnosticPacket) -> ComparatorResult:
    metrics = _metrics(packet)
    return _score_result(
        packet,
        "dcore_event_timing_components",
        {
            "event_fidelity": metrics.get("event_fidelity"),
            "timing_fidelity": metrics.get("timing_fidelity"),
        },
    )


def _core_information_joint_report(packet: DiagnosticPacket) -> ComparatorResult:
    metrics = _metrics(packet)
    merged = metrics.get("merged_pc_ktc", {})
    policy = metrics.get("information_policy_conformance", {})
    return _score_result(
        packet,
        "core_information_joint_report",
        {
            "path_correctness": metrics.get("core_path_correctness"),
            "combined": merged.get("combined"),
            "information_conformance": policy.get("conformance"),
        },
    )


def _scope(value: Any) -> tuple[int, int] | str | None:
    return normalize_scope(value)


def _scope_covers(
    actual: tuple[int, int] | str | None,
    required: tuple[int, int] | str | None,
) -> bool:
    return scope_satisfies(actual, required, "covers")


def _scope_matches(
    actual: tuple[int, int] | str | None,
    required: tuple[int, int] | str | None,
    guard: dict[str, Any],
) -> bool:
    return scope_satisfies(actual, required, str(guard.get("scope_match", "covers")))


def _event_scope(event: dict[str, Any]) -> tuple[int, int] | str | None:
    payload = event.get("payload", {})
    scope = event.get("scope") or payload.get("scope")
    if scope is not None:
        return _scope(scope)
    args = event.get("args") or payload.get("args") or {}
    start = args.get("start_ridge", args.get("ridge_start"))
    end = args.get("end_ridge", args.get("ridge_end"))
    if start is not None and end is not None:
        return (int(start), int(end))
    return None


def _scoped_milestones(packet: DiagnosticPacket) -> ComparatorResult:
    """Occurrence-aware agricultural completion adaptation."""

    events = [
        item
        for item in packet.events
        if item.get("kind") == "action" and item.get("status") == "ok"
    ]
    used: set[int] = set()
    required = [
        item
        for item in packet.reference_transitions
        if item.get("required")
        and item.get("actor_id") != "world"
        and item.get("kind") not in {"send", "receive"}
    ]
    completed = 0
    for transition in required:
        required_scope = _scope(
            transition.get("scope") or transition.get("metadata", {}).get("scope")
        )
        match = next(
            (
                index
                for index, event in enumerate(events)
                if index not in used
                and event.get("actor_id") == transition.get("actor_id")
                and event.get("action") == transition.get("action")
                and _scope_covers(_event_scope(event), required_scope)
            ),
            None,
        )
        if match is not None:
            used.add(match)
            completed += 1
    outcome = packet.outcome or {}
    return _score_result(
        packet,
        "scoped_agricultural_milestones_adaptation",
        {
            "milestone_completion": completed / len(required) if required else None,
            "harvest_complete": float(bool(outcome.get("harvest_complete")))
            if outcome
            else None,
            "storage_complete": float(bool(outcome.get("storage_complete")))
            if outcome
            else None,
        },
    )


def _fact_map(packet: DiagnosticPacket) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("version_id")): item
        for item in packet.fact_versions
        if item.get("version_id") is not None
    }


def _resolve_ids(values: Iterable[str], facts: dict[str, dict[str, Any]]) -> set[str]:
    output: set[str] = set()
    for value in values:
        if value in facts:
            output.add(value)
            continue
        suffix = str(value).rsplit(":", 1)[-1]
        matches = [key for key in facts if key == suffix or key.endswith(f":{suffix}")]
        if len(matches) == 1:
            output.add(matches[0])
    return output


def _compare(value: Any, expected: Any, operator: str | None) -> bool:
    op = str(operator or "eq").lower().split(".")[-1]
    try:
        return {
            "eq": lambda: value == expected,
            "ne": lambda: value != expected,
            "ge": lambda: value >= expected,
            "gt": lambda: value > expected,
            "le": lambda: value <= expected,
            "lt": lambda: value < expected,
            "in": lambda: value in expected,
        }.get(op, lambda: False)()
    except (TypeError, ValueError):
        return False


def _decision_world_time(packet: DiagnosticPacket, decision: dict[str, Any]) -> float:
    event = next(
        (
            item
            for item in packet.events
            if item.get("event_id") == decision.get("decision_id")
        ),
        None,
    )
    fallback = (
        packet.cutoff_world_time
        if packet.cutoff_world_time is not None
        else decision.get("logical_time", 0.0)
    )
    return float((event or {}).get("world_time", fallback))


def _applicable_guards(
    packet: DiagnosticPacket, decision: dict[str, Any]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if packet.operation_resolution is not None:
        if packet.operation_resolution.get("status") != "unique":
            return []
        transition = packet.target_transition
        if transition is None:
            return []
        prerequisites = {
            str(item.get("guard_id")): item for item in packet.target_prerequisites
        }
        obligations = {
            prerequisite_id: str(item.get("obligation_id"))
            for item in packet.target_prerequisites
            if (prerequisite_id := str(item.get("guard_id")))
        }
        output = []
        for guard in transition.get("guards", ()):
            if guard.get("source") == "world":
                continue
            guard_id = str(guard.get("guard_id"))
            prerequisite = prerequisites.get(guard_id)
            if prerequisite is None:
                continue
            output.append(
                (
                    transition,
                    {
                        **guard,
                        **prerequisite,
                        "diagnostic_prerequisite_id": prerequisite.get(
                            "prerequisite_id", guard_id
                        ),
                        "diagnostic_obligation_id": obligations.get(guard_id),
                    },
                )
            )
        return output
    intent = decision.get("proposed_intent", {})
    action = intent.get("action")
    actor = decision.get("actor_id")
    return [
        (transition, guard)
        for transition in packet.reference_transitions
        if transition.get("action") == action
        and transition.get("actor_id") in {None, actor}
        for guard in transition.get("guards", ())
        if guard.get("source") != "world"
    ]


def _target_decisions(packet: DiagnosticPacket) -> tuple[dict[str, Any], ...]:
    if packet.target_decision is not None:
        return (packet.target_decision,)
    return packet.decisions


def _evidence_state(
    packet: DiagnosticPacket,
    decision: dict[str, Any],
    guard: dict[str, Any],
) -> dict[str, Any]:
    facts = _fact_map(packet)
    acquired = _resolve_ids(
        decision.get("knowledge_snapshot", {}).get("item_ids", ()), facts
    )
    prompted = _resolve_ids(decision.get("prompt_item_ids", ()), facts)
    fact_key = guard.get("fact_key")
    required_scope = _scope(guard.get("scope"))
    same_key = [
        facts[item] for item in acquired if facts[item].get("fact_key") == fact_key
    ]
    scoped = [
        item
        for item in same_key
        if _scope_matches(_scope(item.get("scope")), required_scope, guard)
    ]
    at = _decision_world_time(packet, decision)
    selected = max(
        scoped,
        key=lambda item: (
            float(item.get("world_time", 0.0)),
            str(item.get("version_id")),
        ),
        default=None,
    )
    evaluator_scoped = [
        item
        for item in facts.values()
        if item.get("fact_key") == fact_key
        and _scope_matches(_scope(item.get("scope")), required_scope, guard)
        and float(item.get("world_time", 0.0)) <= at
    ]
    return {
        "facts": facts,
        "acquired": acquired,
        "prompted": prompted,
        "same_key": same_key,
        "selected": selected,
        "evaluator_scoped": evaluator_scoped,
        "decision_time": at,
        "required_scope": required_scope,
    }


def _transport_mechanism(
    packet: DiagnosticPacket,
    decision: dict[str, Any],
    state: dict[str, Any],
) -> tuple[str, tuple[str, ...], dict[str, Any] | None]:
    actor = decision.get("actor_id")
    observed = [
        item for item in state["evaluator_scoped"] if not item.get("authoritative")
    ]
    if not observed:
        return "missing_observation", (), None
    source = max(
        observed,
        key=lambda item: (
            float(item.get("world_time", 0.0)),
            str(item.get("version_id")),
        ),
    )
    observed_ids = {str(item.get("version_id")) for item in observed}
    sends = [
        item
        for item in packet.messages
        if item.get("kind") == "message_send"
        and observed_ids & set(item.get("payload", {}).get("fact_versions", ()))
    ]
    receives = [
        item
        for item in packet.messages
        if item.get("kind") == "message_receive"
        and (
            item.get("actor_id") == actor
            or item.get("payload", {}).get("recipient_actor_id") == actor
        )
        and any(item.get("message_id") == send.get("message_id") for send in sends)
    ]
    supporting = tuple(
        str(item.get("source_event_id"))
        for item in observed
        if item.get("source_event_id")
    )
    return ("context_omission" if receives else "failed_delivery"), supporting, source


def _diagnose_guard(
    packet: DiagnosticPacket,
    decision: dict[str, Any],
    transition: dict[str, Any],
    guard: dict[str, Any],
) -> DiagnosticWitness | None:
    state = _evidence_state(packet, decision, guard)
    selected = state["selected"]
    source_record: dict[str, Any] | None = selected
    supporting: tuple[str, ...] = ()
    if selected is None:
        if state["same_key"]:
            mechanism, reason = "incorrect_scope", "wrong_scope"
            source_record = max(
                state["same_key"],
                key=lambda item: (
                    float(item.get("world_time", 0.0)),
                    str(item.get("version_id")),
                ),
            )
        else:
            mechanism, supporting, source_record = _transport_mechanism(
                packet, decision, state
            )
            reason = "not_available_to_actor"
    else:
        version_id = str(selected.get("version_id"))
        valid_until = selected.get("valid_until")
        max_age = guard.get("max_age")
        if valid_until is not None and state["decision_time"] > float(valid_until):
            mechanism, reason = "expired_evidence", "validity_expired"
        elif max_age is not None and state["decision_time"] - float(
            selected.get("world_time", 0.0)
        ) > float(max_age):
            mechanism, reason = "expired_evidence", "max_age_exceeded"
        elif version_id not in state["prompted"]:
            mechanism, reason = "context_omission", "acquired_but_not_prompted"
        elif not _compare(
            selected.get("value"), guard.get("expected"), guard.get("operator")
        ):
            mechanism, reason = (
                "failure_to_use_available_evidence",
                "prompted_predicate_false",
            )
        else:
            return None
        supporting = tuple(
            item for item in (selected.get("source_event_id"),) if item is not None
        )
    fact_ids = (
        (str(source_record.get("version_id")),) if source_record is not None else ()
    )
    actor = str(decision.get("actor_id") or "unknown")
    prerequisite_id = str(
        guard.get("diagnostic_prerequisite_id")
        or guard.get("prerequisite_id")
        or guard.get("guard_id")
        or "unknown"
    )
    obligation_id = str(
        guard.get("diagnostic_obligation_id")
        or guard.get("obligation_id")
        or f"{transition.get('transition_id', transition.get('action'))}:{prerequisite_id}"
    )
    witness_id = stable_digest(
        [packet.packet_digest, decision.get("decision_id"), prerequisite_id, mechanism]
    )[:24]
    return DiagnosticWitness(
        witness_id=witness_id,
        decision_id=str(decision.get("decision_id") or "unknown"),
        obligation_id=obligation_id,
        prerequisite_id=prerequisite_id,
        actor_id=actor,
        mechanism=mechanism,  # type: ignore[arg-type]
        fact_key=guard.get("fact_key"),
        fact_version_ids=fact_ids,
        supporting_event_ids=supporting,
        root_support_group=obligation_id,
        determination="supported",
        target_scope=state["required_scope"],
        decision_time=state["decision_time"],
        deadline=(
            packet.operation_resolution.get("deadline")
            if packet.operation_resolution is not None
            else transition.get("window_end")
        ),
        prerequisite=dict(guard),
        guard_reason=reason,
        evidence_available_to_actor=selected is not None,
        evidence_delivered=(selected is not None or mechanism != "failed_delivery"),
        evidence_in_prompt=(
            selected is not None
            and str(selected.get("version_id")) in state["prompted"]
        ),
        source_version_id=(
            str(source_record.get("version_id")) if source_record else None
        ),
        evidence_holder_ids=tuple(
            str(item) for item in (source_record or {}).get("visible_to", ())
        ),
        intended_recipient_actor_id=actor,
        acquired_at=(
            float(source_record.get("world_time", 0.0)) if source_record else None
        ),
        valid_until=(
            float(source_record["valid_until"])
            if source_record and source_record.get("valid_until") is not None
            else None
        ),
        source_event_id=(
            str(source_record.get("source_event_id"))
            if source_record and source_record.get("source_event_id") is not None
            else None
        ),
        deadline_sources=tuple(
            (packet.operation_resolution or {}).get("deadline_sources", ())
        ),
        operation_resolution=dict(packet.operation_resolution or {}),
    )


def _unresolved_resolution_witness(
    packet: DiagnosticPacket, decision: dict[str, Any]
) -> DiagnosticWitness:
    resolution = dict(packet.operation_resolution or {})
    decision_id = str(decision.get("decision_id") or "unknown")
    actor_id = str(decision.get("actor_id") or "unknown")
    return DiagnosticWitness(
        witness_id=stable_digest(
            [packet.packet_digest, decision_id, "operation_resolution", resolution]
        )[:24],
        decision_id=decision_id,
        obligation_id="unresolved_operation_occurrence",
        prerequisite_id="unresolved_operation_occurrence",
        actor_id=actor_id,
        mechanism="unresolved_evidence",
        root_support_group="unresolved_operation_occurrence",
        determination="unresolved",
        explanation=str(resolution.get("match_basis", {}).get("reason", "unresolved")),
        decision_time=_decision_world_time(packet, decision),
        deadline=resolution.get("deadline"),
        deadline_sources=tuple(resolution.get("deadline_sources", ())),
        operation_resolution=resolution,
    )


def _dcore(packet: DiagnosticPacket) -> ComparatorResult:
    if (
        packet.target_decision is not None
        and packet.operation_resolution is not None
        and packet.operation_resolution.get("status") != "unique"
    ):
        witnesses = (_unresolved_resolution_witness(packet, packet.target_decision),)
    else:
        witnesses = tuple(
            witness
            for decision in _target_decisions(packet)
            for transition, guard in _applicable_guards(packet, decision)
            if (witness := _diagnose_guard(packet, decision, transition, guard))
            is not None
        )
    return ComparatorResult(
        method="dcore",
        capability="diagnosis",
        status="ok",
        packet_digest=packet.packet_digest,
        witnesses=witnesses,
        source_revision="dcore_prefix_diagnosis_v2",
    )


def _full_information_checker(packet: DiagnosticPacket) -> ComparatorResult:
    """Independent predicate checker over the packet, not D-CORE labels."""

    witnesses: list[DiagnosticWitness] = []
    facts = _fact_map(packet)
    for decision in _target_decisions(packet):
        actor = str(decision.get("actor_id") or "unknown")
        acquired = _resolve_ids(
            decision.get("knowledge_snapshot", {}).get("item_ids", ()), facts
        )
        prompted = _resolve_ids(decision.get("prompt_item_ids", ()), facts)
        at = _decision_world_time(packet, decision)
        for transition, guard in _applicable_guards(packet, decision):
            key = guard.get("fact_key")
            scope = _scope(guard.get("scope"))
            candidates = [
                facts[item]
                for item in acquired
                if facts[item].get("fact_key") == key
                and _scope_matches(_scope(facts[item].get("scope")), scope, guard)
            ]
            selected = max(
                candidates,
                key=lambda item: float(item.get("world_time", 0.0)),
                default=None,
            )
            source_record = selected
            violation = None
            if selected is None:
                same_key = [
                    facts[item]
                    for item in acquired
                    if facts[item].get("fact_key") == key
                ]
                if same_key:
                    violation = "incorrect_scope"
                else:
                    prefix_observations = [
                        item
                        for item in facts.values()
                        if item.get("fact_key") == key
                        and _scope_matches(_scope(item.get("scope")), scope, guard)
                        and not item.get("authoritative")
                        and float(item.get("world_time", 0.0)) <= at
                    ]
                    violation = (
                        "failed_delivery"
                        if prefix_observations
                        else "missing_observation"
                    )
                    source_record = max(
                        prefix_observations,
                        key=lambda item: float(item.get("world_time", 0.0)),
                        default=None,
                    )
            elif selected.get("valid_until") is not None and at > float(
                selected["valid_until"]
            ):
                violation = "expired_evidence"
            elif guard.get("max_age") is not None and at - float(
                selected.get("world_time", 0.0)
            ) > float(guard["max_age"]):
                violation = "expired_evidence"
            elif str(selected.get("version_id")) not in prompted:
                violation = "context_omission"
            elif not _compare(
                selected.get("value"), guard.get("expected"), guard.get("operator")
            ):
                violation = "failure_to_use_available_evidence"
            if violation is None:
                continue
            prerequisite_id = str(guard.get("guard_id") or "unknown")
            obligation_id = (
                f"checker:{transition.get('transition_id', transition.get('action'))}"
            )
            witnesses.append(
                DiagnosticWitness(
                    witness_id=stable_digest(
                        [
                            "checker",
                            packet.packet_digest,
                            decision.get("decision_id"),
                            prerequisite_id,
                        ]
                    )[:24],
                    decision_id=str(decision.get("decision_id") or "unknown"),
                    obligation_id=obligation_id,
                    prerequisite_id=prerequisite_id,
                    actor_id=actor,
                    mechanism=violation,  # type: ignore[arg-type]
                    fact_key=key,
                    fact_version_ids=(
                        (str(source_record.get("version_id")),) if source_record else ()
                    ),
                    supporting_event_ids=(
                        (str(source_record.get("source_event_id")),)
                        if source_record
                        else ()
                    ),
                    root_support_group=obligation_id,
                    determination="supported",
                    target_scope=scope,
                    decision_time=at,
                    prerequisite=dict(guard),
                    evidence_available_to_actor=selected is not None,
                    evidence_in_prompt=(
                        selected is not None
                        and str(selected.get("version_id")) in prompted
                    ),
                    source_version_id=(
                        str(source_record.get("version_id")) if source_record else None
                    ),
                    evidence_holder_ids=tuple(
                        str(item)
                        for item in (source_record or {}).get("visible_to", ())
                    ),
                    intended_recipient_actor_id=actor,
                    acquired_at=(
                        float(source_record.get("world_time", 0.0))
                        if source_record
                        else None
                    ),
                    valid_until=(
                        float(source_record["valid_until"])
                        if source_record
                        and source_record.get("valid_until") is not None
                        else None
                    ),
                    source_event_id=(
                        str(source_record.get("source_event_id"))
                        if source_record
                        and source_record.get("source_event_id") is not None
                        else None
                    ),
                    deadline=(packet.operation_resolution or {}).get("deadline"),
                    deadline_sources=tuple(
                        (packet.operation_resolution or {}).get("deadline_sources", ())
                    ),
                    operation_resolution=dict(packet.operation_resolution or {}),
                )
            )
    return ComparatorResult(
        method="full_information_checker",
        capability="diagnosis",
        status="ok",
        packet_digest=packet.packet_digest,
        witnesses=tuple(witnesses),
        source_revision="independent_predicate_checker_v2",
    )


def _generic_reconsideration(packet: DiagnosticPacket) -> ComparatorResult:
    repairs = []
    for decision in _target_decisions(packet):
        candidate_id = stable_digest(
            [
                "generic_reconsideration",
                packet.packet_digest,
                decision.get("decision_id"),
            ]
        )[:24]
        repairs.append(
            RepairCandidate(
                candidate_id=candidate_id,
                witness_id=f"checkpoint:{decision.get('decision_id', 'unknown')}",
                primitives=(
                    RepairPrimitive(
                        primitive="request_reconsideration",
                        actor_id=str(decision.get("actor_id") or "unknown"),
                    ),
                ),
                feasibility="feasible",
                feasibility_evidence={"trigger": "assigned_checkpoint"},
                priority_key=(
                    _decision_world_time(packet, decision),
                    1,
                    0.0,
                    candidate_id,
                ),
            )
        )
    return ComparatorResult(
        method="generic_reconsideration",
        capability="repair",
        status="ok",
        packet_digest=packet.packet_digest,
        repairs=tuple(repairs),
        source_revision="generic_checkpoint_reconsideration_v2",
    )


def _fixed_protocol_repairs(packet: DiagnosticPacket) -> ComparatorResult:
    """Frozen condition-action rules independent of D-CORE witnesses."""

    repairs: list[RepairCandidate] = []
    facts = _fact_map(packet)
    for decision in _target_decisions(packet):
        actor = str(decision.get("actor_id") or "unknown")
        acquired = _resolve_ids(
            decision.get("knowledge_snapshot", {}).get("item_ids", ()), facts
        )
        prompted = _resolve_ids(decision.get("prompt_item_ids", ()), facts)
        at = _decision_world_time(packet, decision)
        for transition, guard in _applicable_guards(packet, decision):
            key = guard.get("fact_key")
            scope = _scope(guard.get("scope"))
            local_key = [
                facts[item] for item in acquired if facts[item].get("fact_key") == key
            ]
            local_scoped = [
                item
                for item in local_key
                if _scope_matches(_scope(item.get("scope")), scope, guard)
            ]
            selected = max(
                local_scoped,
                key=lambda item: float(item.get("world_time", 0.0)),
                default=None,
            )
            global_scoped = [
                item
                for item in facts.values()
                if item.get("fact_key") == key
                and _scope_matches(_scope(item.get("scope")), scope, guard)
                and not item.get("authoritative")
                and float(item.get("world_time", 0.0)) <= at
            ]
            if selected is None:
                rule = (
                    "wrong_scope"
                    if local_key
                    else "held_elsewhere"
                    if global_scoped
                    else "never_observed"
                )
            elif selected.get("valid_until") is not None and at > float(
                selected["valid_until"]
            ):
                rule = "stale"
            elif guard.get("max_age") is not None and at - float(
                selected.get("world_time", 0.0)
            ) > float(guard["max_age"]):
                rule = "stale"
            elif str(selected.get("version_id")) not in prompted:
                rule = "omitted"
            elif not _compare(
                selected.get("value"), guard.get("expected"), guard.get("operator")
            ):
                rule = "predicate_conflict"
            else:
                continue
            primitive_name = {
                "never_observed": "acquire_observation",
                "held_elsewhere": "redeliver_evidence",
                "wrong_scope": "acquire_observation",
                "stale": "refresh_observation",
                "omitted": "restore_context",
                "predicate_conflict": "request_reconsideration",
            }[rule]
            source = selected or max(
                global_scoped or local_key,
                key=lambda item: float(item.get("world_time", 0.0)),
                default=None,
            )
            fact_version_id = str(source.get("version_id")) if source else None
            prerequisite_id = str(
                guard.get("diagnostic_prerequisite_id")
                or guard.get("guard_id")
                or "unknown"
            )
            candidate_id = stable_digest(
                [
                    "fixed_protocol_v3",
                    decision.get("decision_id"),
                    prerequisite_id,
                    rule,
                ]
            )[:24]
            repairs.append(
                RepairCandidate(
                    candidate_id=candidate_id,
                    witness_id=(
                        f"fixed:{decision.get('decision_id')}:{prerequisite_id}"
                    ),
                    primitives=(
                        RepairPrimitive(
                            primitive=primitive_name,  # type: ignore[arg-type]
                            actor_id=actor,
                            recipient_actor_id=(
                                actor
                                if primitive_name == "redeliver_evidence"
                                else None
                            ),
                            fact_key=key,
                            fact_version_id=fact_version_id,
                            scope=scope,
                        ),
                    ),
                    required_evidence_ids=(
                        (fact_version_id,) if fact_version_id else ()
                    ),
                    feasibility="unresolved",
                    rejection_reasons=("native_resolution_required",),
                    feasibility_evidence={
                        "trigger": "frozen_condition_action_table_v1",
                        "rule": f"{rule}->{primitive_name}",
                    },
                    priority_key=(at, 1, 0.0, candidate_id),
                )
            )
    return ComparatorResult(
        method="fixed_protocol_rules",
        capability="repair",
        status="ok",
        packet_digest=packet.packet_digest,
        repairs=tuple(repairs),
        source_revision="independent_fixed_condition_action_rules_v3",
    )


def _action_trace_checker(packet: DiagnosticPacket) -> ComparatorResult:
    """Check proposal conformance without inspecting actor evidence paths."""

    repairs: list[RepairCandidate] = []
    resolution = packet.operation_resolution or {}
    decision = packet.target_decision
    if decision is not None and resolution.get("status") != "unique":
        candidate_id = stable_digest(
            ["action_trace_checker", packet.packet_digest, decision.get("decision_id")]
        )[:24]
        repairs.append(
            RepairCandidate(
                candidate_id=candidate_id,
                witness_id=f"action_trace:{decision.get('decision_id')}",
                primitives=(
                    RepairPrimitive(
                        primitive="request_reconsideration",
                        actor_id=str(decision.get("actor_id") or "unknown"),
                    ),
                ),
                feasibility="feasible",
                feasibility_evidence={
                    "trigger": "nonconforming_or_unresolved_action_occurrence",
                    "actor_specific_evidence_used": False,
                },
                priority_key=(
                    _decision_world_time(packet, decision),
                    1,
                    0.0,
                    candidate_id,
                ),
            )
        )
    return ComparatorResult(
        method="action_trace_checker",
        capability="repair",
        status="ok",
        packet_digest=packet.packet_digest,
        repairs=tuple(repairs),
        source_revision="action_trace_conformance_v1",
        adapter_metadata={"actor_specific_evidence_mechanism": False},
    )


def _ablation_packet(packet: DiagnosticPacket, component: str) -> DiagnosticPacket:
    updates: dict[str, Any] = {}
    decisions = [dict(item) for item in packet.decisions]
    target = dict(packet.target_decision or {})
    facts = [dict(item) for item in packet.fact_versions]
    transition = (
        dict(packet.target_transition or {}) if packet.target_transition else None
    )
    prerequisites = [dict(item) for item in packet.target_prerequisites]
    if component == "temporal_validity":
        facts = [{**item, "valid_until": None} for item in facts]
        prerequisites = [
            {**item, "max_age": None, "valid_for": None} for item in prerequisites
        ]
        if transition:
            transition["guards"] = [
                {**guard, "max_age": None} for guard in transition.get("guards", ())
            ]
    elif component == "prompt_inclusion":
        target["prompt_item_ids"] = list(
            target.get("knowledge_snapshot", {}).get("item_ids", ())
        )
    elif component == "scope":
        facts = [{**item, "scope": None} for item in facts]
        prerequisites = [{**item, "scope": None} for item in prerequisites]
        if transition:
            transition["guards"] = [
                {**guard, "scope": None} for guard in transition.get("guards", ())
            ]
    if target:
        decisions = [
            target if item.get("decision_id") == target.get("decision_id") else item
            for item in decisions
        ]
    updates.update(
        {
            "decisions": tuple(decisions),
            "target_decision": target or packet.target_decision,
            "fact_versions": tuple(facts),
            "target_transition": transition,
            "target_prerequisites": tuple(prerequisites),
        }
    )
    return packet.model_copy(update=updates)


def _dcore_ablation(packet: DiagnosticPacket, method: str) -> ComparatorResult:
    component = method.removeprefix("dcore_no_")
    altered = packet
    if component in {"temporal_validity", "prompt_inclusion", "scope"}:
        altered = _ablation_packet(packet, component)
    diagnosed = _dcore(altered)
    witnesses = list(diagnosed.witnesses)
    if component == "actor_delivery":
        witnesses = [
            item.model_copy(
                update={
                    "mechanism": "missing_observation",
                    "evidence_holder_ids": (),
                    "evidence_delivered": None,
                }
            )
            if item.mechanism == "failed_delivery"
            else item
            for item in witnesses
        ]
    repairs: tuple[RepairCandidate, ...] = ()
    if method == "dcore_no_targeted_selection":
        repairs = tuple(
            RepairCandidate(
                candidate_id=stable_digest([method, item.witness_id])[:24],
                witness_id=item.witness_id,
                primitives=(
                    RepairPrimitive(
                        primitive="request_reconsideration",
                        actor_id=item.actor_id,
                    ),
                ),
                feasibility="feasible",
                priority_key=(
                    item.decision_time or float("inf"),
                    1,
                    0.0,
                    item.witness_id,
                ),
            )
            for item in witnesses
        )
    elif method != "dcore_diagnosis_only":
        from are.simulation.distributed.repair_study import enumerate_repairs

        repairs = tuple(
            candidate
            for witness in witnesses
            for candidate in enumerate_repairs(witness)
        )
    return ComparatorResult(
        method=method,
        capability="diagnosis",
        status="ok",
        packet_digest=packet.packet_digest,
        witnesses=tuple(witnesses),
        repairs=repairs,
        source_revision="dcore_representation_ablation_v1",
        adapter_metadata={
            "removed_component": component,
            "all_other_components_locked": True,
            "native_suffix_required_only_if_intervention_differs": True,
        },
    )


def _dcore_full(packet: DiagnosticPacket) -> ComparatorResult:
    result = _dcore(packet)
    repairs = _dcore_repairs(packet).repairs
    return result.model_copy(update={"method": "dcore_full", "repairs": repairs})


def _dcore_repairs(packet: DiagnosticPacket) -> ComparatorResult:
    from are.simulation.distributed.repair_study import enumerate_repairs

    repairs = tuple(
        candidate
        for witness in _dcore(packet).witnesses
        for candidate in enumerate_repairs(witness)
    )
    return ComparatorResult(
        method="dcore_bounded_repair",
        capability="repair",
        status="ok",
        packet_digest=packet.packet_digest,
        repairs=repairs,
        source_revision="dcore_repair_catalogue_v2",
    )


def register_builtin_adapters() -> None:
    adapters = (
        ("core", "score", _core),
        ("dcore_event_timing_components", "score", _dcore_event_timing),
        ("core_information_joint_report", "score", _core_information_joint_report),
        ("scoped_agricultural_milestones_adaptation", "score", _scoped_milestones),
        ("dcore", "diagnosis", _dcore),
        ("dcore_full", "diagnosis", _dcore_full),
        (
            "dcore_no_temporal_validity",
            "diagnosis",
            lambda packet: _dcore_ablation(packet, "dcore_no_temporal_validity"),
        ),
        (
            "dcore_no_actor_delivery",
            "diagnosis",
            lambda packet: _dcore_ablation(packet, "dcore_no_actor_delivery"),
        ),
        (
            "dcore_no_prompt_inclusion",
            "diagnosis",
            lambda packet: _dcore_ablation(packet, "dcore_no_prompt_inclusion"),
        ),
        (
            "dcore_no_scope",
            "diagnosis",
            lambda packet: _dcore_ablation(packet, "dcore_no_scope"),
        ),
        (
            "dcore_no_targeted_selection",
            "diagnosis",
            lambda packet: _dcore_ablation(packet, "dcore_no_targeted_selection"),
        ),
        (
            "dcore_diagnosis_only",
            "diagnosis",
            lambda packet: _dcore_ablation(packet, "dcore_diagnosis_only"),
        ),
        ("full_information_checker", "diagnosis", _full_information_checker),
        ("action_trace_checker", "repair", _action_trace_checker),
        ("generic_reconsideration", "repair", _generic_reconsideration),
        ("fixed_protocol_rules", "repair", _fixed_protocol_repairs),
        ("dcore_bounded_repair", "repair", _dcore_repairs),
        (
            "fairy_temporal",
            "score",
            lambda packet: _unavailable(
                packet,
                "fairy_temporal",
                "score",
                "legacy_name_did_not_invoke_fairy_evaluator",
            ),
        ),
        (
            "information_enriched_core",
            "score",
            lambda packet: _unavailable(
                packet,
                "information_enriched_core",
                "score",
                "legacy_name_did_not_recompute_core_on_enriched_trace",
            ),
        ),
        (
            "marble_style_agricultural_milestones",
            "score",
            lambda packet: _unavailable(
                packet,
                "marble_style_agricultural_milestones",
                "score",
                "replaced_by_scoped_agricultural_milestones_adaptation",
            ),
        ),
        (
            "dcfa_style_reimplementation",
            "diagnosis",
            lambda packet: _unavailable(
                packet,
                "dcfa_style_reimplementation",
                "diagnosis",
                "defining_counterfactual_mechanism_not_implemented",
            ),
        ),
        (
            "dover_adaptation",
            "repair",
            lambda packet: _unavailable(
                packet,
                "dover_adaptation",
                "repair",
                "defining_hypothesis_intervention_selection_not_implemented",
            ),
        ),
    )
    for name, capability, function in adapters:
        register_adapter(ComparatorAdapter(name, capability, function))
    for adapter in (
        external_adapter(
            "who_when_all_at_once",
            "diagnosis",
            expected_revision="f4d2b6da464a826580e59b3a0eae15ea2d642d7c",
        ),
        external_adapter(
            "agentrx",
            "diagnosis",
            expected_revision="7a18c79708e7671be15124460f4f7296107c2a55",
        ),
        external_adapter(
            "agentrx_reviewed_constraints",
            "diagnosis",
            expected_revision="7a18c79708e7671be15124460f4f7296107c2a55",
        ),
    ):
        register_adapter(adapter)


__all__ = ["register_builtin_adapters"]
