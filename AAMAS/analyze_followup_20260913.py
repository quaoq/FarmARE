"""Rebuild descriptive engineering tables; never emits paper-eligible evidence.

Run from the repository root with the local raw exports retained. The original
pilot is read-only. All assigned model runs, including failures, are retained.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
from collections import Counter
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/aamas-mpl")


def read(path):
    return json.loads(path.read_text())


def table(output, name, rows, *, csv_only=False):
    if not rows:
        return
    fields = list(rows[0])
    with (output / f"{name}.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    if csv_only:
        return

    def tex(value):
        if value is None:
            return "NA"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, float):
            return f"{value:.6g}"
        mapping = {"_": r"\_", "%": r"\%", "&": r"\&", "#": r"\#"}
        return "".join(mapping.get(char, char) for char in str(value))

    lines = [r"% Engineering only; descriptive, no inferential comparisons."]
    lines += [r"\begin{tabular}{" + "l" * len(fields) + "}", r"\hline"]
    lines += [" & ".join(tex(f) for f in fields) + r" \\", r"\hline"]
    lines += [" & ".join(tex(r[f]) for f in fields) + r" \\" for r in rows]
    lines += [r"\hline", r"\end{tabular}"]
    (output / f"{name}.tex").write_text("\n".join(lines) + "\n")


def calibration(source, output):
    summary, pairs = [], []
    for cohort, expected in [("development", 10), ("heldout", 5)]:
        report = read(source / f"calibration_{cohort}" / "calibration_report.json")
        assert report["complete"] and len(report["pairs"]) == expected
        assert report["plan"]["harvest_retry_days"] == 7
        shortfalls = [
            c["shortfall"] for c in report["checks"] if c["shortfall"] is not None
        ]
        summary.append(
            {
                "cohort": cohort,
                "pairs": expected,
                "final_yield_pairs": len(shortfalls),
                "calibration_passes": sum(c["passed"] for c in report["checks"]),
                "accepted_irrigations": sum(
                    p["control"]["target_records"][0]["accepted"]
                    for p in report["pairs"]
                ),
                "stress_criterion_passes": sum(
                    p["control"]["target_records"][0]["stressed_fraction"]
                    >= report["min_stressed_fraction"]
                    for p in report["pairs"]
                ),
                "mean_omission_shortfall_pct": 100 * statistics.mean(shortfalls)
                if shortfalls
                else None,
            }
        )
        for pair, check in zip(report["pairs"], report["checks"], strict=True):
            assert pair["world_seed"] == check["world_seed"]
            control, omission = pair["control"], pair["omission"]
            target = control["target_records"][0]
            attempts = [
                a for arm in (control, omission) for a in arm["harvest_attempts"]
            ]
            pairs.append(
                {
                    "cohort": cohort,
                    "world_seed": pair["world_seed"],
                    "control_final_kg": control["marketable_yield_kg"]
                    if control["yield_is_final"]
                    else None,
                    "omission_final_kg": omission["marketable_yield_kg"]
                    if omission["yield_is_final"]
                    else None,
                    "omission_shortfall_pct": 100 * check["shortfall"]
                    if check["shortfall"] is not None
                    else None,
                    "stressed_fraction": target["stressed_fraction"],
                    "mean_root_vwc": target["mean_root_vwc"],
                    "irrigation_accepted": target["accepted"],
                    "rain_rejections_both_arms": sum(
                        a["error"] == "Cannot harvest in rainy conditions"
                        for a in attempts
                    ),
                    "extra_days_control": max(
                        a["wait_days_used"] for a in control["harvest_attempts"]
                    ),
                    "extra_days_omission": max(
                        a["wait_days_used"] for a in omission["harvest_attempts"]
                    ),
                    "failure_reasons": "; ".join(check["reasons"]),
                }
            )
    table(output, "calibration_summary", summary)
    table(output, "calibration_pairs", pairs)
    return summary


def corrected_guards(previous, source, output):
    from are.simulation.distributed.evaluator_v5 import evaluate_farm_dcore_v5
    from are.simulation.distributed.models import DistributedTrace
    from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5

    rows = []
    destination = source / "corrected_scripted"
    destination.mkdir(exist_ok=True)
    paths = sorted((previous / "scripted-smoke").rglob("trace.dcore_trace_v5.json"))
    assert len(paths) == 3
    for path in paths:
        trace = DistributedTrace.model_validate_json(path.read_text())
        process = FarmProcessSpecV5.model_validate_json(
            (path.parent / "farm_process_spec_v5.json").read_text()
        )
        before = read(path.parent / "metrics.dcore_eval_v5.json")
        after = evaluate_farm_dcore_v5(process, trace)
        assert all(
            before[k] == after[k] for k in ("event_fidelity", "causal_conformance")
        )
        (destination / f"{path.parent.name}.json").write_text(
            json.dumps(after, indent=2, default=str)
        )
        guard = after["guard_effectiveness"]
        rows.append(
            {
                "condition": trace.configuration["condition_id"],
                "old_false_blocks": before["guard_effectiveness"]["false_blocks"],
                "physical_blocks_policy_covered": guard["physical_blocks"],
                "assessed_false_blocks": guard["false_blocks"],
                "prevented_invalid_proposals": guard["prevented_unsafe_writes"],
                "unassessable_blocks": guard["unassessable_blocks"],
                "unassessable_proposals": guard["unassessable_proposals"],
                "false_block_rate": guard["false_block_rate"],
                "ef_cc_unchanged": True,
            }
        )
    table(output, "corrected_guard_metrics", rows)


def llm_runs(source, output):
    from are.simulation.distributed.evaluator_v5 import (
        _guard_effectiveness,
        _physical_guard_prevention,
    )
    from are.simulation.distributed.llm_budget import summarize_budget_usage
    from are.simulation.distributed.models import DistributedTrace

    rows, connectivity, errors = [], [], []
    folders = [
        source / name
        for name in (
            "llm_connectivity",
            "llm_connectivity_network",
            "llm_connectivity_endpoint",
            "llm_connectivity_compatible",
            "llm_pilot",
        )
    ]
    for folder in folders:
        raw_rows = [
            json.loads(line)
            for line in (folder / "results.jsonl").read_text().splitlines()
            if line
        ]
        assert len(raw_rows) == (8 if folder.name == "llm_pilot" else 1)
        for raw in raw_rows:
            assert raw["engineering_llm_pilot"] and not raw["paper_mode"]
            artifact = Path(raw["artifact_dir"])
            trace_path = artifact / "trace.dcore_trace_v5.json"
            trace = read(trace_path) if trace_path.exists() else {}
            outcome = trace.get("outcome", {})
            budget = (
                summarize_budget_usage(
                    raw.get("per_agent_telemetry", {}),
                    max_calls=outcome.get("team_call_budget", raw["max_model_calls"]),
                    max_tokens=outcome.get("team_token_budget"),
                    per_agent_calls=outcome.get("per_agent_call_budget", {}),
                    per_agent_tokens=outcome.get("per_agent_token_budget", {}),
                )
                if trace
                else {}
            )
            metrics_path = artifact / "metrics.dcore_eval_v5.json"
            metrics = read(metrics_path) if metrics_path.exists() else {}
            corrected_guard = {}
            if trace:
                parsed_trace = DistributedTrace.model_validate(trace)
                corrected_guard = _guard_effectiveness(
                    metrics["information_policy_conformance"]["details"],
                    metrics["recovery"],
                    physical_block_count=sum(
                        _physical_guard_prevention(parsed_trace, d)
                        for d in parsed_trace.decisions
                    ),
                )
                destination = source / "corrected_llm_guards"
                destination.mkdir(exist_ok=True)
                (destination / f"{folder.name}_{artifact.name}.json").write_text(
                    json.dumps(corrected_guard, indent=2) + "\n"
                )
            native_errors = Counter(
                e.get("action")
                for e in trace.get("events", [])
                if e["kind"] == "action"
                and e["status"] == "error"
                and e.get("farmare_event_id")
            )
            for action, count in sorted(native_errors.items()):
                errors.append(
                    {
                        "cohort": folder.name,
                        "condition": raw["condition"],
                        "world_seed": raw["world_seed"],
                        "action": action,
                        "error_receipts": count,
                    }
                )
            prompt, completion = (
                raw.get("total_prompt_tokens"),
                raw.get("total_completion_tokens"),
            )
            row = {
                "condition": raw["condition"],
                "world_seed": raw["world_seed"],
                "model_calls": raw.get("total_model_calls"),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "uncached_cost_estimate_usd": (prompt * 0.75 + completion * 4.5) / 1e6
                if prompt is not None and completion is not None
                else None,
                "controller_failure": raw.get("controller_failure"),
                "infrastructure_failure": raw.get("infrastructure_failure"),
                "raw_call_budget_exhausted": raw.get("call_budget_exhausted"),
                "raw_token_budget_exhausted": raw.get("token_budget_exhausted"),
                "corrected_call_budget_exhausted": budget.get("call_budget_exhausted"),
                "corrected_token_budget_exhausted": budget.get(
                    "token_budget_exhausted"
                ),
                "team_token_overshoot": raw.get("token_budget_overshoot"),
                "max_actor_token_overshoot": max(
                    (
                        a["token_budget_overshoot"] or 0
                        for a in budget.get("per_agent_budget_status", {}).values()
                    ),
                    default=None,
                ),
                "final_yield_kg": raw.get("marketable_yield_kg")
                if outcome.get("yield_is_final")
                else None,
                "harvest_complete": raw.get("harvest_complete"),
                "storage_complete": raw.get("storage_complete"),
                "messages_sent": raw.get("message_count"),
                "deliveries": raw.get("delivery_count"),
                "fault": raw["fault"],
                "fault_manifested": raw.get("fault_manifested")
                if raw["fault"] != "none"
                else None,
                "native_error_receipts": sum(native_errors.values()),
                "required_event_coverage": raw.get("required_event_coverage"),
                "policy_decisions": len(
                    metrics.get("information_policy_conformance", {}).get("details", [])
                ),
                "event_fidelity": raw.get("event_fidelity"),
                "causal_conformance": raw.get("causal_conformance"),
                "physical_blocks": raw.get("blocked_write_count"),
                "physical_deferrals": raw.get("deferred_write_count"),
                "physical_block_decisions": corrected_guard.get(
                    "physical_blocks_total"
                ),
                "unassessable_block_decisions": corrected_guard.get(
                    "unassessable_blocks_total"
                ),
                "assessed_false_blocks": corrected_guard.get("false_blocks"),
            }
            if folder.name == "llm_pilot":
                rows.append(row)
            else:
                connectivity.append({"attempt": folder.name, **row})
    assert len(rows) == 8
    condition_order = {
        name: index
        for index, name in enumerate(
            (
                "causal_audit_control",
                "causal_audit_outage",
                "causal_enforce_outage",
                "free_text_off_outage",
            )
        )
    }
    rows.sort(key=lambda row: (condition_order[row["condition"]], row["world_seed"]))
    table(output, "llm_runs", rows, csv_only=True)
    table(output, "connectivity", connectivity, csv_only=True)
    table(output, "native_errors", errors, csv_only=True)
    # A compact display table fits normal manuscript widths; detailed CSVs retain
    # the complete accounting and belong in the artifact rather than the paper.
    table(
        output,
        "llm_summary",
        [
            {
                "condition": {
                    "causal_audit_control": "Audit/control",
                    "causal_audit_outage": "Audit/outage",
                    "causal_enforce_outage": "Enforce/outage",
                    "free_text_off_outage": "Free text/outage",
                }[r["condition"]],
                "world": r["world_seed"],
                "calls": r["model_calls"],
                "messages": r["messages_sent"],
                "fault_active": r["fault_manifested"],
                "coverage_pct": 100 * r["required_event_coverage"]
                if r["required_event_coverage"] is not None
                else None,
                "policy_decisions": r["policy_decisions"],
                "final_yield_kg": r["final_yield_kg"],
            }
            for r in rows
        ],
    )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("results/aamas_followup_20260913")
    )
    parser.add_argument(
        "--previous", type=Path, default=Path("results/aamas_pilot_20260913")
    )
    parser.add_argument("--output", type=Path, default=Path("AAMAS/followup_20260913"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    calibration(args.source, args.output)
    corrected_guards(args.previous, args.source, args.output)
    llm_runs(args.source, args.output)
    sources = sorted(p for p in args.source.rglob("*") if p.is_file())
    sources += sorted(
        p for p in (args.previous / "scripted-smoke").rglob("*") if p.is_file()
    )
    sources += [Path("AAMAS/analyze_followup_20260913.py")]
    sources += sorted(Path("are/simulation/distributed").glob("*.py"))
    sources += [Path("are/simulation/agents/llm/litellm/litellm_engine.py")]
    manifest = {
        "paper_eligible": False,
        "matrix_execution_commit": "1ae54ee",
        "analysis": "descriptive engineering follow-up; all eight assignments retained",
        "source_files": [
            {
                "path": str(p),
                "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in sources
        ],
    }
    (args.output / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(f"Rebuilt follow-up tables; {len(sources)} source files hashed.")


if __name__ == "__main__":
    main()
