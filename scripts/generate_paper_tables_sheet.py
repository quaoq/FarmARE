"""Generate the `Paper Tables` workbook sheet from a summary table.

The script replaces/creates the second worksheet in an existing workbook while
leaving the raw-data sheet untouched. Values are aggregated from
``summary_v3.csv`` or an Excel sheet by model, level, detail flag, A2A flag,
and agent family.

Example:
    uv run python scripts/generate_paper_tables_sheet.py \
        --summary-csv rebatch_outputs/reeval_v4/summary_v3.csv \
        --template-xlsx /Users/quao/Desktop/summary_v2_paper_tables.xlsx \
        --output-xlsx /Users/quao/Desktop/summary_v3_paper_tables.xlsx
"""
from __future__ import annotations

import argparse
import csv
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "Missing dependency: openpyxl. On this machine, run this script with "
        "`python3 scripts/generate_paper_tables_sheet.py ...` instead of "
        "`uv run python ...`, or install openpyxl into the uv environment."
    ) from exc


AGENTS = [
    "adaptive_verifier",
    "baseline_react",
    "critic_refiner",
    "graph_memory",
    "multi_specialist",
    "planner_executor",
    "reflective_memory",
    "rewoo_modular",
    "tree_search",
]

METRICS = [
    "yield_preserved_ratio(%)",
    "path_correctness(%)",
    "ktc_raw(%)",
    "coverage(%)",
    "tool_inflation",
]

LEVEL_LABELS = {
    "L1": "level1_baseline",
    "L2": "level2_episode",
    "L3": "level3_fullseason",
}

MODEL_PREFIX = {
    "Qwen": "qwen",
    "DeepSeek": "deepseek",
    "GPT": "gpt",
}


@dataclass(frozen=True)
class RowInfo:
    row: dict[str, str]
    slug: str
    model: str
    detail: bool | None
    a2a: bool | None
    agent: str
    level: str


def _slug_from_cell_dir(cell_dir: str) -> str:
    parts = Path(cell_dir).parts
    if "phase5_paper_matrix" in parts:
        index = parts.index("phase5_paper_matrix")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _model_from_slug(slug: str) -> str:
    lowered = slug.lower()
    if lowered.startswith("qwen"):
        return "Qwen"
    if lowered.startswith("deepseek"):
        return "DeepSeek"
    if lowered.startswith("gpt"):
        return "GPT"
    return ""


def _display_a2a(value: bool | None) -> str:
    if value is True:
        return "ON"
    if value is False:
        return "OFF"
    return ""


def _detail_from_slug(slug: str) -> bool | None:
    if "detail_true" in slug:
        return True
    if "detail_false" in slug:
        return False
    return None


def _a2a_from_slug(slug: str) -> bool | None:
    if "a2a_on" in slug:
        return True
    if "a2a_off" in slug:
        return False
    return None


def _agent_from_cell(cell: str) -> str:
    if "__" not in cell:
        return ""
    family = cell.split("__", 1)[0]
    return family.removeprefix("farm_")


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _excel_value(value: str | None):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        number = float(text)
    except ValueError:
        return value
    if number.is_integer() and not any(ch in text.lower() for ch in (".", "e")):
        return int(number)
    return number


def _aggregate(values: Iterable[float], method: str) -> float | None:
    vals = list(values)
    if not vals:
        return None
    if method == "median":
        return statistics.median(vals)
    return statistics.mean(vals)


def _load_raw_rows(path: Path, sheet_name: str | None = None) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        workbook = load_workbook(path, data_only=True, read_only=True)
        if sheet_name:
            if sheet_name not in workbook.sheetnames:
                raise SystemExit(
                    f"Sheet {sheet_name!r} not found in {path}. "
                    f"Available sheets: {workbook.sheetnames}"
                )
            worksheet = workbook[sheet_name]
        else:
            worksheet = workbook[workbook.sheetnames[0]]

        rows_iter = worksheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            return []
        headers = ["" if value is None else str(value).strip() for value in header_row]
        out: list[dict[str, str]] = []
        for values in rows_iter:
            if values is None or not any(value is not None for value in values):
                continue
            row: dict[str, str] = {}
            for index, header in enumerate(headers):
                if not header:
                    continue
                value = values[index] if index < len(values) else None
                row[header] = "" if value is None else str(value)
            out.append(row)
        return out

    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader]

    raise SystemExit(f"Unsupported summary input format: {path.suffix}")


def _row_infos_from_raw(rows: list[dict[str, str]]) -> list[RowInfo]:
    out: list[RowInfo] = []
    for row in rows:
        if row.get("status") != "ok":
            continue
        slug = _derive_slug(row)
        out.append(
            RowInfo(
                row=row,
                slug=slug,
                model=_model_from_slug(slug),
                detail=_detail_from_slug(slug),
                a2a=_a2a_from_slug(slug),
                agent=_agent_from_cell(row.get("cell", "")),
                level=row.get("level", ""),
            )
        )
    return out


