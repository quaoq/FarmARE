"""Rebuild the first engineering pilot tables from immutable saved outputs.

Run from the repository root. No simulations, providers, or inferred labels.
Incomplete yields remain missing in comparisons and visible in completion counts.
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


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value):
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def latex(value):
    substitutions = {
        "\\": r"\textbackslash{}",
        "_": r"\_",
        "%": r"\%",
        "&": r"\&",
        "#": r"\#",
        "$": r"\$",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(substitutions.get(c, c) for c in number(value))


def table(output, name, rows):
    columns = list(rows[0])
    with (output / f"{name}.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    headings = {
        "world_pairs": "World pairs",
        "final_yield_pairs": "Final-yield pairs",
        "accepted_irrigations": "Accepted irrigations",
        "stress_criterion_passes": "Stress passes",
        "calibration_passes": "Calibration passes",
        "mean_omission_shortfall_pct_final_pairs_only": "Mean omission loss (\\%)",
        "event_fidelity": "EF",
        "causal_conformance": "CC",
        "marketable_yield_kg": "Yield (kg)",
        "yield_shortfall_pct_vs_scripted_reference": "Reference loss (\\%)",
        "physical_blocks_or_deferrals": "Blocked/deferred",
        "harvest_and_storage_complete": "Completed",
    }
    lines = [
        "% Engineering pilot only. Requires graphicx. See README.md for denominators and limitations.",
        r"\resizebox{\linewidth}{!}{%",
        r"\begin{tabular}{" + "l" * len(columns) + "}",
        " & ".join(
            headings.get(c, latex(c.replace("_", " ").capitalize())) for c in columns
        )
        + r" \\",
        r"\hline",
    ]
    lines += [" & ".join(latex(row[c]) for c in columns) + r" \\" for row in rows]
    lines += [r"\end{tabular}%", "}"]
    (output / f"{name}.tex").write_text("\n".join(lines) + "\n")


def markdown(rows):
    columns = list(rows[0])
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines += ["| " + " | ".join(number(row[c]) for c in columns) + " |" for row in rows]
    return "\n".join(lines)


def analyze(source, output):
    output.mkdir(parents=True, exist_ok=False)
    calibration, pairs, receipts, errors = [], [], [], []
    for variant in ("drought-original", "drought-candidate"):
        report = read(source / variant / "calibration_report.json")
        assert report["complete"] and len(report["pairs"]) == 10
        valid = [c["shortfall"] for c in report["checks"] if c["shortfall"] is not None]
        active = sum(
            p["control"]["target_records"][0]["accepted"] for p in report["pairs"]
        )
        stressed = [
            p["control"]["target_records"][0]["stressed_fraction"]
            for p in report["pairs"]
        ]
        calibration.append(
            {
                "variant": variant,
                "world_pairs": len(report["pairs"]),
                "final_yield_pairs": len(valid),
                "accepted_irrigations": active,
                "stress_criterion_passes": sum(
                    v >= report["min_stressed_fraction"] for v in stressed
                ),
                "calibration_passes": sum(c["passed"] for c in report["checks"]),
                "mean_omission_shortfall_pct_final_pairs_only": 100
                * statistics.mean(valid)
                if valid
                else None,
            }
        )
        for pair, check in zip(report["pairs"], report["checks"], strict=True):
            assert pair["world_seed"] == check["world_seed"]
            c, o = pair["control"], pair["omission"]
            target = c["target_records"][0]
            pairs.append(
                {
                    "variant": variant,
                    "world_seed": pair["world_seed"],
                    "control_yield_final": c["yield_is_final"],
                    "omission_yield_final": o["yield_is_final"],
                    "control_kg": c["marketable_yield_kg"]
                    if c["yield_is_final"]
                    else None,
                    "omission_kg": o["marketable_yield_kg"]
                    if o["yield_is_final"]
                    else None,
                    "omission_shortfall_pct": 100 * check["shortfall"]
                    if check["shortfall"] is not None
                    else None,
                    "stressed_fraction": target["stressed_fraction"],
                    "mean_root_vwc": target["mean_root_vwc"],
                    "irrigation_accepted": target["accepted"],
                    "failure_reasons": "; ".join(check["reasons"]),
                }
            )
        for path in sorted((source / variant).rglob("scenario_*.json")):
            exported = read(path)
            failures = Counter()
            for item in exported["world_logs"]:
                event = json.loads(item) if isinstance(item, str) else item
                result = event.get("output")
                if isinstance(result, dict) and result.get("error"):
                    failures[(event.get("action_name"), result["error"])] += 1
            for (action, error), count in sorted(failures.items()):
                errors.append(
                    {
                        "variant": variant,
                        "world": path.parent.parent.name,
                        "arm": path.parent.name,
                        "action": action,
                        "error": error,
                        "count": count,
                    }
                )

    runs = [
        json.loads(line)
        for line in (source / "scripted-smoke/results.jsonl").read_text().splitlines()
        if line
    ]
    assert len(runs) == 3 and all(
        r["total_model_calls"] == 0 and not r["paper_mode"] for r in runs
    )
    for row in sorted(runs, key=lambda r: r["condition"]):
        trace = read(Path(row["artifact_dir"]) / "trace.dcore_trace_v5.json")
        physical_blocks = sum(
            e["kind"] == "action"
            and e["status"] in {"blocked", "deferred"}
            and e["payload"].get("blocked_before_farmare") is True
            and not e.get("farmare_event_id")
            for e in trace["events"]
        )
        receipts.append(
            {
                "condition": row["condition"],
                "worlds": 1,
                "event_fidelity": row["event_fidelity"],
                "causal_conformance": row["causal_conformance"],
                "marketable_yield_kg": row["marketable_yield_kg"],
                "yield_shortfall_pct_vs_scripted_reference": 100
                * row["marketable_yield_shortfall"],
                "physical_blocks_or_deferrals": physical_blocks,
                "harvest_and_storage_complete": row["harvest_complete"]
                and row["storage_complete"],
            }
        )
    ablations = []
    for path in sorted((source / "saved-trace-ablations").glob("*.json")):
        report = read(path)
        for row in report["ablations"]:
            ablations.append(
                {
                    "condition": report["condition"],
                    "variant": row["variant"],
                    "event_fidelity": row["event_fidelity"],
                    "causal_conformance": row["causal_conformance"],
                }
            )
    assert len(ablations) == 12
    tables = {
        "calibration_summary": calibration,
        "calibration_pairs": pairs,
        "native_tool_errors": errors,
        "scripted_pilot": receipts,
        "saved_trace_ablations": ablations,
    }
    for name, rows in tables.items():
        table(output, name, rows)

    os.environ.setdefault("MPLCONFIGDIR", "/tmp/aamas-matplotlib-cache")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
    for variant, marker in (("drought-original", "o"), ("drought-candidate", "x")):
        rows = [r for r in pairs if r["variant"] == variant]
        axes[0].plot(
            [r["world_seed"] for r in rows],
            [100 * r["stressed_fraction"] for r in rows],
            marker=marker,
            label=variant,
            alpha=0.8,
        )
        valid = [r for r in rows if r["omission_shortfall_pct"] is not None]
        axes[1].scatter(
            [r["world_seed"] for r in valid],
            [r["omission_shortfall_pct"] for r in valid],
            marker=marker,
            label=variant,
        )
    axes[0].axhline(50, color="gray", linestyle="--", label="Screening threshold")
    axes[0].set(
        title="Target ridges under root-zone stress",
        ylabel="Stressed ridges (%)",
        ylim=(-3, 60),
    )
    axes[1].axhline(0, color="gray", linewidth=0.8)
    axes[1].set(
        title="Yield effect on final-yield pairs only", ylabel="Omission shortfall (%)"
    )
    for ax in axes:
        ax.set_xlabel("World seed")
        ax.set_xticks(range(10))
        ax.legend(fontsize=8)
    fig.suptitle(
        "Engineering calibration: seeds 3, 4, 8, 9 lack final yield in both variants"
    )
    fig.savefig(output / "calibration.png", dpi=180)
    fig.savefig(output / "calibration.pdf")
    plt.close(fig)

    source_paths = sorted(p for p in source.rglob("*") if p.is_file())
    manifest = {
        "schema_version": "aamas_engineering_pilot_analysis_v1",
        "execution_code_commit": "23d1defef304a02a3351c80e5f1277105214eec3",
        "paper_eligible": False,
        "llm_calls": 0,
        "calibration_seasons": 40,
        "scripted_seasons": 3,
        "analysis_script_sha256": digest(Path(__file__)),
        "source_sha256": {str(p): digest(p) for p in source_paths},
        "outputs_sha256": {
            p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()
        },
    }
    summary = (
        """# First engineering pilot — 2026-09-13

