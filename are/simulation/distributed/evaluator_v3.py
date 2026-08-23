"""D-CORE v3: non-circular Petri and information-relative farm evaluation."""

from __future__ import annotations

import math
from statistics import median
from typing import Any

from are.simulation.distributed.evaluator_v2 import (
    HARMFUL_EXTRA_LAMBDA,
)
from are.simulation.distributed.evaluator_v2 import (
    evaluate_farm_dcore as _evaluate_v2,
)
from are.simulation.distributed.models import DistributedTrace
from are.simulation.distributed.petri import (
    DataGuardSpec,
    GuardOperator,
    PetriNetSpec,
    TransitionSpec,
)

METRIC_VERSION = "dcore_eval_v3"
PRIMARY_EVENT_WEIGHT = 0.5


def _fact_version_lag(trace: DistributedTrace) -> dict[str, Any]:
    """Measure synchronization per immutable source fact version."""

    sources = {
        fact.version_id: fact
        for fact in trace.fact_versions
        if not fact.authoritative and fact.origin_version_id is None
    }
    sent_versions = {
        version
        for event in trace.events
        if event.kind.value == "message_send"
        for version in event.payload.get("fact_versions", [])
        if version
    }
    # A fact may be re-sent in several handoffs.  Synchronization lag is the
    # first time each recipient learns an immutable fact version, not one
    # observation per delivery copy (which would make duplicate/resend policy
    # change the sampling distribution).
    received: dict[tuple[str, str], Any] = {}
    for fact in trace.fact_versions:
        if fact.origin_version_id:
            recipients = fact.visible_to or ("unknown",)
            for recipient in recipients:
                key = (fact.origin_version_id, recipient)
                current = received.get(key)
                if current is None or (
                    fact.learned_time is not None
                    and (
                        current.learned_time is None
                        or fact.learned_time < current.learned_time
                    )
                ):
                    received[key] = fact
    first_receipts: dict[str, list[Any]] = {}
    for (origin_version, _recipient), fact in received.items():
        first_receipts.setdefault(origin_version, []).append(fact)
    lags = sorted(
        float(copy.learned_time - source.world_time)
        for version, source in sources.items()
        if version in sent_versions
        for copy in first_receipts.get(version, [])
        if copy.learned_time is not None
    )
    normalized = sorted(
        float(copy.learned_time - source.world_time)
        / max(float(source.valid_until - source.world_time), 1e-9)
        for version, source in sources.items()
        if version in sent_versions and source.valid_until is not None
        for copy in first_receipts.get(version, [])
        if copy.learned_time is not None
    )
    p95 = lags[min(len(lags) - 1, math.ceil(0.95 * len(lags)) - 1)] if lags else None
    return {
        "unit": "seconds",
        "median": median(lags) if lags else None,
        "p95": p95,
        "max": max(lags) if lags else None,
        "count": len(lags),
        "deadline_normalized_median": median(normalized) if normalized else None,
        "deadline_normalized_max": max(normalized) if normalized else None,
        "never_received_versions": sorted(sent_versions - set(first_receipts)),
        "sent_fact_versions": len(sent_versions),
        "calculation": "origin_world_time_to_first_recipient_learning_time_v3",
    }


def _scope_covers(actual: Any, required: Any) -> bool:
    if required is None:
        return True
    if actual is None:
        return False
    if isinstance(actual, (tuple, list)) and isinstance(required, (tuple, list)):
        return int(actual[0]) <= int(required[0]) and int(actual[1]) >= int(required[1])
    return actual == required


def _compare(actual: Any, expected: Any, operator: GuardOperator) -> bool:
    operations = {
        GuardOperator.EQ: lambda: actual == expected,
        GuardOperator.NE: lambda: actual != expected,
        GuardOperator.GE: lambda: actual >= expected,
        GuardOperator.GT: lambda: actual > expected,
        GuardOperator.LE: lambda: actual <= expected,
        GuardOperator.LT: lambda: actual < expected,
        GuardOperator.IN: lambda: actual in expected,
    }
    try:
        return bool(operations[operator]())
    except (TypeError, ValueError):
        return False


