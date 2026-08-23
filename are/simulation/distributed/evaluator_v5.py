"""Farm D-CORE v5 scientific evaluator.

All normative conclusions are reconstructed from the frozen process spec,
authoritative FarmARE fact versions, pre-decision information snapshots, and
observed provenance. Runtime guard, branch, transition, harmful-action, and
policy verdicts are treated only as audit records.
"""

from __future__ import annotations

from collections import defaultdict, deque
from statistics import mean, pstdev
from typing import Any, Iterable

from are.simulation.distributed.evaluator import _maximum_weight_assignment
from are.simulation.distributed.models import (
    DistributedTrace,
    EventKind,
    FactVersionRecord,
    IntentKind,
    TraceEvent,
    stable_digest,
)
from are.simulation.distributed.petri import (
    DataGuardSpec,
    FactDefinitionSpec,
    GuardOperator,
    PetriNetSpec,
    ScoringClass,
    TransitionKind,
    TransitionSpec,
    WorldBranchSpec,
    unfold_petri_net,
)
from are.simulation.distributed.scientific_v5 import (
    FarmProcessSpecV5,
    InformationPolicySpecV5,
    TransitionAcceptanceSpec,
    select_policy_rule,
)

METRIC_VERSION = "dcore_eval_v5"


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


def _scope_iou(expected: Any, actual: Any) -> float:
    if expected is None:
        return 1.0
    if actual is None:
        return 0.0
    if isinstance(expected, (tuple, list)) and isinstance(actual, (tuple, list)):
        expected_ids = set(range(int(expected[0]), int(expected[1]) + 1))
        actual_ids = set(range(int(actual[0]), int(actual[1]) + 1))
        return len(expected_ids & actual_ids) / max(1, len(expected_ids | actual_ids))
    return float(expected == actual)


def _scope_covers(actual: Any, required: Any) -> bool:
    if required is None:
        return True
    if actual is None:
        return False
    if isinstance(actual, (tuple, list)) and isinstance(required, (tuple, list)):
        return int(actual[0]) <= int(required[0]) and int(actual[1]) >= int(required[1])
    return actual == required


def _event_kind(transition: TransitionSpec) -> EventKind:
    if transition.kind == TransitionKind.SEND:
        return EventKind.MESSAGE_SEND
    if transition.kind == TransitionKind.RECEIVE:
        return EventKind.MESSAGE_RECEIVE
    return EventKind.ACTION


def _argument_acceptance(
    transition: TransitionSpec,
    acceptance: TransitionAcceptanceSpec,
    event: TraceEvent,
) -> tuple[bool, list[dict[str, Any]]]:
    rows = []
    for constraint in acceptance.arguments:
        actual = event.args.get(constraint.name)
        if constraint.tolerance is not None:
            passed = (
                isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and abs(float(actual) - float(constraint.expected))
                <= constraint.tolerance
            )
        else:
            passed = actual == constraint.expected
        rows.append(
            {
                "name": constraint.name,
                "expected": constraint.expected,
                "actual": actual,
                "tolerance": constraint.tolerance,
                "critical": constraint.critical,
                "passed": passed,
            }
        )
    return all(item["passed"] for item in rows), rows


def _acceptance_components(
    transition: TransitionSpec,
    acceptance: TransitionAcceptanceSpec,
    event: TraceEvent,
) -> dict[str, Any]:
    arguments, argument_rows = _argument_acceptance(transition, acceptance, event)
    scope_iou = _scope_iou(transition.scope, event.payload.get("scope"))
    spatial = scope_iou >= acceptance.scope_iou_threshold
    timing = not (
        (
            acceptance.window_start is not None
            and event.world_time < acceptance.window_start
        )
        or (
            acceptance.window_end is not None
            and event.world_time > acceptance.window_end
        )
    )
    execution = event.status in acceptance.accepted_statuses
    return {
        "arguments": arguments,
        "argument_checks": argument_rows,
        "scope": spatial,
        "scope_iou": scope_iou,
        "timing": timing,
        "execution": execution,
        "fully_conforming": arguments and spatial and timing and execution,
    }


def _match_events(
    process: FarmProcessSpecV5,
    applicable_ids: set[str],
    trace: DistributedTrace,
) -> tuple[dict[str, TraceEvent], dict[str, dict[str, Any]], set[str]]:
    net = process.occurrence_net
    acceptance = {item.transition_id: item for item in process.acceptance}
    references = [
        item
        for item in net.transitions
        if item.transition_id in applicable_ids and item.actor_id != "world"
    ]
    observed = [
        event
        for event in trace.events
        if event.kind
        in {EventKind.ACTION, EventKind.MESSAGE_SEND, EventKind.MESSAGE_RECEIVE}
        and event.status not in {"blocked", "deferred", "duplicate"}
    ]
    matches: dict[str, TraceEvent] = {}
    details: dict[str, dict[str, Any]] = {}
    used: set[int] = set()
    reference_rank = {}
    observed_rank = {}
    counters: defaultdict[tuple[str, str, EventKind], int] = defaultdict(int)
    for index, item in enumerate(references):
        key = (item.actor_id, item.action, _event_kind(item))
        reference_rank[index] = counters[key]
        counters[key] += 1
    counters.clear()
    for index, item in enumerate(observed):
        key = (item.actor_id, str(item.action), item.kind)
        observed_rank[index] = counters[key]
        counters[key] += 1
    # Required coverage is lexicographically prior to optional explanatory fit.
    for required in (True, False):
        ref_indices = [
            index for index, item in enumerate(references) if item.required is required
        ]
        obs_indices = [index for index in range(len(observed)) if index not in used]
        weights: list[list[float]] = []
        components: list[list[dict[str, Any] | None]] = []
        for ref_index in ref_indices:
            transition = references[ref_index]
            row: list[float] = []
            detail_row: list[dict[str, Any] | None] = []
            for obs_index in obs_indices:
                event = observed[obs_index]
                if (
                    event.kind != _event_kind(transition)
                    or event.actor_id != transition.actor_id
                    or event.action != transition.action
                ):
                    row.append(0.0)
                    detail_row.append(None)
                    continue
                item = _acceptance_components(
                    transition, acceptance[transition.transition_id], event
                )
                passed_components = sum(
                    bool(item[key])
                    for key in ("arguments", "scope", "timing", "execution")
                )
                # Binary scientific acceptance is primary. Component count and
                # stable time proximity only disambiguate repeated compatible calls.
                proximity = 1.0 / (
                    1.0 + abs(reference_rank[ref_index] - observed_rank[obs_index])
                )
                row.append(
                    1000.0 * int(item["fully_conforming"])
                    + 10.0
                    + passed_components
                    + proximity
                )
                detail_row.append(item)
            weights.append(row)
            components.append(detail_row)
        for row_index, column_index, _ in _maximum_weight_assignment(weights):
            ref_index = ref_indices[row_index]
            obs_index = obs_indices[column_index]
            detail = components[row_index][column_index]
            if detail is None:
                continue
            transition_id = references[ref_index].transition_id
            matches[transition_id] = observed[obs_index]
            details[transition_id] = detail
            used.add(obs_index)
    return (
        matches,
        details,
        {event.event_id for index, event in enumerate(observed) if index not in used},
    )


def _latest(
    facts: Iterable[FactVersionRecord], *, at: float
) -> FactVersionRecord | None:
    eligible = [
        fact
        for fact in facts
        if fact.world_time <= at
        and (fact.learned_time is None or fact.learned_time <= at)
    ]
    return max(
        eligible,
        key=lambda fact: (
            fact.learned_time if fact.learned_time is not None else fact.world_time,
            fact.world_time,
            fact.version_id,
        ),
        default=None,
    )


def _snapshot_fact_ids(trace: DistributedTrace, item_ids: Iterable[str]) -> set[str]:
    known = {item.version_id for item in trace.fact_versions}
    resolved: set[str] = set()
    for item_id in item_ids:
        if item_id in known:
            resolved.add(item_id)
            continue
        suffix = next(
            (version_id for version_id in known if item_id.endswith(version_id)), None
        )
        if suffix:
            resolved.add(suffix)
    return resolved


