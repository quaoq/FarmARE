from __future__ import annotations

from are.simulation.distributed.evaluator import evaluate_dcore
from are.simulation.distributed.models import (
    ActorSpec,
    CausalEdgeSpec,
    DistributedTaskSpec,
    EventKind,
    ReferenceEventSpec,
)
from are.simulation.distributed.trace import CausalTraceRecorder


def _spec(with_edge: bool = False) -> DistributedTaskSpec:
    return DistributedTaskSpec(
        task_id="metric",
        actors=(ActorSpec(actor_id="a"), ActorSpec(actor_id="b")),
        events=(
            ReferenceEventSpec(event_id="x", actor_id="a", action="x"),
            ReferenceEventSpec(event_id="y", actor_id="b", action="y"),
        ),
        causal_edges=(CausalEdgeSpec(source="x", target="y"),) if with_edge else (),
    )


def test_perfect_trace_and_concurrent_serialization_invariance():
    recorder = CausalTraceRecorder("run", "metric", ("a", "b"))
    a = recorder.record(EventKind.ACTION, "a", 0, action="x")
    b = recorder.record(EventKind.ACTION, "b", 0, action="y")
    trace = recorder.build()
    first = evaluate_dcore(_spec(), trace)
    swapped = trace.model_copy(update={"events": (b, a)})
    second = evaluate_dcore(_spec(), swapped)
    assert first["dcore_score"] == second["dcore_score"] == 1.0


def test_cross_agent_prerequisite_requires_happens_before():
    recorder = CausalTraceRecorder("run", "metric", ("a", "b"))
    recorder.record(EventKind.ACTION, "a", 0, action="x")
    recorder.record(EventKind.ACTION, "b", 0, action="y")
    metrics = evaluate_dcore(_spec(with_edge=True), recorder.build())
    assert metrics["event_fidelity"] == 1.0
    assert metrics["causal_conformance"] == 0.0
    assert metrics["dcore_score"] == 0.5
    assert metrics["attribution"][0]["primary"] == "composition_error"


def test_missing_and_harmful_extra_reduce_fidelity():
    spec = DistributedTaskSpec(
        task_id="harm",
        actors=(ActorSpec(actor_id="a"), ActorSpec(actor_id="b")),
        events=(
            ReferenceEventSpec(event_id="required", actor_id="a", action="required"),
            ReferenceEventSpec(
                event_id="harm", actor_id="a", action="harm", harmful=True
            ),
        ),
    )
    recorder = CausalTraceRecorder("run", "harm", ("a", "b"))
    recorder.record(EventKind.ACTION, "a", 0, action="harm")
    metrics = evaluate_dcore(spec, recorder.build())
    assert metrics["coverage"] == 0.0
    assert metrics["event_fidelity"] == 0.0
    assert metrics["harmful_extra_cost"] == 1.0
