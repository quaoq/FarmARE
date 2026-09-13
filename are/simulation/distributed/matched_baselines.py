"""Fresh, paired FarmARE direct-tool and synchronous-A2A baselines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.distributed.llm_budget import team_llm_budget
from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.native_season import (
    NativeDistributedSeasonRunner,
    _farm_outcome,
)
from are.simulation.scenario_runner import ScenarioRunner
from are.simulation.scenarios.config import ScenarioRunnerConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


def _single_value(
    mapping: dict[str, Any], label: str, *, optional: bool = False
) -> Any:
    values = {value for value in mapping.values() if value is not None}
    if not values and optional:
        return None
    if len(values) != 1:
        raise ValueError(
            f"matched baseline requires one shared {label}; received {sorted(values)!r}"
        )
    return next(iter(values))


def delegation_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Report realized delegation separately from assignment to an A2A arm."""
    if "world_logs" not in payload:
        return {
            "delegation_observed": None,
            "delegation_group_count": None,
            "delegation_evidence_available": False,
        }
    groups = set()

    def walk(logs):
        for raw in logs:
            log = json.loads(raw) if isinstance(raw, str) else raw
            if log.get("log_type") == "subagent" and log.get("children"):
                groups.add(
                    str(log.get("group_id") or log.get("id") or stable_digest(log))
                )
                walk(log["children"])

    walk(payload["world_logs"])
    return {
        "delegation_observed": bool(groups),
        "delegation_group_count": len(groups),
        "delegation_evidence_available": True,
    }


def run_matched_baseline(row: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Run the ordinary FarmARE runner on the exact frozen paired world."""

    execution = row["execution"]
    if execution not in {"farmare_direct", "farmare_a2a"}:
        raise ValueError(f"unsupported matched baseline {execution!r}")
    from are.simulation.distributed.experiments import _config_from_row
    from are.simulation.distributed.teams import load_team_spec

    distributed_config = _config_from_row(row)
    net, process = NativeDistributedSeasonRunner._load_petri_net(distributed_config)
    scenario = create_native_scenario(
        row["scenario_id"],
        world_seed=row["world_seed"],
        scenario_revision=distributed_config.scenario_revision,
        calibration_candidate=distributed_config.calibration_candidate,
    )
    if distributed_config.paper_mode:
        NativeDistributedSeasonRunner._validate_scientific_gate(
            distributed_config,
            net,
            load_team_spec(distributed_config, scenario.get_tools()),
            process,
        )
    # Both baseline and distributed treatments receive this nonprocedural text;
    # neither receives the detailed L3 oracle workflow.
    scenario.detailed_briefing = False
    scenario.public_task_override = NativeDistributedSeasonRunner._task_briefing(  # type: ignore[attr-defined]
        row["scenario_id"]
    )
    farm_world = scenario.get_typed_app(FarmWorldApp)
    initial_inventory = dict(farm_world.get_state().get("inventory", {}))
    exogenous_manifest = getattr(farm_world.physics, "dcore_exogenous_manifest", {})
    exogenous_digest = stable_digest(exogenous_manifest)
    model = _single_value(row.get("model_by_actor", {}), "model")
    provider = _single_value(
        row.get("provider_by_actor", {}), "provider", optional=True
    )
    endpoint = _single_value(
        row.get("endpoint_by_actor", {}), "endpoint", optional=True
    )
    family = _single_value(row.get("agent_family_by_actor", {}), "controller family")
    history_values = {
        int(value) for value in row.get("history_window_by_actor", {}).values()
    }
    history_window = next(iter(history_values)) if len(history_values) == 1 else None
    budget_cap = int(row.get("team_call_budget") or row.get("max_model_calls", 700))
    token_cap = row.get("team_token_budget")
    config = ScenarioRunnerConfig(
        model=model,
        model_provider=provider,
        endpoint=endpoint,
        agent=family,
        max_turns=budget_cap,
        agent_max_iterations=budget_cap,
        history_window=history_window,
        a2a_app_prop=1.0 if execution == "farmare_a2a" else 0.0,
        a2a_policy="typed_experts",
        a2a_model=model,
        a2a_model_provider=provider,
        a2a_endpoint=endpoint,
        export=True,
        output_dir=str(run_dir),
        trace_dump_format="lite",
    )
    with team_llm_budget(budget_cap, int(token_cap) if token_cap else None) as budget:
        validation = ScenarioRunner().run(config, scenario)
    outcome = _farm_outcome(farm_world, initial_inventory)
    outcome["success"] = bool(outcome["success"] and validation.success is True)
    outcome.update(
        {
            "farmare_task_validation": {
                "success": validation.success,
                "rationale": validation.rationale,
                "exception": str(validation.exception)
                if validation.exception
                else None,
            },
            "exogenous_world_digest": exogenous_digest,
            "team_call_budget": budget_cap,
            "team_token_budget": token_cap,
            "total_model_calls": budget.calls,
            "total_tokens": budget.tokens,
            "call_budget_exhausted": budget.calls >= budget_cap,
            "token_budget_exhausted": bool(
                token_cap and budget.tokens >= int(token_cap)
            ),
            "token_budget_policy": "stop_before_next_model_call",
        }
    )
    source = Path(validation.export_path or "")
    if not source.is_file():
        raise RuntimeError(
            "matched FarmARE baseline did not export an authoritative trace"
        )
    from are.simulation.distributed.experiments import evaluate_legacy_farmare_trace

    result = evaluate_legacy_farmare_trace(
        source,
        row["scenario_id"],
        condition=row["condition_id"],
        pair_id=row["pair_id"],
    )
    result.update(row)
    result.update(outcome)
    result.update(
        delegation_observation(json.loads(source.read_text(encoding="utf-8")))
    )
    result.update(
        {
            "schema_version": "farm_dcore_matched_baseline_v1",
            "scenario": row["scenario_id"],
            "condition": row["condition_id"],
            "controller": row["controller_mode"],
            "status": "completed",
            "knowledge_metrics_available": False,
            "public_task_digest": stable_digest(scenario.public_task_override),
            "oracle_visible_to_controller": False,
            "matched_world": True,
            "matched_aggregate_budget": True,
            "source_trace": str(source),
            "artifact_dir": str(run_dir),
        }
    )
    (run_dir / "farm_outcome.json").write_text(
        json.dumps(outcome, indent=2, default=str), encoding="utf-8"
    )
    return result


__all__ = ["run_matched_baseline"]
