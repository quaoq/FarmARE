"""Bounded, legal information repairs and locked matched-study selections."""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Iterable

from are.simulation.distributed.evaluation_adapters.contracts import (
    DiagnosticWitness,
    RepairCandidate,
    RepairPrimitive,
)
from are.simulation.distributed.models import stable_digest


def _default_observation_action(fact_key: str | None) -> str | None:
    prefix = (fact_key or "").split(":", 1)[0]
    return {
        "weather": "WeatherApp__get_current_weather",
        "soil": "SensorApp__read_soil_sensors",
        "drought": "SensorApp__read_soil_sensors",
        "disease": "Mavic3M__fly_survey",
        "crop": "Mavic3M__fly_survey",
        "pest": "Mavic3M__fly_survey",
        "inventory": "FarmWorldApp__get_inventory",
        "equipment": "TractorApp__get_status",
    }.get(prefix)


def _scope_arguments(
    action: str | None, scope: tuple[int, int] | str | None
) -> dict[str, int]:
    if action and action.endswith("__fly_survey") and isinstance(scope, tuple):
        return {"start_ridge": scope[0], "end_ridge": scope[1]}
    return {}


def _primitive_options(
    witness: DiagnosticWitness,
    *,
    observer_by_fact: dict[str, str] | None = None,
    source_actor_by_version: dict[str, str] | None = None,
    native_action_by_fact: dict[str, str] | None = None,
) -> tuple[RepairPrimitive, ...]:
    observer_by_fact = observer_by_fact or {}
    source_actor_by_version = source_actor_by_version or {}
    native_action_by_fact = native_action_by_fact or {}
    observer = observer_by_fact.get(witness.fact_key or "", witness.actor_id)
    source_version = next(iter(witness.fact_version_ids), None)
    source_actor = source_actor_by_version.get(source_version or "", observer)
    native_action = native_action_by_fact.get(
        witness.fact_key or "", _default_observation_action(witness.fact_key)
    )
    common = {
        "fact_key": witness.fact_key,
        "scope": witness.target_scope,
    }
    if witness.mechanism == "missing_observation":
        acquire = RepairPrimitive(
            primitive="acquire_observation",
            actor_id=observer,
            native_action=native_action,
            native_arguments=_scope_arguments(native_action, witness.target_scope),
            **common,
        )
        if observer == witness.actor_id:
            return (acquire,)
        return (
            acquire,
            RepairPrimitive(
                primitive="route_evidence",
                actor_id=observer,
                recipient_actor_id=witness.actor_id,
                **common,
            ),
        )
    if witness.mechanism == "failed_delivery":
        return (
            RepairPrimitive(
                primitive="redeliver_evidence",
                actor_id=source_actor,
                recipient_actor_id=witness.actor_id,
                fact_version_id=source_version,
                **common,
            ),
        )
    if witness.mechanism == "expired_evidence":
        refresh = RepairPrimitive(
                primitive="refresh_observation",
                actor_id=observer,
                native_action=native_action,
                native_arguments=_scope_arguments(native_action, witness.target_scope),
                **common,
            )
        if observer == witness.actor_id:
            return (refresh,)
        return (
            refresh,
            RepairPrimitive(
                primitive="route_evidence",
                actor_id=observer,
                recipient_actor_id=witness.actor_id,
                **common,
            ),
        )
    if witness.mechanism == "incorrect_scope":
        return (
            RepairPrimitive(
                primitive="acquire_observation",
                actor_id=observer,
                native_action=native_action,
                native_arguments=_scope_arguments(native_action, witness.target_scope),
                **common,
            ),
            RepairPrimitive(
                primitive="route_evidence",
                actor_id=observer,
                recipient_actor_id=witness.actor_id,
                **common,
            ),
        )
    if witness.mechanism == "context_omission":
        return (
            RepairPrimitive(
                primitive="restore_context",
                actor_id=witness.actor_id,
                fact_version_id=source_version,
                **common,
            ),
        )
    if witness.mechanism in {
        "failure_to_use_available_evidence",
        "native_execution_failure",
        "transition_order_failure",
    }:
        return (
            RepairPrimitive(
                primitive="request_reconsideration",
                actor_id=witness.actor_id,
                **common,
            ),
        )
    return ()


