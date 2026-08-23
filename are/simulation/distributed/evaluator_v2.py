"""Farm D-CORE v2: Petri-grounded, information-relative evaluation."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from statistics import median
from typing import Any

from are.simulation.distributed.evaluator import _maximum_weight_assignment
from are.simulation.distributed.models import (
    DistributedTrace,
    EventKind,
    GuardVerdict,
    RequirementVerdict,
    TraceEvent,
)
from are.simulation.distributed.petri import (
    OccurrenceNet,
    PetriNetSpec,
    TransitionKind,
    TransitionSpec,
    unfold_petri_net,
)
from are.simulation.scenarios.workflow_validation import evaluate_workflows

METRIC_VERSION = "dcore_eval_v2"
HARMFUL_EXTRA_LAMBDA = 0.25


def _scope_iou(expected: Any, actual: Any) -> float:
    if expected is None:
        return 1.0
    if actual is None:
        return 0.0
    if isinstance(expected, (tuple, list)) and isinstance(actual, (tuple, list)):
        expected_ids = set(range(int(expected[0]), int(expected[1]) + 1))
        actual_ids = set(range(int(actual[0]), int(actual[1]) + 1))
        return len(expected_ids & actual_ids) / max(1, len(expected_ids | actual_ids))
    return 1.0 if expected == actual else 0.0


def transition_similarity(
    reference: TransitionSpec, observed: TraceEvent
) -> tuple[float, dict[str, float]]:
    expected_kind = {
        "send": EventKind.MESSAGE_SEND,
        "receive": EventKind.MESSAGE_RECEIVE,
    }.get(reference.kind.value, EventKind.ACTION)
    if (
        observed.kind != expected_kind
        or observed.status == "blocked"
        or reference.actor_id != observed.actor_id
        or reference.action != observed.action
    ):
        return 0.0, {
            "argument": 0.0,
            "spatial": 0.0,
            "timing": 0.0,
            "execution": 0.0,
        }
    # Reference transition identifiers are deliberately ignored. An observed
    # trace must be alignable from actor/tool/arguments/scope/time alone; using
    # a runtime-supplied oracle ID would make evaluation circular.
    weighted = 0.0
    total_weight = 0.0
    critical_failure = False
    for constraint in reference.arguments:
        actual = observed.args.get(constraint.name)
        if constraint.tolerance is not None and isinstance(actual, (int, float)):
            error = abs(float(actual) - float(constraint.expected))
            value = (
                1.0
                if error <= constraint.tolerance
                else max(
                    0.0,
                    1.0
                    - (error - constraint.tolerance) / max(constraint.tolerance, 1e-9),
                )
            )
        else:
            value = 1.0 if actual == constraint.expected else 0.0
        if constraint.critical and value < 1.0:
            critical_failure = True
        weighted += constraint.weight * value
        total_weight += constraint.weight
    argument = weighted / total_weight if total_weight else 1.0
    spatial = _scope_iou(reference.scope, observed.payload.get("scope"))
    timing = 1.0
    if (
        reference.window_start is not None
        and observed.world_time < reference.window_start
    ):
        timing = 0.0
    if reference.window_end is not None and observed.world_time > reference.window_end:
        timing = 0.0
    execution = 0.0 if observed.status == "error" else 1.0
    if critical_failure or spatial < reference.scope_iou_threshold:
        score = 0.0
    else:
        score = (argument + spatial + timing + execution) / 4.0
    return score, {
        "argument": argument,
        "spatial": spatial,
        "timing": timing,
        "execution": execution,
    }


def _reachability(trace: DistributedTrace) -> dict[str, set[str]]:
    children: defaultdict[str, set[str]] = defaultdict(set)
    previous: dict[str, str] = {}
    sends: dict[str, str] = {}
    for event in trace.events:
        for parent in event.causal_parents:
            children[parent].add(event.event_id)
        if event.actor_id in previous:
            children[previous[event.actor_id]].add(event.event_id)
        previous[event.actor_id] = event.event_id
        if event.kind == EventKind.MESSAGE_SEND and event.message_id:
            sends[event.message_id] = event.event_id
        elif event.kind == EventKind.MESSAGE_RECEIVE and event.message_id in sends:
            children[sends[event.message_id]].add(event.event_id)
    result: dict[str, set[str]] = {}
    for event in trace.events:
        reached: set[str] = set()
        queue = deque(children[event.event_id])
        while queue:
            child = queue.popleft()
            if child in reached:
                continue
            reached.add(child)
            queue.extend(children[child])
        result[event.event_id] = reached
    return result


def _match(
    net: PetriNetSpec,
    occurrence: OccurrenceNet,
    trace: DistributedTrace,
) -> tuple[
    dict[str, TraceEvent],
    dict[str, float],
    dict[str, dict[str, float]],
    set[str],
]:
    applicable = set(occurrence.applicable_transition_ids)
    references = [
        transition
        for transition in net.transitions
        if transition.transition_id in applicable and transition.actor_id != "world"
    ]
    observed = [
        event
        for event in trace.events
        if event.kind
        in {EventKind.ACTION, EventKind.MESSAGE_SEND, EventKind.MESSAGE_RECEIVE}
        and event.status not in {"blocked", "duplicate"}
    ]
    matches: dict[str, TraceEvent] = {}
    similarities: dict[str, float] = {}
    components: dict[str, dict[str, float]] = {}
    used: set[int] = set()

    # A required transition must never lose the only compatible observation to
    # an optional transition with the same label.  We therefore solve two
    # maximum-weight assignments: required references first, then optional
    # references over the remaining observations.  This is a lexicographic
    # scientific policy (required coverage, then optional explanatory fit), not
    # a dependence on transition declaration order.
    for required in (True, False):
        reference_indices = [
            index
            for index, reference in enumerate(references)
            if reference.required is required
        ]
        observed_indices = [
            index for index in range(len(observed)) if index not in used
        ]
        weights: list[list[float]] = []
        details: list[list[dict[str, float]]] = []
        for reference_index in reference_indices:
            row_weights: list[float] = []
            row_details: list[dict[str, float]] = []
            for observed_index in observed_indices:
                score, detail = transition_similarity(
                    references[reference_index], observed[observed_index]
                )
                row_weights.append(score)
                row_details.append(detail)
            weights.append(row_weights)
            details.append(row_details)
        for row, column, score in _maximum_weight_assignment(weights):
            reference_index = reference_indices[row]
            observed_index = observed_indices[column]
            reference_id = references[reference_index].transition_id
            matches[reference_id] = observed[observed_index]
            similarities[reference_id] = score
            components[reference_id] = details[row][column]
            used.add(observed_index)
    unmatched = {
        event.event_id for index, event in enumerate(observed) if index not in used
    }
    return matches, similarities, components, unmatched


def _event_fidelity(
    transitions: list[TransitionSpec],
    matches: dict[str, TraceEvent],
    similarities: dict[str, float],
    components: dict[str, dict[str, float]],
    unmatched: set[str],
    trace: DistributedTrace,
) -> dict[str, float]:
    required = [transition for transition in transitions if transition.required]
    denominator = sum(transition.weight for transition in required) or 1.0
    earned = sum(
        transition.weight * similarities.get(transition.transition_id, 0.0)
        for transition in required
    )
    by_event = {event.event_id: event for event in trace.events}
    harmful = sum(
        1.0
        for event_id in unmatched
        if by_event[event_id].payload.get("harmful")
        or (
            by_event[event_id].payload.get("high_impact")
            and by_event[event_id].status == "ok"
        )
    )
    unnecessary_writes = sum(
        1.0
        for event_id in unmatched
        if by_event[event_id].payload.get("write") and by_event[event_id].status == "ok"
    )
    redundant_reads = sum(
        1.0
        for event_id in unmatched
        if not by_event[event_id].payload.get("write")
        and by_event[event_id].kind == EventKind.ACTION
        and by_event[event_id].status == "ok"
    )
    component_scores: dict[str, float] = {}
    for key in ("argument", "spatial", "timing", "execution"):
        component_scores[key] = (
            sum(
                transition.weight
                * components.get(transition.transition_id, {}).get(key, 0.0)
                for transition in required
            )
            / denominator
        )
    return {
        "event_fidelity": max(
            0.0,
            min(
                1.0,
                (earned - HARMFUL_EXTRA_LAMBDA * harmful) / denominator,
            ),
        ),
        "coverage": sum(
            transition.weight
            for transition in required
            if transition.transition_id in matches
        )
        / denominator,
        "argument_fidelity": component_scores["argument"],
        "spatial_fidelity": component_scores["spatial"],
        "timing_fidelity": component_scores["timing"],
        "execution_fidelity": component_scores["execution"],
        "harmful_extra_cost": harmful,
        "unnecessary_write_count": unnecessary_writes,
        "redundant_read_count": redundant_reads,
    }


def _causal_conformance(
    occurrence: OccurrenceNet,
    transitions: dict[str, TransitionSpec],
    matches: dict[str, TraceEvent],
    reachability: dict[str, set[str]],
) -> tuple[float, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    for source_id, target_id in occurrence.direct_dependencies:
        if (
            transitions[source_id].actor_id == "world"
            or transitions[target_id].actor_id == "world"
        ):
            continue
        target = matches.get(target_id)
        if target is None:
            continue
        source = matches.get(source_id)
        passed = source is not None and target.event_id in reachability[source.event_id]
        checks.append(
            {
                "constraint": f"petri:{source_id}->{target_id}",
                "source": source_id,
                "target": target_id,
                "phase": transitions[target_id].phase,
                "cross_agent": transitions[source_id].actor_id
                != transitions[target_id].actor_id,
                "passed": passed,
                "weight": transitions[target_id].weight,
                "reason": "token_prerequisite",
            }
        )
    for transition_id, event in matches.items():
        transition = transitions[transition_id]
        for requirement_id in event.payload.get("violated_requirements", []):
            checks.append(
                {
                    "constraint": requirement_id,
                    "target": transition_id,
                    "phase": transition.phase,
                    "cross_agent": True,
                    "passed": False,
                    "weight": transition.weight,
                    "reason": "evidence_guard",
                }
            )
    total = sum(float(check["weight"]) for check in checks)
    passed = sum(float(check["weight"]) for check in checks if check["passed"])
    return (passed / total if total else 1.0), checks


def _partial_order_pair_agreement(
    occurrence: OccurrenceNet,
    transitions: dict[str, TransitionSpec],
    matches: dict[str, TraceEvent],
    observed_reachability: dict[str, set[str]],
) -> float:
    """Compatibility baseline over all comparable reference pairs.

    Unlike CC, this intentionally includes transitive pairs. Incomparable Petri
    transitions are excluded, so harmless concurrent swaps are never penalized.
    """
    children: defaultdict[str, set[str]] = defaultdict(set)
    for source, target in occurrence.direct_dependencies:
        children[source].add(target)
    comparable: set[tuple[str, str]] = set()
    applicable = set(occurrence.applicable_transition_ids)
    for source in applicable:
        queue = deque(children[source])
        reached: set[str] = set()
        while queue:
            target = queue.popleft()
            if target in reached:
                continue
            reached.add(target)
            queue.extend(children[target])
        for target in reached:
            if (
                transitions[source].actor_id != "world"
                and transitions[target].actor_id != "world"
            ):
                comparable.add((source, target))
    checks = []
    for source_id, target_id in comparable:
        target = matches.get(target_id)
        if target is None:
            continue
        source = matches.get(source_id)
        checks.append(
            source is not None
            and target.event_id in observed_reachability[source.event_id]
        )
    return sum(checks) / len(checks) if checks else 1.0


def _petri_token_replay_fitness(
    net: PetriNetSpec,
    occurrence: OccurrenceNet,
    matches: dict[str, TraceEvent],
    trace: DistributedTrace,
) -> dict[str, Any]:
    """Classical token-replay fitness on the committed occurrence net."""

    place_ids = {place.place_id for place in net.places}
    transitions = {
        transition.transition_id: transition for transition in net.transitions
    }
    applicable = set(occurrence.applicable_transition_ids)
    inputs: defaultdict[str, set[str]] = defaultdict(set)
    outputs: defaultdict[str, set[str]] = defaultdict(set)
    for arc in net.arcs:
        if arc.source in place_ids and arc.target in applicable:
            inputs[arc.target].add(arc.source)
        elif arc.source in applicable and arc.target in place_ids:
            outputs[arc.source].add(arc.target)
    marking = {place.place_id for place in net.places if place.initially_marked}
    produced = len(marking)
    consumed = missing = 0

    def fire(transition_id: str) -> None:
        nonlocal consumed, produced, missing
        required = inputs[transition_id]
        absent = required - marking
        missing += len(absent)
        marking.update(absent)
        consumed += len(required)
        marking.difference_update(required)
        produced += len(outputs[transition_id])
        marking.update(outputs[transition_id])

    event_order = {event.event_id: index for index, event in enumerate(trace.events)}
    for transition_id in sorted(
        matches, key=lambda identifier: event_order[matches[identifier].event_id]
    ):
        fire(transition_id)
    # World-only completion transitions are deterministic model moves, not
    # agent events, and close the occurrence after all observed prerequisites.
    progress = True
    fired_world: set[str] = set()
    while progress:
        progress = False
        for transition_id in sorted(applicable):
            if transition_id in fired_world:
                continue
            if transitions[transition_id].actor_id != "world":
                continue
            if inputs[transition_id] <= marking:
                fire(transition_id)
                fired_world.add(transition_id)
                progress = True
    terminal = {place.place_id for place in net.places if place.terminal}
    missing_final = len(terminal - marking)
    missing += missing_final
    remaining = len(marking - terminal)
    fitness = 0.5 * (1 - missing / max(1, consumed + missing_final)) + 0.5 * (
        1 - remaining / max(1, produced)
    )
    return {
        "fitness": max(0.0, min(1.0, fitness)),
        "missing_tokens": missing,
        "remaining_tokens": remaining,
        "consumed_tokens": consumed,
        "produced_tokens": produced,
        "method": "classical_token_replay_on_committed_occurrence_net",
    }


def _local_metrics(
    net: PetriNetSpec,
    occurrence: OccurrenceNet,
    trace: DistributedTrace,
    matches: dict[str, TraceEvent],
    similarities: dict[str, float],
    components: dict[str, dict[str, float]],
    unmatched: set[str],
    reachability: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    transitions = {
        transition.transition_id: transition for transition in net.transitions
    }
    local: dict[str, dict[str, Any]] = {}
    action_by_decision = {
        event.decision_context_id: event
        for event in trace.events
        if event.kind == EventKind.ACTION and event.decision_context_id
    }
    for actor_id in net.actors:
        actor_transitions = [
            transition
            for transition in net.transitions
            if transition.actor_id == actor_id
            and transition.transition_id in occurrence.applicable_transition_ids
        ]
        local_unmatched = {
            event_id
            for event_id in unmatched
            if next(
                event.actor_id for event in trace.events if event.event_id == event_id
            )
            == actor_id
        }
        ef = _event_fidelity(
            actor_transitions,
            matches,
            similarities,
            components,
            local_unmatched,
            trace,
        )
        dependency_checks: list[dict[str, Any]] = []
        for source_id, target_id in occurrence.direct_dependencies:
            if (
                transitions[source_id].actor_id != actor_id
                or transitions[target_id].actor_id != actor_id
            ):
                continue
            target = matches.get(target_id)
            if target is None:
                continue
            source = matches.get(source_id)
            dependency_checks.append(
                {
                    "passed": source is not None
                    and target.event_id in reachability[source.event_id]
                }
            )
        justified = contradicted = unknown = correct_response = obligations = 0
        for decision in trace.decisions:
            if decision.actor_id != actor_id or decision.guard is None:
                continue
            verdicts = list(decision.guard.requirement_verdicts.values())
            if not verdicts:
                continue
            obligations += 1
            action = action_by_decision.get(decision.decision_id)
            executed = action is not None and action.status not in {"blocked", "error"}
            if any(verdict == RequirementVerdict.FALSE for verdict in verdicts):
                contradicted += 1
                correct_response += int(not executed)
            elif any(verdict == RequirementVerdict.UNKNOWN for verdict in verdicts):
                unknown += 1
                correct_response += int(
                    not executed
                    and decision.guard.verdict
                    in {GuardVerdict.DEFER, GuardVerdict.BLOCK}
                )
            else:
                justified += 1
                correct_response += int(executed)
        dependency_score = (
            sum(check["passed"] for check in dependency_checks) / len(dependency_checks)
            if dependency_checks
            else 1.0
        )
        response_score = correct_response / obligations if obligations else 1.0
        local_cc = 0.5 * dependency_score + 0.5 * response_score
        local_dcore = 0.5 * ef["event_fidelity"] + 0.5 * local_cc
        local[actor_id] = {
            **{key: round(value, 6) for key, value in ef.items()},
            "causal_conformance": round(local_cc, 6),
            "local_dcore": round(local_dcore, 6),
            "locally_justified_decisions": justified,
            "locally_contradicted_decisions": contradicted,
            "locally_underdetermined_decisions": unknown,
            "epistemic_coverage": (
                round((obligations - unknown) / obligations, 6) if obligations else 1.0
            ),
            "correct_requirement_response_rate": round(response_score, 6),
            "violation_count": obligations - correct_response,
            "unknown_count": unknown,
        }
    return local


def _phase_profile(
    net: PetriNetSpec,
    occurrence: OccurrenceNet,
    trace: DistributedTrace,
    matches: dict[str, TraceEvent],
    similarities: dict[str, float],
    components: dict[str, dict[str, float]],
    unmatched: set[str],
    checks: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    profile: dict[str, dict[str, float]] = {}
    phases = list(
        dict.fromkeys(t.phase for t in net.transitions if t.actor_id != "world")
    )
    for phase in phases:
        transitions = [
            transition
            for transition in net.transitions
            if transition.phase == phase
            and transition.actor_id != "world"
            and transition.transition_id in occurrence.applicable_transition_ids
        ]
        phase_event_ids = {
            event.event_id for event in trace.events if event.season_phase == phase
        }
        ef = _event_fidelity(
            transitions,
            matches,
            similarities,
            components,
            unmatched & phase_event_ids,
            trace,
        )
        relevant_checks = [check for check in checks if check.get("phase") == phase]
        cc = (
            sum(check["passed"] * float(check["weight"]) for check in relevant_checks)
            / sum(float(check["weight"]) for check in relevant_checks)
            if relevant_checks
            else 1.0
        )
        profile[phase] = {
            "event_fidelity": round(ef["event_fidelity"], 6),
            "causal_conformance": round(cc, 6),
            "dcore_score": round(0.5 * ef["event_fidelity"] + 0.5 * cc, 6),
        }
    return profile


def _versioned_lag(trace: DistributedTrace) -> dict[str, Any]:
    observations: dict[str, float] = {}
    receives: defaultdict[str, list[float]] = defaultdict(list)
    deadlines: dict[str, float] = {}
    for event in trace.events:
        version = event.fact_version
        if event.kind == EventKind.OBSERVATION and version:
            observations[version] = event.world_time
            valid_until = event.payload.get("valid_until")
            if valid_until is not None:
                deadlines[version] = float(valid_until)
        elif event.kind == EventKind.MESSAGE_RECEIVE and version:
            receives[version].append(event.world_time)
    # Handoffs carry a new version ID; use the send's evidence observation as
    # the availability time when an identical observation-version is absent.
    sends = {
        event.fact_version: event.world_time
        for event in trace.events
        if event.kind == EventKind.MESSAGE_SEND and event.fact_version
    }
    send_deadlines = {
        event.fact_version: event.payload.get("valid_until")
        for event in trace.events
        if event.kind == EventKind.MESSAGE_SEND and event.fact_version
    }
    receive_delays = {
        event.fact_version: float(event.payload.get("transport_delay_seconds", 0.0))
        for event in trace.events
        if event.kind == EventKind.MESSAGE_RECEIVE and event.fact_version
    }
    lags = sorted(
        max(learned - sends[version], receive_delays.get(version, 0.0))
        for version, learned_times in receives.items()
        if version in sends
        for learned in learned_times
    )
    p95 = lags[min(len(lags) - 1, math.ceil(0.95 * len(lags)) - 1)] if lags else None
    normalized = [
        max(learned - sends[version], receive_delays.get(version, 0.0))
        / max(float(send_deadlines[version]) - sends[version], 1e-9)
        for version, learned_times in receives.items()
        if version in sends and send_deadlines.get(version) is not None
        for learned in learned_times
    ]
    return {
        "median": median(lags) if lags else None,
        "p95": p95,
        "max": max(lags) if lags else None,
        "count": len(lags),
        "deadline_normalized_median": median(normalized) if normalized else None,
        "deadline_normalized_max": max(normalized) if normalized else None,
        "never_received_versions": sorted(set(sends) - set(receives)),
        "observation_versions": len(observations),
    }


def _attribution(
    trace: DistributedTrace,
    failed_checks: list[dict[str, Any]],
    matches: dict[str, TraceEvent],
    transitions: dict[str, TransitionSpec],
) -> list[dict[str, Any]]:
    sends_by_phase: defaultdict[str, list[TraceEvent]] = defaultdict(list)
    receives_by_phase: defaultdict[str, list[TraceEvent]] = defaultdict(list)
    observations_by_phase: defaultdict[str, list[TraceEvent]] = defaultdict(list)
    for event in trace.events:
        phase = event.season_phase or "unknown"
        if event.kind == EventKind.MESSAGE_SEND:
            sends_by_phase[phase].append(event)
        elif event.kind == EventKind.MESSAGE_RECEIVE:
            receives_by_phase[phase].append(event)
        elif event.kind == EventKind.OBSERVATION:
            observations_by_phase[phase].append(event)
    results: list[dict[str, Any]] = []
    decisions = {decision.decision_id: decision for decision in trace.decisions}
    for event in trace.events:
        if event.kind != EventKind.ACTION:
            continue
        for requirement in event.payload.get("violated_requirements", []):
            phase = event.season_phase or "unknown"
            required_fact_key = event.payload.get("required_fact_key")
            candidate_observations = (
                [
                    observation
                    for observations in observations_by_phase.values()
                    for observation in observations
                    if observation.payload.get("fact_key") == required_fact_key
                ]
                if required_fact_key
                else observations_by_phase[phase]
            )
            observed = any(
                observation.world_time <= event.world_time
                for observation in candidate_observations
            )
            sent = any(
                s.logical_time <= event.logical_time for s in sends_by_phase[phase]
            )
            received = any(
                r.logical_time <= event.logical_time for r in receives_by_phase[phase]
            )
            envelope_types = {
                send.payload.get("envelope_type") for send in sends_by_phase[phase]
            }
            decision = decisions.get(event.decision_context_id or "")
            received_message_ids = {
                receive.message_id
                for receive in receives_by_phase[phase]
                if receive.message_id and receive.logical_time <= event.logical_time
            }
            present_in_context = bool(
                decision
                and any(
                    any(
                        item_id.startswith(f"{message_id}:claim:")
                        for item_id in decision.knowledge_snapshot.item_ids
                    )
                    for message_id in received_message_ids
                )
            )
            if not observed:
                primary = "observation_gap"
            elif not sent:
                primary = "handoff_omission"
            elif not received:
                primary = "transit_gap"
            elif "free_text" in envelope_types:
                primary = "unverifiable_handoff"
            elif received and not present_in_context:
                primary = "uptake_error"
            elif event.payload.get("unsupported_claim"):
                primary = "unsupported_claim"
            elif event.payload.get("stale_facts"):
                primary = "stale_information"
            else:
                primary = "reasoning_error"
            results.append(
                {
                    "event_id": event.event_id,
                    "transition_id": event.payload.get("transition_id"),
                    "requirement_id": requirement,
                    "phase": phase,
                    "primary": primary,
                    "observed": observed,
                    "sent": sent,
                    "received": received,
                    "present_in_decision_context": present_in_context,
                }
            )
    attributed_events = {item["event_id"] for item in results}
    for check in failed_checks:
        if check.get("reason") != "token_prerequisite":
            continue
        target = matches.get(str(check.get("target")))
        if target is None or target.event_id in attributed_events:
            continue
        source_id = str(check.get("source"))
        source = matches.get(source_id)
        source_kind = transitions[source_id].kind if source_id in transitions else None
        if source is not None:
            primary = "composition_error"
        elif source_kind == TransitionKind.OBSERVE:
            primary = "observation_gap"
        elif source_kind == TransitionKind.SEND:
            primary = "handoff_omission"
        elif source_kind == TransitionKind.RECEIVE:
            primary = "transit_gap"
        else:
            primary = "composition_error"
        results.append(
            {
                "event_id": target.event_id,
                "transition_id": check.get("target"),
                "requirement_id": check.get("constraint"),
                "phase": check.get("phase"),
                "primary": primary,
                "source_transition_id": source_id,
                "source_observed": source is not None,
            }
        )
    return results


def _baseline_metrics(
    transitions: list[TransitionSpec], trace: DistributedTrace
) -> dict[str, Any]:
    oracle = [
        {
            "tool_name": transition.action,
            "tool_args": {
                constraint.name: constraint.expected
                for constraint in transition.arguments
            },
            "op_type": "WRITE",
        }
        for transition in transitions
        if transition.required
        and transition.actor_id != "world"
        and transition.kind not in {TransitionKind.SEND, TransitionKind.RECEIVE}
    ]
    observed_events = [
        event
        for event in trace.events
        if event.kind == EventKind.ACTION and event.status != "blocked"
    ]
    agent = [
        {"tool_name": event.action, "tool_args": event.args, "op_type": "WRITE"}
        for event in observed_events
    ]
    workflow = evaluate_workflows(oracle, agent)
    local_workflows: dict[str, Any] = {}
    for actor_id in trace.actors:
        local_oracle = [
            {
                "tool_name": transition.action,
                "tool_args": {
                    constraint.name: constraint.expected
                    for constraint in transition.arguments
                },
                "op_type": "WRITE",
            }
            for transition in transitions
            if transition.actor_id == actor_id
            and transition.required
            and transition.kind not in {TransitionKind.SEND, TransitionKind.RECEIVE}
        ]
        local_agent = [
            {
                "tool_name": event.action,
                "tool_args": event.args,
                "op_type": "WRITE",
            }
            for event in observed_events
            if event.actor_id == actor_id
        ]
        local_workflows[actor_id] = evaluate_workflows(local_oracle, local_agent)
    oracle_signatures = {
        (item["tool_name"], repr(sorted(item["tool_args"].items()))) for item in oracle
    }
    agent_signatures = {
        (item["tool_name"], repr(sorted(item["tool_args"].items()))) for item in agent
    }
    bfcl = len(oracle_signatures & agent_signatures) / max(1, len(oracle_signatures))
    average_local = (
        sum(float(result.get("combined", 0.0)) for result in local_workflows.values())
        / len(local_workflows)
        if local_workflows
        else None
    )
    return {
        "bfcl_tool_success": bfcl,
        "merged_pc_ktc": workflow,
        "core_path_correctness": workflow.get("path_correctness"),
        "local_pc_ktc": local_workflows,
        "average_local_pc_ktc": average_local,
    }


def evaluate_farm_dcore(
    net: PetriNetSpec,
    trace: DistributedTrace,
    *,
    world_context: dict[str, Any] | None = None,
    committed_branches: dict[str, str] | None = None,
) -> dict[str, Any]:
    occurrence = unfold_petri_net(
        net,
        world_context=world_context,
        world_fingerprint=stable_world_fingerprint(trace),
        committed_branches=committed_branches,
    )
    transition_map = {
        transition.transition_id: transition.model_copy(update={"required": True})
        if transition.transition_id in occurrence.conditionally_required_transition_ids
        else transition
        for transition in net.transitions
    }
    effective_net = net.model_copy(
        update={"transitions": tuple(transition_map.values())}
    )
    applicable_transitions = [
        transition
        for transition in effective_net.transitions
        if transition.transition_id in occurrence.applicable_transition_ids
        and transition.actor_id != "world"
    ]
    matches, similarities, components, unmatched = _match(
        effective_net, occurrence, trace
    )
    fidelity = _event_fidelity(
        applicable_transitions, matches, similarities, components, unmatched, trace
    )
    reachability = _reachability(trace)
    cc, checks = _causal_conformance(occurrence, transition_map, matches, reachability)
    po_pair_agreement = _partial_order_pair_agreement(
        occurrence, transition_map, matches, reachability
    )
    dcore = 0.5 * fidelity["event_fidelity"] + 0.5 * cc
    local = _local_metrics(
        effective_net,
        occurrence,
        trace,
        matches,
        similarities,
        components,
        unmatched,
        reachability,
    )
    mean_local = (
        sum(item["local_dcore"] for item in local.values()) / len(local)
        if local
        else 0.0
    )
    cross_checks = [check for check in checks if check.get("cross_agent")]
    coordination_failure_rate = (
        sum(not check["passed"] for check in cross_checks) / len(cross_checks)
        if cross_checks
        else 0.0
    )
    missing = [
        transition.transition_id
        for transition in applicable_transitions
        if transition.required and transition.transition_id not in matches
    ]
    parents: defaultdict[str, set[str]] = defaultdict(set)
    for source_id, target_id in occurrence.direct_dependencies:
        parents[target_id].add(source_id)
    depth_cache: dict[str, int] = {}

    def causal_depth(transition_id: str) -> int:
        if transition_id not in depth_cache:
            depth_cache[transition_id] = 1 + max(
                (causal_depth(parent) for parent in parents[transition_id]),
                default=-1,
            )
        return depth_cache[transition_id]

    max_depth = max(
        (
            causal_depth(identifier)
            for identifier in occurrence.applicable_transition_ids
        ),
        default=0,
    )
    failed_checks = [check for check in checks if not check["passed"]]
    token_checks = [
        check for check in checks if check.get("reason") == "token_prerequisite"
    ]
    token_fitness = (
        sum(float(check["weight"]) for check in token_checks if check["passed"])
        / sum(float(check["weight"]) for check in token_checks)
        if token_checks
        else 1.0
    )
    token_replay = _petri_token_replay_fitness(
        effective_net, occurrence, matches, trace
    )
    first_divergence = None
    divergence_candidates = [
        {
            "transition_id": transition_id,
            "reason": "missing_required_transition",
            "constraint": None,
        }
        for transition_id in missing
    ] + [
        {
            "transition_id": str(check["target"]),
            "reason": check["reason"],
            "constraint": check["constraint"],
        }
        for check in failed_checks
    ]
    if divergence_candidates:
        chosen_divergence = min(
            divergence_candidates,
            key=lambda item: (
                causal_depth(item["transition_id"]),
                item["transition_id"],
                item["reason"],
            ),
        )
        first = chosen_divergence["transition_id"]
        depth = causal_depth(first)
        first_divergence = {
            "transition_id": first,
            "phase": transition_map[first].phase,
            "causal_depth": depth,
            "prefix_criticality": 1.0 - depth / max(1, max_depth),
            "reason": chosen_divergence["reason"],
            "constraint": chosen_divergence["constraint"],
        }
    propagation_depth = 0
    propagation_affected = 0
    potential_downstream_reach = 0
    propagation_duration = 0.0
    divergence_targets = set(missing) | {
        str(check.get("target")) for check in failed_checks if check.get("target")
    }
    if divergence_targets:
        targets = divergence_targets
        matched_targets = [matches[target] for target in targets if target in matches]
        if matched_targets:
            first_time = min(event.world_time for event in matched_targets)
            last_time = max(
                (
                    event.world_time
                    for event in trace.events
                    if event.kind == EventKind.ACTION
                ),
                default=first_time,
            )
            propagation_duration = max(0.0, (last_time - first_time) / 86400.0)
        children: defaultdict[str, set[str]] = defaultdict(set)
        for source, target in occurrence.direct_dependencies:
            children[source].add(target)
        reached: set[str] = set()
        distances = {target: 0 for target in targets}
        queue = deque(targets)
        while queue:
            node = queue.popleft()
            for child in children[node]:
                if child not in reached:
                    reached.add(child)
                    distances[child] = distances[node] + 1
                    queue.append(child)
        potential_downstream_reach = len(reached)
        observed_affected = set(missing) | {
            str(check.get("target")) for check in failed_checks if check.get("target")
        }
        observed_affected.update(
            transition_id
            for transition_id, event in matches.items()
            if event.status == "error" or event.payload.get("harmful")
        )
        downstream_affected = (observed_affected & reached) - targets
        propagation_affected = len(downstream_affected)
        propagation_depth = max(
            (distances[transition_id] for transition_id in downstream_affected),
            default=0,
        )
    baselines = _baseline_metrics(applicable_transitions, trace)
    recovery_latencies = [
        float(value) for value in trace.outcome.get("recovery_latencies_seconds", [])
    ]
    result: dict[str, Any] = {
        "metric_version": METRIC_VERSION,
        **{key: round(value, 6) for key, value in fidelity.items()},
        "causal_conformance": round(cc, 6),
        "dcore_score": round(dcore, 6),
        "dcore_sensitivity": {
            str(alpha): round(alpha * fidelity["event_fidelity"] + (1 - alpha) * cc, 6)
            for alpha in (0.25, 0.5, 0.75)
        },
        "local": local,
        "local_global_gap": round(mean_local - dcore, 6),
        # UNKNOWN is an epistemic state, not itself an error. A locally correct
        # agent may defer, reobserve, or block under UNKNOWN; violation_count
        # already records whether its response satisfied that obligation.
        "all_agents_locally_correct": all(
            item["violation_count"] == 0 for item in local.values()
        ),
        "coordination_failure_rate": round(coordination_failure_rate, 6),
        "constraint_detail": checks,
        "phase_profile": _phase_profile(
            effective_net,
            occurrence,
            trace,
            matches,
            similarities,
            components,
            unmatched,
            checks,
        ),
        "first_critical_divergence": first_divergence,
        "error_propagation_depth": propagation_depth,
        "error_propagation_affected_transitions": propagation_affected,
        "potential_downstream_reach": potential_downstream_reach,
        "error_propagation_duration_days": round(propagation_duration, 6),
        "recovery": {
            "blocked": int(trace.outcome.get("blocked_write_count", 0)),
            "deferred": int(trace.outcome.get("deferred_write_count", 0)),
            "recovered": int(trace.outcome.get("recovered_write_count", 0)),
            "successful_replanning_rate": (
                int(trace.outcome.get("recovered_write_count", 0))
                / max(
                    1,
                    int(
                        trace.outcome.get(
                            "blocked_intent_count",
                            trace.outcome.get("blocked_write_count", 0),
                        )
                    ),
                )
            ),
            "latency_seconds_median": (
                median(recovery_latencies) if recovery_latencies else None
            ),
            "latency_seconds_max": (
                max(recovery_latencies) if recovery_latencies else None
            ),
        },
        "synchronization_lag": _versioned_lag(trace),
        "attribution": _attribution(trace, failed_checks, matches, transition_map),
        "matched_transitions": {
            transition_id: {
                "observed_event_id": event.event_id,
                "similarity": round(similarities[transition_id], 6),
            }
            for transition_id, event in matches.items()
        },
        "missing_required_transitions": missing,
        "unmatched_observed_events": sorted(unmatched),
        "po_pair_agreement": round(po_pair_agreement, 6),
        "petri_token_fitness": round(token_replay["fitness"], 6),
        "petri_token_replay": token_replay,
        "direct_dependency_fitness": round(token_fitness, 6),
        "petri_alignment_fitness": round(
            1.0
            - (len(missing) + len(unmatched))
            / max(1, len(applicable_transitions) + len(matches) + len(unmatched)),
            6,
        ),
        "petri_alignment_method": "unit_cost_event_alignment_proxy",
        "bfcl_tool_success": round(baselines["bfcl_tool_success"], 6),
        "merged_pc_ktc": baselines["merged_pc_ktc"],
        "core_path_correctness": baselines["core_path_correctness"],
        "local_pc_ktc": baselines["local_pc_ktc"],
        "average_local_pc_ktc": baselines["average_local_pc_ktc"],
        "final_success": trace.outcome.get("success"),
        "downstream_outcome": trace.outcome,
        "occurrence_net": occurrence.model_dump(mode="json"),
    }
    return result


def stable_world_fingerprint(trace: DistributedTrace) -> str:
    from are.simulation.distributed.models import stable_digest

    return stable_digest(
        {
            "scenario": trace.configuration.get("scenario_id"),
            "world_seed": trace.configuration.get("world_seed"),
            "world_events": [
                {
                    "action": event.action,
                    "world_time": event.world_time,
                    "payload": event.payload,
                }
                for event in trace.events
                if event.kind == EventKind.WORLD_EFFECT
            ],
        }
    )
