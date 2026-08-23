from __future__ import annotations

import json

from click.testing import CliRunner

from are.simulation.distributed.cli import main
from are.simulation.distributed.evaluator import evaluate_dcore
from are.simulation.distributed.experiments import (
    default_experiment_configs,
    run_experiment_matrix,
)
from are.simulation.distributed.models import DistributedRunnerConfig, EventKind
from are.simulation.distributed.mutants import (
    drop_receive,
    mark_stale_information,
    mark_unsupported_claim,
    omit_handoff,
    omit_uptake,
    remove_observation,
)
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.scenarios.scenario_dcore.transaction import transaction_spec


def _run(**kwargs):
    return DistributedScenarioRunner().run(DistributedRunnerConfig(**kwargs))


def test_transaction_exposes_local_global_gap_and_transit_attribution():
    result = _run(
        handoff_mode="causal",
        enforcement_mode="off",
        fault="delay_past_deadline",
    )
    assert result.trace.outcome["executed"] is True
    assert result.trace.outcome["success"] is False
    assert result.metrics["event_fidelity"] == 1.0
    assert result.metrics["local_global_gap"] > 0
    assert all(
        local["effective_local_score"] == 1.0
        for local in result.metrics["local"].values()
    )
    assert result.attribution[0]["primary"] == "transit_gap"


def test_causal_enforcement_blocks_transaction_and_is_deterministic():
    config = DistributedRunnerConfig(
        handoff_mode="causal",
        enforcement_mode="enforce",
        fault="delay_past_deadline",
    )
    first = DistributedScenarioRunner().run(config)
    second = DistributedScenarioRunner().run(config)
    assert first.trace.outcome["executed"] is False
    assert first.trace.outcome["guard_block_count"] == 1
    assert first.trace.model_dump_json() == second.trace.model_dump_json()

    dropped = _run(handoff_mode="causal", enforcement_mode="enforce", fault="drop")
    assert dropped.trace.outcome["executed"] is False
    assert dropped.trace.outcome["guard_block_count"] == 1


def test_duplicate_delivery_is_trace_visible_and_knowledge_idempotent():
    result = _run(handoff_mode="causal", enforcement_mode="enforce", fault="duplicate")
    duplicates = [
        event
        for event in result.trace.events
        if event.kind == EventKind.MESSAGE_RECEIVE and event.status == "duplicate"
    ]
    assert duplicates
    claim_ids = [
        item_id
        for snapshot in result.trace.knowledge_snapshots
        for item_id in snapshot.item_ids
        if ":claim:" in item_id
    ]
    assert len(claim_ids) >= len(set(claim_ids))


def test_reordered_old_handoff_cannot_supersede_newer_revocation():
    result = _run(handoff_mode="causal", enforcement_mode="enforce", fault="reorder")
    assert result.trace.outcome["executed"] is False
    assert result.trace.outcome["guard_block_count"] == 1
    deliveries = result.trace.outcome["transport"]["delivered_copies"]
    assert deliveries[0][0] == "m000002"
    assert deliveries[1][0] == "m000001"


def test_farm_reliable_handoff_treats_and_faulted_handoff_blocks():
    reliable = _run(
        scenario_id="farm_wetjune",
        handoff_mode="causal",
        enforcement_mode="enforce",
        fault="none",
    )
    assert reliable.trace.outcome["treated"] is True
    assert reliable.trace.outcome["tool_errors"] == []
    assert reliable.metrics["dcore_score"] == 1.0

    faulted_free_text = _run(
        scenario_id="farm_wetjune",
        handoff_mode="free_text",
        enforcement_mode="off",
        fault="delay_past_deadline",
    )
    assert faulted_free_text.trace.outcome["attempted"] is True
    assert faulted_free_text.trace.outcome["safety_success"] is False

    enforced = _run(
        scenario_id="farm_wetjune",
        handoff_mode="causal",
        enforcement_mode="enforce",
        fault="delay_past_deadline",
    )
    assert enforced.trace.outcome["attempted"] is False
    assert enforced.trace.outcome["guard_block_count"] == 1
    assert enforced.trace.outcome["safety_success"] is True


def test_artifacts_evaluation_cli_and_mock_llm(tmp_path):
    result = _run(
        controller_mode="mock_llm",
        handoff_mode="causal",
        enforcement_mode="enforce",
        output_dir=str(tmp_path),
    )
    for path in result.artifacts.values():
        assert json.loads(open(path, encoding="utf-8").read())
    cli = CliRunner().invoke(main, ["--evaluate-trace", result.artifacts["trace"]])
    assert cli.exit_code == 0, cli.output
    assert "dcore_score" in cli.output


def test_replay_and_experiment_matrix(tmp_path):
    original_dir = tmp_path / "original"
    original = _run(
        handoff_mode="causal",
        enforcement_mode="enforce",
        output_dir=str(original_dir),
    )
    replay = _run(
        controller_mode="replay",
        replay_trace=original.artifacts["trace"],
        handoff_mode="causal",
        enforcement_mode="enforce",
    )
    assert replay.trace.outcome["executed"] == original.trace.outcome["executed"]
    assert len(default_experiment_configs("transaction_revocation")) == 13
    rows = run_experiment_matrix("transaction_revocation", tmp_path / "matrix")
    assert len(rows) == 13
    assert (tmp_path / "matrix" / "results.jsonl").exists()


def test_farm_export_includes_legacy_trace(tmp_path):
    result = _run(
        scenario_id="farm_wetjune",
        handoff_mode="causal",
        enforcement_mode="enforce",
        output_dir=str(tmp_path),
    )
    assert "farmare_trace" in result.artifacts
    legacy = json.loads(
        open(result.artifacts["farmare_trace"], encoding="utf-8").read()
    )
    assert legacy["version"] == "are_simulation_v1"
    assert result.trace.source_trace == result.artifacts["farmare_trace"]
    assert result.trace.outcome["yield_is_final"] is True
    assert result.trace.outcome["biological_yield_kg"] > 0
    assert result.trace.outcome["continuation"]["harvest_passes"] == 16


def test_controlled_information_path_mutants_receive_expected_attribution():
    base = _run(handoff_mode="causal", enforcement_mode="off", fault="none")
    action = next(
        event
        for event in base.trace.events
        if event.kind == EventKind.ACTION and event.action == "execute_transaction"
    )
    assert base.attribution[0]["primary"] == "reasoning_error"

    mutations = (
        (omit_uptake(base.trace, "execution", "m000002")[0], "uptake_error"),
        (omit_handoff(base.trace, "m000002")[0], "handoff_omission"),
        (drop_receive(base.trace, "m000002")[0], "transit_gap"),
        (
            remove_observation(base.trace, "authorization_valid")[0],
            "observation_gap",
        ),
        (
            mark_unsupported_claim(base.trace, action.event_id, "fresh_authorization")[
                0
            ],
            "unsupported_claim",
        ),
        (
            mark_stale_information(
                base.trace,
                action.event_id,
                "fresh_authorization",
                "authorization_valid",
            )[0],
            "stale_information",
        ),
    )
    for trace, expected in mutations:
        attribution = evaluate_dcore(transaction_spec(), trace)["attribution"]
        assert attribution[0]["primary"] == expected
