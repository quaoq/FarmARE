"""Package single-scenario FARM result rows for sharing and plotting.

This is the raw-row companion to ``generate_results_tables.py``.  It does not
average rows.  Instead it writes one large CSV with the exact scenario-level
records used by Tables E1-E6, plus a second CSV containing every loaded source
row exactly once.

Outputs:
    raw_table_scenario_results.csv
        One row per (table membership, model/context/controller/scenario).
        Rows can appear more than once if they are intentionally reused by
        different paper tables.
    raw_table_scenario_results_slim.csv
        The same table-expanded rows with only plotting/reporting columns.
    raw_scenario_results_slim_unique.csv
        Slim rows deduplicated across table reuse, with table_ids/table_names
        recording all paper-table memberships.
    raw_all_scenario_results.csv
        One row per loaded summary_v2 row, without table expansion.
    *.csv.gz
        Gzip-compressed copies for efficient sharing.
    manifest.json
        Input paths and row counts.
    README.md
        Minimal pandas loading notes.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Iterable

from generate_results_tables import (  # type: ignore
    AGENT_LABELS,
    DEFAULT_INPUTS,
    DETAIL_LABELS,
    DETAIL_LABELS_E1,
    DETAIL_ORDER_MAIN,
    E1_LIBRARY_SCENARIOS,
    _detail_key,
    _filter,
    _load_rows,
    _ok_rows,
    _scenario_set,
)

SOURCE_MODEL = {
    "deepseek_e1": "DeepSeek API",
    "deepseek_e2": "DeepSeek API",
    "deepseek_e3": "DeepSeek API",
    "deepseek_e4": "DeepSeek API",
    "deepseek_e5": "DeepSeek API",
    "qwen_e1": "Qwen API",
    "qwen_e2": "Qwen API",
    "qwen_e3": "Qwen API",
    "qwen_e4": "Qwen API",
    "qwen_e5": "Qwen API",
    "vllm_e6": "Qwen vLLM",
    "vllm_l1l2": "Qwen vLLM",
}

SOURCE_EXPERIMENT = {
    "deepseek_e1": "E1",
    "qwen_e1": "E1",
    "deepseek_e2": "E2",
    "qwen_e2": "E2",
    "deepseek_e3": "E3",
    "qwen_e3": "E3",
    "deepseek_e4": "E4",
    "qwen_e4": "E4",
    "deepseek_e5": "E5",
    "qwen_e5": "E5",
    "vllm_e6": "E6_vLLM",
    "vllm_l1l2": "E5_vLLM",
}

TABLE_NAMES = {
    "e1": "Table E1. Retrieval/Skill Context",
    "e1b": "Table E1b. Baseline Contexts On E1 20",
    "e2": "Table E2. Held-out 70",
    "e3a": "Table E3a. DeepSeek Agent-Family Robustness",
    "e3b": "Table E3b. Qwen Agent-Family Robustness",
    "e4a": "Table E4a. DeepSeek A2A On vs Off",
    "e4b": "Table E4b. Qwen A2A On vs Off",
    "e5a": "Table E5a. L1 Splits",
    "e5b": "Table E5b. L2 Splits",
    "e5c": "Table E5c. Qwen vLLM L1/L2 Splits",
    "e6a": "Table E6a. Model Comparison, Zero Context",
    "e6b": "Table E6b. Model Comparison, Expert Instruct",
}

META_COLUMNS = [
    "table_id",
    "table_name",
    "source_key",
    "source_experiment",
    "source_path",
    "model_display",
    "exact_model_type_size",
    "context_key",
    "context_label",
    "controller_key",
    "controller_label",
    "a2a_label",
    "a2a_enabled",
    "split_level",
    "scenario_id",
    "cell_id",
]

SLIM_COLUMNS = [
    "table_id",
    "table_name",
    "source_key",
    "source_experiment",
    "model_display",
    "exact_model_type_size",
    "context_key",
    "context_label",
    "controller_key",
    "controller_label",
    "a2a_label",
    "a2a_enabled",
    "split_level",
    "scenario_id",
    "cell_id",
    "l3_pool",
    "level",
    "status",
    "llm_calls_with_usage",
    "avg_total_tokens_per_agent_call",
    "avg_input_tokens_per_agent_call",
    "avg_output_tokens_per_agent_call",
    "avg_cached_tokens_per_agent_call",
    "total_tokens_per_scenario",
    "avg_runtime_s_per_agent_call",
    "runtime_s_per_scenario",
    "agent_biological_kg",
    "oracle_biological_kg",
    "agent_recovered_yield_kg",
    "oracle_recovered_yield_kg",
    "yield_loss(%)",
    "recovered_yield_loss(%)",
    "bfcl_success(%)",
    "ktc(%)",
    "ktc_score(%)",
    "ktc_adjusted(%)",
    "coverage(%)",
    "path_correctness(%)",
    "farm_fos(%)",
    "farm_fos_v2_total(%)",
    "oracle_tool_calls",
    "expects_agent_harvest",
]


def _model_type(row: dict[str, str], model: str) -> str:
    value = str(row.get("model_type_size") or row.get("model") or "").strip()
    if model == "DeepSeek API":
        if not value or value == "deepseek-chat":
            return "deepseek-chat (DeepSeek-V4-Flash, non-thinking mode)"
    if model == "Qwen API":
        if value == "qwen3.6-flash-2026-04-16":
            return "qwen3.6-flash-2026-04-16 (Qwen3.6-35B-A3B-FP8)"
        if not value:
            return "qwen3.6-flash-2026-04-16 (Qwen3.6-35B-A3B-FP8)"
    marker_text = " ".join(
        str(row.get(key) or "")
        for key in ("cell_dir", "cell_output_rel", "fos_path")
    )
    if value and "assistant_false" in marker_text:
        return f"{value} (assistant false)"
    return value


def _context_label(row: dict[str, str], *, e1: bool = False) -> str:
    detail = _detail_key(row)
    labels = DETAIL_LABELS_E1 if e1 else DETAIL_LABELS
    return labels.get(detail, detail or "unknown")


def _controller_label(row: dict[str, str]) -> str:
    family = str(row.get("agent_family") or "").strip()
    return AGENT_LABELS.get(family, family)


def _split_level(row: dict[str, str]) -> str:
    scenario = str(row.get("scenario") or "").strip()
    if scenario.startswith("scenario_l1_"):
        return "L1"
    if scenario.startswith("scenario_l2_"):
        return "L2"
    return ""


def _a2a_label_from_row(row: dict[str, str]) -> str:
    raw_enabled = str(row.get("a2a_enabled") or "").strip().lower()
    raw_status = str(row.get("a2a_status") or "").strip().lower()
    if raw_enabled in {"1", "true", "yes", "on"} or raw_status == "on":
        return "A2A On"
    if raw_enabled in {"0", "false", "no", "off"} or raw_status == "off":
        return "A2A Off"
    return ""


def _a2a_enabled_from_row(row: dict[str, str]) -> str:
    label = _a2a_label_from_row(row)
    if label == "A2A On":
        return "true"
    if label == "A2A Off":
        return "false"
    return ""


def _a2a_enabled_from_label(label: str) -> str:
    if label == "A2A On":
        return "true"
    if label == "A2A Off":
        return "false"
    return ""


def _meta(
    row: dict[str, str],
    *,
    source_key: str,
    source_path: Path,
    table_id: str = "",
    table_name: str = "",
    e1_labels: bool = False,
    a2a_label: str = "",
    split_level: str = "",
) -> dict[str, str]:
    model = SOURCE_MODEL[source_key]
    resolved_a2a_label = a2a_label or _a2a_label_from_row(row)
    return {
        "table_id": table_id,
        "table_name": table_name,
        "source_key": source_key,
        "source_experiment": SOURCE_EXPERIMENT[source_key],
        "source_path": source_path.as_posix(),
        "model_display": model,
        "exact_model_type_size": _model_type(row, model),
        "context_key": _detail_key(row),
        "context_label": _context_label(row, e1=e1_labels),
        "controller_key": str(row.get("agent_family") or "").strip(),
        "controller_label": _controller_label(row),
        "a2a_label": resolved_a2a_label,
        "a2a_enabled": _a2a_enabled_from_label(
            resolved_a2a_label
        ) or _a2a_enabled_from_row(row),
        "split_level": split_level or _split_level(row),
        "scenario_id": str(row.get("scenario") or "").strip(),
        "cell_id": str(row.get("cell") or "").strip(),
    }


def _with_meta(row: dict[str, str], meta: dict[str, str]) -> dict[str, str]:
    merged = dict(meta)
    for key, value in row.items():
        if key in merged:
            merged[f"raw_{key}"] = value
        else:
            merged[key] = value
    return merged


def _load_sources(paths: dict[str, Path]) -> dict[str, list[dict[str, str]]]:
    return {key: _ok_rows(_load_rows(path)) for key, path in paths.items()}


def _all_raw_rows(
    loaded: dict[str, list[dict[str, str]]], paths: dict[str, Path]
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source_key, source_rows in loaded.items():
        for row in source_rows:
            rows.append(
                _with_meta(
                    row,
                    _meta(
                        row,
                        source_key=source_key,
                        source_path=paths[source_key],
                        e1_labels=source_key.endswith("_e1"),
                    ),
                )
            )
    return rows


def _append_table_rows(
    out: list[dict[str, str]],
    source_rows: Iterable[dict[str, str]],
    *,
    source_key: str,
    source_path: Path,
    table_id: str,
    e1_labels: bool = False,
    a2a_label: str = "",
    split_level: str = "",
) -> None:
    for row in source_rows:
        out.append(
            _with_meta(
                row,
                _meta(
                    row,
                    source_key=source_key,
                    source_path=source_path,
                    table_id=table_id,
                    table_name=TABLE_NAMES[table_id],
                    e1_labels=e1_labels,
                    a2a_label=a2a_label,
                    split_level=split_level,
                ),
            )
        )


def _append_a2a_comparison_rows(
    out: list[dict[str, str]],
    *,
    off_rows: list[dict[str, str]],
    on_rows: list[dict[str, str]],
    off_source_key: str,
    on_source_key: str,
    paths: dict[str, Path],
    table_id: str,
) -> None:
    for detail in DETAIL_ORDER_MAIN:
        on_detail = _filter(on_rows, detail=detail, family="baseline_react")
        on_scenarios = {row.get("scenario", "") for row in on_detail}
        _append_table_rows(
            out,
            _filter(
                off_rows,
                detail=detail,
                family="baseline_react",
                scenarios=on_scenarios,
                l3_pool="70",
            ),
            source_key=off_source_key,
            source_path=paths[off_source_key],
            table_id=table_id,
            a2a_label="A2A Off",
        )
        _append_table_rows(
            out,
            on_detail,
            source_key=on_source_key,
            source_path=paths[on_source_key],
            table_id=table_id,
            a2a_label="A2A On",
        )


def _expanded_table_rows(
    loaded: dict[str, list[dict[str, str]]], paths: dict[str, Path]
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    for source_key in ("deepseek_e1", "qwen_e1"):
        _append_table_rows(
            out,
            loaded[source_key],
            source_key=source_key,
            source_path=paths[source_key],
            table_id="e1",
            e1_labels=True,
        )

    for source_key in ("deepseek_e2", "qwen_e2"):
        for detail in ("false", "true"):
            _append_table_rows(
                out,
                _filter(
                    loaded[source_key],
                    detail=detail,
                    family="baseline_react",
                    scenarios=E1_LIBRARY_SCENARIOS,
                ),
                source_key=source_key,
                source_path=paths[source_key],
                table_id="e1b",
            )

    for source_key in ("deepseek_e2", "qwen_e2"):
        _append_table_rows(
            out,
            [row for row in loaded[source_key] if row.get("l3_pool") == "70"],
            source_key=source_key,
            source_path=paths[source_key],
            table_id="e2",
        )

    for detail in DETAIL_ORDER_MAIN:
        deepseek_e3_detail = _filter(loaded["deepseek_e3"], detail=detail)
        deepseek_scenarios = {row.get("scenario", "") for row in deepseek_e3_detail}
        _append_table_rows(
            out,
            _filter(
                loaded["deepseek_e2"],
                detail=detail,
                family="baseline_react",
                scenarios=deepseek_scenarios,
            ),
            source_key="deepseek_e2",
            source_path=paths["deepseek_e2"],
            table_id="e3a",
        )
        _append_table_rows(
            out,
            deepseek_e3_detail,
            source_key="deepseek_e3",
            source_path=paths["deepseek_e3"],
            table_id="e3a",
        )

    for detail in DETAIL_ORDER_MAIN:
        qwen_e3_detail = _filter(loaded["qwen_e3"], detail=detail)
        qwen_scenarios = {row.get("scenario", "") for row in qwen_e3_detail}
        _append_table_rows(
            out,
            _filter(
                loaded["qwen_e2"],
                detail=detail,
                family="baseline_react",
                scenarios=qwen_scenarios,
            ),
            source_key="qwen_e2",
            source_path=paths["qwen_e2"],
            table_id="e3b",
        )
        _append_table_rows(
            out,
            qwen_e3_detail,
            source_key="qwen_e3",
            source_path=paths["qwen_e3"],
            table_id="e3b",
        )

    _append_a2a_comparison_rows(
        out,
        off_rows=loaded["deepseek_e2"],
        on_rows=loaded["deepseek_e4"],
        off_source_key="deepseek_e2",
        on_source_key="deepseek_e4",
        paths=paths,
        table_id="e4a",
    )
    _append_a2a_comparison_rows(
        out,
        off_rows=loaded["qwen_e2"],
        on_rows=loaded["qwen_e4"],
        off_source_key="qwen_e2",
        on_source_key="qwen_e4",
        paths=paths,
        table_id="e4b",
    )

    for table_id, split_level in (("e5a", "L1"), ("e5b", "L2")):
        for source_key in ("deepseek_e5", "qwen_e5"):
            _append_table_rows(
                out,
                [
                    row
                    for row in loaded[source_key]
                    if _split_level(row) == split_level
                ],
                source_key=source_key,
                source_path=paths[source_key],
                table_id=table_id,
                split_level=split_level,
            )

    _append_table_rows(
        out,
        loaded["vllm_l1l2"],
        source_key="vllm_l1l2",
        source_path=paths["vllm_l1l2"],
        table_id="e5c",
    )

    for table_id, detail in (("e6a", "false"), ("e6b", "true")):
        scenario_sets = [
            _scenario_set(loaded["deepseek_e2"], detail),
            _scenario_set(loaded["qwen_e2"], detail),
            _scenario_set(loaded["vllm_e6"], detail),
        ]
        common = set.intersection(*scenario_sets) if all(scenario_sets) else set()
        for source_key in ("deepseek_e2", "qwen_e2", "vllm_e6"):
            _append_table_rows(
                out,
                _filter(
                    loaded[source_key],
                    detail=detail,
                    family="baseline_react",
                    scenarios=common or None,
                ),
                source_key=source_key,
                source_path=paths[source_key],
                table_id=table_id,
            )

    return out


def _fieldnames(rows: list[dict[str, str]]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for key in META_COLUMNS:
        seen.add(key)
        ordered.append(key)
    preferred = [
        "status",
        "scenario",
        "cell",
        "agent_family",
        "l3_pool",
        "level",
        "detailed_briefing",
        "llm_model",
        "model_type_size",
        "llm_calls_with_usage",
        "avg_total_tokens_per_agent_call",
        "avg_input_tokens_per_agent_call",
        "avg_output_tokens_per_agent_call",
        "avg_cached_tokens_per_agent_call",
        "total_tokens_per_scenario",
        "avg_runtime_s_per_agent_call",
        "runtime_s_per_scenario",
        "agent_biological_kg",
        "oracle_biological_kg",
        "agent_recovered_yield_kg",
        "oracle_recovered_yield_kg",
        "yield_loss(%)",
        "recovered_yield_loss(%)",
        "bfcl_success(%)",
        "ktc(%)",
        "ktc_score(%)",
        "ktc_adjusted(%)",
        "coverage(%)",
        "path_correctness(%)",
        "farm_fos(%)",
        "farm_fos_v2_total(%)",
    ]
    for key in preferred:
        if key not in seen and any(key in row for row in rows):
            seen.add(key)
            ordered.append(key)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = _fieldnames(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_csv_with_fields(
    path: Path, rows: list[dict[str, str]], fields: list[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _slim_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{key: row.get(key, "") for key in SLIM_COLUMNS} for row in rows]


def _fill_display_labels(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    display_rows: list[dict[str, str]] = []
    for row in rows:
        display_row = dict(row)
        display_row["a2a_label"] = display_row.get("a2a_label") or "A2A Off"
        display_row["a2a_enabled"] = display_row.get("a2a_enabled") or "false"
        display_row["split_level"] = display_row.get("split_level") or "L3"
        display_rows.append(display_row)
    return display_rows


def _unique_slim_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Deduplicate slim rows reused across tables while preserving memberships."""

    identity_columns = [
        key
        for key in SLIM_COLUMNS
        if key not in {"table_id", "table_name", "a2a_label"}
    ]
    by_identity: dict[tuple[str, ...], dict[str, str]] = {}
    table_ids: dict[tuple[str, ...], list[str]] = {}
    table_names: dict[tuple[str, ...], list[str]] = {}
    a2a_labels: dict[tuple[str, ...], list[str]] = {}

    for row in rows:
        key = tuple(row.get(column, "") for column in identity_columns)
        if key not in by_identity:
            by_identity[key] = dict(row)
            table_ids[key] = []
            table_names[key] = []
            a2a_labels[key] = []
        table_id = row.get("table_id", "")
        table_name = row.get("table_name", "")
        a2a_label = row.get("a2a_label", "")
        if table_id and table_id not in table_ids[key]:
            table_ids[key].append(table_id)
        if table_name and table_name not in table_names[key]:
            table_names[key].append(table_name)
        if a2a_label and a2a_label not in a2a_labels[key]:
            a2a_labels[key].append(a2a_label)

    unique_rows: list[dict[str, str]] = []
    for key, row in by_identity.items():
        if a2a_labels[key]:
            row["a2a_label"] = ";".join(a2a_labels[key])
        row["table_ids"] = ";".join(table_ids[key])
        row["table_names"] = ";".join(table_names[key])
        unique_rows.append(row)
    return unique_rows


