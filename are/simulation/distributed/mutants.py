"""Controlled one-defect trace mutations for metric validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from are.simulation.distributed.models import DistributedTrace, EventKind, TraceEvent


@dataclass(frozen=True)
class MutationManifest:
    mutation: str
    source_event_ids: tuple[str, ...]
    expected_attribution: str | None = None
    invariant: str | None = None


def _replace_events(trace: DistributedTrace, events) -> DistributedTrace:
    normalized: list[TraceEvent] = []
    sequences: dict[str, int] = {}
    clocks = {actor: 0 for actor in (*trace.actors, "world")}
    emitted_ids: set[str] = set()
    send_clocks: dict[str, dict[str, int]] = {}
    for index, event in enumerate(events):
        event_id = event.event_id
        if event_id in emitted_ids:
            event_id = f"{event.event_id}:mut:{index:06d}"
        if event.kind == EventKind.MESSAGE_RECEIVE and event.message_id in send_clocks:
            for actor, value in send_clocks[event.message_id].items():
                clocks[actor] = max(clocks.get(actor, 0), value)
        sequences[event.actor_id] = sequences.get(event.actor_id, 0) + 1
        clocks[event.actor_id] = clocks.get(event.actor_id, 0) + 1
        updated = event.model_copy(
            update={
                "event_id": event_id,
                "local_sequence": sequences[event.actor_id],
                "vector_clock": dict(clocks),
                "causal_parents": tuple(
                    parent for parent in event.causal_parents if parent in emitted_ids
                ),
            }
        )
        normalized.append(updated)
        emitted_ids.add(event_id)
        if updated.kind == EventKind.MESSAGE_SEND and updated.message_id:
            send_clocks[updated.message_id] = updated.vector_clock
    decision_ids = {
        event.event_id for event in normalized if event.kind == EventKind.DECISION
    }
    decisions = tuple(
        decision for decision in trace.decisions if decision.decision_id in decision_ids
    )
    return trace.model_copy(
        update={"events": tuple(normalized), "decisions": decisions}
    )


def swap_concurrent(
    trace: DistributedTrace, left: str, right: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = list(trace.events)
    positions = {event.event_id: index for index, event in enumerate(events)}
    i, j = positions[left], positions[right]
    if events[i].actor_id == events[j].actor_id:
        raise ValueError("concurrent-swap fixtures must use different actors")
    events[i], events[j] = events[j], events[i]
    return trace.model_copy(update={"events": tuple(events)}), MutationManifest(
        "concurrent_permutation", (left, right), invariant="dcore_score"
    )


def reorder_within_actor(
    trace: DistributedTrace, left: str, right: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = list(trace.events)
    positions = {event.event_id: index for index, event in enumerate(events)}
    i, j = positions[left], positions[right]
    if events[i].actor_id != events[j].actor_id:
        raise ValueError("within-agent reorder requires one actor")
    events[i], events[j] = events[j], events[i]
    return _replace_events(trace, events), MutationManifest(
        "within_agent_reorder", (left, right), expected_attribution="reasoning_error"
    )


def remove_event(
    trace: DistributedTrace, event_id: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = [event for event in trace.events if event.event_id != event_id]
    return _replace_events(trace, events), MutationManifest(
        "missing_event", (event_id,)
    )


def add_harmful_duplicate(
    trace: DistributedTrace, event_id: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = list(trace.events)
    original = next(event for event in events if event.event_id == event_id)
    duplicate = original.model_copy(
        update={"payload": {**original.payload, "harmful": True}}
    )
    events.append(duplicate)
    return _replace_events(trace, events), MutationManifest(
        "harmful_extra_write", (event_id,)
    )


def drop_receive(
    trace: DistributedTrace, message_id: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = [
        event
        for event in trace.events
        if not (
            event.kind == EventKind.MESSAGE_RECEIVE and event.message_id == message_id
        )
    ]
    return _replace_events(trace, events), MutationManifest(
        "dropped_handoff", (message_id,), expected_attribution="transit_gap"
    )


def delay_receive(
    trace: DistributedTrace, message_id: str, delay: float
) -> tuple[DistributedTrace, MutationManifest]:
    events = [
        event.model_copy(
            update={
                "logical_time": event.logical_time + delay,
                "world_time": event.world_time + delay,
            }
        )
        if event.kind == EventKind.MESSAGE_RECEIVE and event.message_id == message_id
        else event
        for event in trace.events
    ]
    events.sort(key=lambda event: (event.logical_time, event.event_id))
    return _replace_events(trace, events), MutationManifest(
        "delayed_handoff", (message_id,), expected_attribution="transit_gap"
    )


def omit_uptake(
    trace: DistributedTrace, actor_id: str, message_id: str
) -> tuple[DistributedTrace, MutationManifest]:
    decisions = []
    for decision in trace.decisions:
        if decision.actor_id != actor_id:
            decisions.append(decision)
            continue
        snapshot = decision.knowledge_snapshot
        item_ids = tuple(
            item_id
            for item_id in snapshot.item_ids
            if not item_id.startswith(f"{message_id}:claim:")
        )
        decisions.append(
            decision.model_copy(
                update={
                    "knowledge_snapshot": snapshot.model_copy(
                        update={"item_ids": item_ids}
                    )
                }
            )
        )
    return trace.model_copy(update={"decisions": tuple(decisions)}), MutationManifest(
        "uptake_omission", (message_id,), expected_attribution="uptake_error"
    )


def mark_unsupported_claim(
    trace: DistributedTrace, action_event_id: str, requirement_id: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = []
    for event in trace.events:
        if event.event_id == action_event_id:
            violations = list(event.payload.get("violated_requirements", []))
            if requirement_id not in violations:
                violations.append(requirement_id)
            event = event.model_copy(
                update={
                    "payload": {
                        **event.payload,
                        "violated_requirements": violations,
                        "unsupported_claim": True,
                    }
                }
            )
        events.append(event)
    return trace.model_copy(update={"events": tuple(events)}), MutationManifest(
        "unsupported_claim",
        (action_event_id,),
        expected_attribution="unsupported_claim",
    )


def mark_stale_information(
    trace: DistributedTrace, action_event_id: str, requirement_id: str, fact_key: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = []
    for event in trace.events:
        if event.event_id == action_event_id:
            violations = list(event.payload.get("violated_requirements", []))
            if requirement_id not in violations:
                violations.append(requirement_id)
            event = event.model_copy(
                update={
                    "payload": {
                        **event.payload,
                        "violated_requirements": violations,
                        "stale_facts": [
                            *event.payload.get("stale_facts", []),
                            fact_key,
                        ],
                    }
                }
            )
        events.append(event)
    return trace.model_copy(update={"events": tuple(events)}), MutationManifest(
        "out_of_order_stale_handoff",
        (action_event_id,),
        expected_attribution="stale_information",
    )


def omit_handoff(
    trace: DistributedTrace, message_id: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = [
        event
        for event in trace.events
        if not (
            event.message_id == message_id
            and event.kind in {EventKind.MESSAGE_SEND, EventKind.MESSAGE_RECEIVE}
        )
    ]
    return _replace_events(trace, events), MutationManifest(
        "missing_handoff", (message_id,), expected_attribution="handoff_omission"
    )


def duplicate_action(
    trace: DistributedTrace, event_id: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = list(trace.events)
    original = next(event for event in events if event.event_id == event_id)
    events.append(original)
    return _replace_events(trace, events), MutationManifest(
        "duplicate_action", (event_id,), invariant="required_event_coverage"
    )


def remove_observation(
    trace: DistributedTrace, fact_key: str
) -> tuple[DistributedTrace, MutationManifest]:
    events = [
        event
        for event in trace.events
        if not (
            event.kind == EventKind.OBSERVATION
            and event.payload.get("fact_key") == fact_key
        )
    ]
    return _replace_events(trace, events), MutationManifest(
        "observation_gap", (fact_key,), expected_attribution="observation_gap"
    )


def add_harmless_read(
    trace: DistributedTrace, actor_id: str, action: str = "harmless_read"
) -> tuple[DistributedTrace, MutationManifest]:
    actor_events = [event for event in trace.events if event.actor_id == actor_id]
    clock = dict(actor_events[-1].vector_clock) if actor_events else {actor_id: 0}
    clock[actor_id] = clock.get(actor_id, 0) + 1
    event = TraceEvent(
        event_id=f"{trace.run_id}:mut:harmless",
        kind=EventKind.ACTION,
        actor_id=actor_id,
        local_sequence=(actor_events[-1].local_sequence + 1 if actor_events else 1),
        logical_time=max((item.logical_time for item in trace.events), default=0) + 1,
        world_time=max((item.world_time for item in trace.events), default=0) + 1,
        vector_clock=clock,
        action=action,
        payload={"harmful": False},
    )
    return trace.model_copy(
        update={"events": (*trace.events, event)}
    ), MutationManifest(
        "redundant_harmless_read", (event.event_id,), invariant="event_fidelity"
    )


MUTATORS: dict[str, Callable] = {
    "concurrent_permutation": swap_concurrent,
    "within_agent_reorder": reorder_within_actor,
    "missing_event": remove_event,
    "harmful_extra_write": add_harmful_duplicate,
    "dropped_handoff": drop_receive,
    "delayed_handoff": delay_receive,
    "missing_handoff": omit_handoff,
    "out_of_order_stale_handoff": mark_stale_information,
    "uptake_omission": omit_uptake,
    "unsupported_claim": mark_unsupported_claim,
    "observation_gap": remove_observation,
    "duplicate_action": duplicate_action,
    "redundant_harmless_read": add_harmless_read,
}
