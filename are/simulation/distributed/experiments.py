"""Farm-only experiment manifests, resilient execution, and paper-ready aggregation."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

import yaml

from are.simulation.distributed.models import DistributedRunnerConfig, stable_digest
from are.simulation.distributed.petri import TransitionKind
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.scenarios.scenario_dcore.farm_catalog import FARM_SCENARIOS

FAULTS = (
    "none",
    "delay",
    "delay_within_validity",
    "delay_past_validity",
    "delay_past_deadline",
    "drop",
    "duplicate",
    "reorder",
    "mixed",
)

# Legacy conditions import their original authoritative traces. Their local
# knowledge metrics remain NA instead of being reconstructed after the fact.
DEFAULT_CONDITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "scripted_petri_oracle",
        "execution": "dcore",
        "visibility": "shared_blackboard",
        "handoff": "causal",
        "enforcement": "audit",
        "faults": ["none"],
    },
    {"id": "single_agent_direct", "execution": "legacy_import"},
    {"id": "synchronous_a2a", "execution": "legacy_import"},
    {
        "id": "shared_blackboard",
        "execution": "dcore",
        "visibility": "shared_blackboard",
        "handoff": "causal",
        "enforcement": "audit",
        "faults": ["none"],
    },
    {
        "id": "local_free_text",
        "execution": "dcore",
        "visibility": "local",
        "handoff": "free_text",
        "enforcement": "off",
        "faults": list(FAULTS),
    },
    {
        "id": "local_causal_audit",
        "execution": "dcore",
        "visibility": "local",
        "handoff": "causal",
        "enforcement": "audit",
        "faults": list(FAULTS),
    },
    {
        "id": "local_causal_enforce",
        "execution": "dcore",
        "visibility": "local",
        "handoff": "causal",
        "enforcement": "enforce",
        "faults": list(FAULTS),
    },
)

LEGACY_CONDITIONS = (
    ("shared_upper_bound", "shared_blackboard", "causal", "enforce"),
    ("local_free_text_reliable", "local", "free_text", "off"),
    ("local_free_text_faulted", "local", "free_text", "off"),
    ("causal_reliable", "local", "causal", "enforce"),
    ("causal_faulted", "local", "causal", "enforce"),
)
LEGACY_FAULTS = ("none", "delay", "delay_past_deadline", "drop", "duplicate", "reorder")


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("experiment manifest must be a YAML mapping")
    if payload.get("schema_version") != "farm_dcore_matrix_v1":
        raise ValueError("manifest schema_version must be farm_dcore_matrix_v1")
    if payload.get("disabled"):
        raise ValueError(
            str(payload.get("disabled_reason") or "this matrix manifest is disabled")
        )
    unknown = set(payload.get("scenarios", [])) - set(FARM_SCENARIOS)
    if unknown:
        raise ValueError(f"unknown farm scenarios: {sorted(unknown)}")
    for key in (
        "team_spec_by_id",
        "role_refinement_by_team_id",
        "petri_spec_by_team_id",
        "petri_spec_by_scenario",
        "scientific_gate_by_team_id",
        "scientific_gate_by_scenario",
    ):
        mapping = payload.get(key)
        if not isinstance(mapping, dict):
            continue
        payload[key] = {
            item: _manifest_relative_path(value, manifest_path.parent)
            for item, value in mapping.items()
        }
    return payload


def _manifest_relative_path(value: Any, base: Path) -> Any:
    if not isinstance(value, str) or "REPLACE_WITH" in value:
        return value
    candidate = Path(value)
    return str(candidate if candidate.is_absolute() else (base / candidate).resolve())


def resolve_manifest(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve every observational unit without making model calls."""
    conditions = payload.get("conditions") or list(DEFAULT_CONDITIONS)
    scenarios = payload.get("scenarios") or list(FARM_SCENARIOS)
    repetitions = int(payload.get("repetitions", 1))
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    world_seeds = tuple(int(x) for x in payload.get("world_seeds", [0]))
    scheduler_seeds = tuple(int(x) for x in payload.get("scheduler_seeds", [0]))
    model_seeds = tuple(int(x) for x in payload.get("model_seeds", [0]))
    fault_seeds = tuple(int(x) for x in payload.get("fault_seeds", [0]))
    if not all((world_seeds, scheduler_seeds, model_seeds, fault_seeds)):
        raise ValueError(
            "world, scheduler, model, and fault seed lists cannot be empty"
        )
    repeat_indices = tuple(
        int(value) for value in payload.get("repeat_indices", range(repetitions))
    )
    if not repeat_indices or len(set(repeat_indices)) != len(repeat_indices):
        raise ValueError("repeat_indices must be a non-empty set of unique integers")
    if any(value < 0 for value in repeat_indices):
        raise ValueError("repeat_indices cannot be negative")
    planned_repetitions = int(payload.get("planned_repetitions", repetitions))
    if max(repeat_indices) >= planned_repetitions:
        raise ValueError("repeat_indices must be below planned_repetitions")
    if payload.get("paper_mode"):
        if payload.get("scientific_contract") != "v5":
            raise ValueError("paper matrices require scientific_contract: v5")
        block = str(payload.get("analysis_block", "primary"))
        minimum_worlds = 10 if block == "primary" else 5
        minimum_repetitions = 2 if block == "primary" else 1
        if len(set(world_seeds)) < minimum_worlds:
            raise ValueError(
                f"paper {block} block requires at least {minimum_worlds} world seeds"
            )
        if planned_repetitions < minimum_repetitions:
            raise ValueError(
                f"paper {block} block requires at least {minimum_repetitions} repeats"
            )
    if len(fault_seeds) != 1:
        raise ValueError(
            "current named fault schedules are deterministic and do not sample from "
            "fault_seed; provide exactly one fault seed to avoid pseudo-replication"
        )
    rows: list[dict[str, Any]] = []
    configured_teams = tuple(
        payload.get("team_ids", [payload.get("primary_team_id", "wetjune_2agent")])
    )
    allowed_teams = {"wetjune_2agent", "wetjune_3agent", "wetjune_4agent"}
    if not configured_teams or not set(configured_teams) <= allowed_teams:
        raise ValueError("team_ids must contain known 2/3/4-agent team IDs")
    controller_profiles = payload.get("controller_profiles") or [{"id": "default"}]
    if not isinstance(controller_profiles, list) or not controller_profiles:
        raise ValueError("controller_profiles must be a non-empty list")
    profile_ids = [str(profile.get("id", "")) for profile in controller_profiles]
    if any(not identifier for identifier in profile_ids) or len(
        set(profile_ids)
    ) != len(profile_ids):
        raise ValueError("controller profiles require unique non-empty IDs")
    known_profiles = set(profile_ids)
    for condition in conditions:
        selected = set(condition.get("controller_profile_ids") or ())
        if selected - known_profiles:
            raise ValueError(
                f"condition {condition['id']!r} selects unknown controller profiles: "
                f"{sorted(selected - known_profiles)}"
            )
        selected_scenarios = set(condition.get("scenarios") or scenarios)
        unknown_scenarios = selected_scenarios - set(FARM_SCENARIOS)
        if unknown_scenarios:
            raise ValueError(
                f"condition {condition['id']!r} selects unknown scenarios: "
                f"{sorted(unknown_scenarios)}"
            )
        policy = condition.get("repeat_policy", "all_repeats")
        if policy not in {"all_repeats", "once_per_world"}:
            raise ValueError(
                f"condition {condition['id']!r} has invalid repeat_policy {policy!r}"
            )
    for scenario_id in scenarios:
        for repeat_index in repeat_indices:
            for world_seed in world_seeds:
                for scheduler_seed in scheduler_seeds:
                    for model_seed in model_seeds:
                        for fault_seed in fault_seeds:
                            for condition in conditions:
                                if scenario_id not in set(
                                    condition.get("scenarios") or scenarios
                                ):
                                    continue
                                if (
                                    condition.get("repeat_policy", "all_repeats")
                                    == "once_per_world"
                                    and repeat_index != min(repeat_indices)
                                ):
                                    continue
                                selected_profiles = [
                                    profile
                                    for profile in controller_profiles
                                    if not condition.get("controller_profile_ids")
                                    or profile["id"]
                                    in set(condition["controller_profile_ids"])
                                ]
                                if not selected_profiles:
                                    continue
                                condition_teams = tuple(
                                    condition.get("team_ids")
                                    or (
                                        configured_teams
                                        if condition.get("scalability", False)
                                        else (
                                            condition.get(
                                                "team_id",
                                                payload.get(
                                                    "primary_team_id",
                                                    "wetjune_2agent",
                                                ),
                                            ),
                                        )
                                    )
                                )
                                execution = condition.get("execution", "dcore")
                                faults = (
                                    ["none"]
                                    if execution == "legacy_import"
                                    else condition.get("faults", ["none"])
                                )
                                if (
                                    len(condition_teams) > 1
                                    and set(faults) - {"none", "mixed"}
                                    and not payload.get(
                                        "allow_full_team_cartesian", False
                                    )
                                ):
                                    raise ValueError(
                                        "multi-team scalability conditions may use only "
                                        "none/mixed faults unless allow_full_team_cartesian=true"
                                    )
                                for profile in selected_profiles:
                                    for team_id in condition_teams:
                                        for fault in faults:
                                            rows.append(
                                                _resolve_row(
                                                    payload=payload,
                                                    condition=condition,
                                                    profile=profile,
                                                    scenario_id=scenario_id,
                                                    execution=execution,
                                                    fault=fault,
                                                    team_id=team_id,
                                                    repeat_index=repeat_index,
                                                    world_seed=world_seed,
                                                    scheduler_seed=scheduler_seed,
                                                    model_seed=model_seed,
                                                    fault_seed=fault_seed,
                                                )
                                            )
    return rows