def _load_rows(path: Path, sheet_name: str | None = None) -> list[RowInfo]:
    return _row_infos_from_raw(_load_raw_rows(path, sheet_name=sheet_name))


def _derive_slug(row: dict[str, str]) -> str:
    existing = row.get("_slug", "").strip()
    if existing:
        return existing
    return _slug_from_cell_dir(row.get("cell_dir", ""))


def _build_summary_sheet(
    wb,
    raw_rows: list[dict[str, str]],
    *,
    sheet_name: str,
) -> None:
    if sheet_name in wb.sheetnames:
        old = wb[sheet_name]
        index = wb.worksheets.index(old)
        wb.remove(old)
        ws = wb.create_sheet(sheet_name, index)
    else:
        ws = wb.create_sheet(sheet_name, 0)
        wb._sheets.remove(ws)
        wb._sheets.insert(0, ws)

    headers: list[str] = []
    seen: set[str] = set()
    for row in raw_rows:
        for key in row:
            if key and key not in seen:
                headers.append(key)
                seen.add(key)
    if not headers:
        headers = ["_slug"]

    first_header = headers[0]
    remaining_headers = [
        header for header in headers[1:]
        if header not in {"A2A", "LLM"}
    ]
    out_headers = [first_header, "A2A", "LLM", *remaining_headers]

    for col, header in enumerate(out_headers, start=1):
        cell = ws.cell(1, col, header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_index, row in enumerate(raw_rows, start=2):
        slug = _derive_slug(row)
        derived = {
            "A2A": _display_a2a(_a2a_from_slug(slug)),
            "LLM": _model_from_slug(slug),
        }
        for col, header in enumerate(out_headers, start=1):
            value = derived.get(header, row.get(header))
            ws.cell(row_index, col, _excel_value(value))

    ws.freeze_panes = "D2"
    ws.auto_filter.ref = ws.dimensions
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        header = ws.cell(1, col).value
        if header in {"cell_dir", "fos_path", "pc2_path"}:
            ws.column_dimensions[letter].width = 48
        elif header in {"cell", "scenario"}:
            ws.column_dimensions[letter].width = 44
        elif header in {"A2A", "LLM"}:
            ws.column_dimensions[letter].width = 12
        else:
            ws.column_dimensions[letter].width = 18


def _metric_value(
    rows: list[RowInfo],
    *,
    model: str,
    agent: str,
    detail: bool,
    a2a: bool,
    level: str,
    metric: str,
    agg: str,
) -> float | None:
    vals = []
    for info in rows:
        if info.model != model:
            continue
        if info.agent != agent:
            continue
        if info.detail is not detail:
            continue
        if info.a2a is not a2a:
            continue
        if info.level != level:
            continue
        val = _float_or_none(info.row.get(metric))
        if val is not None:
            vals.append(val)
    return _aggregate(vals, agg)


def _write_metric_headers(ws, row: int, start_col: int, group_title: str) -> None:
    end_col = start_col + len(METRICS) - 1
    ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)
    cell = ws.cell(row, start_col, group_title)
    cell.font = Font(bold=True)
    cell.alignment = Alignment(horizontal="center")
    for offset, metric in enumerate(METRICS):
        h = ws.cell(row + 1, start_col + offset, metric)
        h.font = Font(bold=True)
        h.alignment = Alignment(horizontal="center", wrap_text=True)


def _write_group_rows(
    ws,
    rows: list[RowInfo],
    *,
    start_row: int,
    label: str,
    model: str,
    a2a: bool,
    level_groups: list[tuple[str, str, int]],
    agg: str,
) -> int:
    current = start_row
    for agent_index, agent in enumerate(AGENTS):
        for detail_index, detail in enumerate((False, True)):
            ws.cell(current, 1, label if agent_index == 0 and detail_index == 0 else None)
            ws.cell(current, 2, agent if detail_index == 0 else None)
            ws.cell(
                current,
                3,
                "Zero Context (DETAIL = FALSE)"
                if detail is False
                else "Human Expert (DETAIL = TRUE)",
            )
            for _, level, col in level_groups:
                for metric_offset, metric in enumerate(METRICS):
                    value = _metric_value(
                        rows,
                        model=model,
                        agent=agent,
                        detail=detail,
                        a2a=a2a,
                        level=level,
                        metric=metric,
                        agg=agg,
                    )
                    cell = ws.cell(current, col + metric_offset, value)
                    if value is not None:
                        cell.number_format = "0.0000"
            current += 1
    return current


def _style_sheet(ws) -> None:
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if cell.row in {3, 4, 26, 27, 84, 85}:
                cell.fill = header_fill
                cell.font = Font(bold=True)
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        if col == 1:
            ws.column_dimensions[letter].width = 18
        elif col == 2:
            ws.column_dimensions[letter].width = 22
        elif col == 3:
            ws.column_dimensions[letter].width = 28
        else:
            ws.column_dimensions[letter].width = 16
    ws.freeze_panes = "D5"


