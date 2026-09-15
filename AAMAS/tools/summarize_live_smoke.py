"""Summarize every assigned smoke run without promoting it into paper evidence."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from are.simulation.distributed.experiments import load_manifest, resolve_manifest


def summarize(
    manifest: Path, root: Path, *, supersedes: tuple[str, ...] = ()
) -> dict:
    assignments = resolve_manifest(load_manifest(manifest))
    rows = []
    for assignment in assignments:
        key = assignment["run_key"]
        matches = list(root.rglob(f"*_{key}/RUN_IDENTITY.json"))
        # Source-bound run keys change after code edits. Historical evidence is
        # still matched by its recorded experimental identity, never renamed or
        # treated as a run of the new source revision.
        if not matches:
            historical = []
            for candidate in root.rglob("experiment_row.json"):
                recorded = json.loads(candidate.read_text())
                if (
                    recorded.get("scenario_id") == assignment["scenario_id"]
                    and recorded.get("world_seed") == assignment["world_seed"]
                    and recorded.get("condition_id") == assignment["condition_id"]
                    and recorded.get("repeat_index") == assignment["repeat_index"]
                ):
                    historical.append(candidate.with_name("RUN_IDENTITY.json"))
            matches = [item for item in historical if item.exists()]
        if len(matches) > 1:
            raise ValueError(f"duplicate run artifacts for {key}")
        process = json.loads(Path(assignment["petri_spec_path"]).read_text())
        required = {p["policy_id"]: p for p in process["information_policies"]}
        windows = {p["phase"]: p for p in process["phase_windows"]}
        row = {
            "run_key": key,
            "scenario": assignment["scenario_id"],
            "world_seed": assignment["world_seed"],
            "required_policy_ids": sorted(required),
            "reached_policy_ids": [],
            "missing_policy_ids": sorted(required),
            "policy_decisions_reached": False,
            "harvest_storage_complete": False,
            "status": "no_saved_outcome",
        }
        if matches:
            directory = matches[0].parent
            row["artifact_dir"] = str(directory)
            row["artifact_run_key"] = directory.name.rsplit("_", 1)[-1]
            row["source_bound_to_current_manifest"] = row["artifact_run_key"] == key
            failure = directory / "FAILURE.json"
            outcome_path = directory / "farm_outcome.json"
            trace_path = directory / "trace.dcore_trace_v5.json"
            if failure.exists():
                row.update(status="failed", failure=json.loads(failure.read_text())["error"])
            if outcome_path.exists() and trace_path.exists():
                outcome = json.loads(outcome_path.read_text())
                trace = json.loads(trace_path.read_text())
                reached = set()
                for commitment in trace["policy_commitments"]:
                    policy = required.get(commitment["policy_id"])
                    phase = commitment["season_phase"]
                    window = windows.get(phase)
                    if policy and phase in policy["phases"] and window and (
                        window["start_world_time"] <= commitment["world_time"]
                        < window["end_world_time"]
                    ):
                        reached.add(policy["policy_id"])
                row.update(
                    status="saved_outcome",
                    reached_policy_ids=sorted(reached),
                    missing_policy_ids=sorted(set(required) - reached),
                    policy_decisions_reached=set(required) <= reached,
                    harvest_storage_complete=bool(
                        outcome["harvest_complete"] and outcome["storage_complete"]
                    ),
                    provider_requests=outcome.get("provider_request_count"),
                    provider_accounted_usd=outcome.get("provider_accounted_usd"),
                    provider_prompt_tokens=outcome.get("provider_prompt_tokens"),
                    provider_completion_tokens=outcome.get("provider_completion_tokens"),
                    termination_by_actor=outcome.get("termination_by_actor"),
                    infrastructure_failure=outcome.get("infrastructure_failure"),
                    controller_failure=outcome.get("controller_failure"),
                    outcome_sha256=hashlib.sha256(outcome_path.read_bytes()).hexdigest(),
                    trace_sha256=hashlib.sha256(trace_path.read_bytes()).hexdigest(),
                    last_world_time=max(e["world_time"] for e in trace["events"]),
                    harvest_complete=outcome.get("harvest_complete"),
                    storage_complete=outcome.get("storage_complete"),
                    recovered_harvest_kg=outcome.get("recovered_harvest_kg"),
                    combine_grain_kg=outcome.get("combine_grain_kg", 0.0),
                    trailer_grain_kg=outcome.get(
                        "trailer_grain_kg",
                        outcome.get("inventory", {}).get("harvest_grain_kg"),
                    ),
                    warehouse_grain_kg=outcome.get(
                        "warehouse_grain_kg",
                        outcome.get("inventory", {}).get("warehouse_grain_kg"),
                    ),
                    outcome_schema_version=outcome.get(
                        "schema_version", "historical_farm_outcome_v1"
                    ),
                )
        rows.append(row)
    scenarios = []
    for scenario in sorted({row["scenario"] for row in rows}):
        selected = [row for row in rows if row["scenario"] == scenario]
        covered = sum(row["policy_decisions_reached"] for row in selected)
        complete = sum(row["harvest_storage_complete"] for row in selected)
        scenarios.append({
            "scenario": scenario, "assigned": len(selected),
            "worlds": sorted(row["world_seed"] for row in selected),
            "policy_covered_runs": covered, "completed_seasons": complete,
            "progression_gate_passed": len(selected) == 2 and covered == 2 and complete >= 1,
        })
    return {
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "schema_version": "dcore_progression_summary_v2",
        "supersedes": list(supersedes),
        "paper_evidence": False,
        "policy_coverage_rule": "A recorded commitment for each declared policy, "
                                "in its declared phase and half-open phase window. "
                                "This measures decision reach, not correctness.",
        "all_progression_gates_passed": len(scenarios) == 3 and all(
            s["progression_gate_passed"] for s in scenarios
        ),
        "scenario_gates": scenarios, "runs": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supersedes", action="append", default=[])
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = summarize(
        args.manifest, args.artifacts, supersedes=tuple(args.supersedes)
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    fields = [
        "scenario", "world_seed", "status", "policies_reached", "policies_required",
        "policy_decisions_reached", "harvest_storage_complete", "provider_requests",
        "provider_accounted_usd", "infrastructure_failure", "controller_failure",
        "termination_by_actor", "run_key",
        "harvest_complete", "storage_complete", "recovered_harvest_kg",
        "combine_grain_kg", "trailer_grain_kg", "warehouse_grain_kg",
        "outcome_schema_version",
    ]
    with args.output.with_suffix(".csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report["runs"]:
            values = {key: row.get(key) for key in fields}
            values.update(
                policies_reached=len(row["reached_policy_ids"]),
                policies_required=len(row["required_policy_ids"]),
                termination_by_actor=json.dumps(row.get("termination_by_actor"), sort_keys=True),
            )
            writer.writerow(values)
