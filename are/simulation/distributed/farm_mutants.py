"""Controlled farm trace mutants with planted metric/attribution ground truth."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from are.simulation.distributed.evaluator_v3 import evaluate_farm_dcore
from are.simulation.distributed.models import DistributedTrace, EventKind
from are.simulation.distributed.mutants import (
    add_harmful_duplicate,
    add_harmless_read,
    delay_receive,
    drop_receive,
    duplicate_action,
    omit_handoff,
    omit_uptake,
    remove_event,
    remove_observation,
    reorder_within_actor,
    swap_concurrent,
)
from are.simulation.distributed.petri import OccurrenceNet, PetriNetSpec


@dataclass(frozen=True)
class FarmMutationCase:
    name: str
    trace: DistributedTrace
    expected_attribution: str | None = None
    invariant_metrics: tuple[str, ...] = ()
    decreasing_metrics: tuple[str, ...] = ()


def _update_action(
    trace: DistributedTrace, event_id: str, **payload_updates: Any
) -> DistributedTrace:
    return trace.model_copy(
        update={
            "events": tuple(
                event.model_copy(
                    update={"payload": {**event.payload, **payload_updates}}
                )
                if event.event_id == event_id
                else event
                for event in trace.events
            )
        }
    )


def _mark_violation(
    trace: DistributedTrace, action_event_id: str, requirement_id: str
) -> DistributedTrace:
    action = next(event for event in trace.events if event.event_id == action_event_id)
    violations = list(action.payload.get("violated_requirements", []))
    if requirement_id not in violations:
        violations.append(requirement_id)
    return _update_action(
        trace, action_event_id, violated_requirements=violations, harmful=True
    )


def _replace_action(
    trace: DistributedTrace,
    event_id: str,
    *,
    args: dict[str, Any] | None = None,
    scope: tuple[int, int] | None = None,
    status: str | None = None,
) -> DistributedTrace:
    events = []
    for event in trace.events:
        if event.event_id == event_id:
            update: dict[str, Any] = {}
            if args is not None:
                update["args"] = args
            if scope is not None:
                update["payload"] = {**event.payload, "scope": scope}
            if status is not None:
                update["status"] = status
            event = event.model_copy(update=update)
        events.append(event)
    return trace.model_copy(update={"events": tuple(events)})


def _without_message_knowledge(
    trace: DistributedTrace, message_id: str
) -> DistributedTrace:
    prefix = f"{message_id}:claim:"
    decisions = tuple(
        decision.model_copy(
            update={
                "knowledge_snapshot": decision.knowledge_snapshot.model_copy(
                    update={
                        "item_ids": tuple(
                            item_id
                            for item_id in decision.knowledge_snapshot.item_ids
                            if not item_id.startswith(prefix)
                        )
                    }
                )
            }
        )
        for decision in trace.decisions
    )
    facts = tuple(
        fact for fact in trace.fact_versions if not fact.version_id.startswith(prefix)
    )
    return trace.model_copy(update={"decisions": decisions, "fact_versions": facts})


def _mutate_visible_fact(
    trace: DistributedTrace,
    decision_id: str,
    fact_key: str,
    **updates: Any,
) -> DistributedTrace:
    decision = next(item for item in trace.decisions if item.decision_id == decision_id)
    visible = set(decision.knowledge_snapshot.item_ids)
    facts = tuple(
        fact.model_copy(update=updates)
        if fact.fact_key == fact_key
        and (fact.version_id in visible or f"blackboard:{fact.version_id}" in visible)
        else fact
        for fact in trace.fact_versions
    )
    return trace.model_copy(update={"fact_versions": facts})


def build_farm_mutant_suite(
    trace: DistributedTrace,
    net: PetriNetSpec,
    occurrence: OccurrenceNet,
) -> list[FarmMutationCase]:
    """Build one-defect mutants without using their labels during evaluation."""
    alignment = evaluate_farm_dcore(net, trace)["matched_transitions"]
    events_by_id = {event.event_id: event for event in trace.events}
    by_transition = {
        transition_id: events_by_id[match["observed_event_id"]]
        for transition_id, match in alignment.items()
    }
    transition_map = {
        transition.transition_id: transition for transition in net.transitions
    }
    cases: list[FarmMutationCase] = []

    for left, right in occurrence.concurrent_pairs:
        if left in by_transition and right in by_transition:
            left_event, right_event = by_transition[left], by_transition[right]
            if left_event.actor_id != right_event.actor_id:
                mutant, _ = swap_concurrent(
                    trace, left_event.event_id, right_event.event_id
                )
                cases.append(
                    FarmMutationCase(
                        "concurrent_permutation",
                        mutant,
                        invariant_metrics=("dcore_score", "po_pair_agreement"),
                    )
                )
                break

    observed_action_counts = Counter(
        event.action for event in trace.events if event.kind == EventKind.ACTION
    )
    same_actor_edge = next(
        (
            (source, target)
            for source, target in occurrence.direct_dependencies
            if source in by_transition
            and target in by_transition
            and by_transition[source].actor_id == by_transition[target].actor_id
            and by_transition[source].kind == EventKind.ACTION
            and by_transition[target].kind == EventKind.ACTION
            # Repeated seasonal calls can be rematched to equivalent Petri
            # instances after a swap, yielding a false invariant.  Select a
            # dependency with distinct hard-gated tool labels so this fixture
            # plants an actual within-actor causal inversion.
            and by_transition[source].action != by_transition[target].action
            and observed_action_counts[by_transition[source].action] == 1
            and observed_action_counts[by_transition[target].action] == 1
        ),
        None,
    )
    if same_actor_edge:
        mutant, _ = reorder_within_actor(
            trace,
            by_transition[same_actor_edge[0]].event_id,
            by_transition[same_actor_edge[1]].event_id,
        )
        cases.append(
            FarmMutationCase(
                "within_agent_reorder",
                mutant,
                expected_attribution="composition_error",
                decreasing_metrics=("causal_conformance",),
            )
        )

    send_transition_id = next(
        transition_id
        for transition_id, event in by_transition.items()
        if event.kind == EventKind.MESSAGE_SEND
        and any(
            source == transition_id
            and target in by_transition
            and by_transition[target].kind == EventKind.MESSAGE_RECEIVE
            for source, target in occurrence.direct_dependencies
        )
    )
    send = by_transition[send_transition_id]
    receive_transition_id = next(
        target
        for source, target in occurrence.direct_dependencies
        if source == send_transition_id
    )
    target_id = next(
        target
        for source, target in occurrence.direct_dependencies
        if source == receive_transition_id
        and target in by_transition
        and by_transition[target].kind == EventKind.ACTION
    )
    target_action = by_transition[target_id]
    fact_key = transition_map[target_id].guards[0].fact_key
    requirement_id = transition_map[target_id].guards[0].guard_id
    dropped, _ = drop_receive(trace, send.message_id or "")
    dropped = _without_message_knowledge(dropped, send.message_id or "")
    cases.append(
        FarmMutationCase(
            "dropped_handoff",
            _mark_violation(dropped, target_action.event_id, requirement_id),
            expected_attribution="transit_gap",
            decreasing_metrics=("causal_conformance",),
        )
    )
    delayed, _ = delay_receive(trace, send.message_id or "", 10_000.0)
    delayed = _without_message_knowledge(delayed, send.message_id or "")
    cases.append(
        FarmMutationCase(
            "delayed_handoff",
            _mark_violation(delayed, target_action.event_id, requirement_id),
            expected_attribution="transit_gap",
            decreasing_metrics=("causal_conformance",),
        )
    )
    omitted, _ = omit_handoff(trace, send.message_id or "")
    omitted = _without_message_knowledge(omitted, send.message_id or "")
    cases.append(
        FarmMutationCase(
            "missing_handoff",
            _mark_violation(omitted, target_action.event_id, requirement_id),
            expected_attribution="handoff_omission",
            decreasing_metrics=("causal_conformance",),
        )
    )
    uptake, _ = omit_uptake(trace, target_action.actor_id, send.message_id or "")
    uptake = _without_message_knowledge(uptake, send.message_id or "")
    cases.append(
        FarmMutationCase(
            "uptake_omission",
            _mark_violation(uptake, target_action.event_id, requirement_id),
            expected_attribution="uptake_error",
            decreasing_metrics=("causal_conformance",),
        )
    )
    observation = next(
        event
        for event in trace.events
        if event.kind == EventKind.OBSERVATION
        and event.season_phase == target_action.season_phase
    )
    no_observation, _ = remove_observation(trace, str(observation.payload["fact_key"]))
    cases.append(
        FarmMutationCase(
            "observation_gap",
            _mark_violation(no_observation, target_action.event_id, requirement_id),
            expected_attribution="observation_gap",
            decreasing_metrics=("causal_conformance",),
        )
    )
    reasoning = _mutate_visible_fact(
        trace,
        target_action.decision_context_id or "",
        fact_key,
        value=False,
    )
    cases.append(
        FarmMutationCase(
            "reasoning_error",
            reasoning,
            expected_attribution="reasoning_error",
            decreasing_metrics=("causal_conformance",),
        )
    )
    unsupported = _mutate_visible_fact(
        trace,
        target_action.decision_context_id or "",
        fact_key,
        evidence_ids=(),
    )
    cases.append(
        FarmMutationCase(
            "unsupported_claim",
            unsupported,
            expected_attribution="unsupported_claim",
            decreasing_metrics=("causal_conformance",),
        )
    )
    stale = _mutate_visible_fact(
        trace,
        target_action.decision_context_id or "",
        fact_key,
        valid_until=target_action.world_time - 1,
    )
    cases.append(
        FarmMutationCase(
            "out_of_order_stale_handoff",
            stale,
            expected_attribution="stale_information",
            decreasing_metrics=("causal_conformance",),
        )
    )

    required_action = next(
        event
        for transition_id, event in by_transition.items()
        if event.kind == EventKind.ACTION and transition_map[transition_id].required
    )
    missing, _ = remove_event(trace, required_action.event_id)
    cases.append(
        FarmMutationCase(
            "missing_required_action",
            missing,
            decreasing_metrics=("event_fidelity",),
        )
    )
    duplicate, _ = duplicate_action(trace, required_action.event_id)
    cases.append(
        FarmMutationCase(
            "duplicate_action",
            duplicate,
            invariant_metrics=("coverage",),
        )
    )
    harmful, _ = add_harmful_duplicate(trace, required_action.event_id)
    cases.append(
        FarmMutationCase(
            "harmful_extra_write",
            harmful,
            decreasing_metrics=("event_fidelity",),
        )
    )
    harmless, _ = add_harmless_read(trace, "field_intelligence")
    cases.append(
        FarmMutationCase(
            "harmless_redundant_read",
            harmless,
            invariant_metrics=("event_fidelity", "dcore_score"),
        )
    )
    scoped = next(
        event
        for transition_id, event in by_transition.items()
        if event.kind == EventKind.ACTION
        and transition_map[transition_id].scope is not None
    )
    cases.append(
        FarmMutationCase(
            "wrong_ridge_scope",
            _replace_action(trace, scoped.event_id, scope=(100, 101)),
            decreasing_metrics=("spatial_fidelity", "event_fidelity"),
        )
    )
    amount_priority = {
        "liters_per_ridge": 0,
        "amount": 1,
        "depth_cm": 2,
        "seed_spacing_cm": 3,
        "target_moisture_pct": 4,
    }
    numeric_candidates = [
        (amount_priority.get(constraint.name, 100), event, constraint.name)
        for transition_id, event in by_transition.items()
        for transition in [transition_map[transition_id]]
        if event.kind == EventKind.ACTION
        if transition.high_impact
        for constraint in transition.arguments
        if isinstance(constraint.expected, (int, float))
        and not isinstance(constraint.expected, bool)
        and constraint.name not in {"start_ridge", "end_ridge", "ridge_id"}
    ]
    numeric = None
    if numeric_candidates:
        _, event, argument = min(numeric_candidates, key=lambda item: item[0])
        numeric = (event, argument)
    if numeric:
        event, argument = numeric
        args = dict(event.args)
        args[argument] = float(args[argument]) * 10 + 100
        cases.append(
            FarmMutationCase(
                "wrong_treatment_amount",
                _replace_action(trace, event.event_id, args=args),
                decreasing_metrics=("argument_fidelity", "event_fidelity"),
            )
        )
    cases.append(
        FarmMutationCase(
            "safe_unnecessary_abstention",
            _replace_action(trace, required_action.event_id, status="blocked"),
            decreasing_metrics=("event_fidelity",),
        )
    )
    return cases


def attribution_confusion(
    expected_and_predicted: list[tuple[str, str | None]],
) -> dict[str, Any]:
    labels = sorted(
        {expected for expected, _ in expected_and_predicted}
        | {predicted for _, predicted in expected_and_predicted if predicted}
    )
    matrix = {
        expected: {
            predicted: sum(
                1
                for actual, found in expected_and_predicted
                if actual == expected and found == predicted
            )
            for predicted in labels
        }
        for expected in labels
    }
    correct = sum(
        expected == predicted for expected, predicted in expected_and_predicted
    )
    per_label = {}
    for label in labels:
        true_positive = matrix[label][label]
        predicted_total = sum(matrix[expected][label] for expected in labels)
        actual_total = sum(matrix[label].values())
        per_label[label] = {
            "precision": true_positive / predicted_total if predicted_total else None,
            "recall": true_positive / actual_total if actual_total else None,
        }
    return {
        "labels": labels,
        "confusion_matrix": matrix,
        "accuracy": correct / len(expected_and_predicted)
        if expected_and_predicted
        else None,
        "count": len(expected_and_predicted),
        "per_label": per_label,
    }
