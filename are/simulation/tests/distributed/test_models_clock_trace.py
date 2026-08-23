from __future__ import annotations

import pytest

from are.simulation.distributed.clock import (
    ClockRelation,
    compare,
    happens_before,
    merge,
    tick,
)
from are.simulation.distributed.models import (
    ActorSpec,
    CausalEdgeSpec,
    DistributedTaskSpec,
    EventKind,
    ReferenceEventSpec,
)
from are.simulation.distributed.trace import CausalTraceRecorder, validate_trace


def _actors():
    return (ActorSpec(actor_id="a"), ActorSpec(actor_id="b"))


def test_vector_clock_order_and_concurrency():
    origin = {"a": 0, "b": 0}
    a1 = tick(origin, "a")
    b1 = tick(origin, "b")
    assert compare(a1, b1) == ClockRelation.CONCURRENT
    received = tick(merge(b1, a1), "b")
    assert happens_before(a1, received)
    assert happens_before(b1, received)


def test_spec_rejects_cycles_and_transitive_edges():
    events = tuple(
        ReferenceEventSpec(event_id=name, actor_id="a", action=name)
        for name in ("x", "y", "z")
    )
    with pytest.raises(ValueError, match="cycle"):
        DistributedTaskSpec(
            task_id="cycle",
            actors=_actors(),
            events=events,
            causal_edges=(
                CausalEdgeSpec(source="x", target="y"),
                CausalEdgeSpec(source="y", target="x"),
            ),
        )
    with pytest.raises(ValueError, match="redundant"):
        DistributedTaskSpec(
            task_id="redundant",
            actors=_actors(),
            events=events,
            causal_edges=(
                CausalEdgeSpec(source="x", target="y"),
                CausalEdgeSpec(source="y", target="z"),
                CausalEdgeSpec(source="x", target="z"),
            ),
        )


def test_trace_message_clock_and_independent_events():
    recorder = CausalTraceRecorder("run", "task", ("a", "b"))
    independent_a = recorder.record(EventKind.ACTION, "a", 0, action="a")
    independent_b = recorder.record(EventKind.ACTION, "b", 0, action="b")
    assert (
        compare(independent_a.vector_clock, independent_b.vector_clock)
        == ClockRelation.CONCURRENT
    )
    send = recorder.record(EventKind.MESSAGE_SEND, "a", 1, message_id="m1")
    receive = recorder.record(
        EventKind.MESSAGE_RECEIVE,
        "b",
        2,
        received_clock=send.vector_clock,
        message_id="m1",
        causal_parents=(send.event_id,),
    )
    trace = recorder.build()
    validate_trace(trace)
    assert happens_before(send.vector_clock, receive.vector_clock)


def test_trace_rejects_missing_parent():
    recorder = CausalTraceRecorder("run", "task", ("a", "b"))
    recorder.record(EventKind.ACTION, "a", 0, action="x", causal_parents=("missing",))
    with pytest.raises(ValueError, match="missing or forward parent"):
        recorder.build()


def test_explicit_world_effect_parent_is_reflected_in_vector_clock():
    recorder = CausalTraceRecorder("run", "task", ("a", "b"))
    action = recorder.record(EventKind.ACTION, "a", 1, action="write")
    effect = recorder.record(
        EventKind.WORLD_EFFECT,
        "world",
        2,
        action="effect",
        causal_parents=(action.event_id,),
    )
    recorder.build()
    assert happens_before(action.vector_clock, effect.vector_clock)
