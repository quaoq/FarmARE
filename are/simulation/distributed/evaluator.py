"""D-CORE local-to-global metrics and information-path attribution."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from statistics import median
from typing import Any

from are.simulation.distributed.models import (
    DistributedTaskSpec,
    DistributedTrace,
    EventKind,
    ReferenceEventSpec,
    RequirementVerdict,
    TraceEvent,
)
from are.simulation.scenarios.workflow_validation import evaluate_workflows


def _value_similarity(expected: Any, actual: Any) -> float:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        scale = max(abs(float(expected)), 1.0)
        return max(0.0, 1.0 - abs(float(expected) - float(actual)) / scale)
    return 1.0 if expected == actual else 0.0


def _scope_similarity(expected: Any, actual: Any) -> float:
    if expected is None:
        return 1.0
    if actual is None:
        return 0.0
    if isinstance(expected, (tuple, list)) and isinstance(actual, (tuple, list)):
        expected_set = set(range(int(expected[0]), int(expected[1]) + 1))
        actual_set = set(range(int(actual[0]), int(actual[1]) + 1))
        return len(expected_set & actual_set) / max(1, len(expected_set | actual_set))
    return 1.0 if expected == actual else 0.0


def event_similarity(reference: ReferenceEventSpec, observed: TraceEvent) -> float:
    if observed.kind != EventKind.ACTION:
        return 0.0
    if reference.actor_id != observed.actor_id or reference.action != observed.action:
        return 0.0
    components = [1.0]
    for key, expected in reference.args.items():
        components.append(_value_similarity(expected, observed.args.get(key)))
    components.append(_scope_similarity(reference.scope, observed.payload.get("scope")))
    if reference.window_start is not None:
        components.append(1.0 if observed.world_time >= reference.window_start else 0.0)
    if reference.window_end is not None:
        components.append(1.0 if observed.world_time <= reference.window_end else 0.0)
    return sum(components) / len(components)


def _maximum_weight_assignment(
    weights: list[list[float]],
) -> list[tuple[int, int, float]]:
    """Pure-Python Hungarian assignment, padded to a square matrix."""
    if not weights or not weights[0]:
        return []
    rows, columns = len(weights), len(weights[0])
    size = max(rows, columns)
    cost = [[1.0] * size for _ in range(size)]
    for row in range(rows):
        for column in range(columns):
            cost[row][column] = 1.0 - weights[row][column]

    u = [0.0] * (size + 1)
    v = [0.0] * (size + 1)
    p = [0] * (size + 1)
    way = [0] * (size + 1)
    for i in range(1, size + 1):
        p[0] = i
        j0 = 0
        minv = [math.inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = math.inf
            j1 = 0
            for j in range(1, size + 1):
                if used[j]:
                    continue
                current = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if current < minv[j]:
                    minv[j] = current
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment: list[tuple[int, int, float]] = []
    for column in range(1, size + 1):
        row = p[column] - 1
        col = column - 1
        if row < rows and col < columns and weights[row][col] > 0:
            assignment.append((row, col, weights[row][col]))
    return sorted(assignment)


def match_events(
    spec: DistributedTaskSpec, trace: DistributedTrace
) -> tuple[dict[str, TraceEvent], dict[str, float], set[str]]:
    references = [event for event in spec.events if not event.harmful]
    observed = [event for event in trace.events if event.kind == EventKind.ACTION]
    weights = [
        [event_similarity(reference, event) for event in observed]
        for reference in references
    ]
    assignment = _maximum_weight_assignment(weights)
    matches: dict[str, TraceEvent] = {}
    similarities: dict[str, float] = {}
    observed_indices: set[int] = set()
    for reference_index, observed_index, similarity in assignment:
        matches[references[reference_index].event_id] = observed[observed_index]
        similarities[references[reference_index].event_id] = similarity
        observed_indices.add(observed_index)
    unmatched = {
        event.event_id
        for index, event in enumerate(observed)
        if index not in observed_indices
    }
    return matches, similarities, unmatched


def _observed_reachability(trace: DistributedTrace) -> dict[str, set[str]]:
    children: defaultdict[str, set[str]] = defaultdict(set)
    events = list(trace.events)
    previous: dict[str, str] = {}
    sends: dict[str, str] = {}
    for event in events:
        for parent in event.causal_parents:
            children[parent].add(event.event_id)
        if event.actor_id in previous:
            children[previous[event.actor_id]].add(event.event_id)
        previous[event.actor_id] = event.event_id
        if event.kind == EventKind.MESSAGE_SEND and event.message_id:
            sends[event.message_id] = event.event_id
        if event.kind == EventKind.MESSAGE_RECEIVE and event.message_id in sends:
            children[sends[event.message_id]].add(event.event_id)
    reachability: dict[str, set[str]] = {}
    for event in events:
        reached: set[str] = set()
        queue = deque(children[event.event_id])
        while queue:
            node = queue.popleft()
            if node in reached:
                continue
            reached.add(node)
            queue.extend(children[node])
        reachability[event.event_id] = reached
    return reachability


def _reference_reachability(spec: DistributedTaskSpec) -> dict[str, set[str]]:
    children: defaultdict[str, set[str]] = defaultdict(set)
    for edge in spec.causal_edges:
        children[edge.source].add(edge.target)
    reachability: dict[str, set[str]] = {}
    for event in spec.events:
        reached: set[str] = set()
        queue = deque(children[event.event_id])
        while queue:
            node = queue.popleft()
            if node in reached:
                continue
            reached.add(node)
            queue.extend(children[node])
        reachability[event.event_id] = reached
    return reachability


def _event_fidelity(
    spec: DistributedTaskSpec,
    trace: DistributedTrace,
    matches: dict[str, TraceEvent],
    similarities: dict[str, float],
    unmatched: set[str],
    harm_lambda: float,
    actor_id: str | None = None,
) -> tuple[float, float, float]:
    references = [
        event
        for event in spec.events
        if not event.harmful and (actor_id is None or event.actor_id == actor_id)
    ]
    denominator = sum(event.weight for event in references if event.required)
    earned = sum(
        event.weight * similarities.get(event.event_id, 0.0)
        for event in references
        if event.required
    )
    coverage = (
        sum(
            event.weight
            for event in references
            if event.required and event.event_id in matches
        )
        / denominator
        if denominator
        else 1.0
    )
    harmful_specs = [event for event in spec.events if event.harmful]
    by_event = {event.event_id: event for event in trace.events}
    harmful_cost = 0.0
    for event_id in unmatched:
        event = by_event[event_id]
        if actor_id is not None and event.actor_id != actor_id:
            continue
        candidates = [
            reference.weight * event_similarity(reference, event)
            for reference in harmful_specs
        ]
        harmful_cost += max(
            candidates, default=1.0 if event.payload.get("harmful") else 0.0
        )
    score = (
        max(0.0, min(1.0, (earned - harm_lambda * harmful_cost) / denominator))
        if denominator
        else 1.0
    )
    return score, coverage, harmful_cost


def _causal_conformance(
    spec: DistributedTaskSpec,
    trace: DistributedTrace,
    matches: dict[str, TraceEvent],
    observed_reachability: dict[str, set[str]],
    actor_id: str | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    events_by_id = {event.event_id: event for event in spec.events}
    for edge in spec.causal_edges:
        if actor_id is not None and not (
            events_by_id[edge.source].actor_id == actor_id
            and events_by_id[edge.target].actor_id == actor_id
        ):
            continue
        target = matches.get(edge.target)
        if target is None:
            continue
        source = matches.get(edge.source)
        passed = (
            source is not None
            and target.event_id in observed_reachability[source.event_id]
        )
        checks.append(
            {
                "constraint": f"{edge.source}->{edge.target}",
                "passed": passed,
                "weight": edge.weight,
                "reason": edge.reason,
            }
        )
    for conflict in spec.conflict_sets:
        if actor_id is not None:
            relevant = [
                event_id
                for event_id in conflict.event_ids
                if events_by_id[event_id].actor_id == actor_id
            ]
        else:
            relevant = list(conflict.event_ids)
        present = [event_id for event_id in relevant if event_id in matches]
        if len(present) > 1:
            checks.append(
                {
                    "constraint": f"conflict:{','.join(conflict.event_ids)}",
                    "passed": False,
                    "weight": conflict.weight,
                    "reason": "conflict",
                }
            )
    decisions = {decision.decision_id: decision for decision in trace.decisions}
    for reference_id, observed in matches.items():
        reference = events_by_id[reference_id]
        if actor_id is not None and reference.actor_id != actor_id:
            continue
        relevant = [
            requirement
            for requirement in spec.fact_requirements
            if requirement.actor_id == reference.actor_id
            and requirement.action == reference.action
        ]
        for requirement in relevant:
            if actor_id is None:
                passed = requirement.requirement_id not in observed.payload.get(
                    "violated_requirements", []
                )
                verdict = "global"
            else:
                decision = decisions.get(observed.decision_context_id or "")
                local_verdict = (
                    decision.guard.requirement_verdicts.get(requirement.requirement_id)
                    if decision is not None and decision.guard is not None
                    else RequirementVerdict.UNKNOWN
                )
                passed = local_verdict == RequirementVerdict.TRUE
                verdict = local_verdict.value
            checks.append(
                {
                    "constraint": f"fact:{requirement.requirement_id}@{reference_id}",
                    "passed": passed,
                    "weight": 1.0,
                    "reason": "fact_requirement",
                    "verdict": verdict,
                }
            )
    total = sum(check["weight"] for check in checks)
    passed = sum(check["weight"] for check in checks if check["passed"])
    return (passed / total if total else 1.0), checks


def _po_pair_agreement(
    spec: DistributedTaskSpec,
    matches: dict[str, TraceEvent],
    observed_reachability: dict[str, set[str]],
) -> float:
    reference_reachability = _reference_reachability(spec)
    comparable = []
    for source, targets in reference_reachability.items():
        for target in targets:
            if source in matches and target in matches:
                comparable.append((source, target))
    if not comparable:
        return 1.0
    passed = sum(
        matches[target].event_id in observed_reachability[matches[source].event_id]
        for source, target in comparable
    )
    return passed / len(comparable)


def _workflow_baseline(
    references: list[ReferenceEventSpec], observed: list[TraceEvent]
) -> dict[str, float]:
    oracle = [
        {
            "tool_name": event.action,
            "tool_args": event.args,
            "op_type": "WRITE",
        }
        for event in references
        if event.required and not event.harmful
    ]
    agent = [
        {
            "tool_name": event.action,
            "tool_args": event.args,
            "op_type": "WRITE",
        }
        for event in observed
        if event.kind == EventKind.ACTION
    ]
    return evaluate_workflows(oracle, agent)


def _local_scores(
    spec: DistributedTaskSpec,
    trace: DistributedTrace,
    matches: dict[str, TraceEvent],
    similarities: dict[str, float],
    unmatched: set[str],
    reachability: dict[str, set[str]],
    harm_lambda: float,
    alpha: float,
) -> dict[str, dict[str, Any]]:
    local: dict[str, dict[str, Any]] = {}
    for actor in spec.actors:
        ef, coverage, harmful = _event_fidelity(
            spec, trace, matches, similarities, unmatched, harm_lambda, actor.actor_id
        )
        cc, checks = _causal_conformance(
            spec, trace, matches, reachability, actor.actor_id
        )
        verdicts: list[RequirementVerdict] = []
        for decision in trace.decisions:
            if decision.actor_id == actor.actor_id and decision.guard is not None:
                verdicts.extend(decision.guard.requirement_verdicts.values())
        unknown = sum(verdict == RequirementVerdict.UNKNOWN for verdict in verdicts)
        false = sum(verdict == RequirementVerdict.FALSE for verdict in verdicts)
        failed_checks = sum(not check["passed"] for check in checks)
        local[actor.actor_id] = {
            "event_fidelity": ef,
            "coverage": coverage,
            "causal_conformance": cc,
            "effective_local_score": alpha * ef + (1 - alpha) * cc,
            "unknown_count": unknown,
            # A false fact verdict generally induces the matching failed fact check;
            # use the larger count so one failed requirement is not counted twice.
            "violation_count": max(false, failed_checks),
            "unknown_rate": unknown / len(verdicts) if verdicts else 0.0,
            "harmful_extra_cost": harmful,
        }
    return local


def _sync_lag_metrics(trace: DistributedTrace) -> dict[str, Any]:
    observations: dict[str, float] = {}
    deadlines: dict[str, float] = {}
    learns: defaultdict[str, list[float]] = defaultdict(list)
    never_received: set[str] = set()
    for event in trace.events:
        fact_keys = event.payload.get("fact_keys", [])
        if event.payload.get("fact_key"):
            fact_keys = [event.payload["fact_key"], *fact_keys]
        if event.kind == EventKind.OBSERVATION:
            for fact_key in fact_keys:
                observations[fact_key] = min(
                    observations.get(fact_key, event.world_time), event.world_time
                )
                valid_until = event.payload.get("valid_until")
                if valid_until is not None:
                    deadlines[fact_key] = min(
                        deadlines.get(fact_key, float(valid_until)),
                        float(valid_until),
                    )
        if event.kind == EventKind.MESSAGE_RECEIVE:
            for fact_key in fact_keys:
                learns[fact_key].append(event.logical_time)
        if event.kind == EventKind.MESSAGE_SEND and event.status == "dropped":
            never_received.update(fact_keys)
    lags = sorted(
        learned - observations[fact]
        for fact, times in learns.items()
        if fact in observations
        for learned in times
    )
    p95 = lags[min(len(lags) - 1, math.ceil(0.95 * len(lags)) - 1)] if lags else None
    return {
        "median": median(lags) if lags else None,
        "p95": p95,
        "max": max(lags) if lags else None,
        "count": len(lags),
        "deadline_weighted_lag": (
            sum(
                max(0.0, learned - observations[fact])
                / max(1e-9, deadlines[fact] - observations[fact])
                for fact, times in learns.items()
                if fact in observations and fact in deadlines
                for learned in times
            )
            / sum(
                len(times)
                for fact, times in learns.items()
                if fact in observations and fact in deadlines
            )
            if any(
                fact in observations and fact in deadlines and times
                for fact, times in learns.items()
            )
            else None
        ),
        "never_received": sorted(never_received),
    }


def attribute_failures(
    spec: DistributedTaskSpec,
    trace: DistributedTrace,
    matches: dict[str, TraceEvent],
) -> list[dict[str, Any]]:
    attributions: list[dict[str, Any]] = []
    requirements = {
        requirement.requirement_id: requirement
        for requirement in spec.fact_requirements
    }
    observations: defaultdict[str, list[TraceEvent]] = defaultdict(list)
    sends: defaultdict[str, list[TraceEvent]] = defaultdict(list)
    receives: defaultdict[str, list[TraceEvent]] = defaultdict(list)
    free_text_sends: list[TraceEvent] = []
    for event in trace.events:
        fact_keys = set(event.payload.get("fact_keys", []))
        fact_key = event.payload.get("fact_key")
        if fact_key:
            fact_keys.add(fact_key)
        for key in fact_keys:
            if event.kind == EventKind.OBSERVATION:
                observations[key].append(event)
            elif event.kind == EventKind.MESSAGE_SEND:
                sends[key].append(event)
            elif event.kind == EventKind.MESSAGE_RECEIVE:
                receives[key].append(event)
        if (
            event.kind == EventKind.MESSAGE_SEND
            and event.payload.get("envelope_type") == "free_text"
        ):
            free_text_sends.append(event)
    decisions = {decision.decision_id: decision for decision in trace.decisions}
    for event in trace.events:
        if event.kind != EventKind.ACTION:
            continue
        for requirement_id in event.payload.get("violated_requirements", []):
            requirement = requirements.get(requirement_id)
            if requirement is None:
                continue
            fact = requirement.fact_key
            observable = any(fact in actor.observable_facts for actor in spec.actors)
            relevant_observations = [
                observation
                for observation in observations[fact]
                if observation.payload.get("value") != requirement.expected_value
            ]
            observed = bool(relevant_observations)
            sent_before = [
                send
                for send in sends[fact]
                if send.logical_time <= event.logical_time
                and fact in send.payload.get("claim_values", {})
                and send.payload["claim_values"][fact] != requirement.expected_value
            ]
            received_before = [
                receive
                for receive in receives[fact]
                if receive.actor_id == event.actor_id
                and receive.logical_time <= event.logical_time
                and fact in receive.payload.get("claim_values", {})
                and receive.payload["claim_values"][fact] != requirement.expected_value
            ]
            decision = decisions.get(event.decision_context_id or "")
            in_context = False
            if decision is not None:
                received_ids = {receive.message_id for receive in received_before}
                in_context = any(
                    item_id.split(":claim:", 1)[0] in received_ids
                    for item_id in decision.knowledge_snapshot.item_ids
                    if ":claim:" in item_id
                )
            if not observable:
                primary = "observation_gap"
            elif not observed:
                primary = "observation_gap"
            elif not sent_before:
                has_unverifiable = any(
                    send.logical_time <= event.logical_time
                    and send.payload.get("recipient") == event.actor_id
                    for send in free_text_sends
                )
                primary = (
                    "unverifiable_handoff" if has_unverifiable else "handoff_omission"
                )
            elif not received_before:
                primary = "transit_gap"
            elif not in_context:
                primary = "uptake_error"
            elif event.payload.get("unsupported_claim"):
                primary = "unsupported_claim"
            elif (
                event.payload.get("stale_facts")
                and fact in event.payload["stale_facts"]
            ):
                primary = "stale_information"
            else:
                primary = "reasoning_error"
            attributions.append(
                {
                    "event_id": event.event_id,
                    "requirement_id": requirement_id,
                    "fact_key": fact,
                    "primary": primary,
                    "observed": observed,
                    "sent": bool(sent_before),
                    "received": bool(received_before),
                    "in_decision_context": in_context,
                }
            )
    reachability = _observed_reachability(trace)
    event_actor = {event.event_id: event.actor_id for event in spec.events}
    already_attributed = {item["event_id"] for item in attributions}
    for edge in spec.causal_edges:
        source = matches.get(edge.source)
        target = matches.get(edge.target)
        if source is None or target is None or target.event_id in already_attributed:
            continue
        if (
            target.event_id not in reachability[source.event_id]
            and event_actor[edge.source] != event_actor[edge.target]
        ):
            attributions.append(
                {
                    "event_id": target.event_id,
                    "constraint": f"{edge.source}->{edge.target}",
                    "primary": "composition_error",
                }
            )
    return attributions


def evaluate_dcore(
    spec: DistributedTaskSpec,
    trace: DistributedTrace,
    *,
    alpha: float = 0.5,
    harm_lambda: float = 0.25,
) -> dict[str, Any]:
    if not 0 <= alpha <= 1:
        raise ValueError("alpha must be between zero and one")
    matches, similarities, unmatched = match_events(spec, trace)
    reachability = _observed_reachability(trace)
    event_fidelity, coverage, harmful_cost = _event_fidelity(
        spec, trace, matches, similarities, unmatched, harm_lambda
    )
    causal_conformance, constraint_detail = _causal_conformance(
        spec, trace, matches, reachability
    )
    dcore_score = alpha * event_fidelity + (1 - alpha) * causal_conformance
    local = _local_scores(
        spec,
        trace,
        matches,
        similarities,
        unmatched,
        reachability,
        harm_lambda,
        alpha,
    )
    mean_local = (
        sum(values["effective_local_score"] for values in local.values()) / len(local)
        if local
        else 0.0
    )
    observed_actions = [
        event for event in trace.events if event.kind == EventKind.ACTION
    ]
    merged_baseline = _workflow_baseline(list(spec.events), observed_actions)
    local_baselines = {
        actor.actor_id: _workflow_baseline(
            [event for event in spec.events if event.actor_id == actor.actor_id],
            [event for event in observed_actions if event.actor_id == actor.actor_id],
        )
        for actor in spec.actors
    }
    average_local_pc_ktc = (
        sum(baseline["combined"] for baseline in local_baselines.values())
        / len(local_baselines)
        if local_baselines
        else 0.0
    )
    result = {
        "event_fidelity": round(event_fidelity, 6),
        "coverage": round(coverage, 6),
        "harmful_extra_cost": round(harmful_cost, 6),
        "causal_conformance": round(causal_conformance, 6),
        "po_pair_agreement": round(_po_pair_agreement(spec, matches, reachability), 6),
        "dcore_score": round(dcore_score, 6),
        "local_global_gap": round(mean_local - dcore_score, 6),
        "local": local,
        "matched_events": {
            reference_id: {
                "observed_event_id": event.event_id,
                "similarity": round(similarities[reference_id], 6),
            }
            for reference_id, event in matches.items()
        },
        "unmatched_observed_events": sorted(unmatched),
        "constraint_detail": constraint_detail,
        "synchronization_lag": _sync_lag_metrics(trace),
        "merged_pc_ktc": merged_baseline,
        "local_pc_ktc": local_baselines,
        "average_local_pc_ktc": round(average_local_pc_ktc, 6),
        "all_agents_locally_correct": all(
            values["violation_count"] == 0 for values in local.values()
        ),
        "final_success": trace.outcome.get("success"),
        "downstream_outcome": trace.outcome,
    }
    result["attribution"] = attribute_failures(spec, trace, matches)
    return result