def enumerate_repairs(
    witness: DiagnosticWitness,
    *,
    native_cost_by_primitive: dict[str, float] | None = None,
    duration_by_primitive: dict[str, float | None] | None = None,
    observer_by_fact: dict[str, str] | None = None,
    source_actor_by_version: dict[str, str] | None = None,
    native_action_by_fact: dict[str, str] | None = None,
) -> tuple[RepairCandidate, ...]:
    """Enumerate minimal one/two-step repairs without outcome information."""

    if witness.determination != "supported":
        return ()
    native_cost_by_primitive = native_cost_by_primitive or {}
    duration_by_primitive = duration_by_primitive or {}
    options = _primitive_options(
        witness,
        observer_by_fact=observer_by_fact,
        source_actor_by_version=source_actor_by_version,
        native_action_by_fact=native_action_by_fact,
    )
    route_required = len(options) == 2 and options[1].primitive == "route_evidence"
    sequences: list[tuple[RepairPrimitive, ...]] = (
        [tuple(options)] if route_required else [(item,) for item in options]
    )
    # Only observe/refresh followed by routing is a meaningful two-step repair.
    for left, right in combinations(options, 2):
        if left.primitive in {"acquire_observation", "refresh_observation"} and (
            right.primitive == "route_evidence"
        ):
            if (left, right) not in sequences:
                sequences.append((left, right))
    rows = []
    seen_support: set[tuple[str, ...]] = set()
    for sequence in sequences:
        key = tuple(item.primitive for item in sequence)
        if key in seen_support:
            continue
        seen_support.add(key)
        duration_values = [
            duration_by_primitive.get(item.primitive) for item in sequence
        ]
        total_duration = (
            sum(float(value) for value in duration_values if value is not None)
            if all(value is not None for value in duration_values)
            else None
        )
        slack = (
            witness.deadline - witness.decision_time - total_duration
            if witness.deadline is not None
            and witness.decision_time is not None
            and total_duration is not None
            else None
        )
        feasibility = (
            "infeasible"
            if slack is not None and slack < 0
            else "feasible"
            if slack is not None
            else "unresolved"
        )
        cost = sum(
            native_cost_by_primitive.get(item.primitive, 0.0) for item in sequence
        )
        candidate_id = stable_digest(
            [witness.witness_id, [item.model_dump(mode="json") for item in sequence]]
        )[:24]
        rows.append(
            RepairCandidate(
                candidate_id=candidate_id,
                witness_id=witness.witness_id,
                primitives=sequence,
                required_evidence_ids=witness.fact_version_ids,
                total_native_cost=cost,
                timing_slack_seconds=slack,
                feasibility=feasibility,
                rejection_reasons=("negative_timing_slack",)
                if feasibility == "infeasible"
                else (),
                priority_key=(
                    witness.decision_time or float("inf"),
                    len(sequence),
                    cost,
                    candidate_id,
                ),
            )
        )
    return tuple(sorted(rows, key=lambda row: row.priority_key))


def select_repair(
    candidates: Iterable[RepairCandidate], *, strategy: str = "frozen_priority"
) -> RepairCandidate | None:
    eligible = [item for item in candidates if item.feasibility != "infeasible"]
    if not eligible:
        return None
    if strategy == "frozen_priority":
        return min(eligible, key=lambda item: item.priority_key)
    if strategy == "cost_only":
        return min(eligible, key=lambda item: (item.priority_key[2], item.candidate_id))
    if strategy == "unrestricted":
        return min(eligible, key=lambda item: item.candidate_id)
    raise ValueError(f"unknown repair selection strategy {strategy!r}")