def _guard_verdict(
    guard: DataGuardSpec,
    *,
    trace: DistributedTrace,
    at: float,
    snapshot_item_ids: Iterable[str] = (),
    force_world: bool = False,
) -> tuple[str, FactVersionRecord | None, str]:
    fact_ids = _snapshot_fact_ids(trace, snapshot_item_ids)
    if force_world or guard.source == "world":
        candidates = [
            item
            for item in trace.fact_versions
            if item.authoritative and item.fact_key == guard.fact_key
        ]
    else:
        candidates = [
            item
            for item in trace.fact_versions
            if item.version_id in fact_ids and item.fact_key == guard.fact_key
        ]
    if guard.scope is not None:
        candidates = [
            item for item in candidates if _scope_covers(item.scope, guard.scope)
        ]
    fact = _latest(candidates, at=at)
    if fact is None:
        return "unknown", None, "missing_fact"
    if fact.valid_until is not None and at > fact.valid_until:
        return "unknown", fact, "stale_fact"
    if guard.max_age is not None and at - fact.world_time > guard.max_age:
        return "unknown", fact, "max_age_exceeded"
    if not _scope_covers(fact.scope, guard.scope):
        return "false", fact, "wrong_scope"
    if (
        not force_world
        and guard.required_evidence
        and (
            not fact.evidence_ids
            or not set(fact.evidence_ids) <= {event.event_id for event in trace.events}
        )
    ):
        return "unknown", fact, "unsupported_evidence"
    passed = _compare(fact.value, guard.expected, guard.operator)
    return ("true" if passed else "false"), fact, "value_predicate"


def _recompute_world_branches(
    process: FarmProcessSpecV5, trace: DistributedTrace
) -> tuple[dict[str, str], dict[str, Any], list[dict[str, Any]]]:
    records = {item.branch_id: item for item in trace.world_branch_commitments}
    selected: dict[str, str] = {}
    context: dict[str, Any] = {}
    audit = []
    for branch in process.occurrence_net.exogenous_branches:
        if not isinstance(branch, WorldBranchSpec):
            raise ValueError("v5 requires authoritative WorldBranchSpec branches")
        record = records.get(branch.branch_id)
        first_available = {}
        for key in branch.commitment_fact_keys:
            candidates = [
                item
                for item in trace.fact_versions
                if item.authoritative
                and item.fact_key == key
                and (
                    _phase_at(process, item.world_time) == branch.commit_phase
                    if process.phase_windows
                    else item.season_phase == branch.commit_phase
                )
            ]
            if not candidates:
                raise ValueError(
                    f"branch {branch.branch_id!r} has no authoritative {key!r} "
                    f"at its frozen phase {branch.commit_phase!r}"
                )
            first_available[key] = min(item.world_time for item in candidates)
        commitment_time = max(first_available.values())
        evidence: dict[str, FactVersionRecord] = {}
        for key in branch.commitment_fact_keys:
            fact = _latest(
                (
                    item
                    for item in trace.fact_versions
                    if item.authoritative and item.fact_key == key
                ),
                at=commitment_time,
            )
            if fact is None:
                raise ValueError(
                    f"branch {branch.branch_id!r} lacks authoritative {key!r}"
                )
            evidence[key] = fact
            context[key] = fact.value
        guarded = []
        defaults = []
        for alternative in branch.alternatives:
            if alternative.default:
                defaults.append(alternative)
            if alternative.guards and all(
                _compare(evidence[guard.fact_key].value, guard.expected, guard.operator)
                for guard in alternative.guards
            ):
                guarded.append(alternative)
        matches = guarded or (defaults if not guarded else [])
        if len(matches) != 1:
            raise ValueError(
                f"authoritative facts do not select one branch for {branch.branch_id!r}"
            )
        recomputed = matches[0].alternative_id
        selected[branch.branch_id] = recomputed
        expected_digest = stable_digest(
            {key: evidence[key].value for key in sorted(evidence)}
        )
        audit.append(
            {
                "branch_id": branch.branch_id,
                "stored": record.alternative_id if record is not None else None,
                "recomputed": recomputed,
                "agrees": bool(
                    record is not None
                    and record.alternative_id == recomputed
                    and record.world_time == commitment_time
                    and record.world_context_digest == expected_digest
                ),
                "missing_runtime_commitment": record is None,
                "stored_commitment_time": (
                    record.world_time if record is not None else None
                ),
                "recomputed_commitment_time": commitment_time,
                "evidence_fact_version_ids": [
                    evidence[key].version_id for key in sorted(evidence)
                ],
            }
        )
    declared = {
        branch.branch_id for branch in process.occurrence_net.exogenous_branches
    }
    for branch_id in sorted(set(records) - declared):
        record = records[branch_id]
        audit.append(
            {
                "branch_id": branch_id,
                "stored": record.alternative_id,
                "recomputed": None,
                "agrees": False,
                "unexpected_runtime_commitment": True,
                "stored_commitment_time": record.world_time,
                "recomputed_commitment_time": None,
                "evidence_fact_version_ids": [],
            }
        )
    # Only branch-selector facts enter unfolding. Action-time evidence/resource
    # guards are evaluated independently at each observed target; injecting a
    # final-season snapshot here would incorrectly rewrite earlier applicability.
    return selected, context, audit


def _reachability(trace: DistributedTrace) -> dict[str, set[str]]:
    children: defaultdict[str, set[str]] = defaultdict(set)
    prior: dict[str, str] = {}
    sends: dict[str, str] = {}
    for event in trace.events:
        for parent in event.causal_parents:
            children[parent].add(event.event_id)
        if event.actor_id in prior:
            children[prior[event.actor_id]].add(event.event_id)
        prior[event.actor_id] = event.event_id
        if event.kind == EventKind.MESSAGE_SEND and event.message_id:
            sends[event.message_id] = event.event_id
        if event.kind == EventKind.MESSAGE_RECEIVE and event.message_id in sends:
            children[sends[event.message_id]].add(event.event_id)
    result = {}
    for event in trace.events:
        reached: set[str] = set()
        queue = deque(children[event.event_id])
        while queue:
            item = queue.popleft()
            if item in reached:
                continue
            reached.add(item)
            queue.extend(children[item])
        result[event.event_id] = reached
    return result


def _guard_checks_for_target(
    process: FarmProcessSpecV5,
    transition: TransitionSpec,
    event: TraceEvent,
    trace: DistributedTrace,
) -> dict[str, dict[str, Any]]:
    decision = next(
        (
            item
            for item in trace.decisions
            if item.decision_id == event.decision_context_id
        ),
        None,
    )
    snapshot = decision.knowledge_snapshot.item_ids if decision else ()
    fact_definitions = {
        item.fact_key: item
        for item in (
            FactDefinitionSpec.model_validate(raw)
            for raw in process.occurrence_net.metadata.get("fact_definitions", ())
        )
    }
    facts = {item.version_id: item for item in trace.fact_versions}
    result = {}
    for guard in transition.guards:
        local_verdict, local_fact, local_reason = _guard_verdict(
            guard, trace=trace, at=event.world_time, snapshot_item_ids=snapshot
        )
        world_verdict, world_fact, world_reason = _guard_verdict(
            guard,
            trace=trace,
            at=event.world_time,
            snapshot_item_ids=snapshot,
            force_world=True,
        )
        definition = fact_definitions.get(guard.fact_key)
        current_provenance = True
        if process.annotation_status == "frozen" and guard.source != "world":
            if local_fact is None or world_fact is None:
                current_provenance = False
            elif definition is not None and definition.supersession == "never":
                current_provenance = True
            else:
                current_provenance = (
                    _root_version(local_fact.version_id, facts) == world_fact.version_id
                )
        passed = local_verdict == "true"
        if process.annotation_status == "frozen":
            passed = passed and world_verdict == "true" and current_provenance
        result[guard.guard_id] = {
            "guard_id": guard.guard_id,
            "fact_key": guard.fact_key,
            "verdict": "true" if passed else "false",
            "passed": passed,
            "reason": (
                "current_local_and_world_evidence"
                if passed and process.annotation_status == "frozen"
                else local_reason
                if local_verdict != "true"
                else world_reason
                if world_verdict != "true"
                else "superseded_local_evidence"
                if not current_provenance
                else local_reason
            ),
            "fact_version_id": local_fact.version_id if local_fact else None,
            "local_verdict": local_verdict,
            "local_fact_version_id": (
                local_fact.version_id if local_fact is not None else None
            ),
            "world_verdict": world_verdict,
            "authoritative_fact_version_id": (
                world_fact.version_id if world_fact is not None else None
            ),
            "current_provenance": current_provenance,
            "evaluation_source": "v5_frozen_fact_reconstruction",
        }
    return result


