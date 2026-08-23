"""Deterministic, preregistered tables and figures for the AAMAS package."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from are.simulation.distributed.experiments import aggregate_rows
from are.simulation.distributed.models import stable_digest

TABLES = (
    "table1_metric_validation",
    "table2_local_global_discordance",
    "table3_fault_outcomes",
    "table4_failure_localization",
    "table5_guard_mitigation",
    "table6_scalability",
)
FIGURES = (
    "figure1_long_horizon_profile",
    "figure2_yield_shortfall",
    "figure3_synchronization_lag",
    "figure4_localization_confusion",
    "figure5_metric_yield_calibration",
)


def _read_rows(source: Path) -> list[dict[str, Any]]:
    if source.is_dir():
        direct = source / "results.jsonl"
        paths = [direct] if direct.is_file() else sorted(source.rglob("results.jsonl"))
        if not paths:
            raise ValueError(f"no results.jsonl files found below {source}")
    else:
        paths = [source]
    return [
        json.loads(line)
        for path in paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _flatten_group(group: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in group.items():
        if isinstance(value, dict):
            row[key] = value.get("mean", json.dumps(value, sort_keys=True))
        else:
            row[key] = value
    return row


def _write_table(root: Path, name: str, rows: list[dict[str, Any]]) -> None:
    columns = sorted({key for row in rows for key in row}) or ["no_data"]
    csv_path = root / f"{name}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    def escape(value: Any) -> str:
        return str(value).replace("_", r"\_").replace("%", r"\%")
    lines = [
        r"\begin{tabular}{" + "l" * len(columns) + "}",
        " & ".join(map(escape, columns)) + " \\\\",
        r"\hline",
    ]
    lines.extend(
        " & ".join(escape(row.get(column, "")) for column in columns) + " \\\\"
        for row in rows
    )
    lines.append(r"\end{tabular}")
    (root / f"{name}.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _table_rows(
    rows: list[dict[str, Any]],
    aggregate: dict[str, Any],
    controlled_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups = [_flatten_group(item) for item in aggregate["groups"]]
    localization = []
    for row in rows:
        for item in row.get("provenance_failure_localization") or []:
            localization.append(
                {
                    "scenario": row.get("scenario"),
                    "condition": row.get("condition"),
                    "fault": row.get("fault"),
                    "expected": item.get("expected"),
                    "predicted": item.get("primary"),
                }
            )
    return {
        TABLES[0]: controlled_rows
        or [
            item
            for item in groups
            if item.get("condition")
            in {"scripted_petri_oracle", "farmare_direct", "farmare_a2a"}
        ],
        TABLES[1]: [
            {
                key: item.get(key)
                for key in (
                    "scenario",
                    "condition",
                    "fault",
                    "event_fidelity",
                    "causal_conformance",
                    "information_global_discordance",
                    "igd_l0_g0",
                    "igd_l0_g1",
                    "igd_l1_g0",
                    "igd_l1_g1",
                )
            }
            for item in groups
        ],
        TABLES[2]: [
            {
                key: item.get(key)
                for key in (
                    "scenario",
                    "condition",
                    "fault",
                    "dcore_score",
                    "marketable_yield_shortfall",
                    "completion_rate",
                    "safety_rate",
                    "unnecessary_write_count",
                    "harmful_extra_cost",
                )
            }
            for item in groups
        ],
        TABLES[3]: controlled_rows or localization,
        TABLES[4]: [
            item
            for item in aggregate["predeclared_contrasts"]
            if item.get("family") == "enforcement"
            and item.get("metric")
            in {
                "marketable_yield_shortfall",
                "safety_success",
                "successful_replanning_rate",
                "guard_unsafe_proposals",
                "guard_prevented_unsafe_writes",
                "guard_false_blocks",
                "guard_unnecessary_abstentions",
                "guard_eventual_recoveries",
                "guard_safety_benefit_rate",
            }
        ],
        TABLES[5]: [
            {key: item.get(key) for key in ("scenario", "team_id", "condition", "dcore_score", "marketable_yield_shortfall", "total_model_calls", "total_tokens", "message_count", "coordination_edge_density")}
            for item in groups
            if str(item.get("condition", "")).startswith("scalability_")
        ],
    }


def _save_figure(figure: Any, root: Path, name: str) -> None:
    figure.tight_layout()
    figure.savefig(root / f"{name}.png", dpi=180, metadata={"Software": "Farm D-CORE"})
    figure.savefig(
        root / f"{name}.pdf",
        metadata={
            "Creator": "Farm D-CORE",
            "Producer": "Farm D-CORE",
            "CreationDate": datetime(2000, 1, 1, tzinfo=timezone.utc),
            "ModDate": datetime(2000, 1, 1, tzinfo=timezone.utc),
        },
    )


def _write_figures(
    rows: list[dict[str, Any]], root: Path, controlled_rows: list[dict[str, Any]]
) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(root / ".matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    modules: dict[str, list[float]] = {}
    for row in rows:
        raw = row.get("module_profile") or {}
        if not isinstance(raw, dict):
            continue
        for module_id, profile in raw.items():
            ef, cc = profile.get("event_fidelity"), profile.get("causal_conformance")
            if ef is not None:
                modules.setdefault(module_id, []).append(
                    float(ef) if cc is None else 0.5 * float(ef) + 0.5 * float(cc)
                )
    figure, axis = plt.subplots(figsize=(8.0, 3.8))
    module_names = sorted(modules)
    axis.plot(
        range(len(module_names)),
        [sum(modules[item]) / len(modules[item]) for item in module_names],
        marker="o",
    )
    axis.set(xticks=range(len(module_names)), xticklabels=module_names, ylabel="Module D-CORE")
    axis.tick_params(axis="x", rotation=45)
    _save_figure(figure, root, FIGURES[0])
    plt.close(figure)
    yield_by_condition: dict[str, list[float]] = {}
    for row in rows:
        value = row.get("marketable_yield_shortfall")
        if value is not None:
            yield_by_condition.setdefault(str(row.get("condition")), []).append(float(value))
    figure, axis = plt.subplots(figsize=(8.0, 3.8))
    condition_names = sorted(yield_by_condition)
    if condition_names:
        axis.boxplot([yield_by_condition[item] for item in condition_names], tick_labels=condition_names)
    axis.set_ylabel("Paired marketable-yield shortfall")
    axis.tick_params(axis="x", rotation=45)
    _save_figure(figure, root, FIGURES[1])
    plt.close(figure)
    lag_rows = [
        item
        for item in rows
        if item.get("synchronization_lag_p95_seconds") is not None
    ]
    figure, axis = plt.subplots(figsize=(6.0, 3.8))
    axis.scatter(
        [float(item.get("never_received_fact_versions") or 0) for item in lag_rows],
        [float(item["synchronization_lag_p95_seconds"]) for item in lag_rows],
    )
    axis.set(xlabel="Never-received fact versions", ylabel="p95 synchronization lag (s)")
    _save_figure(figure, root, FIGURES[2])
    plt.close(figure)
    pairs = [
        (
            str(item.get("expected_localization") or "none"),
            str(item.get("predicted_localization") or "unlocalized"),
        )
        for item in controlled_rows
        if item.get("expected_localization")
    ]
    labels = sorted({value for pair in pairs for value in pair})
    matrix = [
        [sum(pair == (expected, predicted) for pair in pairs) for predicted in labels]
        for expected in labels
    ]
    figure, axis = plt.subplots(figsize=(6.0, 5.0))
    if labels:
        image = axis.imshow(matrix, cmap="Blues")
        figure.colorbar(image, ax=axis)
        axis.set(xticks=range(len(labels)), yticks=range(len(labels)), xticklabels=labels, yticklabels=labels)
    axis.set(xlabel="Predicted earliest link", ylabel="Planted earliest link")
    axis.tick_params(axis="x", rotation=45)
    _save_figure(figure, root, FIGURES[3])
    plt.close(figure)
    calibration = [
        (1 - float(item["dcore_score"]), float(item["marketable_yield_shortfall"]))
        for item in rows
        if item.get("dcore_score") is not None
        and item.get("marketable_yield_shortfall") is not None
    ]
    figure, axis = plt.subplots(figsize=(5.0, 4.0))
    axis.scatter([item[0] for item in calibration], [item[1] for item in calibration])
    axis.set(xlabel="1 - D-CORE", ylabel="Yield shortfall")
    _save_figure(figure, root, FIGURES[4])
    plt.close(figure)


def generate_paper_report(source: str | Path, output_dir: str | Path) -> dict[str, Any]:
    source_path = Path(source)
    rows = _read_rows(source_path)
    controlled_rows: list[dict[str, Any]] = []
    if source_path.is_dir():
        for path in sorted(source_path.rglob("metric_validation.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            controlled_rows.extend(payload.get("rows", []))
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    aggregate = aggregate_rows(rows)
    for name, table_rows in _table_rows(rows, aggregate, controlled_rows).items():
        _write_table(root, name, table_rows)
    _write_figures(rows, root, controlled_rows)
    source_digests = sorted(stable_digest(row) for row in rows)
    artifacts = sorted(
        path.name for path in root.iterdir() if path.is_file() and path.name != "analysis_manifest.json"
    )
    manifest = {
        "schema_version": "farm_dcore_analysis_manifest_v1",
        "source_row_count": len(rows),
        "controlled_fixture_count": len(controlled_rows),
        "controlled_fixture_digest": stable_digest(controlled_rows),
        "source_row_digests": source_digests,
        "source_set_digest": stable_digest(source_digests),
        "specification_versions": sorted(
            {str(row.get("scientific_contract")) for row in rows}
        ),
        "metric_versions": sorted({str(row.get("metric_version")) for row in rows}),
        "failed_runs": sum(not bool(row.get("success")) for row in rows),
        "infrastructure_failures": sum(bool(row.get("infrastructure_failure")) for row in rows),
        "exclusions": [],
        "plotting_parameters": {"dpi": 180, "backend": "Agg"},
        "tables": list(TABLES),
        "figures": list(FIGURES),
        "artifacts": artifacts,
        "artifact_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in artifacts
        },
    }
    (root / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


__all__ = ["FIGURES", "TABLES", "generate_paper_report"]
