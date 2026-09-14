"""Local execution memory survives time polling without inventing completed work."""

from collections import deque
from types import SimpleNamespace

import pytest

from are.simulation.distributed.controllers import FarmAREBaseAgentController


def controller(actor="operations"):
    value = object.__new__(FarmAREBaseAgentController)
    value.base_agent = SimpleNamespace(
        agent_id=actor, append_agent_log=lambda log: None, make_timestamp=lambda: 0
    )
    value.accepted_write_receipts = deque(maxlen=32)
    value.recent_failures = deque(maxlen=8)
    value.accepted_field_work = {}
    return value


def result(start=0, end=3, *, action="TractorApp__plant_seeds", actor="operations"):
    args = {"start_ridge": start, "end_ridge": end, "depth_cm": 4.0}
    identity = f"{actor}:{action}:{start}:{end}"
    return {
        "selected_action": action,
        "arguments": args,
        "intent_kind": "act",
        "intent_id": identity,
        "executed": True,
        "result_world_time": 10,
        "execution_receipt": {
            "status": "accepted",
            "actor_id": actor,
            "intent_id": identity,
            "action": action,
            "arguments": args,
            "receipt_digest": identity,
        },
    }


def test_planting_coverage_survives_more_than_a_recent_window_of_time_calls():
    c = controller()
    for start in range(0, 28, 4):
        c.observe(result(start, start + 3))
    for _ in range(100):
        c.observe(result(action="SystemApp__advance_time"))
    assert all(
        r["action"] == "SystemApp__advance_time" for r in c.accepted_write_receipts
    )
    assert set(ridge for _, ridge in c.accepted_field_work) == set(range(28))
    assert len(c._field_work_memory()) == 7
    assert c._field_work_memory()[0]["arguments"]["depth_cm"] == 4.0
    assert c._field_work_memory()[0]["result_world_time"] == 10
    assert controller("field_intelligence")._field_work_memory() == []
    from are.simulation.distributed.models import ActorSpec, LocalView

    c.base_agent.logs = []
    c.max_model_calls = 350
    c.knowledge_window = c.message_window = 10
    view = LocalView(
        actor=ActorSpec(actor_id="operations"),
        logical_time=1,
        world_time=20,
        knowledge=(),
        inbox=(),
        vector_clock={},
    )
    text = c._render_local_context(view)
    assert '"historical_accepted_field_work": [{' in text
    assert '"remaining_actor_requests": 350' in text
    assert '"end_ridge": 27' in text


@pytest.mark.parametrize(
    "change",
    [
        {"executed": False},
        {"error": "blocked"},
        {"intent_id": "unrelated"},
        {"selected_action": "TractorApp__harvest"},
        {"arguments": {"start_ridge": 4, "end_ridge": 7}},
    ],
)
def test_rejected_or_mismatched_results_never_establish_coverage(change):
    c = controller()
    c.observe({**result(), **change})
    assert c._field_work_memory() == []


def test_foreign_receipts_and_failed_receipts_do_not_establish_coverage():
    c = controller()
    c.observe(result(actor="someone_else"))
    failed = result()
    failed["execution_receipt"]["status"] = "error"
    c.observe(failed)
    assert c._field_work_memory() == []


def test_inclusive_scope_deduplication_and_harvest_are_separate():
    c = controller()
    c.observe(result(3, 3))
    c.observe(result(3, 3))
    c.observe(result(3, 3, action="TractorApp__harvest"))
    assert len(c.accepted_field_work) == 2
    assert len(c._field_work_memory()) == 2
    assert {ridge for _, ridge in c.accepted_field_work} == {3}


@pytest.mark.parametrize("start,end", [(True, 3), (0, 64), (-1, 3), (4, 3)])
def test_invalid_scopes_never_establish_coverage(start, end):
    c = controller()
    c.observe(result(start, end))
    assert c._field_work_memory() == []
