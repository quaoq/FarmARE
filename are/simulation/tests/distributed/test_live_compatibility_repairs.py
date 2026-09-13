import json
from types import SimpleNamespace

import pytest


def test_recovered_failure_is_marked_only_for_the_same_accepted_request():
    from collections import deque

    from are.simulation.distributed.controllers import FarmAREBaseAgentController

    controller = object.__new__(FarmAREBaseAgentController)
    controller.base_agent = SimpleNamespace(
        append_agent_log=lambda log: None,
        make_timestamp=lambda: 0,
        agent_id="operations",
    )
    controller.recent_failures = deque(maxlen=8)
    controller.accepted_write_receipts = deque(maxlen=32)
    failed = {
        "selected_action": "TractorApp__plant_seeds",
        "intent_kind": "act",
        "arguments": {"start_ridge": 0, "end_ridge": 3},
        "executed": False,
        "error": "insufficient seeds",
        "result_world_time": 10,
    }
    controller.observe(failed)
    accepted = {
        **failed,
        "executed": True,
        "error": None,
        "result_world_time": 20,
        "intent_id": "retry",
        "execution_receipt": {
            "status": "accepted",
            "receipt_digest": "accepted-retry",
        },
    }
    controller.observe({**accepted, "arguments": {"start_ridge": 4, "end_ridge": 7}})
    assert "accepted_retry" not in controller.recent_failures[0]
    controller.observe(accepted)
    assert controller.recent_failures[0]["error"] == "insufficient seeds"
    assert controller.recent_failures[0]["result_world_time"] == 10
    assert controller.recent_failures[0]["accepted_retry"] == {
        "receipt_digest": "accepted-retry",
        "intent_id": "retry",
        "result_world_time": 20,
    }


from are.simulation.distributed.authored_specs import author_process
from are.simulation.distributed.evaluator_v5 import _match_events
from are.simulation.distributed.models import EventKind, TraceEvent
from are.simulation.distributed.teams import (
    build_builtin_team,
    built_in_role_refinement,
    refine_process_for_team,
)
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


@pytest.mark.parametrize("size", [3, 4])
def test_every_refined_handoff_has_acceptance_even_when_optional(size):
    base = author_process("farm_wetjune_recheck")
    team = build_builtin_team(
        f"wetjune_{size}agent",
        create_native_scenario(base.scenario_id, world_seed=0).get_tools(),
    )
    process = refine_process_for_team(
        base, team, built_in_role_refinement(team, base.scenario_id)
    )
    acceptance = {a.transition_id for a in process.acceptance}
    transitions = {
        t.transition_id
        for t in process.occurrence_net.transitions
        if t.actor_id != "world"
    }
    assert transitions == acceptance
    target = next(
        t
        for t in process.occurrence_net.transitions
        if not t.required and t.kind.value == "receive"
    )
    legacy = process.model_copy(
        update={
            "acceptance": tuple(
                a for a in process.acceptance if a.transition_id != target.transition_id
            )
        }
    )
    event = TraceEvent(
        event_id="optional-receive",
        kind=EventKind.MESSAGE_RECEIVE,
        actor_id=target.actor_id,
        local_sequence=1,
        logical_time=1,
        world_time=1,
        vector_clock={target.actor_id: 1},
        action=target.action,
        status="ok",
    )
    matches, details, unmatched = _match_events(
        legacy, {target.transition_id}, SimpleNamespace(events=(event,))
    )
    assert not matches and not details and unmatched == {event.event_id}


def test_matched_baseline_exports_authoritative_schema_and_keeps_provider_accounting(
    tmp_path, monkeypatch
):
    from are.simulation.distributed import matched_baselines, pilot_budget
    from are.simulation.distributed.experiments import resolve_manifest

    row = resolve_manifest(
        {
            "scenarios": ["farm_wetjune_recheck"],
            "world_seeds": [0],
            "conditions": [{"id": "fixture", "execution": "farmare_direct"}],
            "model_by_actor": {"operations": "fixture-model"},
            "agent_family_by_actor": {"operations": "farm_baseline_react"},
        }
    )[0]

    def run(self, config, scenario):
        assert config.trace_dump_format == "hf"
        exported = tmp_path / "fixture_native.json"
        exported.write_text(
            json.dumps({"version": "are_simulation_v1", "world_logs": []})
        )
        return SimpleNamespace(
            success=False,
            rationale="software fixture",
            exception=None,
            export_path=str(exported),
        )

    monkeypatch.setattr(matched_baselines.ScenarioRunner, "run", run)
    monkeypatch.setattr(
        pilot_budget,
        "current_request_usage",
        lambda: {
            "accounting_basis": "provider_requests_v1",
            "provider_request_count": 3,
            "provider_accounted_usd": 0.01,
        },
    )
    result = matched_baselines.run_matched_baseline(row, tmp_path)
    assert result["trace_version"] == "are_simulation_v1"
    assert result["provider_request_count"] == 3
    assert (
        json.loads((tmp_path / "farm_outcome.json").read_text())[
            "provider_accounted_usd"
        ]
        == 0.01
    )