def shard_rows(
    rows: list[dict[str, Any]], *, shard_count: int = 1, shard_index: int = 0
) -> list[dict[str, Any]]:
    """Return a stable, disjoint shard of a resolved matrix.

    Assignment uses the immutable run key rather than list position, so adding
    unrelated rows cannot silently move an existing run between workers.
    """

    if shard_count < 1:
        raise ValueError("shard_count must be at least one")
    if not 0 <= shard_index < shard_count:
        raise ValueError("shard_index must satisfy 0 <= index < shard_count")
    ordered = sorted(rows, key=lambda item: str(item["run_key"]))
    return [
        row
        for row in ordered
        if int(str(row["run_key"]), 16) % shard_count == shard_index
    ]


def summarize_resolved_rows(
    rows: list[dict[str, Any]], *, pricing: dict[str, float] | None = None
) -> dict[str, Any]:
    """Compute a conservative, no-model execution and cost preflight."""

    by_block: defaultdict[str, int] = defaultdict(int)
    by_team_size: defaultdict[str, int] = defaultdict(int)
    by_execution: defaultdict[str, int] = defaultdict(int)
    max_calls = 0
    max_tokens = 0
    for row in rows:
        by_block[str(row.get("analysis_block", "unspecified"))] += 1
        team_id = str(row.get("team_id", "legacy"))
        size = next((value for value in (2, 3, 4) if f"_{value}agent" in team_id), 0)
        by_team_size[str(size or "legacy")] += 1
        by_execution[str(row.get("execution"))] += 1
        if row.get("controller_mode") == "llm":
            max_calls += int(row.get("team_call_budget") or row.get("max_model_calls", 0))
            max_tokens += int(row.get("team_token_budget") or 0)
    estimate = None
    if pricing:
        # The token cap does not predict the input/output split.  A declared
        # output share makes the assumption explicit and keeps pricing out of
        # scientific manifests.
        output_share = float(pricing.get("assumed_output_share", 0.2))
        input_tokens = max_tokens * (1 - output_share)
        output_tokens = max_tokens * output_share
        estimate = (
            input_tokens / 1_000_000 * float(pricing["input_usd_per_million"])
            + output_tokens / 1_000_000 * float(pricing["output_usd_per_million"])
        )
    return {
        "total_runs": len(rows),
        "by_block": dict(sorted(by_block.items())),
        "by_team_size": dict(sorted(by_team_size.items())),
        "by_execution": dict(sorted(by_execution.items())),
        "maximum_model_calls": max_calls,
        "maximum_total_tokens": max_tokens,
        "estimated_api_cost_usd": round(estimate, 2) if estimate is not None else None,
        "cost_assumptions": pricing,
    }


