"""Generate paper result tables from rebatch summary_v2 CSVs.

The default inputs match the current v2 rebatch layout and emit data tables in
experiment order as CSV plus one combined Markdown file:

E1. Retrieval/skill-context comparison.
E1b. Zero-context and expert-instruct baselines on the E1 20 scenarios.
E2. Held-out 70 comparison.
E3a. DeepSeek agent-family robustness.
E3b. Qwen agent-family robustness.
E4a. DeepSeek A2A-on comparison against the matched A2A-off baseline.
E4b. Qwen A2A-on comparison against the matched A2A-off baseline.
E5a. DeepSeek L1 splits.
E5b. DeepSeek L2 splits.
E5c. Qwen vLLM L1/L2 splits.
E6a. Model comparison on the common zero-context 20-scenario subset.
E6b. Model comparison on the common expert-instruct 20-scenario subset.

Each table includes model type/size where applicable and the usage columns
requested for paper cost/runtime reporting.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_INPUTS = {
    "deepseek_e1": Path("rebatch_outputs/v2/deepseek_e1/summary_v2.csv"),
    "qwen_e1": Path("rebatch_outputs/v2/qwen_e1/summary_v2.csv"),
    "deepseek_e2": Path("rebatch_outputs/v2/deepseek_e2/summary_v2.csv"),
    "qwen_e2": Path("rebatch_outputs/v2/qwen_e2/summary_v2.csv"),
    "deepseek_e3": Path("rebatch_outputs/v2/deepseek_e3/summary_v2.csv"),
    "qwen_e3": Path("rebatch_outputs/v2/qwen_e3/summary_v2.csv"),
    "deepseek_e4": Path("rebatch_outputs/v2/deepseek_e4/summary_v2_deepseek_e4.csv"),
    "qwen_e4": Path("rebatch_outputs/v2/qwen_e4/summary_v2_qwen_e4.csv"),
    "deepseek_e5": Path("rebatch_outputs/v2/deepseek_e5/summary_v2.csv"),
    "qwen_e5": Path("rebatch_outputs/v2/qwen_e5/summary_v2.csv"),
    "vllm_e6": Path("rebatch_outputs/v2/deepseek_e6/summary_v2.csv"),
    "vllm_l1l2": Path("rebatch_outputs/v2/vllm_l1l2/summary_v2.csv"),
}

DETAIL_LABELS = {
    "false": "Zero Context",
    "kwoo": "LLM-as-an-Expert / Kwoo",
    "l2_textsim_grouped_differ": "Skills Library",
    "true": "Expert Instruct",
    "l2_human_differ": "L2 Human Differ",
    "l2_textsim_grouped": "L2 TextSim Grouped",
    "l2_pathsim_grouped_differ": "L2 PathSim Grouped",
    "l3_textsim_differ": "L3 TextSim Differ",
    "l3_pathsim_differ": "L3 PathSim Differ",
    "l3_pathsim_same": "L3 PathSim Same",
}

DETAIL_LABELS_E1 = {
    **DETAIL_LABELS,
    "l2_textsim_grouped_differ": "L2 TextSim Grouped",
}

DETAIL_ORDER_E1 = [
    "l2_human_differ",
    "l2_textsim_grouped_differ",
    "l2_pathsim_grouped_differ",
    "l3_textsim_differ",
    "l3_pathsim_differ",
    "l3_pathsim_same",
]

E1_LIBRARY_SCENARIOS = {
    "scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery",
    "scenario_full_season_hb_coldspring_planting_window_heihe50",
    "scenario_full_season_hb_wetcold_high_residue_establishment",
    "scenario_full_season_heinong84_staggered_planting",
    "scenario_full_season_hb_fertilizer_quota_edge_lowfertility",
    "scenario_full_season_hb_insect_after_fungicide_budget_conflict",
    "scenario_full_season_hb_two_dry_patches_one_irrigation",
    "scenario_full_season_hb_wetjune_shortwindow_trafficability",
    "scenario_full_season_hb_storage_capacity_limit_batching",
    "scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence",
    "scenario_full_season_hb_heinong58_water_chemical_priority_under_dual_stress",
    "scenario_full_season_hb_r5_leaf_feeder_defoliation",
    "scenario_full_season_hb_heinong60_highdensity_fertigation_irrigation_water_budget",
    "scenario_full_season_hb_lowcarbon_batch_operations_wetdisease",
    "scenario_full_season_hb_laterain_insect_risk",
    "scenario_full_season_hb_planter_skip_rows_stand_gap",
    "scenario_full_season_hb_high_weed_seedbank_mechanical_only_baseline",
    "scenario_full_season_hb_wetjune_disease_recheck_after_fungicide",
    "scenario_full_season_hb_potassium_deficit_dry_podfill_interaction",
    "scenario_full_season_hb_disease_then_drought_recovery_tradeoff",
}

DETAIL_ORDER_MAIN = [
    "false",
    "kwoo",
    "l2_textsim_grouped_differ",
    "true",
]

AGENT_LABELS = {
    "baseline_react": "ReAct",
    "rewoo_modular": "ReWOO",
    "reflective_memory": "Reflexion",
    "critic_refiner": "CRITIC",
    "graph_memory": "GoT",
    "multi_specialist": "AutoGen",
    "tree_search": "LATS",
    "planner_executor": "Plan&Act",
    "adaptive_verifier": "MMRL",
}

AGENT_ORDER = [
    "baseline_react",
    "rewoo_modular",
    "reflective_memory",
    "critic_refiner",
    "graph_memory",
    "multi_specialist",
    "tree_search",
    "planner_executor",
    "adaptive_verifier",
]


@dataclass(frozen=True)
class Source:
    key: str
    model: str
    path: Path


def _float(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        text = str(row.get(key, "")).strip()
        if not text:
            continue
        try:
            return float(text)
        except ValueError:
            continue
    return None


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _mean(values: Iterable[float | None]) -> float | None:
    vals = [value for value in values if value is not None]
    return statistics.mean(vals) if vals else None


def _fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return ""
    if suffix:
        return f"{value:.2f}{suffix}"
    return f"{value:.2f}"


def _fmt_int(value: int | None) -> str:
    return "" if value is None else str(value)


def _load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing summary CSV: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _ok_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if str(row.get("status", "")).strip() == "ok"]


def _detail_key(row: dict[str, str]) -> str:
    return str(
        row.get("detailed_briefing")
        or row.get("detail_status")
        or row.get("detail")
        or ""
    ).strip()


def _detail_label(detail: str, labels: dict[str, str] | None = None) -> str:
    lookup = labels or DETAIL_LABELS
    return lookup.get(detail, detail or "unknown")


def _model_variant_suffix(row: dict[str, str]) -> str:
    marker_text = " ".join(
        str(row.get(key) or "")
        for key in ("cell_dir", "cell_output_rel", "fos_path")
    )
    if "assistant_false" in marker_text:
        return " (assistant false)"
    return ""


def _model_type(rows: list[dict[str, str]], model: str) -> str:
    values = [
        (
            str(row.get("model_type_size") or row.get("model") or "").strip()
            + _model_variant_suffix(row)
        )
        for row in rows
        if str(row.get("model_type_size") or row.get("model") or "").strip()
    ]
    if not values:
        if model == "DeepSeek API":
            return "deepseek-chat (DeepSeek-V4-Flash, non-thinking mode)"
        if model == "Qwen API":
            return "qwen3.6-flash-2026-04-16 (Qwen3.6-35B-A3B-FP8)"
        return ""
    value = Counter(values).most_common(1)[0][0]
    if model == "DeepSeek API" and value == "deepseek-chat":
        return "deepseek-chat (DeepSeek-V4-Flash, non-thinking mode)"
    if model == "Qwen API" and value == "qwen3.6-flash-2026-04-16":
        return "qwen3.6-flash-2026-04-16 (Qwen3.6-35B-A3B-FP8)"
    return value


def _aggregate(rows: list[dict[str, str]]) -> dict[str, str]:
    calls = [_float(row, "llm_calls_with_usage") for row in rows]
    total_calls = sum(value for value in calls if value is not None)
    total_tokens = [_float(row, "total_tokens_per_scenario") for row in rows]
    runtime_scenario = [_float(row, "runtime_s_per_scenario") for row in rows]
    if total_calls > 0:
        weighted_avg_tokens = sum(
            (_float(row, "avg_total_tokens_per_agent_call") or 0.0)
            * (_float(row, "llm_calls_with_usage") or 0.0)
            for row in rows
        ) / total_calls
        weighted_avg_runtime = sum(
            (_float(row, "avg_runtime_s_per_agent_call") or 0.0)
            * (_float(row, "llm_calls_with_usage") or 0.0)
            for row in rows
        ) / total_calls
    else:
        weighted_avg_tokens = _mean(
            _float(row, "avg_total_tokens_per_agent_call") for row in rows
        )
        weighted_avg_runtime = _mean(
            _float(row, "avg_runtime_s_per_agent_call") for row in rows
        )
    return {
        "N": _fmt_int(len(rows)),
        "BFCL ↑": _fmt(_mean(_float(row, "bfcl_success(%)") for row in rows)),
        "KTC ↑": _fmt(_mean(_float(row, "ktc(%)") for row in rows)),
        "KTC Score ↑": _fmt(_mean(_float(row, "ktc_score(%)") for row in rows)),
        "KTC Adj. ↑": _fmt(_mean(_float(row, "ktc_adjusted(%)") for row in rows)),
        "Coverage ↑": _fmt(_mean(_float(row, "coverage(%)") for row in rows)),
        "Path Corr. ↑": _fmt(_mean(_float(row, "path_correctness(%)") for row in rows)),
        "FARM-FOS ↑": _fmt(_mean(_float(row, "farm_fos(%)", "fos(%)") for row in rows)),
        "FARM-FOS-v2 ↑": _fmt(
            _mean(_float(row, "farm_fos_v2_total(%)") for row in rows)
        ),
        "Agent Biological Yield kg": _fmt(
            _mean(_float(row, "agent_biological_kg") for row in rows)
        ),
        "Oracle Biological Yield kg": _fmt(
            _mean(_float(row, "oracle_biological_kg") for row in rows)
        ),
        "Agent Recovered Yield kg": _fmt(
            _mean(_float(row, "agent_recovered_yield_kg") for row in rows)
        ),
        "Oracle Recovered Yield kg": _fmt(
            _mean(_float(row, "oracle_recovered_yield_kg") for row in rows)
        ),
        "Yield Loss ↓": _fmt(_mean(_float(row, "yield_loss(%)") for row in rows)),
        "Recovered Loss ↓": _fmt(
            _mean(_float(row, "recovered_yield_loss(%)") for row in rows)
        ),
        "Avg. Total Tokens / Agent Call": _fmt(weighted_avg_tokens),
        "Avg. Total Tokens / Scenario": _fmt(_mean(total_tokens)),
        "Avg. Runtime / Agent Call": _fmt(weighted_avg_runtime),
        "Avg. Runtime / Scenario": _fmt(_mean(runtime_scenario)),
    }


def _e5_level(row: dict[str, str]) -> str:
    scenario = str(row.get("scenario") or "").strip()
    if scenario.startswith("scenario_l1_"):
        return "L1"
    if scenario.startswith("scenario_l2_"):
        return "L2"
    return "unknown"


def _e5_agent_field_value(row: dict[str, str]) -> float | None:
    if _truthy(row.get("expects_agent_harvest")):
        return _float(row, "agent_recovered_yield_kg")
    return _float(row, "agent_biological_kg")


def _e5_oracle_field_value(row: dict[str, str]) -> float | None:
    if _truthy(row.get("expects_agent_harvest")):
        return _float(row, "oracle_recovered_yield_kg")
    return _float(row, "oracle_biological_kg")


def _e5_field_loss(row: dict[str, str]) -> float | None:
    agent = _e5_agent_field_value(row)
    oracle = _e5_oracle_field_value(row)
    if agent is None or oracle is None or oracle == 0:
        return None
    return max(0.0, (oracle - agent) / oracle * 100.0)


def _aggregate_e5(rows: list[dict[str, str]]) -> dict[str, str]:
    base = _aggregate(rows)
    harvest_rows = [row for row in rows if _truthy(row.get("expects_agent_harvest"))]
    field_rows = [row for row in rows if not _truthy(row.get("expects_agent_harvest"))]
    base.update(
        {
            "Harvest-Recovery Rows": _fmt_int(len(harvest_rows)),
            "Field Rows": _fmt_int(len(field_rows)),
            "Agent Field kg (mixed)": _fmt(_mean(_e5_agent_field_value(row) for row in rows)),
            "Oracle Field kg (mixed)": _fmt(
                _mean(_e5_oracle_field_value(row) for row in rows)
            ),
            "Field Loss ↓ (mixed)": _fmt(_mean(_e5_field_loss(row) for row in rows)),
        }
    )
    return base


def _scenario_set(rows: Iterable[dict[str, str]], detail: str) -> set[str]:
    return {
        str(row.get("scenario", "")).strip()
        for row in rows
        if _detail_key(row) == detail and str(row.get("scenario", "")).strip()
    }


def _filter(
    rows: Iterable[dict[str, str]],
    *,
    detail: str | None = None,
    family: str | None = None,
    scenarios: set[str] | None = None,
    l3_pool: str | None = None,
) -> list[dict[str, str]]:
    out = []
    for row in rows:
        if detail is not None and _detail_key(row) != detail:
            continue
        if family is not None and row.get("agent_family") != family:
            continue
        if scenarios is not None and row.get("scenario") not in scenarios:
            continue
        if l3_pool is not None and row.get("l3_pool") != l3_pool:
            continue
        out.append(row)
    return out


def _make_e1_table(model_rows: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for model in ("DeepSeek API", "Qwen API"):
        source_rows = model_rows[model]
        for detail in DETAIL_ORDER_E1:
            group = _filter(source_rows, detail=detail)
            if not group:
                continue
            rows.append(
                {
                    "Model": model,
                    "Exact Model Type / Size": _model_type(group, model),
                    "Context": _detail_label(detail, DETAIL_LABELS_E1),
                    **_aggregate(group),
                }
            )
    return rows


def _make_e1_baseline_contexts_table(
    model_rows: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for model in ("DeepSeek API", "Qwen API"):
        source_rows = model_rows[model]
        for detail in ("false", "true"):
            group = _filter(
                source_rows,
                detail=detail,
                family="baseline_react",
                scenarios=E1_LIBRARY_SCENARIOS,
            )
            if not group:
                continue
            rows.append(
                {
                    "Model": model,
                    "Exact Model Type / Size": _model_type(group, model),
                    "Context": _detail_label(detail),
                    **_aggregate(group),
                }
            )
    return rows


def _make_e2_table(model_rows: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for model in ("DeepSeek API", "Qwen API"):
        source_rows = model_rows[model]
        for detail in DETAIL_ORDER_MAIN:
            group = _filter(source_rows, detail=detail, l3_pool="70")
            if not group:
                continue
            rows.append(
                {
                    "Model": model,
                    "Exact Model Type / Size": _model_type(group, model),
                    "Context": _detail_label(detail),
                    **_aggregate(group),
                }
            )
    return rows


def _make_e3_table(
    source_rows: list[dict[str, str]],
    model: str,
    details: list[str],
    baseline_rows: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for detail in details:
        detail_rows = _filter(source_rows, detail=detail)
        if not detail_rows:
            continue
        average_rows = list(detail_rows)
        detail_scenarios = {
            str(row.get("scenario", "")).strip()
            for row in detail_rows
            if str(row.get("scenario", "")).strip()
        }
        if baseline_rows:
            baseline_group = _filter(
                baseline_rows,
                detail=detail,
                family="baseline_react",
                scenarios=detail_scenarios,
            )
            if baseline_group:
                average_rows.extend(baseline_group)
                rows.append(
                    {
                        "Model": model,
                        "Exact Model Type / Size": _model_type(baseline_group, model),
                        "Context": _detail_label(detail),
                        "Controller": "ReAct",
                        **_aggregate(baseline_group),
                    }
                )
        for family in AGENT_ORDER:
            group = _filter(detail_rows, family=family)
            if not group:
                continue
            rows.append(
                {
                    "Model": model,
                    "Exact Model Type / Size": _model_type(group, model),
                    "Context": _detail_label(detail),
                    "Controller": AGENT_LABELS.get(family, family),
                    **_aggregate(group),
                }
            )
        rows.append(
            {
                "Model": model,
                "Exact Model Type / Size": _model_type(average_rows, model),
                "Context": _detail_label(detail),
                "Controller": "Average",
                **_aggregate(average_rows),
            }
        )
    return rows


def _make_model_comparison_table(
    deepseek_rows: list[dict[str, str]],
    qwen_rows: list[dict[str, str]],
    vllm_rows: list[dict[str, str]],
    detail: str,
) -> list[dict[str, str]]:
    scenario_sets = [
        _scenario_set(deepseek_rows, detail),
        _scenario_set(qwen_rows, detail),
        _scenario_set(vllm_rows, detail),
    ]
    common = set.intersection(*scenario_sets) if all(scenario_sets) else set()
    sources = [
        ("DeepSeek API", deepseek_rows),
        ("Qwen API", qwen_rows),
        ("Qwen vLLM", vllm_rows),
    ]
    out = []
    for model, source_rows in sources:
        group = _filter(
            source_rows,
            detail=detail,
            family="baseline_react",
            scenarios=common or None,
        )
        if not group:
            group = _filter(source_rows, detail=detail, family="baseline_react")
        by_model_type: dict[str, list[dict[str, str]]] = {}
        for row in group:
            model_type = _model_type([row], model)
            by_model_type.setdefault(model_type, []).append(row)
        for model_type, model_group in by_model_type.items():
            out.append(
                {
                    "Model": model,
                    "Exact Model Type / Size": model_type,
                    "Context": _detail_label(detail),
                    **_aggregate(model_group),
                }
            )
    return out


def _make_a2a_comparison_table(
    off_rows: list[dict[str, str]],
    on_rows: list[dict[str, str]],
    model: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for a2a_label in ("A2A On", "A2A Off"):
        average_rows: list[dict[str, str]] = []
        for detail in DETAIL_ORDER_MAIN:
            on_group = _filter(on_rows, detail=detail, family="baseline_react")
            if not on_group:
                continue
            scenarios = {
                str(row.get("scenario", "")).strip()
                for row in on_group
                if str(row.get("scenario", "")).strip()
            }
            if a2a_label == "A2A On":
                group = on_group
            else:
                group = _filter(
                    off_rows,
                    detail=detail,
                    family="baseline_react",
                    scenarios=scenarios,
                    l3_pool="70",
                )
            if not group:
                continue
            average_rows.extend(group)
            rows.append(
                {
                    "Model": model,
                    "Exact Model Type / Size": _model_type(group, model),
                    "Context": _detail_label(detail),
                    "A2A": a2a_label,
                    **_aggregate(group),
                }
            )
        if average_rows:
            rows.append(
                {
                    "Model": model,
                    "Exact Model Type / Size": _model_type(
                        average_rows, model
                    ),
                    "Context": "Average",
                    "A2A": a2a_label,
                    **_aggregate(average_rows),
                }
            )
    return rows


def _make_e5_split_table_for_model(
    source_rows: list[dict[str, str]],
    level: str,
    model: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    average_rows: list[dict[str, str]] = []
    for detail in DETAIL_ORDER_MAIN:
        group = [
            row
            for row in _filter(source_rows, detail=detail, family="baseline_react")
            if _e5_level(row) == level
        ]
        if not group:
            continue
        average_rows.extend(group)
        rows.append(
            {
                "Model": model,
                "Exact Model Type / Size": _model_type(group, model),
                "Split Level": level,
                "Context": _detail_label(detail),
                "Field Metric Rule": (
                    "recovered yield if expects_agent_harvest=True; "
                    "biological yield otherwise"
                ),
                **_aggregate_e5(group),
            }
        )
    if average_rows:
        rows.append(
            {
                "Model": model,
                "Exact Model Type / Size": _model_type(average_rows, model),
                "Split Level": level,
                "Context": "Average",
                "Field Metric Rule": (
                    "recovered yield if expects_agent_harvest=True; "
                    "biological yield otherwise"
                ),
                **_aggregate_e5(average_rows),
            }
        )
    return rows


def _make_e5_split_table(
    source_rows_by_model: dict[str, list[dict[str, str]]],
    level: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for model, source_rows in source_rows_by_model.items():
        rows.extend(_make_e5_split_table_for_model(source_rows, level, model))
    return rows


def _make_vllm_l1l2_split_table(source_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for model_type in sorted(
        {
            _model_type([row], "Qwen vLLM")
            for row in source_rows
            if str(row.get("model_type_size") or "").strip()
        }
    ):
        model_rows = [
            row
            for row in source_rows
            if _model_type([row], "Qwen vLLM") == model_type
        ]
        for detail in DETAIL_ORDER_MAIN:
            detail_rows = _filter(model_rows, detail=detail, family="baseline_react")
            if not detail_rows:
                continue
            average_rows: list[dict[str, str]] = []
            for level in ("L1", "L2"):
                level_rows = [row for row in detail_rows if _e5_level(row) == level]
                if not level_rows:
                    continue
                average_rows.extend(level_rows)
                rows.append(
                    {
                        "Model": "Qwen vLLM",
                        "Exact Model Type / Size": model_type,
                        "Split Level": level,
                        "Context": _detail_label(detail),
                        "Field Metric Rule": (
                            "recovered yield if expects_agent_harvest=True; "
                            "biological yield otherwise"
                        ),
                        **_aggregate_e5(level_rows),
                    }
                )
            if average_rows:
                rows.append(
                    {
                        "Model": "Qwen vLLM",
                        "Exact Model Type / Size": model_type,
                        "Split Level": "Average",
                        "Context": _detail_label(detail),
                        "Field Metric Rule": (
                            "recovered yield if expects_agent_harvest=True; "
                            "biological yield otherwise"
                        ),
                        **_aggregate_e5(average_rows),
                    }
                )
    return rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        writer.writerows(rows)


def _markdown_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "_No rows._\n"
    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return "\n".join(lines) + "\n"


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
        default=Path("paper_tables/results_tables"),
        help="Directory for per-table CSV files and combined Markdown.",
    )
    args = parser.parse_args()

    paths = _parse_inputs(args.input)
    loaded = {key: _ok_rows(_load_rows(path)) for key, path in paths.items()}

    e1_rows = {
        "DeepSeek API": loaded["deepseek_e1"],
        "Qwen API": loaded["qwen_e1"],
    }
    e2_rows = {
        "DeepSeek API": loaded["deepseek_e2"],
        "Qwen API": loaded["qwen_e2"],
    }

    tables = [
        (
            "table_e1_retrieval_skills",
            "Table E1. Retrieval/Skill Context",
            _make_e1_table(e1_rows),
        ),
        (
            "table_e1b_baseline_contexts",
            "Table E1b. Baseline Contexts On E1 20",
            _make_e1_baseline_contexts_table(e2_rows),
        ),
        (
            "table_e2_heldout70",
            "Table E2. Held-out 70",
            _make_e2_table(e2_rows),
        ),
        (
            "table_e3a_deepseek_agent_families",
            "Table E3a. DeepSeek Agent-Family Robustness",
            _make_e3_table(
                loaded["deepseek_e3"],
                "DeepSeek API",
                DETAIL_ORDER_MAIN,
                baseline_rows=loaded["deepseek_e2"],
            ),
        ),
        (
            "table_e3b_qwen_agent_families",
            "Table E3b. Qwen Agent-Family Robustness",
            _make_e3_table(
                loaded["qwen_e3"],
                "Qwen API",
                DETAIL_ORDER_MAIN,
                baseline_rows=loaded["qwen_e2"],
            ),
        ),
        (
            "table_e4a_deepseek_a2a_comparison",
            "Table E4a. DeepSeek A2A On vs Off",
            _make_a2a_comparison_table(
                loaded["deepseek_e2"],
                loaded["deepseek_e4"],
                "DeepSeek API",
            ),
        ),
        (
            "table_e4b_qwen_a2a_comparison",
            "Table E4b. Qwen A2A On vs Off",
            _make_a2a_comparison_table(
                loaded["qwen_e2"],
                loaded["qwen_e4"],
                "Qwen API",
            ),
        ),
        (
            "table_e5a_l1_splits",
            "Table E5a. L1 Splits",
            _make_e5_split_table(
                {
                    "DeepSeek API": loaded["deepseek_e5"],
                    "Qwen API": loaded["qwen_e5"],
                },
                "L1",
            ),
        ),
        (
            "table_e5b_l2_splits",
            "Table E5b. L2 Splits",
            _make_e5_split_table(
                {
                    "DeepSeek API": loaded["deepseek_e5"],
                    "Qwen API": loaded["qwen_e5"],
                },
                "L2",
            ),
        ),
        (
            "table_e5c_vllm_l1_l2_splits",
            "Table E5c. Qwen vLLM L1/L2 Splits",
            _make_vllm_l1l2_split_table(loaded["vllm_l1l2"]),
        ),
        (
            "table_e6a_model_comparison_zero_context",
            "Table E6a. Model Comparison, Zero Context",
            _make_model_comparison_table(
                loaded["deepseek_e2"],
                loaded["qwen_e2"],
                loaded["vllm_e6"],
                "false",
            ),
        ),
        (
            "table_e6b_model_comparison_expert_instruct",
            "Table E6b. Model Comparison, Expert Instruct",
            _make_model_comparison_table(
                loaded["deepseek_e2"],
                loaded["qwen_e2"],
                loaded["vllm_e6"],
                "true",
            ),
        ),
    ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for old_csv in args.out_dir.glob("table*.csv"):
        old_csv.unlink()
    markdown_parts = []
    for stem, title, rows in tables:
        _write_csv(args.out_dir / f"{stem}.csv", rows)
        markdown_parts.append(f"## {title}\n\n{_markdown_table(rows)}")
    (args.out_dir / "results_tables.md").write_text(
        "\n".join(markdown_parts), encoding="utf-8"
    )

    print(f"Wrote {len(tables)} tables to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