def _build_paper_tables_sheet(wb, rows: list[RowInfo], agg: str, sheet_name: str) -> None:
    if sheet_name in wb.sheetnames:
        old = wb[sheet_name]
        index = wb.worksheets.index(old)
        wb.remove(old)
        ws = wb.create_sheet(sheet_name, index)
    else:
        ws = wb.create_sheet(sheet_name, 1 if wb.worksheets else 0)

    # Table 1: Qwen, A2A off, all levels side by side.
    ws.cell(3, 2, "Table 1. Qwen A2A=OFF")
    table1_groups = [
        ("L1", LEVEL_LABELS["L1"], 4),
        ("L2", LEVEL_LABELS["L2"], 9),
        ("L3", LEVEL_LABELS["L3"], 14),
    ]
    for title, _, col in table1_groups:
        _write_metric_headers(ws, 3, col, title)
    _write_group_rows(
        ws,
        rows,
        start_row=6,
        label="Qwen A2A=OFF",
        model="Qwen",
        a2a=False,
        level_groups=table1_groups,
        agg=agg,
    )

    # Table 2: L2, A2A off, all model families.
    ws.cell(27, 2, "Table 2")
    _write_metric_headers(ws, 26, 4, "L2")
    current = 28
    for model in ("Qwen", "DeepSeek", "GPT"):
        current = _write_group_rows(
            ws,
            rows,
            start_row=current,
            label=f"{model} A2A=OFF",
            model=model,
            a2a=False,
            level_groups=[("L2", LEVEL_LABELS["L2"], 4)],
            agg=agg,
        )

    # Table 3: Qwen L2, A2A off vs on.
    ws.cell(85, 2, "Table 3")
    table3_groups = [
        ("L2 A2A=OFF", LEVEL_LABELS["L2"], 4),
        ("L2 A2A=ON", LEVEL_LABELS["L2"], 9),
    ]
    for title, _, col in table3_groups:
        _write_metric_headers(ws, 84, col, title)
    # Fill OFF and ON separately but keep one pair of agent/detail labels.
    current = 86
    for agent_index, agent in enumerate(AGENTS):
        for detail_index, detail in enumerate((False, True)):
            ws.cell(current, 1, "Qwen" if agent_index == 0 and detail_index == 0 else None)
            ws.cell(current, 2, agent if detail_index == 0 else None)
            ws.cell(
                current,
                3,
                "Zero Context (DETAIL = FALSE)"
                if detail is False
                else "Human Expert (DETAIL = TRUE)",
            )
            for a2a, col in ((False, 4), (True, 9)):
                for metric_offset, metric in enumerate(METRICS):
                    value = _metric_value(
                        rows,
                        model="Qwen",
                        agent=agent,
                        detail=detail,
                        a2a=a2a,
                        level=LEVEL_LABELS["L2"],
                        metric=metric,
                        agg=agg,
                    )
                    cell = ws.cell(current, col + metric_offset, value)
                    if value is not None:
                        cell.number_format = "0.0000"
            current += 1

    note_row = current + 2
    ws.cell(note_row, 2, f"Generated from summary table; aggregate={agg}; status=ok only.")
    ws.cell(note_row, 2).font = Font(italic=True, color="666666")
    _style_sheet(ws)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary-csv",
        "--summary",
        dest="summary",
        type=Path,
        default=Path("rebatch_outputs/reeval_v4/summary_v3.csv"),
        help="Input summary table: .csv, .xlsx, or .xlsm.",
    )
    parser.add_argument(
        "--summary-sheet",
        default=None,
        help="Sheet name to read when --summary is an Excel workbook. Defaults to the first sheet.",
    )
    parser.add_argument("--template-xlsx", type=Path, default=None)
    parser.add_argument("--output-xlsx", type=Path, required=True)
    parser.add_argument("--sheet-name", default="Paper Tables")
    parser.add_argument("--summary-output-sheet", default=None)
    parser.add_argument("--agg", choices=("mean", "median"), default="mean")
    args = parser.parse_args()

    raw_rows = _load_raw_rows(args.summary, sheet_name=args.summary_sheet)
    rows = _row_infos_from_raw(raw_rows)
    if args.template_xlsx is not None:
        wb = load_workbook(args.template_xlsx)
    else:
        wb = Workbook()
        wb.active.title = "summary"

    summary_sheet_name = args.summary_output_sheet
    if summary_sheet_name is None:
        if args.template_xlsx is not None and wb.worksheets:
            first_title = wb.worksheets[0].title
            summary_sheet_name = (
                first_title if first_title != args.sheet_name else "summary"
            )
        else:
            summary_sheet_name = (
                args.summary_sheet
                or ("summary_v3" if args.summary.suffix.lower() == ".csv" else "summary")
            )
    _build_summary_sheet(wb, raw_rows, sheet_name=summary_sheet_name)
    _build_paper_tables_sheet(wb, rows, args.agg, args.sheet_name)
    args.output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.output_xlsx)
    print(
        f"Wrote {args.output_xlsx} "
        f"({len(raw_rows)} summary rows, {len(rows)} ok rows, aggregate={args.agg})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
