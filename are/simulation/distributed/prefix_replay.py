"""Decision-boundary manifests and deterministic replay verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from are.simulation.distributed.evaluation_adapters.contracts import (
    ContinuationManifest,
    RepairCandidate,
)
from are.simulation.distributed.journal import load_journal
from are.simulation.distributed.models import stable_digest

_SIMULATION_TIMESTAMP_KEYS = {
    "world_time",
    "observed_at",
    "valid_until",
    "send_time",
    "delivered_at",
    "processed_at",
    "scheduled_at",
    "measurement_time",
    "controller_termination_world_time",
    "from_world_time",
    "to_world_time",
    "advanced_seconds",
    "elapsed_s",
    "last_physics_sim_time",
    "transport_delay_seconds",
}


def _semantic_value(value: Any, *, parent_key: str | None = None) -> Any:
    if hasattr(value, "model_dump"):
        return _semantic_value(value.model_dump(mode="json"), parent_key=parent_key)
    if isinstance(value, dict):
        return {
            key: _semantic_value(item, parent_key=key)
            for key, item in value.items()
            if not (
                isinstance(key, str)
                and (
                    key.endswith("_id")
                    or key.endswith("_ids")
                    or key.endswith("_digest")
                    or "version" in key
                    or key
                    in {
                        "run_id",
                        "source_trace",
                        "configuration_digest",
                        "causal_parents",
                        "parents",
                        "replay_checkpoint_verification",
                    }
                )
            )
        }
    if isinstance(value, (list, tuple)):
        output = [_semantic_value(item) for item in value]
        if parent_key in {
            "fact_keys",
            "claims",
            "unresolved",
            "supporting_events",
            "target_ids",
        }:
            return sorted(output, key=lambda item: json.dumps(item, sort_keys=True))
        return output
    if isinstance(value, float) and parent_key in _SIMULATION_TIMESTAMP_KEYS:
        # Native apps use sub-second ticks to make zero-delay callbacks due.
        # Those internal ticks can vary with controller bookkeeping, while the
        # declared scientific clock has one-second resolution.
        return round(value)
    return value


def semantic_event(event: dict[str, Any]) -> dict[str, Any]:
    """Remove generated identifiers while retaining execution semantics."""

    return {
        "kind": event.get("kind"),
        "actor_id": event.get("actor_id"),
        "logical_time": event.get("logical_time"),
        "world_time": _semantic_value(
            event.get("world_time"), parent_key="world_time"
        ),
        "action": event.get("action"),
        "args": event.get("args", {}),
        "status": event.get("status"),
        "season_phase": event.get("season_phase"),
        "payload": _semantic_value(event.get("payload", {})),
    }


def semantic_state_digest(value: Any) -> str:
    """Digest replay state after removing generated identifiers and clock noise."""

    return stable_digest(_semantic_value(value))


def semantic_trace_digest(trace: dict[str, Any], *, before: float | None = None) -> str:
    events = [
        semantic_event(item)
        for item in trace.get("events", ())
        if before is None or float(item.get("logical_time", 0.0)) < before
    ]
    return stable_digest(events)


def build_checkpoint_manifest(
    run_dir: str | Path,
    decision_id: str,
    *,
    remaining_call_budget: int,
    remaining_token_budget: int | None = None,
) -> ContinuationManifest:
    root = Path(run_dir)
    trace_path = next(root.glob("trace.dcore_trace*.json"), None)
    if trace_path is None:
        raise ValueError("run directory lacks a D-CORE trace")
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    decision = next(
        (item for item in trace.get("decisions", ()) if item["decision_id"] == decision_id),
        None,
    )
    if decision is None:
        raise ValueError("checkpoint decision does not exist")
    configuration = trace.get("configuration", {})
    journal_path = root / "progress.dcore.jsonl"
    journal_checkpoint: dict[str, Any] = {}
    if journal_path.is_file():
        proposal = next(
            (
                record.get("payload", {})
                for record in load_journal(journal_path)
                if record.get("kind") == "parsed_proposal"
                and record.get("payload", {}).get("intent_id") == decision_id
            ),
            None,
        )
        if proposal is not None:
            journal_checkpoint = dict(proposal.get("checkpoint") or {})
    semantic_prefix = semantic_trace_digest(
        trace, before=float(decision["logical_time"])
    )
    fallback_contexts = {
        decision.get("actor_id", "unknown"): stable_digest(
            decision.get("knowledge_snapshot", {})
        )
    }
    checkpoint = {
        "semantic_prefix": semantic_prefix,
        "knowledge_snapshot": decision["knowledge_snapshot"],
        "pending_transport": trace.get("outcome", {}).get("transport", {}).get(
            "pending", []
        ),
        "world_time": next(
            (
                item.get("world_time")
                for item in trace.get("events", ())
                if item.get("event_id") == decision_id
            ),
            None,
        ),
        "remaining_call_budget": remaining_call_budget,
        "remaining_token_budget": remaining_token_budget,
        "recorded_checkpoint": journal_checkpoint,
    }
    return ContinuationManifest(
        source_run_id=trace["run_id"],
        checkpoint_decision_id=decision_id,
        checkpoint_digest=stable_digest(checkpoint),
        semantic_prefix_digest=semantic_prefix,
        physical_state_digest=journal_checkpoint.get("semantic_physical_state_digest")
        or journal_checkpoint.get("physical_state_digest")
        or stable_digest({"unavailable_for_historical_trace": trace["run_id"]}),
        actor_context_digests=journal_checkpoint.get(
            "semantic_actor_context_digests"
        )
        or journal_checkpoint.get("actor_context_digests")
        or fallback_contexts,
        knowledge_digests=journal_checkpoint.get("semantic_knowledge_digests")
        or journal_checkpoint.get("knowledge_digests")
        or fallback_contexts,
        pending_delivery_digest=journal_checkpoint.get(
            "semantic_pending_delivery_digest"
        )
        or journal_checkpoint.get("pending_delivery_digest")
        or stable_digest(checkpoint["pending_transport"]),
        clock_digest=journal_checkpoint.get("clock_digest")
        or stable_digest(decision.get("knowledge_snapshot", {}).get("vector_clock", {})),
        world_seed=int(configuration.get("world_seed", 0)),
        scheduler_seed=int(configuration.get("scheduler_seed", 0)),
        model_seed=int(configuration.get("model_seed", 0)),
        fault_seed=int(configuration.get("fault_seed", 0)),
        remaining_call_budget=remaining_call_budget,
        remaining_token_budget=remaining_token_budget,
        controller_settings={
            key: configuration.get(key)
            for key in (
                "controller_mode",
                "model_by_actor",
                "provider_by_actor",
                "temperature_by_actor",
                "max_output_tokens",
            )
        },
        scenario_horizon=float(
            trace.get("outcome", {}).get("scenario_horizon")
            or max(item.get("world_time", 0.0) for item in trace.get("events", ()))
        ),
    )


def verify_unchanged_replay(
    original_trace: dict[str, Any], replay_trace: dict[str, Any]
) -> dict[str, Any]:
    original = semantic_trace_digest(original_trace)
    replayed = semantic_trace_digest(replay_trace)
    original_outcome = stable_digest(_semantic_value(original_trace.get("outcome")))
    replay_outcome = stable_digest(_semantic_value(replay_trace.get("outcome")))
    return {
        "schema_version": "prefix_replay_verification_v1",
        "semantic_trace_match": original == replayed,
        "outcome_match": original_outcome == replay_outcome,
        "original_semantic_digest": original,
        "replay_semantic_digest": replayed,
        "original_outcome_digest": original_outcome,
        "replay_outcome_digest": replay_outcome,
        "verified": original == replayed and original_outcome == replay_outcome,
    }


def execute_unchanged_replay(
    run_dir: str | Path,
    output_dir: str | Path,
    *,
    response_level: bool = False,
    checkpoint: ContinuationManifest | None = None,
) -> dict[str, Any]:
    """Reconstruct a run through the ordinary controller and gateway runtime."""

    from are.simulation.distributed.models import DistributedRunnerConfig
    from are.simulation.distributed.runner import DistributedScenarioRunner

    root = Path(run_dir).resolve()
    destination = Path(output_dir).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("replay output directory must be absent or empty")
    trace_path = next(root.glob("trace.dcore_trace*.json"), None)
    if trace_path is None:
        raise ValueError("run directory lacks a D-CORE trace")
    original = json.loads(trace_path.read_text(encoding="utf-8"))
    raw_config = dict(original.get("configuration") or {})
    source_app_seeds = raw_config.pop("app_random_seeds", None)
    if response_level and not source_app_seeds:
        raise ValueError(
            "response-level replay requires app_random_seeds from a current trace"
        )
    raw_config.update(
        {
            "controller_mode": "response_replay" if response_level else "replay",
            "replay_trace": str(trace_path.resolve()),
            "output_dir": str(destination),
            "paper_mode": False,
            "engineering_llm_pilot": False,
            "scientific_gate_manifest": None,
            "replay_app_seeds": source_app_seeds or {},
            "replay_checkpoint": (
                checkpoint.model_dump(mode="json") if checkpoint is not None else None
            ),
        }
    )
    config = DistributedRunnerConfig.model_validate(raw_config)
    replayed = DistributedScenarioRunner().run(config)
    verification = verify_unchanged_replay(
        original, replayed.trace.model_dump(mode="json")
    )
    checkpoint_verified = (
        bool(
            replayed.trace.outcome.get("replay_checkpoint_verification", {}).get(
                "verified"
            )
        )
        if checkpoint is not None
        else None
    )
    if checkpoint is not None:
        verification["verified"] = bool(
            verification["verified"] and checkpoint_verified
        )
    payload = {
        **verification,
        "schema_version": "prefix_replay_execution_v1",
        "source_run_dir": str(root),
        "replay_output_dir": str(destination),
        "provider_requests": 0,
        "management_actions_inserted": 0,
        "replay_level": "recorded_model_response" if response_level else "proposal",
        "checkpoint_verified": checkpoint_verified,
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "REPLAY_VERIFICATION.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def execute_repaired_continuation(
    run_dir: str | Path,
    output_dir: str | Path,
    *,
    checkpoint: ContinuationManifest,
    repair: RepairCandidate,
    execution_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a prefix, apply one locked repair, and resume with live calls."""

    root = Path(run_dir).resolve()
    destination = Path(output_dir).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("repaired continuation output must be absent or empty")
    if repair.feasibility != "feasible":
        raise ValueError("repaired continuation requires a feasible candidate")
    trace_path = next(root.glob("trace.dcore_trace*.json"), None)
    if trace_path is None:
        raise ValueError("run directory lacks a D-CORE trace")
    original = json.loads(trace_path.read_text(encoding="utf-8"))
    raw_config = dict(original.get("configuration") or {})
    app_seeds = raw_config.pop("app_random_seeds", None)
    if not app_seeds:
        raise ValueError("repaired continuation requires recorded app_random_seeds")
    locked_checkpoint = checkpoint.model_copy(
        update={"repair_candidate_id": repair.candidate_id}
    )
    raw_config.update(
        {
            "controller_mode": "response_replay",
            "replay_trace": str(trace_path.resolve()),
            "replay_app_seeds": app_seeds,
            "replay_checkpoint": locked_checkpoint.model_dump(mode="json"),
            "replay_repair_candidate": repair.model_dump(mode="json"),
            "replay_live_suffix": True,
            "output_dir": str(destination),
            "team_call_budget": checkpoint.remaining_call_budget,
            "team_token_budget": checkpoint.remaining_token_budget,
            "resume": False,
        }
    )
    raw_config.update(execution_overrides or {})
    from are.simulation.distributed.models import DistributedRunnerConfig
    from are.simulation.distributed.runner import DistributedScenarioRunner

    config = DistributedRunnerConfig.model_validate(raw_config)
    result = DistributedScenarioRunner().run(config)
    application = result.trace.outcome.get("replay_repair_application")
    checkpoint_result = result.trace.outcome.get("replay_checkpoint_verification")
    payload = {
        "schema_version": "repaired_continuation_execution_v1",
        "source_run_dir": str(root),
        "output_dir": str(destination),
        "checkpoint_decision_id": checkpoint.checkpoint_decision_id,
        "checkpoint_verified": bool(
            isinstance(checkpoint_result, dict) and checkpoint_result.get("verified")
        ),
        "repair_candidate_id": repair.candidate_id,
        "repair_application": application,
        "provider_request_count": result.trace.outcome.get(
            "provider_request_count", 0
        ),
        "provider_accounted_usd": result.trace.outcome.get(
            "provider_accounted_usd"
        ),
        "outcome": result.trace.outcome,
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "REPAIRED_CONTINUATION.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


__all__ = [
    "build_checkpoint_manifest",
    "execute_unchanged_replay",
    "execute_repaired_continuation",
    "semantic_trace_digest",
    "semantic_state_digest",
    "verify_unchanged_replay",
]
