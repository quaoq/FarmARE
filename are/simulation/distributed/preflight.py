"""No-model scientific preflight for every selected farm world."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.distributed.experiments import (
    _config_from_row,
    load_manifest,
    resolve_manifest,
)
from are.simulation.distributed.models import EventKind, stable_digest
from are.simulation.distributed.native_season import (
    _farm_outcome,
)
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.scenario_runner import ScenarioRunner
from are.simulation.scenarios.config import ScenarioRunnerConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


def _native_oracle(scenario_id: str, world_seed: int, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    scenario = create_native_scenario(scenario_id, world_seed=world_seed)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    initial = dict(farm_world.get_state().get("inventory", {}))
    exogenous = getattr(farm_world.physics, "dcore_exogenous_manifest", {})
    validation = ScenarioRunner().run(
        ScenarioRunnerConfig(oracle=True, export=False, output_dir=str(output)), scenario
    )
    result = _farm_outcome(farm_world, initial)
    result["validation_success"] = validation.success
    result["exogenous_world_digest"] = stable_digest(exogenous)
    return result


def _fault_contract(fault: str, message_ids: set[str]) -> tuple[bool, str]:
    if fault == "none":
        return True, "control"
    if fault == "reorder":
        targets = {"handoff:midseason:v1", "handoff:midseason:v2"}
        return targets <= message_ids, "requires two explicit old/new message versions"
    prefixes = (
        ("handoff:midseason:", "handoff:r5:", "handoff:harvest:")
        if fault == "mixed"
        else ("handoff:midseason:",)
    )
    active = all(any(item.startswith(prefix) for item in message_ids) for prefix in prefixes)
    return active, f"stable prefixes={prefixes!r}"


def run_no_model_preflight(
    manifest_path: Path, output_dir: Path, *, limit_worlds: int | None = None
) -> dict[str, Any]:
    payload = load_manifest(manifest_path)
    rows = resolve_manifest(payload)
    unresolved_review_paths = sorted(
        {
            str(row.get(field))
            for row in rows
            for field in (
                "petri_spec_path",
                "team_spec_path",
                "role_refinement_path",
                "scientific_gate_manifest",
            )
            if "REPLACE_WITH" in str(row.get(field) or "")
        }
    )
    if unresolved_review_paths and limit_worlds is None:
        raise ValueError(
            "scientific preflight requires resolved reviewed artifacts; "
            "use --limit-worlds only for an engineering mechanism check"
        )
    worlds = sorted({(row["scenario_id"], row["world_seed"]) for row in rows})
    if limit_worlds is not None:
        worlds = worlds[:limit_worlds]
    output_dir.mkdir(parents=True, exist_ok=True)
    runner = DistributedScenarioRunner()
    reports = []
    for scenario_id, world_seed in worlds:
        candidates = [
            row
            for row in rows
            if row["scenario_id"] == scenario_id and row["world_seed"] == world_seed
        ]
        oracle_row = next(
            (row for row in candidates if row["condition_id"] == "scripted_petri_oracle"),
            None,
        )
        if oracle_row is None:
            # Pass 2 deliberately omits deterministic duplicates. Use its first
            # distributed row but replace only the controller treatment.
            oracle_row = {
                **candidates[0],
                "controller_mode": "scripted",
                "condition_id": "scripted_petri_oracle",
                "visibility_mode": "shared_blackboard",
                "handoff_mode": "causal",
                "enforcement_mode": "audit",
                "fault": "none",
                "paper_mode": False,
            }
        if limit_worlds is not None:
            # Engineering preflight intentionally exercises the mechanism with
            # the built-in draft contracts. It cannot satisfy scientific review.
            oracle_row = {
                **oracle_row,
                "paper_mode": False,
                "petri_spec_path": None,
                "team_spec_path": None,
                "role_refinement_path": None,
                "scientific_gate_manifest": None,
            }
        run_root = output_dir / scenario_id / f"world_{world_seed}"
        distributed_dir = run_root / "distributed_oracle"
        distributed = runner.run(_config_from_row(oracle_row, str(distributed_dir)))
        native = _native_oracle(scenario_id, world_seed, run_root / "native_oracle")
        outcome = distributed.trace.outcome
        message_ids = {
            str(event.message_id)
            for event in distributed.trace.events
            if event.kind == EventKind.MESSAGE_SEND and event.message_id
        }
        fault_checks = {
            fault: {
                "active": _fault_contract(fault, message_ids)[0],
                "contract": _fault_contract(fault, message_ids)[1],
            }
            for fault in sorted({str(row["fault"]) for row in candidates})
        }
        same_digest = outcome.get("exogenous_world_digest") == native["exogenous_world_digest"]
        biological_difference = abs(
            float(outcome.get("biological_yield_kg") or 0)
            - float(native.get("biological_yield_kg") or 0)
        )
        marketable_difference = abs(
            float(outcome.get("marketable_yield_kg") or 0)
            - float(native.get("marketable_yield_kg") or 0)
        )
        review_paths_valid = all(
            Path(str(oracle_row.get(field))).is_file()
            for field in ("petri_spec_path", "team_spec_path", "scientific_gate_manifest")
        )
        mechanism_passed = bool(
            outcome.get("harvest_complete")
            and outcome.get("storage_complete")
            and native.get("harvest_complete")
            and native.get("storage_complete")
            and native.get("validation_success") is True
            and same_digest
            and biological_difference <= 1e-6
            and marketable_difference <= 1e-6
            and all(item["active"] for item in fault_checks.values())
        )
        passed = mechanism_passed and (
            review_paths_valid or limit_worlds is not None
        )
        reports.append(
            {
                "scenario_id": scenario_id,
                "world_seed": world_seed,
                "passed": passed,
                "mechanism_passed": mechanism_passed,
                "harvest_complete": outcome.get("harvest_complete"),
                "drying_and_storage_complete": outcome.get("storage_complete"),
                "native_validation_success": native.get("validation_success"),
                "biological_yield_absolute_difference_kg": biological_difference,
                "marketable_yield_absolute_difference_kg": marketable_difference,
                "exogenous_digest_match": same_digest,
                "fault_contracts": fault_checks,
                "prompt_leakage_semantic_test_required": True,
                "review_and_release_paths_valid": review_paths_valid,
            }
        )
    report = {
        "schema_version": "farm_dcore_no_model_preflight_v1",
        "manifest": str(manifest_path),
        "manifest_digest": stable_digest(payload),
        "world_count": len(reports),
        "passed": bool(reports) and all(item["passed"] for item in reports),
        "limited_engineering_run": limit_worlds is not None,
        "scientific_review_satisfied": not unresolved_review_paths,
        "worlds": reports,
    }
    (output_dir / "preflight_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


__all__ = ["run_no_model_preflight"]
