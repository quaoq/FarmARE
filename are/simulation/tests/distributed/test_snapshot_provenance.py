import pytest

from are.simulation.distributed.evaluator_v5 import _current_source
from are.simulation.distributed.models import FactVersionRecord
from are.simulation.distributed.petri import DataGuardSpec


def fact(identifier, value, time, scope=(20, 43), authoritative=True):
    return FactVersionRecord(
        version_id=identifier,
        fact_key="ready",
        value=value,
        source_event_id=identifier,
        scope=scope,
        world_time=time,
        authoritative=authoritative,
    )


@pytest.mark.parametrize(
    "updates, expected",
    [
        ([fact("same", True, 1)], True),
        ([fact("changed", False, 1)], False),
        ([fact("away", False, 1), fact("back", True, 2)], False),
        ([fact("other_region", False, 1, scope=(0, 19))], True),
        ([fact("different_aggregate", False, 1, scope=(20, 21))], True),
        ([fact("future_change", False, 10)], True),
    ],
)
def test_repeat_reads_and_actual_changes_have_distinct_provenance(updates, expected):
    root = fact("origin", True, 0)
    guard = DataGuardSpec(guard_id="guard", fact_key="ready", scope=(20, 43))
    assert _current_source(root, guard, [root, *updates], at=3) is expected


def test_unverified_or_future_root_cannot_establish_current_provenance():
    guard = DataGuardSpec(guard_id="guard", fact_key="ready", scope=(20, 43))
    for root in (fact("claim", True, 0, authoritative=False), fact("future", True, 4)):
        assert not _current_source(root, guard, [root], at=3)


def test_regional_aggregate_requires_exact_scope_when_declared():
    from are.simulation.distributed.evaluator_v5 import _guard_verdict
    from are.simulation.distributed.models import DistributedTrace

    broad = fact("field_any", True, 0, (0, 63))
    trace = DistributedTrace(
        run_id="scope",
        task_id="scope",
        actors=("a",),
        events=(),
        fact_versions=(broad,),
    )
    guard = DataGuardSpec(
        guard_id="regional", fact_key="ready", scope=(20, 43), scope_match="exact"
    )
    assert _guard_verdict(guard, trace=trace, at=1)[0] == "unknown"
    regional = fact("regional", False, 0, (20, 43))
    trace = trace.model_copy(update={"fact_versions": (broad, regional)})
    assert _guard_verdict(guard, trace=trace, at=1)[0] == "false"
    # Historical scope defaults preserve their serialized representation.
    assert (
        "scope_match"
        not in DataGuardSpec(guard_id="old", fact_key="ready").model_dump()
    )


def test_legacy_absolute_fault_serialization_has_no_new_default_field():
    from are.simulation.distributed.scientific_v5 import CommunicationFaultTreatmentSpec

    old = CommunicationFaultTreatmentSpec(
        fault_id="old", mode="drop", target_ids=("message",)
    )
    assert "delivery_delay_seconds" not in old.model_dump()


def test_observation_origin_is_bound_to_returned_scope_and_native_request():
    from are.simulation.distributed.farm_adapter import ExtractedFact
    from are.simulation.distributed.knowledge import KnowledgeStore
    from are.simulation.distributed.models import EventKind
    from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
    from are.simulation.distributed.trace import CausalTraceRecorder

    class Adapter:
        def extract_observed(self, **kwargs):
            return (ExtractedFact("ready", True, (20, 43), 86400),)

        def authoritative_snapshot(self, **kwargs):
            assert kwargs["args"] == {"start_ridge": 20, "end_ridge": 43}
            assert kwargs["source_event_id"] == action.event_id
            return (
                fact("this_read", True, 10).model_copy(
                    update={"source_event_id": kwargs["source_event_id"]}
                ),
            )

    recorder = CausalTraceRecorder("origin", "farm", ("field_intelligence",))
    # Same timestamp, unrelated request, lexicographically later ID and wrong
    # regional aggregate. This used to win the timestamp-only origin lookup.
    unrelated = recorder.record(
        EventKind.ACTION, "field_intelligence", 0, world_time=10
    )
    recorder.add_fact_version(
        fact("zz_wrong_request", False, 10, (0, 63)).model_copy(
            update={"source_event_id": unrelated.event_id}
        )
    )
    action = recorder.record(EventKind.ACTION, "field_intelligence", 0, world_time=10)
    store = KnowledgeStore("field_intelligence")
    NativeDistributedSeasonRunner._record_observation_facts(
        recorder=recorder,
        store=store,
        all_stores={"field_intelligence": store},
        shared=False,
        evidence_actor=True,
        adapter=Adapter(),
        actor_id="field_intelligence",
        action_event_id=action.event_id,
        farmare_event_id="read-123",
        action="SensorApp__read_soil",
        args={"sensor_id": "opaque"},
        result={},
        logical_time=0.1,
        world_time=10,
        phase="reproduction",
        provenance_ids=set(),
    )
    observation = next(f for f in recorder.fact_versions if not f.authoritative)
    assert observation.origin_version_id == "this_read"
    assert observation.scope == (20, 43)
    assert len(store.items) == 1
    assert all(not f.visible_to for f in recorder.fact_versions if f.authoritative)
