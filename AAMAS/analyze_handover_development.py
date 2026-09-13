"""Rebuild compact engineering evidence; never convert it into paper results."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "results/aamas_handover"
OUTPUT = ROOT / "AAMAS/handover_validation"


def write_table(name, rows):
    with (OUTPUT / f"{name}.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]) if rows else ["no_observations"]
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    OUTPUT.mkdir(exist_ok=True)
    sources, intervention_rows, calibration_rows, pilot_rows = {}, [], [], []

    def read(path):
        sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        return json.loads(path.read_text())

    for path in sorted(RAW.rglob("intervention_report.json")):
        report = read(path)
        for comparison in report["comparisons"]:
            seed, arm = comparison["world_seed"], comparison["arm"]
            control = next(
                r
                for r in report["rows"]
                if r["world_seed"] == seed and r["arm"] == "reference"
            )
            treated = next(
                r for r in report["rows"] if r["world_seed"] == seed and r["arm"] == arm
            )
            intervention_rows.append(
                {
                    "scenario": report["plan"]["scenario_id"],
                    **comparison,
                    "reference_final_yield_kg": control["final_marketable_yield_kg"],
                    "intervention_final_yield_kg": treated["final_marketable_yield_kg"],
                    "recovery_wait_days": max(
                        (
                            r["recovery_wait_days_used"]
                            for r in treated["treatment_attempts"]
                        ),
                        default=0,
                    ),
                    "area_changes_in_swap": arm == "swap_treatment_regions",
                    "paper_eligible": False,
                }
            )
    for path in sorted(RAW.rglob("calibration_report.json")):
        report = read(path)
        for pair, check in zip(report["pairs"], report["checks"], strict=True):
            control, omission = pair["control"], pair["omission"]
            complete = all(
                arm.get("harvest_complete") and arm.get("storage_complete")
                for arm in (control, omission)
            )
            calibration_rows.append(
                {
                    "attempt": path.parent.name,
                    "world_seed": pair["world_seed"],
                    "scenario_revision": report["plan"].get(
                        "scenario_revision", "original"
                    ),
                    "weather_candidate": report["plan"]["candidate"],
                    "workflow_variant": report["plan"].get(
                        "workflow_variant", "original"
                    ),
                    "complete_pair": complete,
                    "control_final_yield_kg": control["marketable_yield_kg"]
                    if complete
                    else None,
                    "omission_final_yield_kg": omission["marketable_yield_kg"]
                    if complete
                    else None,
                    "omission_shortfall": check["shortfall"] if complete else None,
                    "target_stressed_fraction": control["target_records"][0][
                        "stressed_fraction"
                    ],
                    "intervention_accepted": control["target_records"][0]["accepted"],
                    "pair_screening_passed": check["passed"],
                    "failure_reasons": json.dumps(check["reasons"]),
                    "paper_eligible": False,
                }
            )
    for path in sorted(RAW.glob("development_*/**/farm_outcome.json")):
        outcome = read(path)
        trace = read(path.parent / "trace.dcore_trace_v5.json")
        pilot_rows.append(
            {
                "attempt": path.relative_to(RAW).parts[0],
                "world_seed": outcome["world_seed"],
                "scenario": trace["configuration"]["scenario_id"],
                "provider_requests": outcome["provider_request_count"],
                "prompt_tokens": outcome["provider_prompt_tokens"],
                "completion_tokens": outcome["provider_completion_tokens"],
                "accounted_usd": outcome["provider_accounted_usd"],
                "harvest_complete": outcome["harvest_complete"],
                "storage_complete": outcome["storage_complete"],
                "high_impact_policy_commitments": len(
                    trace.get("policy_commitments", [])
                ),
                "native_rejections": len(outcome.get("native_execution_errors", [])),
                "termination_by_actor_as_recorded": json.dumps(
                    outcome.get("termination_by_actor", {}), sort_keys=True
                ),
                "paper_eligible": False,
            }
        )
    write_table("native_interventions", intervention_rows)
    write_table("drought_development", calibration_rows)
    write_table("development_pilots", pilot_rows)
    (OUTPUT / "development_analysis_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "dcore_handover_development_analysis_v1",
                "paper_eligible": False,
                "source_files": sources,
                "native_comparisons": len(intervention_rows),
                "drought_pairs": len(calibration_rows),
                "live_attempts": len(pilot_rows),
                "incomplete_yields": "unavailable; raw historical amounts retained unchanged",
                "readiness_certified": False,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"{len(intervention_rows)} native comparisons; {len(calibration_rows)} drought pairs; {len(pilot_rows)} live attempts; all exploratory"
    )


if __name__ == "__main__":
    main()