def _gzip_copy(path: Path) -> Path:
    gz_path = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    return gz_path


def _parse_inputs(raw: list[str]) -> dict[str, Path]:
    paths = dict(DEFAULT_INPUTS)
    for item in raw:
        if "=" not in item:
            raise SystemExit("--input must be KEY=PATH")
        key, value = item.split("=", 1)
        key = key.strip()
        if key not in paths:
            raise SystemExit(
                f"Unknown input key {key!r}. Expected one of: {', '.join(paths)}"
            )
        paths[key] = Path(value)
    return paths


def _write_readme(out_dir: Path) -> None:
    (out_dir / "README.md").write_text(
        """# FARM Raw Results Package

Main file:

```python
import pandas as pd
df = pd.read_csv("raw_table_scenario_results.csv.gz")
```

`raw_table_scenario_results.csv(.gz)` contains one row per single scenario
result as used by Tables E1-E6.  Use `table_id`, `model_display`,
`context_label`, `controller_label`, and `scenario_id` for grouping/plotting.

`raw_table_scenario_results_slim.csv(.gz)` keeps only the plotting/reporting
columns, including `oracle_tool_calls`.

`raw_scenario_results_slim_unique.csv(.gz)` deduplicates rows reused across
tables and records their memberships in `table_ids`/`table_names`.

`raw_all_scenario_results.csv(.gz)` contains each loaded source summary row
once, without table expansion.

No metrics in these CSVs are averaged by this packaging script.  Numeric
columns are copied from the scenario-level rebatch summaries.
""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Override default input as KEY=PATH. Keys: " + ", ".join(DEFAULT_INPUTS),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("paper_tables/raw_results_pkg"),
        help="Package output directory.",
    )
    args = parser.parse_args()

    paths = _parse_inputs(args.input)
    loaded = _load_sources(paths)
    all_rows = _all_raw_rows(loaded, paths)
    table_rows = _expanded_table_rows(loaded, paths)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_csv = args.out_dir / "raw_all_scenario_results.csv"
    table_csv = args.out_dir / "raw_table_scenario_results.csv"
    table_slim_csv = args.out_dir / "raw_table_scenario_results_slim.csv"
    unique_slim_csv = args.out_dir / "raw_scenario_results_slim_unique.csv"
    table_slim_rows = _slim_rows(table_rows)
    unique_slim_rows = _unique_slim_rows(table_slim_rows)
    table_slim_display_rows = _fill_display_labels(table_slim_rows)
    unique_slim_display_rows = _fill_display_labels(unique_slim_rows)
    _write_csv(all_csv, all_rows)
    _write_csv(table_csv, table_rows)
    _write_csv_with_fields(table_slim_csv, table_slim_display_rows, SLIM_COLUMNS)
    _write_csv_with_fields(
        unique_slim_csv,
        unique_slim_display_rows,
        SLIM_COLUMNS + ["table_ids", "table_names"],
    )
    all_gz = _gzip_copy(all_csv)
    table_gz = _gzip_copy(table_csv)
    table_slim_gz = _gzip_copy(table_slim_csv)
    unique_slim_gz = _gzip_copy(unique_slim_csv)
    _write_readme(args.out_dir)

    manifest = {
        "inputs": {key: path.as_posix() for key, path in paths.items()},
        "source_row_counts": {key: len(rows) for key, rows in loaded.items()},
        "raw_all_scenario_results_rows": len(all_rows),
        "raw_table_scenario_results_rows": len(table_rows),
        "raw_table_scenario_results_slim_rows": len(table_slim_rows),
        "raw_scenario_results_slim_unique_rows": len(unique_slim_rows),
        "table_row_counts": Counter(row.get("table_id", "") for row in table_rows),
        "files": [
            all_csv.name,
            all_gz.name,
            table_csv.name,
            table_gz.name,
            table_slim_csv.name,
            table_slim_gz.name,
            unique_slim_csv.name,
            unique_slim_gz.name,
            "README.md",
            "manifest.json",
        ],
    }
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=dict),
        encoding="utf-8",
    )

    print(
        f"Wrote raw package to {args.out_dir} "
        f"({len(table_rows)} table rows, {len(all_rows)} source rows)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