def _resolve_row(
    *,
    payload: dict[str, Any],
    condition: dict[str, Any],
    profile: dict[str, Any],
    scenario_id: str,
    execution: str,
    fault: str,
    team_id: str,
    repeat_index: int,
    world_seed: int,
    scheduler_seed: int,
    model_seed: int,
    fault_seed: int,
) -> dict[str, Any]:
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault!r}")
    enforcement = condition.get("enforcement", "audit")
    if enforcement is False:
        enforcement = "off"

    def actor_map(name: str) -> dict[str, Any]:
        return dict(
            (condition.get(f"{name}_by_team_id", {}) or {}).get(team_id)
            or (profile.get(f"{name}_by_team_id", {}) or {}).get(team_id)
            or (payload.get(f"{name}_by_team_id", {}) or {}).get(team_id)
            or condition.get(name)
            or profile.get(name)
            or payload.get(name, {})
        )

    row = {
        "analysis_block": payload.get("analysis_block", "engineering"),
        "scenario_id": scenario_id,
        "condition_id": condition["id"],
        "controller_profile_id": profile["id"],
        "execution": execution,
        "fault": fault,
        "team_id": team_id,
        "team_spec_path": condition.get("team_spec_path")
        or (payload.get("team_spec_by_id", {}) or {}).get(team_id),
        "role_refinement_path": condition.get("role_refinement_path")
        or (payload.get("role_refinement_by_team_id", {}) or {}).get(team_id),
        "activation_policy": condition.get("activation_policy")
        or payload.get("activation_policy"),
        "communication_topology": condition.get("communication_topology")
        or payload.get("communication_topology"),
        "team_call_budget": condition.get("team_call_budget")
        or (condition.get("team_call_budget_by_team_id", {}) or {}).get(team_id)
        or (payload.get("team_call_budget_by_team_id", {}) or {}).get(team_id)
        or payload.get("team_call_budget"),
        "per_agent_call_budget": condition.get("per_agent_call_budget")
        or (condition.get("per_agent_call_budget_by_team_id", {}) or {}).get(team_id)
        or (payload.get("per_agent_call_budget_by_team_id", {}) or {}).get(team_id)
        or payload.get("per_agent_call_budget"),
        "team_token_budget": condition.get("team_token_budget")
        or (condition.get("team_token_budget_by_team_id", {}) or {}).get(team_id)
        or (payload.get("team_token_budget_by_team_id", {}) or {}).get(team_id)
        or payload.get("team_token_budget"),
        "per_agent_token_budget": condition.get("per_agent_token_budget")
        or (condition.get("per_agent_token_budget_by_team_id", {}) or {}).get(team_id)
        or (payload.get("per_agent_token_budget_by_team_id", {}) or {}).get(team_id)
        or payload.get("per_agent_token_budget"),
        "max_messages": int(payload.get("max_messages", 10000)),
        "repeat_index": repeat_index,
        "world_seed": world_seed,
        "scheduler_seed": scheduler_seed,
        "model_seed": model_seed,
        "fault_seed": fault_seed,
        "controller_mode": condition.get(
            "controller_mode",
            profile.get("controller_mode", payload.get("controller_mode", "scripted")),
        ),
        "visibility_mode": condition.get("visibility", "local"),
        "handoff_mode": condition.get("handoff", "causal"),
        "enforcement_mode": enforcement,
        "model_by_actor": actor_map("model_by_actor"),
        "provider_by_actor": actor_map("provider_by_actor"),
        "endpoint_by_actor": actor_map("endpoint_by_actor"),
        "agent_family_by_actor": actor_map("agent_family_by_actor"),
        "history_window_by_actor": actor_map("history_window_by_actor"),
        "temperature_by_actor": actor_map("temperature_by_actor"),
        "max_model_calls": int(
            profile.get("max_model_calls", payload.get("max_model_calls", 700))
        ),
        "max_output_tokens": int(
            profile.get("max_output_tokens", payload.get("max_output_tokens", 1024))
        ),
        "trace_pattern": condition.get("trace_pattern"),
        "outcome_pattern": condition.get("outcome_pattern"),
        "fault_target_ids": condition.get("fault_target_ids", []),
        "paper_mode": bool(
            condition.get("paper_mode", payload.get("paper_mode", False))
        ),
        "scientific_contract": condition.get(
            "scientific_contract", payload.get("scientific_contract", "v4")
        ),
        "petri_spec_path": condition.get("petri_spec_path")
        or (payload.get("petri_spec_by_team_id", {}) or {}).get(team_id)
        or (payload.get("petri_spec_by_scenario", {}) or {}).get(scenario_id)
        or payload.get("petri_spec_path"),
        "scientific_gate_manifest": condition.get("scientific_gate_manifest")
        or (payload.get("scientific_gate_by_team_id", {}) or {}).get(team_id)
        or (payload.get("scientific_gate_by_scenario", {}) or {}).get(scenario_id)
        or payload.get("scientific_gate_manifest"),
    }
    model_configuration_id = stable_digest(
        {
            "models": row["model_by_actor"],
            "providers": row["provider_by_actor"],
            "families": row["agent_family_by_actor"],
            "endpoints": row["endpoint_by_actor"],
            "history_windows": row["history_window_by_actor"],
            "temperatures": row["temperature_by_actor"],
            "max_model_calls": row["max_model_calls"],
            "max_output_tokens": row["max_output_tokens"],
            "team_id": team_id,
            "team_call_budget": row["team_call_budget"],
            "per_agent_call_budget": row["per_agent_call_budget"],
            "controller_profile_id": row["controller_profile_id"],
        }
    )[:16]
    row["model_configuration_id"] = model_configuration_id
    row["pair_id"] = (
        f"{scenario_id}:t{team_id}:w{world_seed}:q{scheduler_seed}:m{model_seed}:"
        f"c{model_configuration_id}:r{repeat_index}"
    )
    row["world_cluster_id"] = f"{scenario_id}:w{world_seed}"
    row["run_key"] = stable_digest(row)[:16]
    return row


