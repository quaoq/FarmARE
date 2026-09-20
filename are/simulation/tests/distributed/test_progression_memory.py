"""Local execution memory survives time polling without inventing completed work."""

import json
from collections import deque
from types import SimpleNamespace

import pytest

from are.simulation.distributed.controllers import FarmAREBaseAgentController
from are.simulation.distributed.models import (
    ActorSpec,
    CausalHandoff,
    EpistemicStatus,
    KnowledgeItem,
    LocalView,
)
from are.simulation.distributed.pilot_budget import estimate_tokens


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


def test_prompt_budget_retains_current_evidence_before_historical_failures():
    c = controller()
    c.base_agent.logs = []
    c.max_model_calls = 350
    c.knowledge_window = 120
    c.message_window = 24
    for start in range(0, 64, 4):
        c.observe(result(start, start + 3, action="TractorApp__harvest"))
    c.persistent_failures = {
        ("TractorApp__harvest", str(index), "stale evidence"): {
            "action": "TractorApp__harvest",
            "error": "stale evidence " + "x" * 500,
            "count": 1,
            "active": index >= 12,
            "last_arguments": {"start_ridge": index, "end_ridge": index},
            "last_world_time": float(index),
            "source_receipt_digest": f"failure-{index}",
        }
        for index in range(24)
    }
    evidence = tuple(
        KnowledgeItem(
            item_id=f"fresh-{index}",
            fact_key="crop:mature" if index % 2 == 0 else "soil:trafficable",
            value=True,
            scope=(index, index),
            status=EpistemicStatus.OBSERVED,
            source_actor="field_intelligence",
            observed_at=1000.0 + index,
            learned_at=1000.0 + index,
            valid_until=2000.0,
        )
        for index in range(30)
    )
    inbox = tuple(
        CausalHandoff(
            message_id=f"handoff-{index}",
            sender="field_intelligence",
            recipient="operations",
            text="fresh harvest evidence " + "y" * 500,
            send_time=1000.0 + index,
        )
        for index in range(30)
    )
    view = LocalView(
        actor=ActorSpec(actor_id="operations"),
        logical_time=30.0,
        world_time=1030.0,
        knowledge=evidence,
        inbox=inbox,
        vector_clock={},
    )
    rendered = c._render_local_context(view)
    payload = json.loads(rendered[rendered.index("{") :])
    assert estimate_tokens(payload) <= 12000
    assert payload["knowledge"]
    assert "fresh-29" in {item["item_id"] for item in payload["knowledge"]}
    assert "fresh-29" in c.last_prompt_item_ids
    assert payload["delivered_messages"][-1]["message_id"] == "handoff-29"
    assert "handoff-29" in c.last_prompt_message_ids
    assert (
        payload["accepted_field_work_coverage"]["operations"]["harvest"][
            "accepted_ridge_count"
        ]
        == 64
    )


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


def test_guard_rejection_reasons_persist_after_short_history_rolls_over():
    c = controller()
    rejected = {
        "selected_action": "TractorApp__harvest",
        "arguments": {"start_ridge": 0, "end_ridge": 3},
        "executed": False,
        "guard_verdict": "block",
        "guard_reasons": [
            "fact crop:grain_moisture contradicts the requirement",
            "fact soil:trafficable exceeds max_age",
        ],
        "season_phase": "harvest_a",
        "result_world_time": 50,
    }
    c.observe(rejected)

    failure = c._failure_memory()[0]
    assert failure["error"] == (
        "fact crop:grain_moisture contradicts the requirement; "
        "fact soil:trafficable exceeds max_age"
    )
    assert failure["guard_reasons"] == tuple(rejected["guard_reasons"])
    assert failure["guard_verdict"] == "block"
    assert failure["season_phase"] == "harvest_a"
    assert c.recent_failures[-1]["guard_reasons"] == rejected["guard_reasons"]


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


def test_postharvest_completion_receipts_are_invalidated_by_new_grain():
    c = controller()
    c.actor_spec = SimpleNamespace(
        tool_schemas={
            action: {}
            for action in (
                "TractorApp__harvest",
                "TractorApp__unload_grain",
                "FarmWorldApp__dry_grain",
                "FarmWorldApp__store_grain",
            )
        }
    )

    def accepted(action, index):
        value = result(action=action)
        value["arguments"] = {}
        value["intent_id"] = f"postharvest:{index}"
        value["execution_receipt"].update(
            intent_id=value["intent_id"],
            action=action,
            arguments={},
            receipt_digest=f"receipt:{index}",
        )
        return value

    c.observe(accepted("TractorApp__unload_grain", 1))
    c.observe(accepted("FarmWorldApp__dry_grain", 2))
    c.observe(accepted("FarmWorldApp__store_grain", 3))
    assert c._field_work_coverage()["operations"]["postharvest"] == {
        "unload": True,
        "dry": True,
        "store": True,
    }

    c.observe(result(4, 7, action="TractorApp__harvest"))
    assert c._field_work_coverage()["operations"]["postharvest"] == {
        "unload": False,
        "dry": False,
        "store": False,
    }

    c.observe(accepted("TractorApp__unload_grain", 4))
    c.observe(accepted("FarmWorldApp__dry_grain", 5))
    c.observe(accepted("FarmWorldApp__store_grain", 6))
    c.observe(accepted("TractorApp__unload_grain", 7))
    assert c._field_work_coverage()["operations"]["postharvest"] == {
        "unload": True,
        "dry": False,
        "store": False,
    }


def test_observer_does_not_receive_unowned_field_work_gaps():
    c = controller("field_intelligence")
    assert c._field_work_coverage()["operations"] == {}
