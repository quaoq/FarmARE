"""Generate paper-style FARM tables from one or more summary CSV files.

This script is a lightweight post-processing layer on top of the rebatch
summary tables. It accepts one or more `summary_v2*.csv` files, normalizes
their labels, aggregates matching rows, and writes LaTeX tables that follow
the same multi-panel style used in the paper drafts.

Typical usage:

    python scripts/farm_paper_tables.py \
        --summary-csv rebatch/deepseek_e3/summary_v2_deepseek_e3.csv \
        --source-label DeepSeek-V4-Flash \
        --summary-csv rebatch/qwen_l3_20_3L2groups_20260608_121221/summary_v2_qwen_l3_20_3L2groups_20260608_121221.csv \
        --source-label Qwen3.6-35B-A3B \
        --out-dir figures/farm_paper_tables

Outputs:
    paper_tables.tex           Combined LaTeX file with all tables.
    paper_table_1.tex          Table 1 block.
    paper_table_2.tex          Table 2 block.
    paper_table_3.tex          Table 3 block.
    paper_table_1_rows.csv      Aggregated rows behind Table 1.
    paper_table_2_rows.csv      Aggregated rows behind Table 2.
    paper_table_3_rows.csv      Aggregated rows behind Table 3.

The script is intentionally conservative:
  - It never replays the simulation.
  - It only aggregates columns already present in the summary CSVs.
  - If a preferred label is missing, it falls back to the raw source path.

By default the tables use:
  - yield_loss
  - outcome (success score)
  - ktc
  - fos
  - tool_inflation

Use `--metric ...` to override the metric list. Metrics can be repeated or
comma-separated, e.g. `--metric recovered_yield,bfcl,farm_fos_v2_total`.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Iterable


@dataclass(frozen=True)
class MetricSpec:
    slug: str
    header: str
    kind: str  # "percent" or "number"
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class RowRecord:
    source: Path
    source_label: str
    row: dict[str, str]
    bundle: str
    model: str
    a2a: str
    detail: str
    level: str
    family: str


METRIC_LIBRARY: dict[str, MetricSpec] = {
    "yield_loss": MetricSpec(
        "yield_loss",
        "Yield Loss",
        "percent",
        (
            "yield_loss(%)",
            "yield_loss",
            "crop_loss_pct(%)",
            "crop_loss_pct",
            "recovered_yield_loss(%)",
            "recovered_yield_loss_v2(%)",
        ),
    ),
    "recovered_yield": MetricSpec(
        "recovered_yield",
        "Recovered Yield",
        "number",
        (
            "recovered_yield_kg",
            "agent_recovered_yield_kg",
            "oracle_recovered_yield_kg",
            "focus_agent_biological_kg",
        ),
    ),
    "bfcl": MetricSpec(
        "bfcl",
        "BFCL Success",
        "percent",
        ("bfcl_success(%)", "bfcl_success", "bfcl"),
    ),
    "outcome": MetricSpec(
        "outcome",
        "Succ. Score",
        "percent",
        ("outcome(%)", "outcome"),
    ),
    "farm_fos_v2_total": MetricSpec(
        "farm_fos_v2_total",
        "FOS v2",
        "percent",
        ("farm_fos_v2_total(%)", "farm_fos_v2_total"),
    ),
    "fos": MetricSpec(
        "fos",
        "FOS Score",
        "percent",
        ("fos(%)", "fos", "farm_fos(%)", "farm_fos"),
    ),
    "coverage": MetricSpec(
        "coverage",
        "Coverage",
        "percent",
        ("coverage(%)", "coverage_v1(%)", "coverage1", "coverage"),
    ),
    "ktc_score": MetricSpec(
        "ktc_score",
        "KTC Score",
        "percent",
        ("ktc_score(%)", "ktc_score", "ktc_raw", "ktc_raw1", "ktc(%)", "ktc"),
    ),
    "ktc_adjusted": MetricSpec(
        "ktc_adjusted",
        "KTC Adj.",
        "percent",
        ("ktc_adjusted(%)", "ktc_adjusted"),
    ),
    "ktc": MetricSpec(
        "ktc",
        "KTC Score",
        "percent",
        ("ktc(%)", "ktc", "ktc_score(%)", "ktc_raw", "ktc_raw1"),
    ),
    "tool_inflation": MetricSpec(
        "tool_inflation",
        "Tool Inflation",
        "number",
        ("tool_inflation",),
    ),
}

DEFAULT_METRICS = ["yield_loss", "outcome", "ktc", "fos", "tool_inflation"]


def _first_present(row: dict[str, str], candidates: Iterable[str]) -> str | None:
    for key in candidates:
        value = row.get(key, "")
        if value not in ("", None):
            return value
    return None


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    value = str(text).strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _percent_for_display(value: float) -> float:
    # Summary CSVs mix 0-1 and 0-100 conventions; values near 1 are ratios.
    return value * 100.0 if abs(value) <= 1.5 else value


def _percent_raw(value: float) -> float:
    return value * 100.0 if abs(value) <= 1.5 else value


def _format_display(value: float | None, kind: str) -> str:
    if value is None:
        return "--"
    if kind == "number":
        return f"{value:.2f}"
    return f"{_percent_for_display(value):.1f}%"


def _format_csv(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def _humanize_source_label(path: Path) -> str:
    stem = path.stem
    if stem.startswith("summary_v2_"):
        stem = stem[len("summary_v2_") :]
    stem = stem.replace("_", " ").strip()
    return stem if stem else path.parent.name.replace("_", " ").strip()


def _infer_source_label(path: Path, provided: str | None) -> str:
    if provided:
        return provided
    return _humanize_source_label(path)


def _infer_model(row: dict[str, str], source_label: str) -> str:
    value = _first_present(row, ("LLM", "llm_model"))
    if value:
        return value.strip()
    return source_label


def _infer_a2a(row: dict[str, str]) -> str:
    value = _first_present(row, ("A2A", "a2a_status"))
    if not value:
        return "OFF"
    text = value.strip().upper()
    if text in {"ON", "TRUE", "YES", "1"}:
        return "ON"
    if text in {"OFF", "FALSE", "NO", "0"}:
        return "OFF"
    if "ON" in text:
        return "ON"
    if "OFF" in text:
        return "OFF"
    return "OFF"


def _infer_detail(row: dict[str, str]) -> str:
    value = _first_present(row, ("detail", "detail_status", "detailed_briefing"))
    if not value:
        return "Zero Context (DETAIL = FALSE)"
    text = value.strip().lower()
    if text in {"true", "human", "detail_true", "1", "yes", "on"}:
        return "Human Expert (DETAIL = TRUE)"
    if text in {"false", "zero", "detail_false", "0", "no", "off"}:
        return "Zero Context (DETAIL = FALSE)"
    if "true" in text or "human" in text:
        return "Human Expert (DETAIL = TRUE)"
    return "Zero Context (DETAIL = FALSE)"


def _infer_level(row: dict[str, str]) -> str:
    value = _first_present(row, ("level", "run_level"))
    if not value:
        return ""
    text = value.strip()
    lowered = text.lower()
    if lowered.startswith("level1") or re.search(r"(^|[^0-9])l1([^0-9]|$)", lowered):
        return "L1"
    if lowered.startswith("level2") or re.search(r"(^|[^0-9])l2([^0-9]|$)", lowered):
        return "L2"
    if lowered.startswith("level3") or re.search(r"(^|[^0-9])l3([^0-9]|$)", lowered):
        return "L3"
    return text


def _infer_family(row: dict[str, str]) -> str:
    value = _first_present(row, ("agent_family", "family"))
    if value:
        return value.strip()
    cell = row.get("cell", "")
    if "__" in cell:
        return cell.split("__", 1)[0].removeprefix("farm_")
    return ""


def _metric_value(row: dict[str, str], spec: MetricSpec) -> float | None:
    if spec.slug == "yield_loss":
        value = _first_present(row, spec.candidates)
        if value is not None:
            parsed = _parse_float(value)
            return parsed
        ypr = _first_present(
            row,
            (
                "yield_preserved_ratio(%)",
                "yield_preserved_ratio",
                "focus_yield_preserved_ratio(%)",
                "focus_yield_preserved_ratio",
                "yield_ratio(%)",
                "yield_ratio",
            ),
        )
        parsed = _parse_float(ypr)
        if parsed is None:
            return None
        return 100.0 - _percent_raw(parsed)

    value = _first_present(row, spec.candidates)
    return _parse_float(value)


def _ordered_unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _sorted_level_values(values: Iterable[str]) -> list[str]:
    preferred = {"L1": 0, "L2": 1, "L3": 2}
    uniq = _ordered_unique(values)
    return sorted(uniq, key=lambda v: (preferred.get(v, 99), v))


def _sorted_a2a_values(values: Iterable[str]) -> list[str]:
    uniq = _ordered_unique(values)
    return sorted(uniq, key=lambda v: (0 if v == "OFF" else 1 if v == "ON" else 2, v))


def _aggregate(values: list[float], agg: str) -> float | None:
    if not values:
        return None
    return median(values) if agg == "median" else mean(values)


def _load_records(paths: list[Path], labels: list[str | None]) -> list[RowRecord]:
    records: list[RowRecord] = []
    for index, path in enumerate(paths):
        label = labels[index] if index < len(labels) else None
        source_label = _infer_source_label(path, label)
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if row.get("status", "ok") != "ok":
                    continue
                model = _infer_model(row, source_label)
                a2a = _infer_a2a(row)
                detail = _infer_detail(row)
                level = _infer_level(row)
                family = _infer_family(row)
                records.append(
                    RowRecord(
                        source=path,
                        source_label=source_label,
                        row=row,
                        bundle=f"{model} A2A={a2a}",
                        model=model,
                        a2a=a2a,
                        detail=detail,
                        level=level,
                        family=family,
                    )
                )
    return records


def _build_group_order(records: list[RowRecord], key: str, *, prefer: list[str] | None = None) -> list[str]:
    values = [getattr(rec, key) for rec in records if getattr(rec, key)]
    if key == "level":
        ordered = _sorted_level_values(values)
    elif key == "a2a":
        ordered = _sorted_a2a_values(values)
    else:
        ordered = _ordered_unique(values)
    if prefer:
        preferred = [value for value in prefer if value in ordered]
        rest = [value for value in ordered if value not in preferred]
        return preferred + rest
    return ordered


def _select_metric_specs(metric_names: list[str] | None) -> list[MetricSpec]:
    names = metric_names or DEFAULT_METRICS
    specs: list[MetricSpec] = []
    for raw_name in names:
        for name in (part.strip() for part in raw_name.split(",")):
            if not name:
                continue
            if name not in METRIC_LIBRARY:
                raise SystemExit(
                    f"Unknown metric {name!r}. Available metrics: "
                    f"{', '.join(sorted(METRIC_LIBRARY))}"
                )
            specs.append(METRIC_LIBRARY[name])
    return specs


def _render_table(
    records: list[RowRecord],
    *,
    csv_name: str,
    tex_name: str,
    row_field_name: str,
    row_attr: str,
    group_attr: str,
    group_title: str,
    metric_specs: list[MetricSpec],
    agg: str,
    preferred_groups: list[str] | None = None,
) -> tuple[list[dict[str, str]], str]:
    row_order = _ordered_unique(getattr(rec, row_attr) for rec in records)
    group_order = _build_group_order(records, group_attr, prefer=preferred_groups)

    cell_bucket: dict[tuple[str, str, str, str, str], list[float]] = defaultdict(list)
    bundle_bucket: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    row_order_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for rec in records:
        row_key = getattr(rec, row_attr)
        group_value = getattr(rec, group_attr)
        pair = (rec.family, rec.detail)
        if pair not in row_order_pairs[row_key]:
            row_order_pairs[row_key].append(pair)
        for spec in metric_specs:
            value = _metric_value(rec.row, spec)
            if value is None:
                continue
            cell_bucket[(row_key, rec.family, rec.detail, group_value, spec.slug)].append(value)
            bundle_bucket[(row_key, group_value, spec.slug)].append(value)

    out_rows: list[dict[str, str]] = []
    for row_key in row_order:
        pairs = row_order_pairs.get(row_key, [])
        for family, detail in pairs:
            row: dict[str, str] = {
                row_field_name: row_key,
                "family": family,
                "detail": detail,
                "row_type": "cell",
            }
            for group_value in group_order:
                for spec in metric_specs:
                    vals = cell_bucket.get((row_key, family, detail, group_value, spec.slug), [])
                    row[f"{group_value}_{spec.slug}"] = _format_csv(_aggregate(vals, agg))
            out_rows.append(row)

        avg_row: dict[str, str] = {
            row_field_name: "Average",
            "family": "",
            "detail": "",
            "row_type": "average",
        }
        for group_value in group_order:
            for spec in metric_specs:
                vals = bundle_bucket.get((row_key, group_value, spec.slug), [])
                avg_row[f"{group_value}_{spec.slug}"] = _format_csv(_aggregate(vals, agg))
        out_rows.append(avg_row)

    fieldnames = [row_field_name, "family", "detail", "row_type"]
    for group_value in group_order:
        for spec in metric_specs:
            fieldnames.append(f"{group_value}_{spec.slug}")

    tex = _render_tex(
        out_rows,
        row_field_name=row_field_name,
        group_order=group_order,
        group_title=group_title,
        metric_specs=metric_specs,
        title=tex_name,
    )
    return out_rows, tex


def _render_tex(
    rows: list[dict[str, str]],
    *,
    row_field_name: str,
    group_order: list[str],
    group_title: str,
    metric_specs: list[MetricSpec],
    title: str,
) -> str:
    lines: list[str] = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(r"\small")
    lines.append(f"\\caption{{{_tex_escape(title)}}}")
    label_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    lines.append(f"\\label{{tab:{label_slug}}}")

    n_row_headers = 3
    n_metric_cols = len(group_order) * len(metric_specs)
    colspec = "lll" + ("r" * n_metric_cols)
    lines.append(rf"\begin{{tabular}}{{{colspec}}}")
    lines.append(r"\toprule")

    first_header = [_tex_escape(row_field_name), "Family", "Detail"]
    group_cells = []
    for group_value in group_order:
        group_cells.append(
            rf"\multicolumn{{{len(metric_specs)}}}{{c}}{{{_tex_escape(group_value)}}}"
        )
    lines.append(" & ".join(first_header + group_cells) + r" \\")
    cmidrules = []
    start = n_row_headers + 1
    for offset in range(len(group_order)):
        left = start + offset * len(metric_specs)
        right = left + len(metric_specs) - 1
        cmidrules.append(rf"\cmidrule(lr){{{left}-{right}}}")
    if cmidrules:
        lines.append("".join(cmidrules))
    metric_header_cells = ["", "", ""]
    for _group_value in group_order:
        for spec in metric_specs:
            metric_header_cells.append(_tex_escape(spec.header))
    lines.append(" & ".join(metric_header_cells) + r" \\")
    lines.append(r"\midrule")

    last_row_value = None
    last_family = None
    for row in rows:
        row_value = row[row_field_name]
        family = row["family"]
        detail = row["detail"]
        if row["row_type"] == "average":
            display_row_value = "Average"
            display_family = ""
            display_detail = ""
            last_row_value = None
            last_family = None
        else:
            display_row_value = row_value if row_value != last_row_value else ""
            display_family = family if (row_value != last_row_value or family != last_family) else ""
            display_detail = detail
            last_row_value = row_value
            last_family = family

        values = [display_row_value, display_family, display_detail]
        for group_value in group_order:
            for spec in metric_specs:
                raw = row.get(f"{group_value}_{spec.slug}", "")
                value = _parse_float(raw) if raw not in ("", None) else None
                values.append(_format_display(value, spec.kind))
        lines.append(" & ".join(_tex_escape(value) for value in values) + r" \\")

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    return "\n".join(lines) + "\n"


def _tex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    out = text
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    return out


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-csv",
        action="append",
        type=Path,
        required=True,
        help="Input summary_v2 CSV. Repeatable.",
    )
    parser.add_argument(
        "--source-label",
        action="append",
        default=[],
        help="Optional pretty label for the matching --summary-csv input. Repeatable.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("figures/farm_paper_tables"),
        help="Directory for the LaTeX and CSV outputs.",
    )
    parser.add_argument(
        "--agg",
        choices=("mean", "median"),
        default="mean",
        help="Aggregation method for duplicate cells.",
    )
    parser.add_argument(
        "--comparison-level",
        default="L2",
        help="Preferred level for Table 2 and Table 3 when multiple levels exist.",
    )
    parser.add_argument(
        "--metric",
        action="append",
        default=[],
        help=(
            "Metric key to include, repeatable and comma-separated. "
            f"Available: {', '.join(sorted(METRIC_LIBRARY))}"
        ),
    )
    parser.add_argument(
        "--include-farm-fos-v2",
        action="store_true",
        help="Backward-compatible shorthand for --metric farm_fos_v2_total.",
    )
    args = parser.parse_args()

    if not args.summary_csv:
        raise SystemExit("at least one --summary-csv is required")
    if args.source_label and len(args.source_label) > len(args.summary_csv):
        raise SystemExit("--source-label may not outnumber --summary-csv")

    records = _load_records(args.summary_csv, args.source_label)
    if not records:
        raise SystemExit("no ok rows found in the provided summaries")

    metric_names = list(args.metric)
    if args.include_farm_fos_v2 and "farm_fos_v2_total" not in ",".join(metric_names):
        metric_names.append("farm_fos_v2_total")
    metric_specs = _select_metric_specs(metric_names or None)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Table 1: bundle x family x detail, with all levels side by side.
    table1_rows, table1_tex = _render_table(
        records,
        csv_name="paper_table_1_rows.csv",
        tex_name="Table 1. Bundle summary across available levels",
        row_field_name="bundle",
        row_attr="bundle",
        group_attr="level",
        group_title="Level",
        metric_specs=metric_specs,
        agg=args.agg,
    )

    # Table 2: bundle x family x detail, one preferred level.
    table2_records = [rec for rec in records if (rec.level == args.comparison_level or not rec.level)]
    if not table2_records:
        level_candidates = _build_group_order(records, "level")
        level_choice = level_candidates[0] if level_candidates else ""
        table2_records = [rec for rec in records if rec.level == level_choice or not rec.level]
    table2_rows, table2_tex = _render_table(
        table2_records,
        csv_name="paper_table_2_rows.csv",
        tex_name="Table 2. Preferred-level bundle comparison",
        row_field_name="bundle",
        row_attr="bundle",
        group_attr="level",
        group_title="Level",
        metric_specs=metric_specs,
        agg=args.agg,
        preferred_groups=[args.comparison_level],
    )

    # Table 3: model x family x detail, comparing A2A OFF/ON.
    table3_records = [rec for rec in records if not rec.level or rec.level == args.comparison_level]
    if not table3_records:
        table3_records = records
    table3_rows, table3_tex = _render_table(
        table3_records,
        csv_name="paper_table_3_rows.csv",
        tex_name="Table 3. Model comparison across A2A settings",
        row_field_name="model",
        row_attr="model",
        group_attr="a2a",
        group_title="A2A",
        metric_specs=metric_specs,
        agg=args.agg,
        preferred_groups=["OFF", "ON"],
    )

    _write_csv(args.out_dir / "paper_table_1_rows.csv", table1_rows)
    _write_csv(args.out_dir / "paper_table_2_rows.csv", table2_rows)
    _write_csv(args.out_dir / "paper_table_3_rows.csv", table3_rows)
    (args.out_dir / "paper_table_1.tex").write_text(table1_tex, encoding="utf-8")
    (args.out_dir / "paper_table_2.tex").write_text(table2_tex, encoding="utf-8")
    (args.out_dir / "paper_table_3.tex").write_text(table3_tex, encoding="utf-8")
    (args.out_dir / "paper_tables.tex").write_text(
        "\n".join([table1_tex, table2_tex, table3_tex]),
        encoding="utf-8",
    )

    print(
        f"Wrote paper tables to {args.out_dir} "
        f"({len(records)} source rows, agg={args.agg}, "
        f"comparison_level={args.comparison_level})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