Executed on commit `23d1def`: 40 drought calibration seasons and three Wet-June
scripted seasons. All outputs are engineering observations. No LLM calls or
independent human annotation were performed. Raw native exports, including all
failures, remain under `results/aamas_pilot_20260913/` in the repository workspace.
The analysis manifest binds every raw file by SHA-256; raw exports are retained
locally rather than committed as a 167 MB Git payload.

## Calibration findings

Neither the original nor the candidate passes the predeclared screening rule.
Every world has zero target ridges below the native root-zone stress threshold
at irrigation. Both variants lack final yield in seeds 3, 4, 8, and 9; their
native harvest receipts report rainy conditions. Those missing outcomes are
not final zero yields and are not silently included in a yield-effect average.
The original accepts irrigation in 6/10 worlds; the candidate accepts it in
10/10. Acceptance alone does not establish agronomic value.

"""
        + markdown(calibration)
        + """

Negative shortfall means omission produced slightly more marketable yield.
The mean above is descriptive and conditional on the six final-yield pairs,
not an intention-to-treat estimate over ten worlds. The planned screening
required at least 1% omission loss in every pair and at least 50% target-ridge
stress; neither variant qualifies. Native action-presence validation returned
success even in incomplete-harvest worlds; the stricter calibration checks
correctly rejected them. The per-world and native-error CSVs retain all details.