def select_repair_checkpoints(
    results: Path, plan_path: Path
) -> dict[str, object]:
    """Freeze scenario-balanced replayable checkpoints from completed rows."""

    from are.simulation.distributed.journal import (
        load_journal,
        uncertain_native_writes,
    )
    from are.simulation.distributed.prefix_replay import build_checkpoint_manifest

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    quotas = {str(key): int(value) for key, value in plan["scenario_quotas"].items()}
    rows = [
        json.loads(line)
        for line in results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidates: dict[str, list[dict[str, object]]] = {key: [] for key in quotas}
    exclusions: list[dict[str, str]] = []
    for row in rows:
        scenario = str(row.get("scenario"))
        if scenario not in quotas or not row.get("artifact_dir"):
            continue
        root = Path(str(row["artifact_dir"]))
        trace_path = next(root.glob("trace.dcore_trace*.json"), None)
        metrics_path = next(root.glob("metrics.dcore_eval*.json"), None)
        journal_path = root / "progress.dcore.jsonl"
        if not trace_path or not metrics_path or not journal_path.is_file():
            exclusions.append({"artifact_dir": str(root), "reason": "missing_evidence"})
            continue
        journal = load_journal(journal_path)
        if uncertain_native_writes(journal):
            exclusions.append(
                {"artifact_dir": str(root), "reason": "uncertain_native_write"}
            )
            continue
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        events = {item["event_id"]: item for item in trace.get("events", ())}
        decisions = {
            item["decision_id"]: item for item in trace.get("decisions", ())
        }
        for witness in metrics.get("provenance_failure_localization", ()):
            target = events.get(witness.get("target_event_id"), {})
            decision_id = target.get("decision_context_id")
            decision = decisions.get(decision_id)
            if not decision_id or decision is None:
                continue
            logical_time = float(decision["logical_time"])
            prefix = [
                item
                for item in journal
                if float(item.get("payload", {}).get("logical_time", -1))
                < logical_time
            ]
            requests_used = sum(item.get("kind") == "model_request" for item in prefix)
            tokens_used = sum(
                int(item.get("payload", {}).get("metadata", {}).get("total_tokens") or 0)
                for item in prefix
                if item.get("kind") == "model_response"
            )
            configuration = trace.get("configuration", {})
            remaining_calls = int(
                configuration.get("team_call_budget")
                or configuration.get("max_model_calls", 0)
            ) - requests_used
            token_budget = configuration.get("team_token_budget")
            remaining_tokens = (
                int(token_budget) - tokens_used if token_budget is not None else None
            )
            if remaining_calls < 1 or (
                remaining_tokens is not None and remaining_tokens < 1
            ):
                continue
            manifest = build_checkpoint_manifest(
                root,
                str(decision_id),
                remaining_call_budget=remaining_calls,
                remaining_token_budget=remaining_tokens,
            )
            key = stable_digest(
                [
                    int(plan["selection_seed"]),
                    trace["run_id"],
                    decision_id,
                    witness.get("witness_id"),
                ]
            )
            candidates[scenario].append(
                {
                    "selection_key": key,
                    "scenario": scenario,
                    "world_seed": configuration.get("world_seed"),
                    "run_dir": str(root),
                    "decision_id": decision_id,
                    "witness": witness,
                    "continuation_manifest": manifest.model_dump(mode="json"),
                }
            )
    selected = []
    shortfalls = {}
    for scenario, quota in quotas.items():
        unique: dict[tuple[str, str], dict[str, object]] = {}
        for item in sorted(candidates[scenario], key=lambda value: value["selection_key"]):
            unique.setdefault((str(item["run_dir"]), str(item["decision_id"])), item)
        chosen = list(unique.values())[:quota]
        selected.extend(chosen)
        shortfalls[scenario] = quota - len(chosen)
    return {
        "schema_version": "dcore_repair_checkpoint_selection_v1",
        "plan_digest": stable_digest(plan),
        "selection_seed": plan["selection_seed"],
        "selected_count": len(selected),
        "shortfalls": shortfalls,
        "selection_complete": all(value == 0 for value in shortfalls.values()),
        "selected": selected,
        "exclusions": exclusions,
    }


__all__ = ["enumerate_repairs", "select_repair", "select_repair_checkpoints"]
