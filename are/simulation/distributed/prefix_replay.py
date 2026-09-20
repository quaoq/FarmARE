"""Decision-boundary manifests and deterministic replay verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from are.simulation.distributed.evaluation_adapters.contracts import (
    ContinuationManifest,
    RepairCandidate,
)
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


_SEMANTIC_IDENTIFIER_KEYS = {
    "event_id",
    "decision_id",
    "decision_context_id",
    "message_id",
    "version_id",
    "item_id",
    "fact_version_id",
    "origin_version_id",
    "source_event_id",
    "source_action_event_id",
    "target_event_id",
    "farmare_event_id",
    "llm_input_log_id",
    "response_id",
    "intent_id",
    "causal_parents",
    "parents",
    "evidence_ids",
    "fact_version_ids",
    "fact_versions",
    "root_message_id",
    "supporting_event_ids",
    "failed_event_ids",
    "supporting_item_ids",
    "inspection_id",
    "mission_id",
    "recovery_observation_event_ids",
    "recovery_of_action_event_ids",
    "recovery_receive_event_ids",
    "prompt_item_ids",
    "prompt_message_ids",
}


class _SemanticCanonicalizer:
    """Rename generated identities while retaining equality and graph edges."""

    def __init__(self) -> None:
        self.identities: dict[str, str] = {}

    def identity(self, value: str) -> str:
        if value not in self.identities:
            self.identities[value] = f"id:{len(self.identities)}"
        return self.identities[value]

    def transform(self, value: Any, *, parent_key: str | None = None) -> Any:
        if hasattr(value, "model_dump"):
            return self.transform(value.model_dump(mode="json"), parent_key=parent_key)
        if isinstance(value, dict):
            return {
                key: self.transform(item, parent_key=key)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if key
                not in {
                    "run_id",
                    "source_trace",
                    "replay_checkpoint_verification",
                }
                and not str(key).endswith("_digest")
            }
        if isinstance(value, (list, tuple)):
            output = [self.transform(item, parent_key=parent_key) for item in value]
            if parent_key in {
                "fact_keys",
                "claims",
                "unresolved",
                "supporting_events",
                "target_ids",
            }:
                return sorted(output, key=lambda item: json.dumps(item, sort_keys=True))
            return output
        if (
            isinstance(value, str)
            and parent_key == "content"
            and value.startswith("D-CORE runtime result: ")
        ):
            try:
                result = json.loads(value.removeprefix("D-CORE runtime result: "))
            except json.JSONDecodeError:
                pass
            else:
                return {
                    "dcore_runtime_result": self.transform(
                        result, parent_key="runtime_result"
                    )
                }
        if isinstance(value, str) and (
            parent_key in _SEMANTIC_IDENTIFIER_KEYS
            or bool(parent_key and parent_key.endswith("_event_id"))
            or bool(parent_key and parent_key.endswith("_version_id"))
        ):
            return self.identity(value)
        if isinstance(value, float) and parent_key in _SIMULATION_TIMESTAMP_KEYS:
            return round(value)
        return value


def _semantic_value(
    value: Any,
    *,
    parent_key: str | None = None,
    canonicalizer: _SemanticCanonicalizer | None = None,
) -> Any:
    return (canonicalizer or _SemanticCanonicalizer()).transform(
        value, parent_key=parent_key
    )


def semantic_event(
    event: dict[str, Any], canonicalizer: _SemanticCanonicalizer | None = None
) -> dict[str, Any]:
    """Canonicalize generated identifiers while retaining execution semantics."""

    canonicalizer = canonicalizer or _SemanticCanonicalizer()

    return {
        "kind": event.get("kind"),
        "actor_id": event.get("actor_id"),
        "logical_time": event.get("logical_time"),
        "world_time": _semantic_value(event.get("world_time"), parent_key="world_time"),
        "action": event.get("action"),
        "args": event.get("args", {}),
        "status": event.get("status"),
        "season_phase": event.get("season_phase"),
        "event_id": canonicalizer.transform(
            event.get("event_id"), parent_key="event_id"
        ),
        "message_id": canonicalizer.transform(
            event.get("message_id"), parent_key="message_id"
        ),
        "decision_context_id": canonicalizer.transform(
            event.get("decision_context_id"), parent_key="decision_context_id"
        ),
        "causal_parents": canonicalizer.transform(
            event.get("causal_parents", ()), parent_key="causal_parents"
        ),
        "evidence_ids": canonicalizer.transform(
            event.get("evidence_ids", ()), parent_key="evidence_ids"
        ),
        "fact_version": canonicalizer.transform(
            event.get("fact_version"), parent_key="fact_version_id"
        ),
        "payload": canonicalizer.transform(event.get("payload", {})),
    }


def semantic_state_digest(value: Any) -> str:
    """Digest state after graph-preserving identity canonicalization."""

    return stable_digest(_semantic_value(value))


def semantic_trace_digest(
    trace: dict[str, Any],
    *,
    before: float | None = None,
    before_event_id: str | None = None,
) -> str:
    raw_events = list(trace.get("events", ()))
    boundary = next(
        (
            index
            for index, item in enumerate(raw_events)
            if item.get("event_id") == before_event_id
        ),
        None,
    )
    if before_event_id is not None and boundary is None:
        raise ValueError("semantic trace boundary event is absent")
    canonicalizer = _SemanticCanonicalizer()
    events = [
        semantic_event(item, canonicalizer)
        for index, item in enumerate(raw_events)
        if (boundary is None or index < boundary)
        and (before is None or float(item.get("logical_time", 0.0)) < before)
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
        (
            item
            for item in trace.get("decisions", ())
            if item["decision_id"] == decision_id
        ),
        None,
    )
    if decision is None:
        raise ValueError("checkpoint decision does not exist")
    configuration = trace.get("configuration", {})
    journal_path = root / "progress.dcore.jsonl"
    journal_checkpoint: dict[str, Any] = {}
    if journal_path.is_file():
        from are.simulation.distributed.journal import proposal_checkpoint

        journal_checkpoint = proposal_checkpoint(journal_path, decision_id)
    semantic_prefix = semantic_trace_digest(trace, before_event_id=decision_id)
    fallback_contexts = {
        decision.get("actor_id", "unknown"): stable_digest(
            decision.get("knowledge_snapshot", {})
        )
    }
    checkpoint = {
        "semantic_prefix": semantic_prefix,
        "knowledge_snapshot": decision["knowledge_snapshot"],
        "pending_transport": trace.get("outcome", {})
        .get("transport", {})
        .get("pending", []),
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
    is_v2 = bool(
        journal_checkpoint.get("configuration_digest")
        and journal_checkpoint.get("controller_state_digests")
        and journal_checkpoint.get("prompt_history_digests")
    )
    return ContinuationManifest(
        schema_version=(
            "continuation_manifest_v2" if is_v2 else "continuation_manifest_v1"
        ),
        source_run_id=trace["run_id"],
        checkpoint_decision_id=decision_id,
        checkpoint_digest=stable_digest(checkpoint),
        semantic_prefix_digest=semantic_prefix,
        physical_state_digest=journal_checkpoint.get("semantic_physical_state_digest")
        or journal_checkpoint.get("physical_state_digest")
        or stable_digest({"unavailable_for_historical_trace": trace["run_id"]}),
        actor_context_digests=journal_checkpoint.get("semantic_actor_context_digests")
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
        or stable_digest(
            decision.get("knowledge_snapshot", {}).get("vector_clock", {})
        ),
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
        configuration_digest=journal_checkpoint.get("configuration_digest"),
        controller_state_digests=journal_checkpoint.get("controller_state_digests", {}),
        controller_state=journal_checkpoint.get("controller_state", {}),
        prompt_history_digests=journal_checkpoint.get("prompt_history_digests", {}),
        prompt_history=journal_checkpoint.get("prompt_history", {}),
        actor_memory_digests=journal_checkpoint.get("actor_memory_digests", {}),
        actor_memory=journal_checkpoint.get("actor_memory", {}),
        request_counters=journal_checkpoint.get("request_counters", {}),
        scheduler_state_digest=journal_checkpoint.get("scheduler_state_digest"),
        scheduler_state=journal_checkpoint.get("scheduler_state", {}),
        random_state_digest=journal_checkpoint.get("random_state_digest"),
        pending_delivery_envelopes=tuple(
            journal_checkpoint.get("pending_delivery_envelopes", ())
        ),
        original_per_actor_call_limits={
            actor: int(
                state.get("max_model_calls")
                or configuration.get("per_agent_call_budget")
                or configuration.get("max_model_calls", 1)
            )
            for actor, state in journal_checkpoint.get("controller_state", {}).items()
        },
        original_per_actor_token_limits={
            actor: (
                int(value)
                if (value := state.get("max_total_tokens")) is not None
                else configuration.get("per_agent_token_budget")
            )
            for actor, state in journal_checkpoint.get("controller_state", {}).items()
        },
        original_team_call_limit=configuration.get("team_call_budget")
        or configuration.get("max_model_calls"),
        original_team_token_limit=configuration.get("team_token_budget"),
        native_feasibility_state=journal_checkpoint.get("native_feasibility_state", {}),
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
    repair: RepairCandidate | None,
    execution_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a prefix and run a fresh suffix with an optional locked repair."""

    root = Path(run_dir).resolve()
    destination = Path(output_dir).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("repaired continuation output must be absent or empty")
    if repair is not None and repair.feasibility != "feasible":
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
        update={"repair_candidate_id": repair.candidate_id if repair else None}
    )
    raw_config.update(
        {
            "controller_mode": "response_replay",
            "replay_trace": str(trace_path.resolve()),
            "replay_app_seeds": app_seeds,
            "replay_checkpoint": locked_checkpoint.model_dump(mode="json"),
            "replay_repair_candidate": (
                repair.model_dump(mode="json") if repair is not None else None
            ),
            "replay_live_suffix": True,
            "output_dir": str(destination),
            "replay_suffix_call_budget": checkpoint.remaining_call_budget,
            "replay_suffix_token_budget": checkpoint.remaining_token_budget,
            "resume": False,
        }
    )
    overrides = dict(execution_overrides or {})
    allowed_test_overrides = {
        "model_by_actor",
        "provider_by_actor",
        "replay_live_responses_by_actor",
    }
    if set(overrides) - allowed_test_overrides:
        raise ValueError("continuation overrides may not replace scientific fields")
    if overrides and (
        raw_config.get("paper_mode")
        or set(overrides.get("provider_by_actor", {}).values()) != {"mock"}
    ):
        raise ValueError("execution overrides are restricted to offline mock suffixes")
    raw_config.update(overrides)
    from are.simulation.distributed.models import DistributedRunnerConfig
    from are.simulation.distributed.runner import DistributedScenarioRunner

    config = DistributedRunnerConfig.model_validate(raw_config)
    if config.engineering_llm_pilot:
        from are.simulation.distributed.pilot_budget import pilot_request_scope

        pilot_row = config.model_dump(mode="json")
        pilot_row["execution"] = "dcore"
        with pilot_request_scope(pilot_row, destination):
            result = DistributedScenarioRunner().run(config)
    else:
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
        "condition": "repaired" if repair is not None else "fresh_no_intervention",
        "repair_candidate_id": repair.candidate_id if repair else None,
        "repair_application": application,
        "provider_request_count": result.trace.outcome.get("provider_request_count", 0),
        "provider_accounted_usd": result.trace.outcome.get("provider_accounted_usd"),
        "outcome": result.trace.outcome,
    }
    destination.mkdir(parents=True, exist_ok=True)
    record_name = (
        "REPAIRED_CONTINUATION.json"
        if repair is not None
        else "FRESH_CONTINUATION.json"
    )
    (destination / record_name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def execute_fresh_continuation(
    run_dir: str | Path,
    output_dir: str | Path,
    *,
    checkpoint: ContinuationManifest,
    execution_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a newly sampled untreated suffix from the verified checkpoint."""

    return execute_repaired_continuation(
        run_dir,
        output_dir,
        checkpoint=checkpoint,
        repair=None,
        execution_overrides=execution_overrides,
    )


__all__ = [
    "build_checkpoint_manifest",
    "execute_unchanged_replay",
    "execute_repaired_continuation",
    "execute_fresh_continuation",
    "semantic_trace_digest",
    "semantic_state_digest",
    "verify_unchanged_replay",
]
