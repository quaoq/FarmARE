"""Controlled v5 provenance mutants with planted earliest-link ground truth.

The mutations operate on exact event, message, decision, and fact-version IDs.
They never set runtime violation labels and therefore exercise the independent
v5 evaluator rather than confirming conclusions planted in the trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from are.simulation.distributed.evaluator_v5 import evaluate_farm_dcore_v5
from are.simulation.distributed.farm_mutants import attribution_confusion
from are.simulation.distributed.models import (
    DistributedTrace,
    EventKind,
    FactVersionRecord,
)
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5


@dataclass(frozen=True)
class FarmMutationCaseV5:
    name: str
    trace: DistributedTrace
    obligation_id: str
    expected_localization: str


def _root_id(fact: FactVersionRecord, facts: dict[str, FactVersionRecord]) -> str:
    seen: set[str] = set()
    current = fact
    while current.origin_version_id:
        if current.version_id in seen:
            raise ValueError("fact provenance contains a cycle")
        seen.add(current.version_id)
        current = facts[current.origin_version_id]
    return current.version_id


def _clean_trace(
    trace: DistributedTrace,
    *,
    event_ids: set[str] | None = None,
    fact_ids: set[str] | None = None,
) -> DistributedTrace:
    removed_events = event_ids or set()
    removed_facts = fact_ids or set()
    return trace.model_copy(
        update={
            "events": tuple(
                event.model_copy(
                    update={
                        "causal_parents": tuple(
                            parent
                            for parent in event.causal_parents
                            if parent not in removed_events
                        )
                    }
                )
                for event in trace.events
                if event.event_id not in removed_events
            ),
            "fact_versions": tuple(
                fact
                for fact in trace.fact_versions
                if fact.version_id not in removed_facts
            ),
            "decisions": tuple(
                decision.model_copy(
                    update={
                        "knowledge_snapshot": decision.knowledge_snapshot.model_copy(
                            update={
                                "item_ids": tuple(
                                    item
                                    for item in decision.knowledge_snapshot.item_ids
                                    if item not in removed_facts
                                )
                            }
                        ),
                        "prompt_item_ids": tuple(
                            item
                            for item in decision.prompt_item_ids
                            if item not in removed_facts
                        ),
                    }
                )
                for decision in trace.decisions
            ),
        }
    )


def _without_decision_facts(
    trace: DistributedTrace, decision_id: str, fact_ids: set[str]
) -> DistributedTrace:
    return trace.model_copy(
        update={
            "decisions": tuple(
                decision.model_copy(
                    update={
                        "knowledge_snapshot": decision.knowledge_snapshot.model_copy(
                            update={
                                "item_ids": tuple(
                                    item
                                    for item in decision.knowledge_snapshot.item_ids
                                    if item not in fact_ids
                                )
                            }
                        ),
                        "prompt_item_ids": tuple(
                            item
                            for item in decision.prompt_item_ids
                            if item not in fact_ids
                        ),
                    }
                )
                if decision.decision_id == decision_id
                else decision
                for decision in trace.decisions
            )
        }
    )


def _fixture(process: FarmProcessSpecV5, trace: DistributedTrace) -> dict[str, Any]:
    metrics = evaluate_farm_dcore_v5(process, trace)
    facts = {item.version_id: item for item in trace.fact_versions}
    events = {item.event_id: item for item in trace.events}
    decisions = {item.decision_id: item for item in trace.decisions}
    for obligation in metrics["semantic_causal_obligations"]["details"]:
        if (
            not obligation.get("fact_key")
            or not obligation["passed"]
            or not obligation.get("target_event_ids")
        ):
            continue
        target = events[obligation["target_event_ids"][0]]
        decision = decisions.get(target.decision_context_id or "")
        if decision is None:
            continue
        visible = set(decision.knowledge_snapshot.item_ids)
        local = [
            fact
            for fact in facts.values()
            if fact.fact_key == obligation["fact_key"] and fact.version_id in visible
        ]
        if not local:
            continue
        chosen = max(
            local,
            key=lambda fact: (
                fact.learned_time if fact.learned_time is not None else fact.world_time,
                fact.world_time,
                fact.version_id,
            ),
        )
        source = events[chosen.source_event_id]
        if source.kind != EventKind.MESSAGE_RECEIVE or not source.message_id:
            continue
        root = _root_id(chosen, facts)
        descendants = {
            fact.version_id
            for fact in facts.values()
            if fact.fact_key == chosen.fact_key and _root_id(fact, facts) == root
        }
        observation_events = {
            facts[fact_id].source_event_id
            for fact_id in descendants
            if not facts[fact_id].authoritative
            and events[facts[fact_id].source_event_id].kind == EventKind.OBSERVATION
        }
        message_events = {
            event.event_id
            for event in trace.events
            if event.message_id == source.message_id
            and event.kind in {EventKind.MESSAGE_SEND, EventKind.MESSAGE_RECEIVE}
        }
        send = next(
            (
                event
                for event in trace.events
                if event.message_id == source.message_id
                and event.kind == EventKind.MESSAGE_SEND
            ),
            None,
        )
        if observation_events and send is not None:
            root_fact = facts[root]
            normalized = trace
            # Engineering migration traces predate authoritative provenance for
            # synthetic phase-evidence facts.  Build a common controlled base
            # fixture with one explicit world root; every mutant below shares
            # this same exogenous record, so the planted defect remains the only
            # between-case difference.  Frozen paper traces must already have
            # such a root and take the first branch unchanged.
            if not root_fact.authoritative:
                world_event_id = f"mutant:v5:world:{root_fact.version_id}"
                world_fact_id = f"mutant:v5:authoritative:{root_fact.version_id}"
                world_sequence = 1 + max(
                    (
                        event.local_sequence
                        for event in trace.events
                        if event.actor_id == "world"
                    ),
                    default=0,
                )
                world_event = next(iter(trace.events)).model_copy(
                    update={
                        "event_id": world_event_id,
                        "kind": EventKind.WORLD_EFFECT,
                        "actor_id": "world",
                        "local_sequence": world_sequence,
                        "logical_time": root_fact.world_time,
                        "world_time": root_fact.world_time,
                        "vector_clock": {"world": world_sequence},
                        "action": "controlled_authoritative_fact",
                        "args": {},
                        "causal_parents": (),
                        "evidence_ids": (),
                        "message_id": None,
                        "decision_context_id": None,
                        "farmare_event_id": None,
                        "status": "ok",
                        "payload": {"controlled_fixture": True},
                        "season_phase": root_fact.season_phase,
                        "fact_version": world_fact_id,
                    }
                )
                world_fact = root_fact.model_copy(
                    update={
                        "version_id": world_fact_id,
                        "source_event_id": world_event_id,
                        "origin_version_id": None,
                        "authoritative": True,
                        "visible_to": (),
                        "learned_time": None,
                    }
                )
                normalized = trace.model_copy(
                    update={
                        "events": (*trace.events, world_event),
                        "fact_versions": (
                            *(
                                fact.model_copy(
                                    update={"origin_version_id": world_fact_id}
                                )
                                if fact.version_id == root_fact.version_id
                                else fact
                                for fact in trace.fact_versions
                            ),
                            world_fact,
                        ),
                    }
                )
                descendants.add(world_fact_id)
            return {
                "trace": normalized,
                "obligation_id": obligation["obligation_id"],
                "target": target,
                "decision": decision,
                "chosen": chosen,
                "descendants": descendants,
                "observation_events": observation_events,
                "message_events": message_events,
                "send_event_id": send.event_id,
                "receive_event_id": source.event_id,
            }
    raise ValueError("trace has no complete observation-to-message-to-action fixture")


def build_farm_mutant_suite_v5(
    process: FarmProcessSpecV5, trace: DistributedTrace
) -> tuple[FarmMutationCaseV5, ...]:
    """Plant one defect at each recorded information-path link."""

    item = _fixture(process, trace)
    trace = item["trace"]
    obligation_id = item["obligation_id"]
    decision_id = item["decision"].decision_id
    chosen_id = item["chosen"].version_id
    descendants = set(item["descendants"])
    receive_derived = {
        fact.version_id
        for fact in trace.fact_versions
        if fact.version_id in descendants
        and fact.source_event_id == item["receive_event_id"]
    }

    observation_gap = _clean_trace(
        trace,
        event_ids=set(item["observation_events"]) | set(item["message_events"]),
        fact_ids={
            fact_id
            for fact_id in descendants
            if not next(
                fact for fact in trace.fact_versions if fact.version_id == fact_id
            ).authoritative
        },
    )
    handoff_omission = _clean_trace(
        trace,
        event_ids=set(item["message_events"]),
        fact_ids=receive_derived,
    )
    transit_gap = _clean_trace(
        trace,
        event_ids={item["receive_event_id"]},
        fact_ids=receive_derived,
    )
    uptake_error = _without_decision_facts(trace, decision_id, descendants)
    unsupported_claim = trace.model_copy(
        update={
            "fact_versions": tuple(
                fact.model_copy(update={"origin_version_id": None, "evidence_ids": ()})
                if fact.version_id == chosen_id
                else fact
                for fact in trace.fact_versions
            )
        }
    )
    stale_information = trace.model_copy(
        update={
            "fact_versions": tuple(
                fact.model_copy(update={"valid_until": item["target"].world_time - 1.0})
                if fact.version_id == chosen_id
                else fact
                for fact in trace.fact_versions
            )
        }
    )
    return (
        FarmMutationCaseV5(
            "observation_gap", observation_gap, obligation_id, "observation_gap"
        ),
        FarmMutationCaseV5(
            "handoff_omission",
            handoff_omission,
            obligation_id,
            "handoff_omission",
        ),
        FarmMutationCaseV5("transit_gap", transit_gap, obligation_id, "transit_gap"),
        FarmMutationCaseV5("uptake_error", uptake_error, obligation_id, "uptake_error"),
        FarmMutationCaseV5(
            "unsupported_claim",
            unsupported_claim,
            obligation_id,
            "unsupported_claim",
        ),
        FarmMutationCaseV5(
            "stale_information",
            stale_information,
            obligation_id,
            "stale_information",
        ),
    )


def evaluate_farm_mutant_suite_v5(
    process: FarmProcessSpecV5, cases: tuple[FarmMutationCaseV5, ...]
) -> dict[str, Any]:
    rows = []
    expected_and_predicted = []
    for case in cases:
        metrics = evaluate_farm_dcore_v5(process, case.trace)
        located = next(
            (
                item
                for item in metrics["provenance_failure_localization"]
                if item["obligation_id"] == case.obligation_id
            ),
            None,
        )
        predicted = located["primary"] if located else None
        expected_and_predicted.append((case.expected_localization, predicted))
        rows.append(
            {
                "mutation": case.name,
                "obligation_id": case.obligation_id,
                "expected": case.expected_localization,
                "predicted": predicted,
                "passed": predicted == case.expected_localization,
            }
        )
    return {
        "schema_version": "farm_dcore_v5_mutant_report",
        "rows": rows,
        "localization_metrics": attribution_confusion(expected_and_predicted),
        "runtime_violation_labels_used": False,
    }


__all__ = [
    "FarmMutationCaseV5",
    "build_farm_mutant_suite_v5",
    "evaluate_farm_mutant_suite_v5",
]