def _fact_actor_path(version_id: str | None, trace: DistributedTrace) -> list[str]:
    """Return the exact actor route recorded by a fact's origin chain."""

    if not version_id:
        return []
    facts = {item.version_id: item for item in trace.fact_versions}
    events = {item.event_id: item for item in trace.events}
    chain = []
    current = version_id
    seen: set[str] = set()
    while current in facts:
        if current in seen:
            raise ValueError("fact provenance contains a cycle")
        seen.add(current)
        chain.append(facts[current])
        parent = facts[current].origin_version_id
        if not parent:
            break
        current = parent
    actors = []
    for fact in reversed(chain):
        event = events.get(fact.source_event_id)
        actor = event.actor_id if event is not None else None
        if actor and actor != "world" and (not actors or actors[-1] != actor):
            actors.append(actor)
    return actors


def _is_subsequence(expected: tuple[str, ...], observed: list[str]) -> bool:
    cursor = 0
    for actor in observed:
        if cursor < len(expected) and actor == expected[cursor]:
            cursor += 1
    return cursor == len(expected)


def _causal_profile(
    process: FarmProcessSpecV5,
    matches: dict[str, TraceEvent],
    trace: DistributedTrace,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    net = process.occurrence_net
    transitions = {item.transition_id: item for item in net.transitions}
    reachability = _reachability(trace)
    guard_checks: dict[str, dict[str, dict[str, Any]]] = {}
    for transition_id, event in matches.items():
        guard_checks[transition_id] = _guard_checks_for_target(
            process, transitions[transition_id], event, trace
        )
    details = []
    by_module: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for obligation in process.causal_obligations:
        executed_targets = [
            target for target in obligation.target_transition_ids if target in matches
        ]
        if not executed_targets:
            continue
        alternatives = []
        for path in obligation.alternatives:
            edge_rows = []
            for source, target in path.transition_edges:
                source_event, target_event = matches.get(source), matches.get(target)
                passed = (
                    source_event is not None
                    and target_event is not None
                    and target_event.event_id in reachability[source_event.event_id]
                )
                edge_rows.append({"source": source, "target": target, "passed": passed})
            guard_rows = []
            for guard_id in path.guard_ids:
                located = next(
                    (
                        checks[guard_id]
                        for target_id, checks in guard_checks.items()
                        if target_id in executed_targets and guard_id in checks
                    ),
                    None,
                )
                guard_rows.append(
                    located
                    or {
                        "guard_id": guard_id,
                        "passed": False,
                        "reason": "guard_not_applicable",
                    }
                )
            supporting_version = next(
                (
                    item.get("fact_version_id")
                    for item in guard_rows
                    if item.get("fact_version_id")
                ),
                None,
            )
            observed_actor_path = _fact_actor_path(supporting_version, trace)
            actor_path_passed = (
                _is_subsequence(path.required_actor_path, observed_actor_path)
                if path.required_actor_path
                else True
            )
            alternatives.append(
                {
                    "path_id": path.path_id,
                    "passed": actor_path_passed
                    and all(item["passed"] for item in (*edge_rows, *guard_rows)),
                    "edges": edge_rows,
                    "guards": guard_rows,
                    "required_actor_path": list(path.required_actor_path),
                    "observed_actor_path": observed_actor_path,
                    "actor_path_passed": actor_path_passed,
                }
            )
        row = {
            "obligation_id": obligation.obligation_id,
            "label": obligation.label,
            "module_id": obligation.module_id,
            "fact_key": obligation.fact_key,
            "target_transition_ids": executed_targets,
            "target_event_ids": [matches[item].event_id for item in executed_targets],
            "weight": obligation.weight,
            "passed": any(item["passed"] for item in alternatives),
            "alternatives": alternatives,
        }
        details.append(row)
        by_module[obligation.module_id].append(row)
    module_rows = {}
    for module in net.modules:
        rows = by_module.get(module.module_id, [])
        denominator = sum(item["weight"] for item in rows)
        module_rows[module.module_id] = {
            "causal_conformance": (
                sum(item["weight"] * int(item["passed"]) for item in rows) / denominator
                if denominator
                else None
            ),
            "semantic_obligation_count": len(rows),
            "causal_denominator_weight": denominator,
        }
    applicable_modules = [
        (module, module_rows[module.module_id])
        for module in net.modules
        if module_rows[module.module_id]["causal_conformance"] is not None
    ]
    denominator = sum(module.weight_budget for module, _ in applicable_modules)
    overall = (
        sum(
            module.weight_budget * row["causal_conformance"]
            for module, row in applicable_modules
        )
        / denominator
        if denominator
        else None
    )
    return {"overall": overall, "modules": module_rows}, guard_checks, details


def _world_rule_matches(rule: Any, event: TraceEvent, trace: DistributedTrace) -> bool:
    return all(
        _guard_verdict(guard, trace=trace, at=event.world_time, force_world=True)[0]
        == "true"
        for guard in rule.world_guards
    )


def _event_profile(
    process: FarmProcessSpecV5,
    applicable: set[str],
    matches: dict[str, TraceEvent],
    details: dict[str, dict[str, Any]],
    unmatched: set[str],
    trace: DistributedTrace,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    net = process.occurrence_net
    by_event = {item.event_id: item for item in trace.events}
    required = [
        item
        for item in net.transitions
        if item.transition_id in applicable
        and item.actor_id != "world"
        and item.required
        and item.scoring_class
        not in {
            ScoringClass.BENIGN_LOOP,
            ScoringClass.OPTIONAL_SAFE,
            ScoringClass.HARMFUL,
        }
    ]
    classifications = []
    penalties: defaultdict[str, float] = defaultdict(float)
    for event_id in sorted(unmatched):
        event = by_event[event_id]
        event_phase = (
            _phase_at(process, event.world_time)
            if process.phase_windows
            else event.season_phase
        )
        candidates = [
            item
            for item in process.negative_action_obligations
            if item.actor_id == event.actor_id
            and item.action == event.action
            and (not item.phases or event_phase in item.phases)
            and _world_rule_matches(item, event, trace)
        ]
        classes = {item.classification for item in candidates}
        if len(classes) > 1:
            raise ValueError(f"unmatched action {event_id} has conflicting classifiers")
        classification = next(iter(classes), "unresolved")
        weight = sum(item.weight for item in candidates)
        module_id = candidates[0].module_id if candidates else None
        if classification in {"prohibited", "unnecessary"} and module_id:
            penalties[module_id] += weight
        classifications.append(
            {
                "event_id": event_id,
                "actor_id": event.actor_id,
                "action": event.action,
                "phase": event_phase,
                "classification": classification,
                "module_id": module_id,
                "penalty_weight": weight,
                "source": "frozen_world_predicate_classifier"
                if candidates
                else "no_applicable_classifier",
            }
        )
    module_rows = {}
    for module in net.modules:
        items = [item for item in required if item.module_id == module.module_id]
        denominator = sum(item.weight for item in items)
        matched_items = [item for item in items if item.transition_id in matches]
        matched_weight = sum(item.weight for item in matched_items)
        full_weight = sum(
            item.weight
            for item in matched_items
            if details[item.transition_id]["fully_conforming"]
        )

        def conditional(key: str) -> float | None:
            return (
                sum(
                    item.weight * int(details[item.transition_id][key])
                    for item in matched_items
                )
                / matched_weight
                if matched_weight
                else None
            )

        positive = full_weight / denominator if denominator else None
        module_rows[module.module_id] = {
            "presence_coverage": matched_weight / denominator if denominator else None,
            "conditional_argument_acceptance": conditional("arguments"),
            "conditional_scope_acceptance": conditional("scope"),
            "conditional_timing_acceptance": conditional("timing"),
            "conditional_execution_acceptance": conditional("execution"),
            "fully_conforming_transition_rate": positive,
            "negative_obligation_cost": penalties[module.module_id],
            "event_fidelity": (
                max(0.0, positive - penalties[module.module_id] / module.weight_budget)
                if positive is not None
                else None
            ),
            "required_transition_count": len(items),
        }
    scored = [
        (module, module_rows[module.module_id])
        for module in net.modules
        if module_rows[module.module_id]["event_fidelity"] is not None
    ]
    denominator = sum(module.weight_budget for module, _ in scored)
    overall = (
        sum(module.weight_budget * row["event_fidelity"] for module, row in scored)
        / denominator
        if denominator
        else 0.0
    )
    return {"overall": overall, "modules": module_rows}, classifications


def _response(decision: Any, policy: InformationPolicySpecV5) -> str | None:
    intent = decision.proposed_intent
    if intent.kind == IntentKind.ABSTAIN:
        return "abstain"
    if intent.kind == IntentKind.WAIT:
        return "defer"
    if intent.kind == IntentKind.OBSERVE:
        return "reobserve"
    if intent.kind == IntentKind.SEND and set(intent.claim_fact_keys) & {
        item.fact_key for item in policy.requirements
    }:
        return "handoff"
    if intent.kind == IntentKind.ACT and intent.action in policy.action_patterns:
        return "execute"
    return None


def _channel_state(trace: DistributedTrace, actor_id: str, at: float) -> str:
    closed = any(
        event.kind == EventKind.WATERMARK
        and event.actor_id == actor_id
        and event.logical_time <= at
        and int(event.payload.get("pending", 0)) == 0
        for event in trace.events
    )
    return "closed" if closed else "open"


def _policy_verdicts(
    policy: InformationPolicySpecV5,
    decision: Any,
    trace: DistributedTrace,
    *,
    world: bool,
) -> tuple[dict[str, str], dict[str, str | None]]:
    verdicts, versions = {}, {}
    intent_scope = None
    args = decision.proposed_intent.args
    for start_key, end_key in (
        ("start_ridge", "end_ridge"),
        ("start", "end"),
        ("ridge_start", "ridge_end"),
    ):
        if start_key in args and end_key in args:
            intent_scope = (int(args[start_key]), int(args[end_key]))
            break
    if intent_scope is None:
        intent_scope = decision.proposed_intent.scope
    for guard in policy.requirements:
        scoped_guard = (
            guard.model_copy(update={"scope": intent_scope})
            if intent_scope is not None and guard.scope is None
            else guard
        )
        verdict, fact, _ = _guard_verdict(
            scoped_guard,
            trace=trace,
            at=_decision_world_time(trace, decision.decision_id),
            snapshot_item_ids=decision.knowledge_snapshot.item_ids,
            force_world=world,
        )
        verdicts[guard.fact_key] = verdict
        versions[guard.fact_key] = fact.version_id if fact else None
    return verdicts, versions


def _decision_world_time(trace: DistributedTrace, decision_id: str) -> float:
    return next(
        event.world_time for event in trace.events if event.event_id == decision_id
    )


def _phase_at(process: FarmProcessSpecV5, world_time: float) -> str | None:
    matches = [
        item.phase
        for item in process.phase_windows
        if item.start_world_time <= world_time < item.end_world_time
    ]
    if len(matches) > 1:
        raise ValueError("frozen phase windows overlap at a decision time")
    return matches[0] if matches else None


def _policy_and_igd(
    process: FarmProcessSpecV5,
    trace: DistributedTrace,
    matching: dict[str, TraceEvent],
    match_details: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    policies = {item.policy_id: item for item in process.information_policies}
    policy_by_actor_phase = {
        (item.actor_id, phase): item
        for item in process.information_policies
        for phase in item.phases
    }
    commitments_by_decision = {
        item.decision_id: item for item in trace.policy_commitments
    }
    if len(commitments_by_decision) != len(trace.policy_commitments):
        raise ValueError("runtime emitted multiple policy commitments for one decision")
    observed_by_decision = {
        event.decision_context_id: event
        for event in trace.events
        if event.decision_context_id and event.kind == EventKind.ACTION
    }
    matched_by_event = {
        event.event_id: transition_id for transition_id, event in matching.items()
    }
    rows = []
    cells: defaultdict[str, float] = defaultdict(float)
    by_actor: defaultdict[str, list[bool]] = defaultdict(list)
    audit_agreement = []
    audit_rows = []
    evaluated_decision_ids = set()
    for decision in trace.decisions:
        commitment = commitments_by_decision.get(decision.decision_id)
        world_time = _decision_world_time(trace, decision.decision_id)
        phase = (
            _phase_at(process, world_time)
            if process.phase_windows
            else (
                commitment.season_phase
                if commitment is not None
                else decision.season_phase
            )
        )
        policy = policy_by_actor_phase.get((decision.actor_id, phase))
        if policy is None:
            continue
        evaluated_decision_ids.add(decision.decision_id)
        response = _response(decision, policy)
        deadline = (
            "closed"
            if policy.deadline_world_time is not None
            and world_time >= policy.deadline_world_time
            else "open"
        )
        channel = _channel_state(trace, decision.actor_id, decision.logical_time)
        local_verdicts, local_versions = _policy_verdicts(
            policy, decision, trace, world=False
        )
        global_verdicts, global_versions = _policy_verdicts(
            policy, decision, trace, world=True
        )
        local_rule = select_policy_rule(policy, local_verdicts, deadline, channel)
        global_rule = select_policy_rule(policy, global_verdicts, deadline, channel)
        stored_verdicts = (
            {key: value.value for key, value in commitment.requirement_verdicts.items()}
            if commitment is not None
            else {}
        )
        agrees = bool(
            commitment is not None
            and commitment.policy_id == policy.policy_id
            and commitment.season_phase == phase
            and stored_verdicts == local_verdicts
            and commitment.deadline_state == deadline
            and commitment.channel_state == channel
            and set(commitment.permitted_responses)
            == {item.value for item in local_rule.permitted_responses}
        )
        audit_agreement.append(agrees)
        audit_rows.append(
            {
                "decision_id": decision.decision_id,
                "commitment_id": (
                    commitment.commitment_id if commitment is not None else None
                ),
                "policy_id": policy.policy_id,
                "phase": phase,
                "agrees": agrees,
                "missing_commitment": commitment is None,
            }
        )
        if response is None:
            continue
        local_ok = response in {item.value for item in local_rule.permitted_responses}
        global_ok = response in {item.value for item in global_rule.permitted_responses}
        action_event = observed_by_decision.get(decision.decision_id)
        if response == "execute":
            transition_id = (
                matched_by_event.get(action_event.event_id) if action_event else None
            )
            global_ok = bool(
                global_ok
                and transition_id
                and match_details[transition_id]["fully_conforming"]
            )
        key = f"L{int(local_ok)}_G{int(global_ok)}"
        cells[key] += policy.decision_weight
        by_actor[decision.actor_id].append(local_ok)
        rows.append(
            {
                "decision_id": decision.decision_id,
                "policy_id": policy.policy_id,
                "phase": phase,
                "phase_reconstruction_source": (
                    "frozen_world_time_window"
                    if process.phase_windows
                    else "engineering_runtime_hint"
                ),
                "actor_id": decision.actor_id,
                "response": response,
                "local_verdicts": local_verdicts,
                "global_verdicts": global_verdicts,
                "local_fact_versions": local_versions,
                "global_fact_versions": global_versions,
                "local_rule_id": local_rule.rule_id,
                "global_rule_id": global_rule.rule_id,
                "local_conforming": local_ok,
                "global_conforming": global_ok,
                "runtime_audit_agrees": agrees,
                "guard_prevented_write": bool(
                    decision.guard and decision.guard.verdict.value != "allow"
                ),
                "weight": policy.decision_weight,
            }
        )
    unexpected_commitments = [
        item.commitment_id
        for item in trace.policy_commitments
        if item.decision_id not in evaluated_decision_ids
        or item.policy_id not in policies
    ]
    total = sum(cells.values())
    profile = {
        "overall": (
            sum(row["weight"] * int(row["local_conforming"]) for row in rows) / total
            if total
            else None
        ),
        "by_actor": {
            actor: {
                "evaluated": len(values),
                "policy_conformance": mean(values) if values else None,
            }
            for actor in trace.actors
            for values in (by_actor.get(actor, []),)
        },
        "details": rows,
        "runtime_commitment_agreement": (
            all(audit_agreement) and not unexpected_commitments
            if audit_agreement or unexpected_commitments
            else None
        ),
        "runtime_commitment_mismatch_count": sum(not item for item in audit_agreement)
        + len(unexpected_commitments),
        "unexpected_runtime_commitment_ids": unexpected_commitments,
        "runtime_commitment_audit": audit_rows,
        "runtime_commitments_consumed_for_applicability": False,
        "evaluation_source": "independent_v5_reconstruction",
    }
    igd = {
        "weighted_table": {
            key: cells.get(key, 0.0) for key in ("L0_G0", "L0_G1", "L1_G0", "L1_G1")
        },
        "total_weight": total,
        "information_global_discordance": cells.get("L1_G0", 0.0) / total
        if total
        else None,
        "definition": "weighted_fraction_of_decisions_with_local_conformance_and_global_nonconformance",
    }
    return profile, igd


def _root_version(version_id: str, facts: dict[str, FactVersionRecord]) -> str:
    visiting: set[str] = set()
    current = version_id
    while facts[current].origin_version_id:
        if current in visiting:
            raise ValueError("fact provenance contains a cycle")
        visiting.add(current)
        parent = facts[current].origin_version_id
        if parent not in facts:
            raise ValueError("fact provenance references a missing origin")
        current = parent
    return current


def _provenance_localization(
    process: FarmProcessSpecV5,
    trace: DistributedTrace,
    failed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    facts = {item.version_id: item for item in trace.fact_versions}
    events = {item.event_id: item for item in trace.events}
    decisions = {item.decision_id: item for item in trace.decisions}
    sends = {
        item.message_id: item
        for item in trace.events
        if item.kind == EventKind.MESSAGE_SEND and item.message_id
    }
    receives = defaultdict(list)
    for item in trace.events:
        if item.kind == EventKind.MESSAGE_RECEIVE and item.message_id:
            receives[item.message_id].append(item)
    rows = []
    for obligation in failed:
        fact_key = obligation.get("fact_key")
        target_event_id = next(iter(obligation.get("target_event_ids", ())), None)
        target = events.get(target_event_id)
        if target is None:
            continue
        if not fact_key:
            rows.append(
                {
                    "obligation_id": obligation["obligation_id"],
                    "target_event_id": target_event_id,
                    "fact_key": None,
                    "primary": "composition_error",
                    "method": "provenance_failure_localization",
                    "actual_cause_claimed": False,
                    "authoritative_root_version_id": None,
                    "exact_fact_version_chain": [],
                }
            )
            continue
        decision = decisions.get(target.decision_context_id or "")
        world = _latest(
            (
                item
                for item in facts.values()
                if item.authoritative and item.fact_key == fact_key
            ),
            at=target.world_time,
        )
        if world is None:
            primary = "observation_gap"
            chain: list[dict[str, Any]] = []
        else:
            descendants = [
                item
                for item in facts.values()
                if item.fact_key == fact_key
                and _root_version(item.version_id, facts) == world.version_id
            ]
            observed = [
                item
                for item in descendants
                if not item.authoritative
                and events[item.source_event_id].kind == EventKind.OBSERVATION
            ]
            snapshot_ids = _snapshot_fact_ids(
                trace, decision.knowledge_snapshot.item_ids if decision else ()
            )
            local = [
                item
                for item in facts.values()
                if item.fact_key == fact_key and item.version_id in snapshot_ids
            ]
            if local:
                chosen = _latest(local, at=target.world_time)
                assert chosen is not None
                chosen_root = facts[_root_version(chosen.version_id, facts)]
                if not chosen_root.authoritative or chosen.value != chosen_root.value:
                    primary = "unsupported_claim"
                elif chosen_root.version_id != world.version_id:
                    primary = "stale_information"
                elif (
                    chosen.valid_until is not None
                    and target.world_time > chosen.valid_until
                ):
                    primary = "stale_information"
                elif chosen.version_id not in _snapshot_fact_ids(
                    trace, decision.prompt_item_ids if decision else ()
                ):
                    primary = "uptake_error"
                elif obligation["passed"]:
                    primary = None
                else:
                    primary = "reasoning_error"
            elif not observed:
                primary = "observation_gap"
            else:
                descendant_sends = [
                    event
                    for event in sends.values()
                    if any(
                        version in event.payload.get("fact_versions", ())
                        for version in {item.version_id for item in descendants}
                    )
                    and event.world_time <= target.world_time
                ]
                if not descendant_sends:
                    has_unverifiable_free_text = any(
                        event.payload.get("envelope_type") == "free_text"
                        and event.world_time <= target.world_time
                        for event in sends.values()
                    )
                    primary = (
                        "unverifiable_handoff"
                        if has_unverifiable_free_text
                        else "handoff_omission"
                    )
                elif not any(
                    receive.status != "duplicate"
                    and receive.world_time <= target.world_time
                    for send in descendant_sends
                    for receive in receives.get(send.message_id, ())
                ):
                    primary = "transit_gap"
                else:
                    primary = "uptake_error"
            chain = []
            for item in sorted(
                descendants,
                key=lambda value: (
                    value.world_time,
                    value.learned_time or -1,
                    value.version_id,
                ),
            ):
                source = events[item.source_event_id]
                chain.append(
                    {
                        "fact_version_id": item.version_id,
                        "origin_version_id": item.origin_version_id,
                        "source_event_id": item.source_event_id,
                        "source_kind": source.kind.value,
                        "actor_id": source.actor_id,
                        "message_id": source.message_id,
                    }
                )
        rows.append(
            {
                "obligation_id": obligation["obligation_id"],
                "target_event_id": target_event_id,
                "fact_key": fact_key,
                "primary": primary,
                "method": "provenance_failure_localization",
                "actual_cause_claimed": False,
                "authoritative_root_version_id": world.version_id if world else None,
                "exact_fact_version_chain": chain,
            }
        )
    return rows


def _decision_failure_localization(
    trace: DistributedTrace,
    policy_profile: dict[str, Any],
    matches: dict[str, TraceEvent],
    match_details: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Localize directly recorded policy/action inconsistencies.

    This deliberately covers only the defensible case in which every frozen
    prerequisite is true in both the actor and world views, yet the executed
    action violates a categorical acceptance predicate.  Information-path
    failures remain the responsibility of exact-ID provenance localization.
    """

    action_by_decision = {
        event.decision_context_id: event
        for event in trace.events
        if event.kind == EventKind.ACTION and event.decision_context_id
    }
    transition_by_event = {
        event.event_id: transition_id for transition_id, event in matches.items()
    }
    rows = []
    for item in policy_profile["details"]:
        if item["response"] != "execute" or item["global_conforming"]:
            continue
        if not all(value == "true" for value in item["local_verdicts"].values()):
            continue
        if not all(value == "true" for value in item["global_verdicts"].values()):
            continue
        action = action_by_decision.get(item["decision_id"])
        transition_id = (
            transition_by_event.get(action.event_id) if action is not None else None
        )
        acceptance = match_details.get(transition_id or "")
        if not acceptance or acceptance["fully_conforming"]:
            continue
        failed_predicates = [
            key
            for key in ("arguments", "scope", "timing", "execution")
            if not acceptance[key]
        ]
        rows.append(
            {
                "decision_id": item["decision_id"],
                "target_event_id": action.event_id,
                "transition_id": transition_id,
                "primary": "reasoning_error",
                "failed_acceptance_predicates": failed_predicates,
                "method": "recorded_policy_action_inconsistency",
                "actual_cause_claimed": False,
            }
        )
    return rows


def _structural_exposure(
    process: FarmProcessSpecV5,
    failed: list[dict[str, Any]],
    matches: dict[str, TraceEvent],
) -> list[dict[str, Any]]:
    children: defaultdict[str, set[str]] = defaultdict(set)
    for obligation in process.causal_obligations:
        for path in obligation.alternatives:
            for source, target in path.transition_edges:
                children[source].add(target)
    rows = []
    event_times = [event.world_time for event in matches.values()]
    end = max(event_times, default=0.0)
    for item in failed:
        target = next(iter(item.get("target_transition_ids", ())), None)
        reached: set[str] = set()
        queue = deque(children.get(target, ()))
        while queue:
            child = queue.popleft()
            if child in reached:
                continue
            reached.add(child)
            queue.extend(children.get(child, ()))
        event = matches.get(target) if target else None
        downstream_times = [
            matches[item].world_time for item in reached if item in matches
        ]
        rows.append(
            {
                "obligation_id": item["obligation_id"],
                "structural_downstream_reach": len(reached),
                "observed_downstream_transition_count": len(downstream_times),
                "structural_exposure_duration_days": (
                    max(0.0, max(downstream_times) - event.world_time) / 86400
                    if event and downstream_times
                    else 0.0
                    if event
                    else None
                ),
                "remaining_horizon_exposure_seconds": max(0.0, end - event.world_time)
                if event
                else None,
                "causal_propagation_claimed": False,
            }
        )
    return rows


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _synchronization_profile(
    process: FarmProcessSpecV5, trace: DistributedTrace
) -> dict[str, Any]:
    facts = {item.version_id: item for item in trace.fact_versions}
    required_by_actor: defaultdict[str, set[str]] = defaultdict(set)
    deadlines: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
    for policy in process.information_policies:
        for guard in policy.requirements:
            required_by_actor[policy.actor_id].add(guard.fact_key)
            if policy.deadline_world_time is not None:
                deadlines[(policy.actor_id, guard.fact_key)].append(
                    policy.deadline_world_time
                )
    lags = []
    never_received = []
    roots = [item for item in trace.fact_versions if item.authoritative]
    for root in roots:
        for actor, keys in required_by_actor.items():
            if root.fact_key not in keys:
                continue
            learned = [
                item
                for item in trace.fact_versions
                if not item.authoritative
                and actor in item.visible_to
                and item.learned_time is not None
                and _root_version(item.version_id, facts) == root.version_id
            ]
            if not learned:
                never_received.append(
                    {
                        "root_version_id": root.version_id,
                        "fact_key": root.fact_key,
                        "actor_id": actor,
                    }
                )
                continue
            first = min(
                learned,
                key=lambda item: (item.learned_time, item.version_id),
            )
            assert first.learned_time is not None
            lag = first.learned_time - root.world_time
            applicable_deadlines = [
                deadline
                for deadline in deadlines.get((actor, root.fact_key), ())
                if deadline >= root.world_time
            ]
            deadline = min(applicable_deadlines, default=None)
            available = deadline - root.world_time if deadline is not None else None
            lags.append(
                {
                    "fact_version_id": first.version_id,
                    "root_version_id": root.version_id,
                    "fact_key": root.fact_key,
                    "actor_id": actor,
                    "lag_seconds": lag,
                    "deadline_world_time": deadline,
                    "deadline_normalized_lag": (
                        lag / available
                        if available is not None and available > 0
                        else None
                    ),
                    "freshness_slack_seconds": (
                        first.valid_until - first.learned_time
                        if first.valid_until is not None
                        else None
                    ),
                    "recorded_actor_path": _fact_actor_path(first.version_id, trace),
                }
            )
    values = [item["lag_seconds"] for item in lags]
    normalized = [
        item["deadline_normalized_lag"]
        for item in lags
        if item["deadline_normalized_lag"] is not None
    ]
    return {
        "fact_versions": lags,
        "mean_seconds": mean(values) if values else None,
        "median_seconds": _percentile(values, 0.5),
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": max(values) if values else None,
        "mean_deadline_normalized_lag": mean(normalized) if normalized else None,
        "never_received_versions": never_received,
        "fact_version_not_fact_key": True,
    }


def _recovery_profile(
    trace: DistributedTrace, policy_profile: dict[str, Any]
) -> dict[str, Any]:
    rows = sorted(
        policy_profile["details"],
        key=lambda item: _decision_world_time(trace, item["decision_id"]),
    )
    pending: dict[tuple[str, str], dict[str, Any]] = {}
    episodes = []
    for item in rows:
        key = (item["actor_id"], item["policy_id"])
        response = item["response"]
        at = _decision_world_time(trace, item["decision_id"])
        if response in {"defer", "reobserve", "handoff"} and item["local_conforming"]:
            episode = pending.setdefault(
                key,
                {
                    "actor_id": item["actor_id"],
                    "policy_id": item["policy_id"],
                    "start_decision_id": item["decision_id"],
                    "start_world_time": at,
                    "response_classes": set(),
                },
            )
            episode["response_classes"].add(response)
        elif response == "execute" and item["global_conforming"] and key in pending:
            episode = pending.pop(key)
            episodes.append(
                {
                    **episode,
                    "response_classes": sorted(episode["response_classes"]),
                    "recovered": True,
                    "end_decision_id": item["decision_id"],
                    "latency_seconds": at - episode["start_world_time"],
                }
            )
    episodes.extend(
        {
            **episode,
            "response_classes": sorted(episode["response_classes"]),
            "recovered": False,
            "end_decision_id": None,
            "latency_seconds": None,
        }
        for episode in pending.values()
    )
    recovered = [item for item in episodes if item["recovered"]]
    latencies = [item["latency_seconds"] for item in recovered]
    return {
        "episodes": episodes,
        "opportunity_count": len(episodes),
        "recovered_count": len(recovered),
        "successful_replanning_rate": (
            len(recovered) / len(episodes) if episodes else None
        ),
        "median_recovery_latency_seconds": _percentile(latencies, 0.5),
        "reobservation_recovery_count": sum(
            "reobserve" in item["response_classes"] for item in recovered
        ),
        "new_delivery_recovery_count": sum(
            "handoff" in item["response_classes"] for item in recovered
        ),
        "evaluation_source": "independent_policy_decision_sequence",
    }


def _phase_profile(
    process: FarmProcessSpecV5,
    module_profile: dict[str, dict[str, Any]],
    policy_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    phases: dict[str, dict[str, Any]] = {}
    prefix_rows = []
    ef_numerator = ef_denominator = cc_numerator = cc_denominator = 0.0
    first_divergence = None
    for module in process.occurrence_net.modules:
        row = module_profile[module.module_id]
        ef = row["event_fidelity"]
        cc = row["causal_conformance"]
        weight = module.weight_budget
        if ef is not None:
            ef_numerator += weight * ef
            ef_denominator += weight
        if cc is not None:
            cc_numerator += weight * cc
            cc_denominator += weight
        policy_rows = [
            item for item in policy_profile["details"] if item["phase"] == module.phase
        ]
        local_policy = (
            sum(item["weight"] * int(item["local_conforming"]) for item in policy_rows)
            / sum(item["weight"] for item in policy_rows)
            if policy_rows
            else None
        )
        phase_row = {
            "module_id": module.module_id,
            "phase": module.phase,
            "event_fidelity": ef,
            "causal_conformance": cc,
            "dcore_score": (
                0.5 * ef + 0.5 * cc if ef is not None and cc is not None else ef
            ),
            "local_information_policy_conformance": local_policy,
        }
        phases[module.module_id] = phase_row
        prefix_ef = ef_numerator / ef_denominator if ef_denominator else None
        prefix_cc = cc_numerator / cc_denominator if cc_denominator else None
        prefix_rows.append(
            {
                "through_module_id": module.module_id,
                "through_phase": module.phase,
                "event_fidelity": prefix_ef,
                "causal_conformance": prefix_cc,
                "dcore_score": (
                    0.5 * prefix_ef + 0.5 * prefix_cc
                    if prefix_ef is not None and prefix_cc is not None
                    else prefix_ef
                ),
            }
        )
        violated = (
            (ef is not None and ef < 1 - 1e-12)
            or (cc is not None and cc < 1 - 1e-12)
            or (local_policy is not None and local_policy < 1 - 1e-12)
        )
        if violated and first_divergence is None:
            first_divergence = {
                "module_id": module.module_id,
                "phase": module.phase,
                "module_index": len(prefix_rows) - 1,
            }
    return phases, {
        "first_critical_divergence": first_divergence,
        "core_style_prefix_profile": prefix_rows,
        "prefix_order_source": "frozen_module_order",
    }


def _pm4py_sequential_projection(
    process: FarmProcessSpecV5,
    applicable: set[str],
    trace: DistributedTrace,
) -> dict[str, Any]:
    try:
        from importlib.metadata import version as package_version

        from pm4py.algo.conformance.tokenreplay import algorithm as token_replay
        from pm4py.objects.log.obj import Event, EventLog, Trace
        from pm4py.objects.petri_net.obj import Marking, PetriNet
        from pm4py.objects.petri_net.utils import petri_utils
    except ImportError:
        return {
            "available": False,
            "fitness": None,
            "method": "pm4py_token_replay_on_deterministic_sequential_projection",
            "reason": "pm4py_not_installed",
        }
    net_spec = process.occurrence_net
    transition_by_id = {item.transition_id: item for item in net_spec.transitions}
    dependencies = [
        (source, target)
        for source, target in _petri_edges(net_spec)
        if source in applicable and target in applicable
    ]
    indegree = {item: 0 for item in applicable}
    children: defaultdict[str, set[str]] = defaultdict(set)
    for source, target in dependencies:
        children[source].add(target)
        indegree[target] += 1
    frontier = sorted(item for item, degree in indegree.items() if degree == 0)
    order = []
    while frontier:
        item = frontier.pop(0)
        order.append(item)
        for child in sorted(children[item]):
            indegree[child] -= 1
            if indegree[child] == 0:
                frontier.append(child)
        frontier.sort()
    order = [item for item in order if transition_by_id[item].actor_id != "world"]
    pm_net = PetriNet("farm_dcore_v5_sequential_projection")
    places = [PetriNet.Place(f"p{index}") for index in range(len(order) + 1)]
    for place in places:
        pm_net.places.add(place)
    reference_occurrence: defaultdict[tuple[str, str], int] = defaultdict(int)
    for index, transition_id in enumerate(order):
        item = transition_by_id[transition_id]
        key = (item.actor_id, item.action)
        occurrence = reference_occurrence[key]
        reference_occurrence[key] += 1
        # Duplicate labels make token replay depend on set iteration. Stable
        # occurrence labels make this legacy sequential projection replayable.
        label = f"{item.actor_id}::{item.action}::occ{occurrence}"
        transition = PetriNet.Transition(transition_id, label)
        pm_net.transitions.add(transition)
        petri_utils.add_arc_from_to(places[index], transition, pm_net)
        petri_utils.add_arc_from_to(transition, places[index + 1], pm_net)
    initial, final = Marking(), Marking()
    initial[places[0]], final[places[-1]] = 1, 1
    observed = Trace()
    observed_occurrence: defaultdict[tuple[str, str], int] = defaultdict(int)
    for item in trace.events:
        if item.kind in {
            EventKind.ACTION,
            EventKind.MESSAGE_SEND,
            EventKind.MESSAGE_RECEIVE,
        } and item.status not in {"blocked", "deferred", "duplicate"}:
            key = (item.actor_id, str(item.action))
            occurrence = observed_occurrence[key]
            observed_occurrence[key] += 1
            observed.append(
                Event(
                    {
                        "concept:name": (
                            f"{item.actor_id}::{item.action}::occ{occurrence}"
                        )
                    }
                )
            )
    # Exact alignment is exponential on the hundreds of seasonal transition
    # instances in this projection.  PM4Py token replay is the preregistered,
    # scalable legacy baseline; it is not used by any D-CORE component.
    result = token_replay.apply(EventLog([observed]), pm_net, initial, final)[0]
    return {
        "available": True,
        "fitness": result.get("trace_fitness"),
        "missing_tokens": result.get("missing_tokens"),
        "remaining_tokens": result.get("remaining_tokens"),
        "consumed_tokens": result.get("consumed_tokens"),
        "produced_tokens": result.get("produced_tokens"),
        "method": "pm4py_token_replay_on_deterministic_sequential_projection",
        "concurrency_limitation": True,
        "duplicate_label_resolution": "stable_actor_action_occurrence_rank",
        "pm4py_version": package_version("pm4py"),
        "pm4py_version_expected": "2.7.23.4",
        "pm4py_version_matches_pin": package_version("pm4py") == "2.7.23.4",
    }


def _petri_edges(net: PetriNetSpec) -> set[tuple[str, str]]:
    places = {item.place_id for item in net.places}
    incoming: defaultdict[str, set[str]] = defaultdict(set)
    outgoing: defaultdict[str, set[str]] = defaultdict(set)
    transitions = {item.transition_id for item in net.transitions}
    for arc in net.arcs:
        if arc.source in transitions and arc.target in places:
            incoming[arc.target].add(arc.source)
        elif arc.source in places and arc.target in transitions:
            outgoing[arc.source].add(arc.target)
    return {
        (source, target)
        for place in places
        for source in incoming[place]
        for target in outgoing[place]
    }


def _partial_order_pair_agreement(
    net: PetriNetSpec,
    applicable: set[str],
    matches: dict[str, TraceEvent],
    trace: DistributedTrace,
) -> dict[str, Any]:
    """Compatibility baseline over comparable reference transition pairs.

    Unlike CC, this deliberately includes transitive comparable pairs.  It is
    reported only as a legacy ordering baseline and never drives diagnosis.
    """

    children: defaultdict[str, set[str]] = defaultdict(set)
    for source, target in _petri_edges(net):
        if source in applicable and target in applicable:
            children[source].add(target)
    reference_reach: dict[str, set[str]] = {}
    for source in applicable:
        reached: set[str] = set()
        queue = deque(children[source])
        while queue:
            target = queue.popleft()
            if target in reached:
                continue
            reached.add(target)
            queue.extend(children[target])
        reference_reach[source] = reached
    observed_reach = _reachability(trace)
    pairs = [
        (source, target)
        for source, targets in reference_reach.items()
        for target in targets
        if source in matches and target in matches
    ]
    passed = sum(
        matches[target].event_id in observed_reach[matches[source].event_id]
        for source, target in pairs
    )
    return {
        "agreement": passed / len(pairs) if pairs else None,
        "comparable_pair_count": len(pairs),
        "passed_pair_count": passed,
        "transitive_pairs_included": True,
        "used_for_diagnosis": False,
    }


def evaluate_farm_dcore_v5(
    process: FarmProcessSpecV5, trace: DistributedTrace
) -> dict[str, Any]:
    """Evaluate one season under the frozen v5 scientific contract."""

    if trace.schema_version != "dcore_trace_v5":
        raise ValueError("D-CORE v5 requires dcore_trace_v5")
    if trace.task_id not in {process.process_id, process.occurrence_net.net_id}:
        raise ValueError("trace and v5 process specification disagree")
    branches, world_context, branch_audit = _recompute_world_branches(process, trace)
    occurrence = unfold_petri_net(
        process.occurrence_net,
        world_context=world_context,
        world_fingerprint=str(trace.configuration.get("exogenous_world_digest", "")),
        committed_branches=branches,
    )
    applicable = set(occurrence.applicable_transition_ids)
    matches, match_details, unmatched = _match_events(process, applicable, trace)
    event_profile, classifications = _event_profile(
        process, applicable, matches, match_details, unmatched, trace
    )
    causal_profile, guard_checks, causal_details = _causal_profile(
        process, matches, trace
    )
    policy_profile, igd = _policy_and_igd(process, trace, matches, match_details)
    failed = [item for item in causal_details if not item["passed"]]
    localization = _provenance_localization(process, trace, failed)
    decision_localization = _decision_failure_localization(
        trace, policy_profile, matches, match_details
    )
    cc = causal_profile["overall"]
    ef = event_profile["overall"]
    # A run with no applicable causal obligation is reported NA; the secondary
    # scalar reduces to EF rather than manufacturing a perfect causal score.
    scalar = ef if cc is None else 0.5 * ef + 0.5 * cc
    module_profile = {}
    for module in process.occurrence_net.modules:
        module_profile[module.module_id] = {
            **event_profile["modules"][module.module_id],
            **causal_profile["modules"][module.module_id],
            "weight_budget": module.weight_budget,
        }
    actor_scores = policy_profile["by_actor"]
    module_budget = {
        item.module_id: item.weight_budget for item in process.occurrence_net.modules
    }
    matched_modules = [
        (module_id, row)
        for module_id, row in module_profile.items()
        if row["presence_coverage"] is not None
    ]

    def weighted_conditional(key: str) -> float | None:
        rows = [
            (module_budget[module_id], row[key])
            for module_id, row in matched_modules
            if row[key] is not None
        ]
        denominator = sum(weight for weight, _ in rows)
        return (
            sum(weight * value for weight, value in rows) / denominator
            if denominator
            else None
        )

    per_agent_telemetry = {}
    for actor in trace.actors:
        decisions = [item for item in trace.decisions if item.actor_id == actor]
        per_agent_telemetry[actor] = {
            "decision_count": len(decisions),
            "model_call_count": sum(
                item.llm_input_log_id is not None or item.model_name is not None
                for item in decisions
            ),
            "prompt_tokens": sum(item.prompt_tokens or 0 for item in decisions),
            "completion_tokens": sum(item.completion_tokens or 0 for item in decisions),
            "total_tokens": sum(item.total_tokens or 0 for item in decisions),
            "completion_duration": sum(
                item.completion_duration or 0.0 for item in decisions
            ),
            "messages_sent": sum(
                item.kind == EventKind.MESSAGE_SEND and item.actor_id == actor
                for item in trace.events
            ),
            "messages_received": sum(
                item.kind == EventKind.MESSAGE_RECEIVE and item.actor_id == actor
                for item in trace.events
            ),
        }
    sync = _synchronization_profile(process, trace)
    pm4py = _pm4py_sequential_projection(process, applicable, trace)
    po_baseline = _partial_order_pair_agreement(
        process.occurrence_net, applicable, matches, trace
    )
    exposure = _structural_exposure(process, failed, matches)
    recovery = _recovery_profile(trace, policy_profile)
    policy_rows = policy_profile["details"]
    unsafe_proposals = [
        row for row in policy_rows if row["response"] == "execute" and not row["global_conforming"]
    ]
    prevented_writes = [row for row in unsafe_proposals if row["guard_prevented_write"]]
    false_blocks = [
        row
        for row in policy_rows
        if row["guard_prevented_write"] and row["global_conforming"]
    ]
    unnecessary_abstentions = [
        row
        for row in policy_rows
        if row["response"] == "abstain"
        and row["global_conforming"]
        and all(value == "TRUE" for value in row["global_verdicts"].values())
    ]
    guard_effectiveness = {
        "unsafe_proposals": len(unsafe_proposals),
        "prevented_unsafe_writes": len(prevented_writes),
        "false_blocks": len(false_blocks),
        "unnecessary_abstentions": len(unnecessary_abstentions),
        "eventual_recoveries": recovery["recovered_count"],
        "recovery_opportunities": recovery["opportunity_count"],
        "safety_benefit_rate": (
            len(prevented_writes) / len(unsafe_proposals) if unsafe_proposals else None
        ),
        "false_block_rate": (
            len(false_blocks)
            / sum(row["guard_prevented_write"] for row in policy_rows)
            if any(row["guard_prevented_write"] for row in policy_rows)
            else None
        ),
        "yield_cost": None,
        "yield_cost_requires_paired_audit_enforce_runs": True,
        "policy_validity_reconstructed_independently": True,
        "physical_prevention_source": "decision_to_action_linkage",
    }
    phase_profile, long_horizon = _phase_profile(
        process, module_profile, policy_profile
    )
    obligation_specs = {item.obligation_id: item for item in process.causal_obligations}
    pairwise_coordination: defaultdict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"applicable": 0, "violated": 0}
    )
    role_coordination: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"applicable": 0, "violated": 0}
    )
    transition_owners = {
        item.transition_id: item.actor_id for item in process.occurrence_net.transitions
    }
    declared_coordination_edges: set[tuple[str, str]] = set()
    for item in causal_details:
        specification = obligation_specs[item["obligation_id"]]
        actor_edges = {
            edge
            for path in specification.alternatives
            for edge in zip(path.required_actor_path, path.required_actor_path[1:])
        }
        declared_coordination_edges.update(actor_edges)
        for edge in actor_edges:
            pairwise_coordination[edge]["applicable"] += 1
            pairwise_coordination[edge]["violated"] += int(not item["passed"])
        target_roles = {
            transition_owners[target]
            for target in item["target_transition_ids"]
            if target in transition_owners
        }
        for actor in target_roles:
            role_coordination[actor]["applicable"] += 1
            role_coordination[actor]["violated"] += int(not item["passed"])
    message_path_lengths = [
        max(0, len(item["recorded_actor_path"]) - 1) for item in sync["fact_versions"]
    ]
    classifications_by_name = defaultdict(int)
    for item in classifications:
        classifications_by_name[item["classification"]] += 1
    result = {
        "metric_version": METRIC_VERSION,
        "specification_version": process.schema_version,
        "specification_digest": process.digest,
        "event_fidelity": round(ef, 6),
        "causal_conformance": round(cc, 6) if cc is not None else None,
        "dcore_score": round(scalar, 6),
        "dcore_sensitivity": {
            str(alpha): round(ef if cc is None else alpha * ef + (1 - alpha) * cc, 6)
            for alpha in (0.25, 0.5, 0.75)
        },
        "module_profile": module_profile,
        "coverage": weighted_conditional("presence_coverage") or 0.0,
        "argument_fidelity": weighted_conditional("conditional_argument_acceptance"),
        "spatial_fidelity": weighted_conditional("conditional_scope_acceptance"),
        "timing_fidelity": weighted_conditional("conditional_timing_acceptance"),
        "event_acceptance": {
            "matches": {
                transition_id: {
                    "observed_event_id": event.event_id,
                    **match_details[transition_id],
                }
                for transition_id, event in matches.items()
            },
            "unmatched_event_ids": sorted(unmatched),
            "unmatched_action_classification": classifications,
            "runtime_harmful_flags_consumed": False,
            "soft_numeric_decay_used": False,
        },
        "semantic_causal_obligations": {
            "details": causal_details,
            "applicable": len(causal_details),
            "violated": len(failed),
            "raw_edge_count_is_score_denominator": False,
        },
        "guard_reconstruction": guard_checks,
        "information_policy_conformance": policy_profile,
        "information_global_discordance": igd,
        "guard_effectiveness": guard_effectiveness,
        "local": actor_scores,
        "local_global_gap": None,
        "provenance_failure_localization": localization,
        "decision_failure_localization": decision_localization,
        # Compatibility alias is intentionally explicit about its semantics.
        "attribution": [*localization, *decision_localization],
        "structural_exposure": exposure,
        "long_horizon_profile": long_horizon,
        "synchronization": sync,
        "world_branch_audit": {
            "details": branch_audit,
            "runtime_commitments_agree": all(item["agrees"] for item in branch_audit),
            "runtime_branch_selection_consumed": False,
        },
        "pm4py_sequential_alignment": pm4py,
        "metric_profile": {
            "primary": "module_and_semantic_obligation_profile",
            "scalar_secondary": True,
            "execution_happens_before_is_not_normative_causality": True,
            "provenance_is_not_actual_cause": True,
            "actual_causal_effect_requires_controlled_pair": True,
            "runtime_transition_ids_consumed": False,
            "runtime_guard_verdicts_consumed": False,
            "runtime_policy_verdicts_consumed": False,
            "runtime_phase_labels_consumed": not bool(process.phase_windows),
            "runtime_outcome_labels_consumed": False,
            "paper_eligible": bool(
                process.annotation_status == "frozen"
                and process.expert_review_status == "confirmed"
                and all(item["agrees"] for item in branch_audit)
                and policy_profile["runtime_commitment_mismatch_count"] == 0
            ),
        },
    }
    # Compatibility baselines/columns.  These are never inputs to v5 EF, CC,
    # IGD, or localization and are explicitly nullable when inapplicable.
    result.update(
        {
            "occurrence_net": occurrence.model_dump(mode="json"),
            "coordination_failure_rate": (
                len(failed) / len(causal_details) if causal_details else None
            ),
            "team_profile": {
                "team_id": trace.team_id,
                "team_size": len(trace.actors),
                "n_local_scored": sum(
                    item["policy_conformance"] is not None
                    for item in actor_scores.values()
                ),
                "coordination_failure_rate": (
                    len(failed) / len(causal_details) if causal_details else None
                ),
                "message_count": sum(
                    item.kind == EventKind.MESSAGE_SEND for item in trace.events
                ),
                "delivery_count": sum(
                    item.kind == EventKind.MESSAGE_RECEIVE for item in trace.events
                ),
                "mean_message_path_length": (
                    mean(message_path_lengths) if message_path_lengths else None
                ),
                "max_message_path_length": (
                    max(message_path_lengths) if message_path_lengths else None
                ),
                "coordination_edge_density": (
                    len(declared_coordination_edges)
                    / (len(trace.actors) * (len(trace.actors) - 1))
                    if len(trace.actors) > 1
                    else 0.0
                ),
                "pairwise_coordination_failure_rates": {
                    f"{source}->{target}": {
                        **counts,
                        "failure_rate": counts["violated"] / counts["applicable"],
                    }
                    for (source, target), counts in sorted(
                        pairwise_coordination.items()
                    )
                },
                "per_role_coordination_failure_rates": {
                    actor: {
                        **counts,
                        "failure_rate": counts["violated"] / counts["applicable"],
                    }
                    for actor, counts in sorted(role_coordination.items())
                },
                "model_call_dispersion_sd": pstdev(
                    [item["model_call_count"] for item in per_agent_telemetry.values()]
                )
                if per_agent_telemetry
                else None,
                "token_dispersion_sd": pstdev(
                    [item["total_tokens"] for item in per_agent_telemetry.values()]
                )
                if per_agent_telemetry
                else None,
                "per_agent_telemetry": per_agent_telemetry,
            },
            "po_pair_agreement": po_baseline["agreement"],
            "partial_order_pair_baseline": po_baseline,
            "petri_token_fitness": pm4py.get("fitness"),
            "petri_alignment_fitness": None,
            "harmful_extra_cost": classifications_by_name["prohibited"],
            "unnecessary_write_count": classifications_by_name["unnecessary"],
            "redundant_read_count": classifications_by_name["benign"],
            "error_propagation_depth": max(
                (item["structural_downstream_reach"] for item in exposure),
                default=0,
            ),
            "error_propagation_affected_transitions": sum(
                item["structural_downstream_reach"] for item in exposure
            ),
            "error_propagation_duration_days": None,
            "structural_exposure_duration_days": max(
                (
                    item["structural_exposure_duration_days"]
                    for item in exposure
                    if item["structural_exposure_duration_days"] is not None
                ),
                default=0.0,
            ),
            "recovery": recovery,
            "synchronization_lag": {
                "median": sync["median_seconds"],
                "p95": sync["p95_seconds"],
                "never_received_versions": sync["never_received_versions"],
                "v5_profile": sync,
            },
            "bfcl_tool_success": (
                sum(
                    item.kind == EventKind.ACTION and item.status == "ok"
                    for item in trace.events
                )
                / max(1, sum(item.kind == EventKind.ACTION for item in trace.events))
            ),
            "merged_pc_ktc": {"combined": None},
            "core_path_correctness": None,
            "average_local_pc_ktc": None,
            "phase_profile": phase_profile,
        }
    )
    return result


__all__ = ["METRIC_VERSION", "evaluate_farm_dcore_v5"]
