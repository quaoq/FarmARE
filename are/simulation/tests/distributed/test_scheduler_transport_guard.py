from __future__ import annotations

from are.simulation.distributed.guard import CausalGuard
from are.simulation.distributed.knowledge import KnowledgeStore
from are.simulation.distributed.models import (
    ActorSpec,
    CausalHandoff,
    Claim,
    EpistemicStatus,
    FactRequirement,
    FreeTextEnvelope,
    GuardVerdict,
    KnowledgeItem,
)
from are.simulation.distributed.scheduler import (
    DeterministicScheduler,
    SchedulerPriority,
    enumerate_interleavings,
)
from are.simulation.distributed.transport import (
    FaultMode,
    FaultRule,
    FaultSchedule,
    InProcessTransport,
)


def test_scheduler_stable_order_and_interleavings():
    scheduler = DeterministicScheduler()
    scheduler.schedule(1, SchedulerPriority.ACTOR_ACTIVATION, "b", "b")
    scheduler.schedule(1, SchedulerPriority.MESSAGE_DELIVERY, "a", "delivery")
    scheduler.schedule(1, SchedulerPriority.ACTOR_ACTIVATION, "a", "a")
    assert [scheduler.pop().kind for _ in range(3)] == ["delivery", "a", "b"]
    schedules = enumerate_interleavings(("a", "b", "c"), (("a", "c"),))
    assert set(schedules) == {("a", "b", "c"), ("a", "c", "b"), ("b", "a", "c")}


def test_transport_delay_drop_duplicate_and_reorder():
    actors = ("a", "b")
    delayed = InProcessTransport(
        actors, FaultSchedule(default=FaultRule(mode=FaultMode.DELAY, delay=2))
    )
    delayed.send(FreeTextEnvelope(sender="a", recipient="b", text="x"), 0)
    assert delayed.deliver_next(1) == []
    assert len(delayed.deliver_next(2)) == 1

    dropped = InProcessTransport(
        actors, FaultSchedule(default=FaultRule(mode=FaultMode.DROP))
    )
    message = dropped.send(FreeTextEnvelope(sender="a", recipient="b", text="x"), 0)
    assert message.message_id in dropped.snapshot()["dropped"]
    assert dropped.watermark("b")

    duplicate = InProcessTransport(
        actors,
        FaultSchedule(default=FaultRule(mode=FaultMode.DUPLICATE, duplicate_delay=1)),
    )
    duplicate.send(CausalHandoff(sender="a", recipient="b"), 0)
    assert len(duplicate.deliver_next(1)) == 2

    reordered = InProcessTransport(
        actors,
        FaultSchedule(
            by_send_index={
                1: FaultRule(mode=FaultMode.REORDER, delay=3, reorder_bias=1)
            }
        ),
    )
    first = reordered.send(FreeTextEnvelope(sender="a", recipient="b", text="old"), 0)
    second = reordered.send(FreeTextEnvelope(sender="a", recipient="b", text="new"), 1)
    deliveries = reordered.deliver_next(4)
    assert [delivery.envelope.message_id for delivery in deliveries] == [
        second.message_id,
        first.message_id,
    ]

    version_targeted = InProcessTransport(
        actors,
        FaultSchedule(
            by_fact_version={"fact:stable-v7": FaultRule(mode=FaultMode.DROP)}
        ),
    )
    targeted = version_targeted.send(
        CausalHandoff(
            sender="a",
            recipient="b",
            claims=(
                Claim(
                    fact_key="disease:confirmed",
                    value=True,
                    fact_version_id="fact:stable-v7",
                    observed_at=0,
                ),
            ),
        ),
        0,
    )
    assert targeted.message_id in version_targeted.snapshot()["dropped"]

    phase_targeted = InProcessTransport(
        actors,
        FaultSchedule(
            by_message_prefix={
                "handoff:midseason:": FaultRule(mode=FaultMode.DROP)
            }
        ),
    )
    phase_message = phase_targeted.send(
        FreeTextEnvelope(
            message_id="handoff:midseason:controller-defined-id",
            sender="a",
            recipient="b",
            text="critical evidence",
        ),
        0,
    )
    assert phase_message.message_id in phase_targeted.snapshot()["dropped"]


def test_guard_true_unknown_stale_and_watermark():
    actor = ActorSpec(actor_id="operator", permitted_actions=("write",))
    requirement = FactRequirement(
        requirement_id="r",
        actor_id="operator",
        action="write",
        fact_key="ready",
        max_age=5,
        require_evidence=True,
    )
    store = KnowledgeStore("operator")
    guard = CausalGuard()
    unknown = guard.evaluate(
        actor=actor,
        action="write",
        requirements=(requirement,),
        knowledge=store,
        logical_time=1,
        evidence_ids=set(),
        transport_closed=False,
    )
    assert unknown.verdict == GuardVerdict.DEFER
    item = KnowledgeItem(
        item_id="e1",
        fact_key="ready",
        value=True,
        status=EpistemicStatus.OBSERVED,
        source_actor="scout",
        evidence_ids=("e1",),
        observed_at=0,
        learned_at=0,
    )
    store.add(item)
    waiting = guard.evaluate(
        actor=actor,
        action="write",
        requirements=(requirement,),
        knowledge=store,
        logical_time=1,
        evidence_ids={"e1"},
        transport_closed=False,
    )
    assert waiting.verdict == GuardVerdict.DEFER
    allowed = guard.evaluate(
        actor=actor,
        action="write",
        requirements=(requirement,),
        knowledge=store,
        logical_time=1,
        evidence_ids={"e1"},
        transport_closed=True,
    )
    assert allowed.verdict == GuardVerdict.ALLOW
    stale = guard.evaluate(
        actor=actor,
        action="write",
        requirements=(requirement,),
        knowledge=store,
        logical_time=6,
        evidence_ids={"e1"},
        transport_closed=True,
    )
    assert stale.verdict == GuardVerdict.BLOCK