## Scripted pipeline observations

"""
        + markdown(receipts)
        + """

These three cells use one world and change multiple factors. The yield
shortfall is relative to the scripted reference, not an isolated causal effect
of enforcement. The free-text/drop cell has reference-level yield despite
lower causal conformance: this is an example of diagnostics and yield
disagreeing, not independent validation of the diagnosis. All three completed
harvest/storage; both assigned drop conditions recorded an active fault.

## Saved-trace component analysis

"""
        + markdown(ablations)
        + """

The component definitions were committed before these runs. They are evaluated
on identical saved inputs and show score dependence only, not accuracy against
human labels. The serialization variant changes only causal edge checks.
No reviewed specification alternatives are available, so this table cannot
support a specification-sensitivity claim.
At the evaluator's six-decimal precision, all four variants give identical
EF/CC values within each condition. These pilot traces therefore provide no
evidence of component necessity; the tiny extra serialization digits are
rounding differences, not an ablation effect.

## Reporting defect found by the pilot

The original v5 guard-effectiveness output counts a non-allow guard verdict as
physical prevention, even when enforcement is off/audit. It reports 22 false
blocks in the free-text/off cell. The pilot table instead counts actual
blocked/deferred action records with `blocked_before_farmare=true` and no native
event receipt. It does not label those interventions beneficial or false.

Further, `global_conforming` includes successful execution, so a prevented
action is not an adequate counterfactual basis for a false-block or safety-benefit
claim. Correct this using independent pre-execution proposal validity before
using those metrics in paper results. The original outputs remain intact;
the stock `scripted-report/table5_guard_mitigation` is quarantined from substantive
interpretation. No claim is made that this defect has been fixed in the evaluator.

## Next decision

Do not promote the drought candidate or launch the full paid matrix on this
evidence. Review the irrigation exposure and the oracle's handling of rainy
harvest windows, record any revised design prospectively, and rerun a separately
versioned calibration. Do not change thresholds to make these observations pass.

The OpenAI key is present, but `DistributedRunnerConfig.validate_modes` and
the native scientific gate require independently reviewed process/team files
and an `offline_complete` gate before the bounded LLM smoke. Those artifacts
were not found. Supplying credentials does not provide the missing reviews;
no review status or gate attestation has been fabricated.

Rebuild the tables with:

```bash
.venv/bin/python AAMAS/analyze_pilot_20260913.py \\
  --results results/aamas_pilot_20260913 --output /tmp/rebuilt-aamas-pilot
```
"""
    )
    (output / "README.md").write_text(summary)
    manifest["outputs_sha256"]["README.md"] = digest(output / "README.md")
    (output / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    analyze(args.results, args.output)