def _guard_check(
    *,
    guard: DataGuardSpec,
    transition: TransitionSpec,
    action: Any,
    decision: Any,
    trace: DistributedTrace,
) -> dict[str, Any]:
    facts = {fact.version_id: fact for fact in trace.fact_versions}
    known_event_ids = {event.event_id for event in trace.events}
    snapshot_ids = (
        set(decision.prompt_item_ids or decision.knowledge_snapshot.item_ids)
        if decision
        else set()
    )
    if guard.source == "knowledge":
        candidates = []
        for item_id in snapshot_ids:
            fact = facts.get(item_id) or facts.get(item_id.removeprefix("blackboard:"))
            learned = (
                fact.learned_time
                if fact and fact.learned_time is not None
                else (fact.world_time if fact else None)
            )
            if (
                fact
                and fact.fact_key == guard.fact_key
                and learned is not None
                and learned <= action.world_time
            ):
                candidates.append(fact)
    else:
        candidates = [
            fact
            for fact in trace.fact_versions
            if fact.authoritative
            and fact.fact_key == guard.fact_key
            and fact.world_time <= action.world_time
        ]
    latest = max(
        candidates,
        key=lambda fact: (
            fact.learned_time if fact.learned_time is not None else fact.world_time,
            fact.version_id,
        ),
        default=None,
    )
    passed = latest is not None
    verdict = "unknown" if latest is None else "true"
    reason = "missing_recorded_fact"
    if latest is not None:
        passed = _compare(latest.value, guard.expected, guard.operator)
        verdict = "true" if passed else "false"
        reason = "value_guard"
        if not _scope_covers(latest.scope, guard.scope or transition.scope):
            passed = False
            verdict = "false"
            reason = "scope_guard"
        if latest.valid_until is not None and action.world_time > latest.valid_until:
            passed = False
            verdict = "false"
            reason = "freshness_guard"
        if (
            guard.max_age is not None
            and action.world_time - latest.world_time > guard.max_age
        ):
            passed = False
            verdict = "false"
            reason = "max_age_guard"
        if guard.required_evidence and (
            not latest.evidence_ids or not set(latest.evidence_ids) <= known_event_ids
        ):
            passed = False
            verdict = "false"
            reason = "evidence_provenance_guard"
    return {
        "constraint": guard.guard_id,
        "target": transition.transition_id,
        "target_event_id": action.event_id,
        "phase": transition.phase,
        "cross_agent": guard.source == "knowledge",
        "passed": passed,
        "verdict": verdict,
        "weight": transition.weight,
        "reason": reason,
        "fact_key": guard.fact_key,
        "fact_version": latest.version_id if latest else None,
        "evaluation_source": "dcore_eval_v3_reconstruction",
    }