def evaluate_legacy_farmare_trace(
    trace_path: str | Path,
    scenario_id: str,
    *,
    condition: str = "legacy",
    pair_id: str | None = None,
    outcome_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate compatible legacy tool paths without inventing local state."""
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        compile_native_petri_net,
    )
    from are.simulation.scenarios.workflow_validation import evaluate_workflows

    payload = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    if payload.get("version") != "are_simulation_v1":
        raise ValueError("legacy input must be an are_simulation_v1 trace")
    net = compile_native_petri_net(scenario_id)
    reference = [
        {
            "tool_name": transition.action,
            "tool_args": {
                constraint.name: constraint.expected
                for constraint in transition.arguments
            },
            "op_type": "WRITE",
        }
        for transition in net.transitions
        if transition.actor_id != "world"
        and transition.kind not in {TransitionKind.SEND, TransitionKind.RECEIVE}
    ]
    observed = []
    tool_successes = 0
    for raw in payload.get("world_logs", []):
        log = json.loads(raw) if isinstance(raw, str) else raw
        if log.get("log_type") != "action" or not log.get("action_name"):
            continue
        observed.append(
            {
                "tool_name": f"{log.get('app_name')}__{log['action_name']}",
                "tool_args": log.get("input") or {},
                "op_type": "WRITE",
            }
        )
        tool_successes += int(not log.get("exception"))
    workflow = evaluate_workflows(reference, observed)
    outcome: dict[str, Any] = {}
    if outcome_path is not None:
        candidate = Path(outcome_path)
        if not candidate.is_file():
            raise FileNotFoundError(f"legacy outcome sidecar not found: {candidate}")
        outcome = json.loads(candidate.read_text(encoding="utf-8"))
    else:
        for candidate in (
            Path(trace_path).with_name("farm_outcome.json"),
            Path(trace_path).with_name("experiment_row.json"),
        ):
            if candidate.is_file():
                outcome = json.loads(candidate.read_text(encoding="utf-8"))
                break
    return {
        "scenario": scenario_id,
        "condition": condition,
        "pair_id": pair_id,
        "source_trace": str(trace_path),
        "trace_version": "are_simulation_v1",
        "final_success": outcome.get("success"),
        "success": outcome.get("success"),
        "safety_success": outcome.get("safety_success"),
        "infrastructure_failure": False,
        "bfcl_tool_success": tool_successes / max(1, len(observed)),
        "merged_pc_ktc": workflow.get("combined"),
        "core_path_correctness": workflow.get("path_correctness"),
        "event_fidelity": None,
        "causal_conformance": None,
        "dcore_score": None,
        "local_global_gap": None,
        "local_metrics": None,
        "knowledge_metrics_available": False,
        "biological_yield_kg": outcome.get("biological_yield_kg"),
        "marketable_yield_kg": outcome.get("marketable_yield_kg"),
        "harvest_complete": outcome.get("harvest_complete"),
        "storage_complete": outcome.get("storage_complete"),
        "runtime_seconds": outcome.get("runtime_seconds"),
        "total_tokens": outcome.get("total_tokens"),
        "legacy_outcome_available": bool(outcome),
    }


def _config_from_row(
    row: dict[str, Any], output_dir: str | None = None
) -> DistributedRunnerConfig:
    return DistributedRunnerConfig(
        scenario_id=row["scenario_id"],
        controller_mode=row["controller_mode"],
        visibility_mode=row["visibility_mode"],
        handoff_mode=row["handoff_mode"],
        enforcement_mode=row["enforcement_mode"],
        fault=row["fault"],
        world_seed=row["world_seed"],
        scheduler_seed=row["scheduler_seed"],
        model_seed=row["model_seed"],
        fault_seed=row["fault_seed"],
        repeat_index=row["repeat_index"],
        condition_id=row["condition_id"],
        controller_profile_id=row.get("controller_profile_id", "default"),
        output_dir=output_dir,
        model_by_actor=row.get("model_by_actor", {}),
        provider_by_actor=row.get("provider_by_actor", {}),
        endpoint_by_actor=row.get("endpoint_by_actor", {}),
        agent_family_by_actor=row.get("agent_family_by_actor", {}),
        history_window_by_actor=row.get("history_window_by_actor", {}),
        temperature_by_actor=row.get("temperature_by_actor", {}),
        max_model_calls=int(row.get("max_model_calls", 700)),
        max_output_tokens=int(row.get("max_output_tokens", 1024)),
        fault_target_ids=tuple(row.get("fault_target_ids", ())),
        paper_mode=bool(row.get("paper_mode", False)),
        petri_spec_path=row.get("petri_spec_path"),
        scientific_gate_manifest=row.get("scientific_gate_manifest"),
        team_id=row.get("team_id", "wetjune_2agent"),
        team_spec_path=row.get("team_spec_path"),
        role_refinement_path=row.get("role_refinement_path"),
        activation_policy=row.get("activation_policy"),
        communication_topology=row.get("communication_topology"),
        team_call_budget=row.get("team_call_budget"),
        per_agent_call_budget=row.get("per_agent_call_budget"),
        team_token_budget=row.get("team_token_budget"),
        per_agent_token_budget=row.get("per_agent_token_budget"),
        max_messages=int(row.get("max_messages", 10000)),
        scientific_contract=row.get("scientific_contract", "v4"),
    )


def default_experiment_configs(
    scenario_id: str,
    *,
    seeds: tuple[int, ...] = (0,),
    controller_mode: str = "scripted",
) -> list[tuple[str, DistributedRunnerConfig]]:
    if scenario_id not in FARM_SCENARIOS:
        configs = []
        for condition, visibility, handoff, enforcement in LEGACY_CONDITIONS:
            condition_faults = (
                ("none",)
                if condition
                in {"shared_upper_bound", "local_free_text_reliable", "causal_reliable"}
                else LEGACY_FAULTS[1:]
            )
            for fault in condition_faults:
                for seed in seeds:
                    configs.append(
                        (
                            condition,
                            DistributedRunnerConfig(
                                scenario_id=scenario_id,
                                controller_mode=controller_mode,
                                visibility_mode=visibility,
                                handoff_mode=handoff,
                                enforcement_mode=enforcement,
                                fault=fault,
                                scheduler_seed=seed,
                            ),
                        )
                    )
        return configs
    payload = {
        "schema_version": "farm_dcore_matrix_v1",
        "scenarios": [scenario_id],
        "world_seeds": list(seeds),
        "controller_mode": controller_mode,
        "conditions": [c for c in DEFAULT_CONDITIONS if c.get("execution") == "dcore"],
    }
    return [
        (row["condition_id"], _config_from_row(row))
        for row in resolve_manifest(payload)
    ]


def run_resolved_matrix(
    rows: list[dict[str, Any]], output_dir: str | Path, *, resume: bool = True
) -> list[dict[str, Any]]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    runner = DistributedScenarioRunner()
    completed_rows: list[dict[str, Any]] = []
    missing_legacy_inputs: list[dict[str, Any]] = []
    for row in rows:
        if row["execution"] == "legacy_import":
            pattern = row.get("trace_pattern")
            if pattern:
                trace_path = Path(
                    str(pattern).format(
                        scenario_id=row["scenario_id"],
                        world_seed=row["world_seed"],
                        repeat_index=row["repeat_index"],
                        controller_profile_id=row.get(
                            "controller_profile_id", "default"
                        ),
                    )
                )
                if trace_path.exists():
                    outcome_path = None
                    if row.get("outcome_pattern"):
                        outcome_path = Path(
                            str(row["outcome_pattern"]).format(
                                scenario_id=row["scenario_id"],
                                world_seed=row["world_seed"],
                                repeat_index=row["repeat_index"],
                                controller_profile_id=row.get(
                                    "controller_profile_id", "default"
                                ),
                            )
                        )
                    completed_rows.append(
                        evaluate_legacy_farmare_trace(
                            trace_path,
                            row["scenario_id"],
                            condition=row["condition_id"],
                            pair_id=row["pair_id"],
                            outcome_path=outcome_path,
                        )
                    )
                else:
                    missing_legacy_inputs.append(
                        {**row, "resolved_trace_path": str(trace_path)}
                    )
            else:
                missing_legacy_inputs.append(row)
            continue
        run_dir = root / row["scenario_id"] / f"{row['condition_id']}_{row['run_key']}"
        completion, row_file = (
            run_dir / "COMPLETED.json",
            run_dir / "experiment_row.json",
        )
        if resume and completion.exists() and row_file.exists():
            completed_rows.append(json.loads(row_file.read_text(encoding="utf-8")))
            continue
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            if row["execution"] in {"farmare_direct", "farmare_a2a"}:
                from are.simulation.distributed.matched_baselines import (
                    run_matched_baseline,
                )

                completed = run_matched_baseline(row, run_dir)
                row_file.write_text(
                    json.dumps(completed, indent=2, default=str), encoding="utf-8"
                )
                completion.write_text(
                    json.dumps(
                        {"status": "completed", "run_key": row["run_key"]},
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                completed_rows.append(completed)
            elif row["execution"] == "dcore":
                result = runner.run(_config_from_row(row, str(run_dir)))
                completed = {
                    **row,
                    **json.loads(
                        Path(result.artifacts["row"]).read_text(encoding="utf-8")
                    ),
                }
                Path(result.artifacts["row"]).write_text(
                    json.dumps(completed, indent=2, default=str), encoding="utf-8"
                )
                completed_rows.append(completed)
            else:
                raise ValueError(f"unsupported execution mode {row['execution']!r}")
        except Exception as error:
            failure = {
                "schema_version": "dcore_failure_v1",
                "status": "failed",
                "run": row,
                "error_type": type(error).__name__,
                "error": str(error),
            }
            (run_dir / "FAILURE.json").write_text(
                json.dumps(failure, indent=2), encoding="utf-8"
            )
            completed_rows.append(
                {
                    **row,
                    "scenario": row["scenario_id"],
                    "condition": row["condition_id"],
                    "controller": row["controller_mode"],
                    "status": "failed",
                    "success": False,
                    "safety_success": False,
                    "infrastructure_failure": True,
                    "controller_failure": False,
                    "error_type": type(error).__name__,
                    "artifact_dir": str(run_dir),
                }
            )
    _add_yield_shortfall(completed_rows)
    write_tidy_rows(completed_rows, root)
    write_normalized_outputs(completed_rows, root)
    (root / "legacy_inputs_required.json").write_text(
        json.dumps(missing_legacy_inputs, indent=2), encoding="utf-8"
    )
    return completed_rows


def run_experiment_matrix(
    scenario_id: str,
    output_dir: str | Path,
    *,
    seeds: tuple[int, ...] = (0,),
    controller_mode: str = "scripted",
) -> list[dict[str, Any]]:
    if scenario_id not in FARM_SCENARIOS:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        runner = DistributedScenarioRunner()
        results = []
        for index, (condition, config) in enumerate(
            default_experiment_configs(
                scenario_id, seeds=seeds, controller_mode=controller_mode
            )
        ):
            run_dir = (
                root
                / f"{index:03d}_{condition}_{config.fault}_seed{config.scheduler_seed}"
            )
            result = runner.run(config.model_copy(update={"output_dir": str(run_dir)}))
            results.append(
                {
                    "run_id": result.trace.run_id,
                    "scenario": scenario_id,
                    "condition": condition,
                    "fault": config.fault,
                    "seed": config.scheduler_seed,
                    "controller": controller_mode,
                    "event_fidelity": result.metrics["event_fidelity"],
                    "causal_conformance": result.metrics["causal_conformance"],
                    "dcore_score": result.metrics["dcore_score"],
                    "local_global_gap": result.metrics["local_global_gap"],
                    "success": result.trace.outcome.get("success"),
                    "artifact_dir": str(run_dir),
                }
            )
        write_tidy_rows(results, root)
        return results
    rows = []
    for condition, config in default_experiment_configs(
        scenario_id, seeds=seeds, controller_mode=controller_mode
    ):
        payload = config.model_dump(mode="json")
        payload.update(
            {
                "execution": "dcore",
                "condition_id": condition,
                "run_key": stable_digest(payload)[:16],
            }
        )
        rows.append(payload)
    return run_resolved_matrix(rows, output_dir)


def _add_yield_shortfall(rows: list[dict[str, Any]]) -> None:
    oracle = {
        row["pair_id"]: row
        for row in rows
        if row.get("condition") == "scripted_petri_oracle"
        and not row.get("infrastructure_failure")
    }
    for row in rows:
        reference = oracle.get(row.get("pair_id", ""), {})
        for field in ("biological_yield_kg", "marketable_yield_kg"):
            expected, actual = reference.get(field), row.get(field)
            name = field.removesuffix("_kg").removesuffix("_yield") + "_yield_shortfall"
            row[name] = (
                (float(expected) - float(actual)) / float(expected)
                if expected not in (None, 0) and actual is not None
                else None
            )


def write_tidy_rows(rows: list[dict[str, Any]], root: Path) -> None:
    (root / "results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )
    keys = sorted({key for row in rows for key in row})
    with (root / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _write_normalized_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not keys:
            return
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "run_id",
            "pair_id",
            "world_cluster_id",
            "scenario",
            "team_id",
            "condition",
            "fault",
            "controller_profile_id",
            "repeat_index",
            "world_seed",
            "scheduler_seed",
            "model_seed",
        )
    }


def write_normalized_outputs(rows: list[dict[str, Any]], root: Path) -> None:
    """Export analysis-ready long tables used by paper plots and checks."""

    phases: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    ridges: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []
    for row in rows:
        identity = _identity(row)
        for phase, profile in (row.get("phase_profile") or {}).items():
            phases.append({**identity, "phase": phase, **profile})
        raw_modules = row.get("module_profile") or {}
        if isinstance(raw_modules, dict):
            for module_id, profile in raw_modules.items():
                modules.append({**identity, "module_id": module_id, **dict(profile)})
        else:
            for profile in raw_modules:
                modules.append({**identity, **profile})
        for ridge in row.get("per_ridge_yield") or []:
            ridges.append({**identity, **ridge})
        for actor, telemetry in (row.get("per_agent_telemetry") or {}).items():
            agents.append({**identity, "actor_id": actor, **telemetry})
    _write_normalized_csv(root / "phase_metrics.csv", phases)
    _write_normalized_csv(root / "module_metrics.csv", modules)
    _write_normalized_csv(root / "ridge_yields.csv", ridges)
    _write_normalized_csv(root / "agent_telemetry.csv", agents)


def _bootstrap_ci(
    values: list[float], *, seed: int = 0, draws: int = 2000
) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    samples = sorted(mean(rng.choices(values, k=len(values))) for _ in range(draws))
    return [samples[int(0.025 * draws)], samples[min(draws - 1, int(0.975 * draws))]]


def _paired_bootstrap_p(
    values: list[float], *, seed: int = 0, draws: int = 2000
) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    estimates = [mean(rng.choices(values, k=len(values))) for _ in range(draws)]
    lower = sum(value <= 0 for value in estimates) / draws
    upper = sum(value >= 0 for value in estimates) / draws
    return min(1.0, 2 * min(lower, upper))


def _holm_adjust(rows: list[dict[str, Any]]) -> None:
    available = sorted(
        (row for row in rows if row.get("p_value") is not None),
        key=lambda row: float(row["p_value"]),
    )
    running = 0.0
    count = len(available)
    for index, row in enumerate(available):
        adjusted = min(1.0, (count - index) * float(row["p_value"]))
        running = max(running, adjusted)
        row["holm_adjusted_p"] = running


def _iqm(values: list[float]) -> float | None:
    """Interquartile mean with deterministic fractional trimming."""

    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) < 4:
        return mean(ordered)
    lower = len(ordered) // 4
    upper = len(ordered) - lower
    return mean(ordered[lower:upper])


def _wilson_interval(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    radius = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, center - radius), min(1.0, center + radius)]


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and values[order[end]] == values[order[index]]:
            end += 1
        rank = (index + end - 1) / 2 + 1
        for position in order[index:end]:
            ranks[position] = rank
        index = end
    return ranks


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left)
        * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator else None


def _calibration(
    rows: list[dict[str, Any]],
    *,
    metric: str = "dcore_score",
    yield_field: str = "marketable_yield_shortfall",
    completing_only: bool,
) -> dict[str, Any]:
    pairs = [
        (1 - float(row[metric]), float(row[yield_field]))
        for row in rows
        if row.get(metric) is not None
        and row.get(yield_field) is not None
        and (not completing_only or row.get("success"))
    ]
    if len(pairs) < 2:
        return {
            "metric": metric,
            "yield_field": yield_field,
            "n": len(pairs),
            "available": False,
        }
    x, y = [item[0] for item in pairs], [item[1] for item in pairs]
    x_mean, y_mean = mean(x), mean(y)
    denominator = sum((value - x_mean) ** 2 for value in x)
    slope = (
        sum((left - x_mean) * (right - y_mean) for left, right in pairs) / denominator
        if denominator
        else 0.0
    )
    intercept = y_mean - slope * x_mean
    predictions = [intercept + slope * value for value in x]
    residual = sum(
        (actual - predicted) ** 2 for actual, predicted in zip(y, predictions)
    )
    total = sum((actual - y_mean) ** 2 for actual in y)
    return {
        "metric": metric,
        "yield_field": yield_field,
        "n": len(pairs),
        "available": True,
        "slope": slope,
        "intercept": intercept,
        "rmse": math.sqrt(residual / len(pairs)),
        "r_squared": 1 - residual / total if total else None,
        "spearman": _correlation(_ranks(x), _ranks(y)),
    }


def _loco_calibration(
    rows: list[dict[str, Any]],
    *,
    metric: str,
    yield_field: str,
) -> dict[str, Any]:
    """Leave-one-world-cluster-out predictive calibration."""

    usable = [
        row
        for row in rows
        if row.get(metric) is not None and row.get(yield_field) is not None
    ]

    def cluster_id(row: dict[str, Any]) -> str:
        return str(
            row.get("world_cluster_id")
            or f"{row.get('scenario')}:w{row.get('world_seed')}"
        )

    clusters = sorted({cluster_id(row) for row in usable})
    predictions: list[tuple[float, float]] = []
    for held_out in clusters:
        training = [row for row in usable if cluster_id(row) != held_out]
        testing = [row for row in usable if cluster_id(row) == held_out]
        if len(training) < 2:
            continue
        x = [1 - float(row[metric]) for row in training]
        y = [float(row[yield_field]) for row in training]
        x_mean, y_mean = mean(x), mean(y)
        denominator = sum((value - x_mean) ** 2 for value in x)
        slope = (
            sum((left - x_mean) * (right - y_mean) for left, right in zip(x, y))
            / denominator
            if denominator
            else 0.0
        )
        intercept = y_mean - slope * x_mean
        predictions.extend(
            (
                intercept + slope * (1 - float(row[metric])),
                float(row[yield_field]),
            )
            for row in testing
        )
    if not predictions:
        return {"available": False, "n": 0, "cluster_count": len(clusters)}
    actual = [item[1] for item in predictions]
    predicted = [item[0] for item in predictions]
    actual_mean = mean(actual)
    residual = sum((a - p) ** 2 for p, a in predictions)
    total = sum((a - actual_mean) ** 2 for a in actual)
    return {
        "available": True,
        "n": len(predictions),
        "cluster_count": len(clusters),
        "rmse": math.sqrt(residual / len(predictions)),
        "r_squared": 1 - residual / total if total else None,
        "spearman": _correlation(_ranks(predicted), _ranks(actual)),
        "method": "leave_one_world_cluster_out",
    }


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate by condition, keeping one full season as the observational unit."""
    scientific_rows = [row for row in rows if not row.get("infrastructure_failure")]
    grouped: defaultdict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for row in rows:
        grouped[
            (
                str(row.get("scenario")),
                str(row.get("team_id", "legacy")),
                str(row.get("condition")),
                str(row.get("fault", "none")),
                str(row.get("controller_profile_id", "legacy")),
                str(row.get("model_configuration_id", "legacy")),
            )
        ].append(row)
    summaries = []
    for (
        scenario,
        team_id,
        condition,
        fault,
        controller_profile_id,
        model_configuration_id,
    ), group in sorted(grouped.items()):
        primary = [row for row in group if not row.get("infrastructure_failure")]
        analysis = primary
        summary: dict[str, Any] = {
            "scenario": scenario,
            "team_id": team_id,
            "condition": condition,
            "fault": fault,
            "controller_profile_id": controller_profile_id,
            "model_configuration_id": model_configuration_id,
            "n": len(group),
            "n_primary": len(primary),
            "completion_rate": (
                mean(bool(row.get("success")) for row in analysis) if analysis else None
            ),
            "safety_rate": (
                mean(bool(row.get("safety_success")) for row in analysis)
                if analysis
                else None
            ),
            "infrastructure_failure_rate": mean(
                bool(row.get("infrastructure_failure")) for row in group
            ),
        }
        for label, field in (("completion", "success"), ("safety", "safety_success")):
            cluster_binary: defaultdict[str, list[float]] = defaultdict(list)
            for row in analysis:
                cluster_binary[str(row.get("world_cluster_id"))].append(
                    float(bool(row.get(field)))
                )
            summary[f"{label}_95_ci"] = _bootstrap_ci(
                [mean(values) for values in cluster_binary.values()]
            )
            summary[f"{label}_all_failures_sensitivity"] = mean(
                bool(row.get(field)) for row in group
            )
        for metric in (
            "dcore_score",
            "event_fidelity",
            "causal_conformance",
            "information_global_discordance",
            "biological_yield_kg",
            "marketable_yield_kg",
            "biological_yield_shortfall",
            "marketable_yield_shortfall",
            "bfcl_tool_success",
            "merged_pc_ktc",
            "core_path_correctness",
            "po_pair_agreement",
            "petri_token_fitness",
            "petri_alignment_fitness",
            "total_model_calls",
            "total_tokens",
            "tokens_per_model_call",
            "model_completion_duration_seconds",
            "runtime_seconds",
            "runtime_per_model_call_seconds",
            "message_count",
            "coordination_edge_density",
            "coordination_failure_rate",
            "structural_exposure_duration_days",
            "successful_replanning_rate",
            "synchronization_lag_median_seconds",
            "synchronization_lag_p95_seconds",
            "never_received_fact_versions",
            "mean_message_path_length",
            "guard_unsafe_proposals",
            "guard_prevented_unsafe_writes",
            "guard_false_blocks",
            "guard_unnecessary_abstentions",
            "guard_eventual_recoveries",
            "guard_safety_benefit_rate",
            "guard_false_block_rate",
            "harmful_write_count",
            "unnecessary_write_count",
            "harmful_extra_cost",
            "igd_l0_g0",
            "igd_l0_g1",
            "igd_l1_g0",
            "igd_l1_g1",
        ):
            values = [
                float(row[metric])
                for row in analysis
                if row.get(metric) is not None and math.isfinite(float(row[metric]))
            ]
            bounded_failure_metric = metric in {
                "dcore_score",
                "event_fidelity",
                "causal_conformance",
                "bfcl_tool_success",
                "core_path_correctness",
                "po_pair_agreement",
            }
            itt_values = [
                (float(row[metric]) if row.get(metric) is not None else 0.0)
                for row in analysis
                if bounded_failure_metric
            ]
            if values:
                cluster_values: defaultdict[str, list[float]] = defaultdict(list)
                for row in analysis:
                    if row.get(metric) is not None:
                        cluster_values[
                            str(
                                row.get("world_cluster_id")
                                or f"{row.get('scenario')}:w{row.get('world_seed')}"
                            )
                        ].append(float(row[metric]))
                cluster_means = [mean(items) for items in cluster_values.values()]
                summary[metric] = {
                    "mean": mean(values),
                    "median": median(values),
                    "iqm": _iqm(values),
                    "sd": stdev(values) if len(values) > 1 else 0.0,
                    "n_available": len(values),
                    "n_missing": len(analysis) - len(values),
                    "availability_rate": len(values) / len(analysis),
                    "estimand": "available_trace_descriptive",
                    "intention_to_treat_mean": (
                        mean(itt_values) if itt_values else None
                    ),
                    "cluster_count": len(cluster_means),
                    "cluster_bootstrap_95_ci": _bootstrap_ci(cluster_means),
                }
            else:
                summary[metric] = {
                    "n_available": 0,
                    "n_missing": len(analysis),
                    "availability_rate": 0.0,
                    "estimand": "available_trace_descriptive",
                    "intention_to_treat_mean": (
                        mean(itt_values) if itt_values else None
                    ),
                }
        summaries.append(summary)
    oracle_by_pair = {
        row.get("pair_id"): row
        for row in scientific_rows
        if row.get("condition") == "scripted_petri_oracle"
    }
    paired: defaultdict[tuple[str, str, str, str], defaultdict[str, list[float]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    for row in scientific_rows:
        oracle = oracle_by_pair.get(row.get("pair_id"))
        if oracle is None or row.get("condition") == "scripted_petri_oracle":
            continue
        for metric in ("dcore_score", "marketable_yield_kg", "biological_yield_kg"):
            if row.get(metric) is not None and oracle.get(metric) is not None:
                paired[
                    (
                        str(row.get("condition")),
                        str(row.get("team_id", "legacy")),
                        str(row.get("fault", "none")),
                        metric,
                    )
                ][str(row.get("world_cluster_id"))].append(
                    float(row[metric]) - float(oracle[metric])
                )
    paired_summaries = [
        {
            "condition": condition,
            "team_id": team_id,
            "fault": fault,
            "metric": metric,
            "n_runs": sum(len(values) for values in clusters.values()),
            "n_pairs": sum(len(values) for values in clusters.values()),
            "n_world_clusters": len(clusters),
            "mean_paired_difference": mean(
                [mean(values) for values in clusters.values()]
            ),
            "paired_cluster_bootstrap_95_ci": _bootstrap_ci(
                [mean(values) for values in clusters.values()]
            ),
        }
        for (condition, team_id, fault, metric), clusters in sorted(paired.items())
    ]
    calibration_metrics = (
        "dcore_score",
        "event_fidelity",
        "causal_conformance",
        "information_global_discordance",
        "bfcl_tool_success",
        "merged_pc_ktc",
        "core_path_correctness",
        "po_pair_agreement",
        "petri_token_fitness",
        "petri_alignment_fitness",
    )
    calibration_by_metric = {
        yield_field: {
            metric: {
                "primary": _calibration(
                    scientific_rows,
                    metric=metric,
                    yield_field=yield_field,
                    completing_only=False,
                ),
                "completing_runs_only": _calibration(
                    scientific_rows,
                    metric=metric,
                    yield_field=yield_field,
                    completing_only=True,
                ),
            }
            for metric in calibration_metrics
        }
        for yield_field in (
            "marketable_yield_shortfall",
            "biological_yield_shortfall",
        )
    }
    loco = {
        yield_field: {
            metric: _loco_calibration(
                scientific_rows, metric=metric, yield_field=yield_field
            )
            for metric in calibration_metrics
        }
        for yield_field in (
            "marketable_yield_shortfall",
            "biological_yield_shortfall",
        )
    }
    indexed = {
        (
            str(row.get("pair_id")),
            str(row.get("condition")),
            str(row.get("fault", "none")),
        ): row
        for row in scientific_rows
    }
    contrasts = [
        (
            "representation",
            "causal_audit_minus_free_text_reliable",
            "local_causal_audit",
            "none",
            "local_free_text",
            "none",
        ),
        (
            "partial_information",
            "causal_audit_minus_shared_blackboard",
            "local_causal_audit",
            "none",
            "shared_blackboard",
            "none",
        ),
    ]
    observed_faults = sorted(
        {
            str(row.get("fault", "none"))
            for row in scientific_rows
            if row.get("fault") != "none"
        }
    )
    contrasts.extend(
        (
            "enforcement",
            f"enforce_minus_audit_{fault}",
            "local_causal_enforce",
            fault,
            "local_causal_audit",
            fault,
        )
        for fault in ("none", *observed_faults)
    )
    for condition in ("local_free_text", "local_causal_audit", "local_causal_enforce"):
        contrasts.extend(
            (
                "fault_effect",
                f"{condition}_{fault}_minus_reliable",
                condition,
                fault,
                condition,
                "none",
            )
            for fault in observed_faults
        )
    contrast_rows: list[dict[str, Any]] = []
    pair_ids = sorted({str(row.get("pair_id")) for row in scientific_rows})
    for (
        family,
        name,
        left_condition,
        left_fault,
        right_condition,
        right_fault,
    ) in contrasts:
        for metric in (
            "dcore_score",
            "event_fidelity",
            "causal_conformance",
            "information_global_discordance",
            "marketable_yield_kg",
            "marketable_yield_shortfall",
            "safety_success",
            "success",
            "successful_replanning_rate",
            "harmful_write_count",
            "unnecessary_write_count",
            "harmful_extra_cost",
            "synchronization_lag_p95_seconds",
            "guard_unsafe_proposals",
            "guard_prevented_unsafe_writes",
            "guard_false_blocks",
            "guard_unnecessary_abstentions",
            "guard_eventual_recoveries",
            "guard_safety_benefit_rate",
        ):
            differences_by_cluster: defaultdict[str, list[float]] = defaultdict(list)
            for pair_id in pair_ids:
                left = indexed.get((pair_id, left_condition, left_fault))
                right = indexed.get((pair_id, right_condition, right_fault))
                if left is None or right is None:
                    continue
                left_value, right_value = left.get(metric), right.get(metric)
                if left_value is None or right_value is None:
                    if metric in {"dcore_score", "safety_success", "success"}:
                        left_value = left_value if left_value is not None else 0.0
                        right_value = right_value if right_value is not None else 0.0
                    else:
                        continue
                cluster_id = str(
                    left.get("world_cluster_id")
                    or right.get("world_cluster_id")
                    or f"{left.get('scenario')}:w{left.get('world_seed')}"
                )
                differences_by_cluster[cluster_id].append(
                    float(left_value) - float(right_value)
                )
            cluster_differences = [
                mean(values) for values in differences_by_cluster.values()
            ]
            contrast_rows.append(
                {
                    "family": family,
                    "contrast": name,
                    "metric": metric,
                    "left": {"condition": left_condition, "fault": left_fault},
                    "right": {"condition": right_condition, "fault": right_fault},
                    "n_pairs": sum(
                        len(values) for values in differences_by_cluster.values()
                    ),
                    "n_world_clusters": len(differences_by_cluster),
                    "mean_paired_difference": mean(cluster_differences)
                    if cluster_differences
                    else None,
                    "paired_cluster_bootstrap_95_ci": _bootstrap_ci(
                        cluster_differences
                    ),
                    "p_value": _paired_bootstrap_p(cluster_differences),
                }
            )
    for family in {row["family"] for row in contrast_rows}:
        _holm_adjust([row for row in contrast_rows if row["family"] == family])
    return {
        "schema_version": "dcore_aggregate_v2",
        "observational_unit": "one_full_season_run",
        "bootstrap_cluster": "scenario_world_seed",
        "groups": summaries,
        "paired_comparisons": paired_summaries,
        "predeclared_contrasts": contrast_rows,
        "multiple_comparison_control": "holm_within_contrast_family",
        "metric_yield_calibration": {
            "interpretation": "predictive_validity_not_causal_effect",
            "primary": _calibration(scientific_rows, completing_only=False),
            "completing_runs_only": _calibration(scientific_rows, completing_only=True),
            "infrastructure_failure_sensitivity": _calibration(
                rows, completing_only=False
            ),
            "by_metric": calibration_by_metric,
            "leave_one_world_cluster_out": loco,
        },
    }


def aggregate_directory(
    input_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    paper_mode: bool = False,
) -> dict[str, Any]:
    path = Path(input_path)
    if path.is_dir():
        direct = path / "results.jsonl"
        sources = [direct] if direct.is_file() else sorted(path.rglob("results.jsonl"))
        if not sources:
            raise ValueError(f"no results.jsonl files found below {path}")
    else:
        sources = [path]
    rows = [
        json.loads(line)
        for source in sources
        for line in source.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if paper_mode:
        unresolved = {
            value
            for row in rows
            for value in _find_placeholder_values(row)
        }
        if unresolved:
            raise ValueError("paper rows contain unresolved placeholders")
        run_keys = [row.get("run_key") for row in rows if row.get("run_key")]
        if len(run_keys) != len(set(run_keys)):
            raise ValueError("paper aggregation contains duplicate run keys")
        for index, row in enumerate(rows):
            if row.get("paper_mode") is not True:
                raise ValueError(f"paper row {index} was not executed in paper mode")
            if row.get("bounded_llm_smoke") is True:
                raise ValueError(
                    f"paper row {index} is a prerelease connectivity smoke"
                )
            if row.get("infrastructure_failure"):
                if row.get("scientific_contract") != "v5":
                    raise ValueError(
                        f"infrastructure-failure row {index} is not a v5 treatment"
                    )
                # No trace-derived metric can exist when infrastructure fails.
                # Keep the row for the prespecified failure-rate sensitivity.
                continue
            legacy = row.get("trace_version") == "are_simulation_v1"
            if legacy:
                if any(
                    row.get(key) is not None
                    for key in ("event_fidelity", "causal_conformance", "dcore_score")
                ):
                    raise ValueError(
                        f"legacy row {index} fabricates unavailable v5 metrics"
                    )
                continue
            if row.get("metric_version") != "dcore_eval_v5":
                raise ValueError(f"paper row {index} is not dcore_eval_v5")
            if row.get("trace_schema_version") != "dcore_trace_v5":
                raise ValueError(f"paper row {index} is not dcore_trace_v5")
            if not row.get("process_spec_digest"):
                raise ValueError(f"paper row {index} has no frozen process digest")
            if row.get("metric_paper_eligible") is not True:
                raise ValueError(f"paper row {index} failed scientific audit gates")
            if row.get("fault") not in {None, "none"} and not row.get(
                "fault_manifested"
            ):
                raise ValueError(f"paper row {index} has an inactive fault treatment")
        primary = [
            row
            for row in rows
            if row.get("analysis_block") in {"primary_pass_1", "primary_pass_2"}
            and row.get("condition") != "scripted_petri_oracle"
        ]
        repeat_groups: defaultdict[tuple[Any, ...], set[int]] = defaultdict(set)
        for row in primary:
            repeat_groups[
                (
                    row.get("scenario"),
                    row.get("condition"),
                    row.get("fault"),
                    row.get("world_seed"),
                    row.get("controller_profile_id"),
                    row.get("model_configuration_id"),
                )
            ].add(int(row.get("repeat_index", -1)))
        incomplete = [key for key, repeats in repeat_groups.items() if repeats != {0, 1}]
        if incomplete:
            raise ValueError(
                f"paper aggregation is missing confirmatory repeats for {len(incomplete)} cells"
            )
    report = aggregate_rows(rows)
    target = Path(output_dir) if output_dir else sources[0].parent
    target.mkdir(parents=True, exist_ok=True)
    write_tidy_rows(rows, target)
    write_normalized_outputs(rows, target)
    (target / "aggregate.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


def _find_placeholder_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if "REPLACE_WITH" in value else set()
    if isinstance(value, dict):
        return {
            found
            for nested in value.values()
            for found in _find_placeholder_values(nested)
        }
    if isinstance(value, (list, tuple)):
        return {
            found for nested in value for found in _find_placeholder_values(nested)
        }
    return set()
