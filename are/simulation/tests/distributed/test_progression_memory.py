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
    value.persistent_failures = {}
    value.accepted_field_work = {}
    value.intent_kind_counts = {}
    value.season_start_world_time = 10
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
    assert '"world_days_elapsed": 0.0' in text
    assert '"intent_kind_counts": {}' in text
    assert '"end_ridge": 27' in text
    assert '"accepted_ridge_count": 28' in text
    assert '"accepted_ranges": [[0, 27]]' in text
    assert '"missing_receipt_ranges": [[28, 63]]' in text
    assert "Any farm-time advance while planting is incomplete" in text
    assert "Communication is for missing or newly changed evidence" in text
    assert "should not narrate its own actions or receipts" in text


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


def test_persistent_failures_are_deduplicated_and_marked_recovered():
    c = controller("field_intelligence")
    failed = {
        "selected_action": "Matrice4T__fly_survey",
        "arguments": {"start_ridge": 0, "end_ridge": 7},
        "executed": False,
        "error": "Battery 0.0% below minimum 15%",
        "result_world_time": 20,
        "execution_receipt": {"status": "error", "receipt_digest": "failed-1"},
    }
    c.observe(failed)
    c.observe({**failed, "result_world_time": 30})
    assert c._failure_memory() == [
        {
            "action": "Matrice4T__fly_survey",
            "error": "Battery 0.0% below minimum 15%",
            "count": 2,
            "first_world_time": 20,
            "active": True,
            "last_arguments": {"start_ridge": 0, "end_ridge": 7},
            "last_world_time": 30,
            "source_receipt_digest": "failed-1",
            "recovered_by_receipt_digest": None,
            "recovered_at_world_time": None,
        }
    ]
    c.observe(
        {
            "selected_action": "Matrice4T__fly_survey",
            "arguments": {"start_ridge": 0, "end_ridge": 7},
            "intent_kind": "act",
            "executed": True,
            "result_world_time": 40,
            "execution_receipt": {
                "status": "accepted",
                "receipt_digest": "recovered-1",
            },
        }
    )
    assert c._failure_memory()[0]["active"] is False
    assert c._failure_memory()[0]["recovered_by_receipt_digest"] == "recovered-1"


def test_success_on_another_scope_does_not_recover_persistent_failure():
    c = controller()
    failed = result(48, 51, action="TractorApp__harvest")
    failed.update(executed=False, error="All ridges must be planted before harvest")
    failed["execution_receipt"].update(status="error", receipt_digest="failed-48")
    c.observe(failed)

    c.observe(result(12, 15, action="TractorApp__harvest"))

    assert c._failure_memory()[0]["active"] is True
    assert c._failure_memory()[0]["last_arguments"]["start_ridge"] == 48


def test_same_native_error_on_distinct_scopes_keeps_independent_failures():
    c = controller()
    for start in (0, 48):
        failed = result(start, start + 3, action="TractorApp__harvest")
        failed.update(executed=False, error="All ridges must be planted before harvest")
        failed["execution_receipt"].update(
            status="error", receipt_digest=f"failed-{start}"
        )
        c.observe(failed)

    assert [row["last_arguments"]["start_ridge"] for row in c._failure_memory()] == [
        0,
        48,
    ]

    c.observe(result(0, 3, action="TractorApp__harvest"))
    by_scope = {
        row["last_arguments"]["start_ridge"]: row for row in c._failure_memory()
    }
    assert by_scope[0]["active"] is False
    assert by_scope[48]["active"] is True


def test_legal_batches_recover_rejected_wide_range_only_after_full_coverage():
    c = controller()
    failed = result(0, 63)
    failed.update(executed=False, error="Range cannot exceed 4 ridges per pass")
    failed["execution_receipt"].update(status="error", receipt_digest="failed-wide")
    c.observe(failed)

    for start in range(0, 60, 4):
        c.observe(result(start, start + 3))
    assert c._failure_memory()[0]["active"] is True

    c.observe(result(60, 63))
    failure = c._failure_memory()[0]
    assert failure["active"] is False
    assert failure["recovery_mode"] == "accepted_scope_coverage"
    assert failure["recovered_by_receipt_digest"] == (
        "operations:TractorApp__plant_seeds:60:63"
    )


def test_field_work_coverage_keeps_planting_and_harvest_gaps_separate():
    c = controller()
    c.observe(result(0, 3))
    c.observe(result(4, 7, action="TractorApp__replant_seeds"))
    c.observe(result(0, 3, action="TractorApp__harvest"))

    assert c._field_work_coverage() == {
        "task_ridge_scope": [0, 63],
        "basis": "this actor's accepted native receipts only",
        "operations": {
            "planting": {
                "accepted_ridge_count": 8,
                "accepted_ranges": [[0, 7]],
                "missing_receipt_ranges": [[8, 63]],
            },
            "harvest": {
                "accepted_ridge_count": 4,
                "accepted_ranges": [[0, 3]],
                "missing_receipt_ranges": [[4, 63]],
            },
        },
    }


def test_observer_does_not_receive_unowned_field_work_gaps():
    c = controller("field_intelligence")
    assert c._field_work_coverage()["operations"] == {}