def _independent_guard_checks(
    net: PetriNetSpec,
    trace: DistributedTrace,
    matched: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    events = {event.event_id: event for event in trace.events}
    decisions = {decision.decision_id: decision for decision in trace.decisions}
    transitions = {
        transition.transition_id: transition for transition in net.transitions
    }
    checks: list[dict[str, Any]] = []
    for transition_id, match in matched.items():
        transition = transitions[transition_id]
        if not transition.guards:
            continue
        action = events[match["observed_event_id"]]
        decision = decisions.get(action.decision_context_id or "")
        for guard in transition.guards:
            checks.append(
                _guard_check(
                    guard=guard,
                    transition=transition,
                    action=action,
                    decision=decision,
                    trace=trace,
                )
            )
    return checks


def _local_reconstruction(
    net: PetriNetSpec,
    trace: DistributedTrace,
    result: dict[str, Any],
    direct_checks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Recompute local obligations from pre-decision snapshots, never guards."""

    decisions = {decision.decision_id: decision for decision in trace.decisions}
    transitions = [transition for transition in net.transitions if transition.guards]
    obligations: dict[str, list[dict[str, Any]]] = {actor: [] for actor in net.actors}
    for action in trace.events:
        if action.kind.value != "action" or not action.decision_context_id:
            continue
        candidates = [
            transition
            for transition in transitions
            if transition.actor_id == action.actor_id
            and transition.action == action.action
            and transition.phase == action.season_phase
        ]
        if not candidates:
            continue

        def compatibility(transition: TransitionSpec) -> tuple[int, int]:
            exact = sum(
                action.args.get(constraint.name) == constraint.expected
                for constraint in transition.arguments
            )
            return exact, -abs(len(action.args) - len(transition.arguments))

        transition = max(candidates, key=compatibility)
        decision = decisions.get(action.decision_context_id)
        checks = [
            _guard_check(
                guard=guard,
                transition=transition,
                action=action,
                decision=decision,
                trace=trace,
            )
            for guard in transition.guards
            if guard.source == "knowledge"
        ]
        if not checks:
            continue
        verdict = (
            "false"
            if any(check["verdict"] == "false" for check in checks)
            else "unknown"
            if any(check["verdict"] == "unknown" for check in checks)
            else "true"
        )
        # Local justification evaluates the decision to attempt the action.
        # A native tool error can be a downstream composition consequence and
        # must not retroactively turn a justified proposal into reasoning error.
        executed = action.status not in {"blocked", "deferred"}
        correct = executed if verdict == "true" else not executed
        obligations[action.actor_id].append(
            {
                "decision_id": action.decision_context_id,
                "action_event_id": action.event_id,
                "transition_id": transition.transition_id,
                "verdict": verdict,
                "action_status": action.status,
                "executed": executed,
                "correct_response": correct,
                "checks": checks,
            }
        )
    rebuilt = result["local"]
    transition_actor = {
        transition.transition_id: transition.actor_id for transition in net.transitions
    }
    for actor, profile in rebuilt.items():
        actor_obligations = obligations[actor]
        dependency = [
            check
            for check in direct_checks
            if check.get("source") in transition_actor
            and check.get("target") in transition_actor
            and transition_actor[check["source"]] == actor
            and transition_actor[check["target"]] == actor
        ]
        dependency_score = (
            sum(bool(check["passed"]) for check in dependency) / len(dependency)
            if dependency
            else 1.0
        )
        response_score = (
            sum(item["correct_response"] for item in actor_obligations)
            / len(actor_obligations)
            if actor_obligations
            else 1.0
        )
        local_cc = 0.5 * dependency_score + 0.5 * response_score
        profile.update(
            {
                "causal_conformance": round(local_cc, 6),
                "local_dcore": round(
                    0.5 * float(profile["event_fidelity"]) + 0.5 * local_cc,
                    6,
                ),
                "locally_justified_decisions": sum(
                    item["verdict"] == "true" for item in actor_obligations
                ),
                "locally_contradicted_decisions": sum(
                    item["verdict"] == "false" for item in actor_obligations
                ),
                "locally_underdetermined_decisions": sum(
                    item["verdict"] == "unknown" for item in actor_obligations
                ),
                "epistemic_coverage": round(
                    sum(item["verdict"] != "unknown" for item in actor_obligations)
                    / len(actor_obligations)
                    if actor_obligations
                    else 1.0,
                    6,
                ),
                "correct_requirement_response_rate": round(response_score, 6),
                "violation_count": sum(
                    not item["correct_response"] for item in actor_obligations
                ),
                "unknown_count": sum(
                    item["verdict"] == "unknown" for item in actor_obligations
                ),
                "correct_execution_count": sum(
                    item["verdict"] == "true" and item["executed"]
                    for item in actor_obligations
                ),
                "correct_defer_count": sum(
                    item["verdict"] == "unknown" and item["action_status"] == "deferred"
                    for item in actor_obligations
                ),
                "correct_block_count": sum(
                    item["verdict"] in {"false", "unknown"}
                    and item["action_status"] == "blocked"
                    for item in actor_obligations
                ),
                "unsafe_execution_count": sum(
                    item["verdict"] in {"false", "unknown"} and item["executed"]
                    for item in actor_obligations
                ),
                "obligations": actor_obligations,
                "evaluation_source": "predecision_snapshot_reconstruction_v3",
            }
        )
    return rebuilt


def _attribution_v3(
    trace: DistributedTrace, failed_guards: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    events = {event.event_id: event for event in trace.events}
    decisions = {decision.decision_id: decision for decision in trace.decisions}
    results = []
    for check in failed_guards:
        action = events[check["target_event_id"]]
        decision = decisions.get(action.decision_context_id or "")
        fact_key = check["fact_key"]
        observations = [
            event
            for event in trace.events
            if event.kind.value == "observation"
            and event.payload.get("fact_key") == fact_key
            and event.world_time <= action.world_time
        ]
        sends = [
            event
            for event in trace.events
            if event.kind.value == "message_send"
            and event.world_time <= action.world_time
            and (
                fact_key in event.payload.get("fact_keys", [])
                or event.payload.get("envelope_type") == "free_text"
            )
        ]
        receives = [
            event
            for event in trace.events
            if event.kind.value == "message_receive"
            and event.world_time <= action.world_time
            and any(event.message_id == send.message_id for send in sends)
            and event.status != "duplicate"
        ]
        snapshot_ids = (
            set(decision.prompt_item_ids or decision.knowledge_snapshot.item_ids)
            if decision
            else set()
        )
        visible = [
            fact
            for fact in trace.fact_versions
            if fact.fact_key == fact_key
            and (
                fact.version_id in snapshot_ids
                or f"blackboard:{fact.version_id}" in snapshot_ids
            )
        ]
        contributing = []
        if sends and all(
            send.payload.get("envelope_type") == "free_text" for send in sends
        ):
            primary = "unverifiable_handoff"
        elif not observations:
            primary = "observation_gap"
        elif not sends:
            primary = "handoff_omission"
        elif any(send.status == "dropped" for send in sends) or not receives:
            primary = "transit_gap"
        elif not visible:
            primary = "uptake_error"
        elif check["reason"] in {"freshness_guard", "max_age_guard"}:
            primary = "stale_information"
        elif check["reason"] == "evidence_provenance_guard":
            primary = "unsupported_claim"
        else:
            primary = "reasoning_error"
        results.append(
            {
                "target_event_id": action.event_id,
                "target_transition_id": check["target"],
                "fact_key": fact_key,
                "primary": primary,
                "contributing": contributing,
                "evidence_path": {
                    "observation_ids": [event.event_id for event in observations],
                    "send_ids": [event.event_id for event in sends],
                    "receive_ids": [event.event_id for event in receives],
                    "decision_id": decision.decision_id if decision else None,
                    "fact_version": check.get("fact_version"),
                },
            }
        )
    return results


def evaluate_farm_dcore(net: PetriNetSpec, trace: DistributedTrace) -> dict[str, Any]:
    """Evaluate a trace without consuming runtime oracle identifiers or flags."""

    world_context = dict(trace.configuration.get("committed_world_context", {}))
    committed_branches = dict(trace.configuration.get("committed_branches", {}))
    if net.exogenous_branches:
        evidence_manifest = dict(
            trace.configuration.get("branch_commitment_evidence", {})
        )
        authoritative = {
            fact.version_id: fact for fact in trace.fact_versions if fact.authoritative
        }
        for branch in net.exogenous_branches:
            if branch.branch_id not in committed_branches:
                raise ValueError(
                    f"trace has no frozen commitment for branch {branch.branch_id!r}"
                )
            evidence_ids = evidence_manifest.get(branch.branch_id, [])
            evidence = [
                authoritative[version]
                for version in evidence_ids
                if version in authoritative
            ]
            alternative = next(
                item
                for item in branch.alternatives
                if item.alternative_id == committed_branches[branch.branch_id]
            )
            for guard in alternative.guards:
                if not any(
                    fact.fact_key == guard.fact_key
                    and _compare(fact.value, guard.expected, guard.operator)
                    for fact in evidence
                ):
                    raise ValueError(
                        f"branch {branch.branch_id!r} lacks authoritative evidence "
                        f"for {guard.fact_key!r}"
                    )
    result = _evaluate_v2(
        net,
        trace,
        world_context=world_context,
        committed_branches=committed_branches,
    )
    direct_checks = [
        check
        for check in result["constraint_detail"]
        if check.get("reason") != "evidence_guard"
    ]
    guard_checks = _independent_guard_checks(net, trace, result["matched_transitions"])
    checks = [*direct_checks, *guard_checks]
    denominator = sum(float(check.get("weight", 1.0)) for check in checks)
    causal = (
        sum(
            float(check.get("weight", 1.0)) * int(bool(check.get("passed")))
            for check in checks
        )
        / denominator
        if denominator
        else 1.0
    )
    event_fidelity = float(result["event_fidelity"])
    dcore = (
        PRIMARY_EVENT_WEIGHT * event_fidelity + (1.0 - PRIMARY_EVENT_WEIGHT) * causal
    )
    result.update(
        {
            "metric_version": METRIC_VERSION,
            "metric_profile": {
                "profile_role": "primary_profile_scalar_secondary",
                "event_matching": "required_first_lexicographic_max_weight_v1",
                "actor_and_tool_are_hard_gates": True,
                "runtime_transition_ids_consumed": False,
                "runtime_violation_flags_consumed": False,
                "harmful_extra_lambda": HARMFUL_EXTRA_LAMBDA,
                "harmful_extra_classifier": (
                    "native_high_impact_tool_or_controlled_mutant_annotation"
                ),
                "dcore_event_weight": PRIMARY_EVENT_WEIGHT,
                "unknown_execution_is_violation": True,
                "unknown_defer_or_reobserve_is_correct": True,
            },
            "causal_conformance": round(causal, 6),
            "dcore_score": round(dcore, 6),
            "dcore_sensitivity": {
                str(alpha): round(alpha * event_fidelity + (1 - alpha) * causal, 6)
                for alpha in (0.25, 0.5, 0.75)
            },
            "constraint_detail": checks,
            "guard_reconstruction": {
                "source": "frozen_fact_versions_and_predecision_snapshots",
                "checks": len(guard_checks),
                "runtime_violation_flags_consumed": False,
                "runtime_transition_ids_consumed": False,
            },
            "synchronization_lag": _fact_version_lag(trace),
        }
    )
    result["local"] = _local_reconstruction(net, trace, result, direct_checks)
    result["all_agents_locally_correct"] = all(
        profile["violation_count"] == 0 for profile in result["local"].values()
    )
    mean_local = (
        sum(float(item["local_dcore"]) for item in result["local"].values())
        / len(result["local"])
        if result["local"]
        else dcore
    )
    result["local_global_gap"] = round(mean_local - dcore, 6)
    cross_checks = [check for check in checks if check.get("cross_agent")]
    result["coordination_failure_rate"] = round(
        sum(not bool(check.get("passed")) for check in cross_checks) / len(cross_checks)
        if cross_checks
        else 0.0,
        6,
    )
    recovered_actions = [
        event
        for event in trace.events
        if event.kind.value == "action"
        and event.payload.get("recovery_of_action_event_ids")
        and event.status == "ok"
    ]
    challenged_intents = int(trace.outcome.get("blocked_intent_count", 0))
    result["recovery"].update(
        {
            "guard_rejections": int(
                trace.outcome.get(
                    "guard_rejection_count",
                    int(trace.outcome.get("blocked_write_count", 0))
                    + int(trace.outcome.get("deferred_write_count", 0)),
                )
            ),
            "successful_recovery_count": len(recovered_actions),
            "reobservation_recovery_count": sum(
                bool(event.payload.get("recovery_observation_event_ids"))
                for event in recovered_actions
            ),
            "new_delivery_recovery_count": sum(
                bool(event.payload.get("recovery_receive_event_ids"))
                for event in recovered_actions
            ),
            "successful_replanning_rate": round(
                len(recovered_actions) / challenged_intents
                if challenged_intents
                else 0.0,
                6,
            ),
            "safe_abstention_count": int(
                trace.outcome.get("guard_abstention_count", 0)
            ),
            "provenance": [
                {
                    "recovery_action_event_id": event.event_id,
                    "rejected_action_event_ids": event.payload.get(
                        "recovery_of_action_event_ids", ()
                    ),
                    "observation_event_ids": event.payload.get(
                        "recovery_observation_event_ids", ()
                    ),
                    "receive_event_ids": event.payload.get(
                        "recovery_receive_event_ids", ()
                    ),
                    "latency_seconds": event.payload.get("recovery_latency_seconds"),
                }
                for event in recovered_actions
            ],
        }
    )
    for phase, profile in result.get("phase_profile", {}).items():
        phase_checks = [check for check in checks if check.get("phase") == phase]
        phase_denominator = sum(
            float(check.get("weight", 1.0)) for check in phase_checks
        )
        phase_cc = (
            sum(
                float(check.get("weight", 1.0)) * int(bool(check.get("passed")))
                for check in phase_checks
            )
            / phase_denominator
            if phase_denominator
            else 1.0
        )
        profile["causal_conformance"] = round(phase_cc, 6)
        profile["dcore_score"] = round(
            PRIMARY_EVENT_WEIGHT * float(profile["event_fidelity"])
            + (1.0 - PRIMARY_EVENT_WEIGHT) * phase_cc,
            6,
        )
    failed_guards = [check for check in guard_checks if not check["passed"]]
    if failed_guards:
        result["attribution"] = _attribution_v3(trace, failed_guards)
    return result
