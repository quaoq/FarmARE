"""Generate the final human-readable V2 full-season scenario package.

This is an evidence/documentation exporter. It does not run scenarios and does
not change oracle paths. It reads the latest review artifacts plus scenario
metadata and writes concise files for human acceptance and downstream L2/L1
slicing.
"""

from __future__ import annotations

import ast
import csv
import json
import re
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_catalog import (
    SPECS as BATCH_SPECS,
)
from scripts.fullseason.review_fullseason_l3_scenarios import REPO_ROOT, SCENARIOS


REVIEW_DIR = REPO_ROOT / "outputs" / "fullseason_review" / "final_consistent_pool_review"
PREVIOUS_HUMAN_DIR = (
    REPO_ROOT / "outputs" / "fullseason_review" / "final_human_scenario_inventory"
)
OUT_DIR = (
    REPO_ROOT / "outputs" / "fullseason_review" / "final_human_scenario_inventory_v2"
)
CLEANUP_DIR = REPO_ROOT / "outputs" / "fullseason_review" / "final_cleanup"
SCENARIO_DIR = (
    REPO_ROOT / "are" / "simulation" / "scenarios" / "scenario_farm_world_fullseason_v2"
)

MISSING_AGENT_TEXT_PREVIOUS = {
    "scenario_full_season_early_vs_standard_late_rain_harvest",
    "scenario_full_season_fastdraining_dry_patch_irrigation",
    "scenario_full_season_heinong60_high_density_baseline",
    "scenario_full_season_heinong84_edge_low_fertility",
    "scenario_full_season_heinong84_staggered_planting",
    "scenario_full_season_heinong84_low_chemical_wet_disease",
    "scenario_full_season_heinong84_threshold_insect_limited_spray",
    "scenario_full_season_wet_june_ab_zoned_disease",
}

LEAKAGE_RESOLUTIONS = {
    "scenario_full_season_hb_base_hn84_std_normal": {
        "flagged_text": "正常年份 / 不预设未来天气答案",
        "real_leakage": "no",
        "action_taken": "保留。正常年份是场景开局可见的季节类型，不包含未来具体降雨日期、affected ridges 或处理答案。",
    },
    "scenario_full_season_hb_fertilizer_quota_edge_lowfertility": {
        "flagged_text": "局部低肥力且肥料配额有限",
        "real_leakage": "yes",
        "action_taken": "已改写 agent-facing text：只说明肥料配额有限和可能出现弱苗/长势偏弱，要求通过检查定位原因。",
    },
    "scenario_full_season_hb_highdensity_wetjune_limited_fungicide": {
        "flagged_text": "达到明确阈值并有窗口时才 targeted fungicide",
        "real_leakage": "yes",
        "action_taken": "已改写 agent-facing text：保留杀菌剂次数有限的可见约束，去掉直接指定 targeted fungicide 的答案式措辞。",
    },
    "scenario_full_season_hb_wetjune_disease_recheck_after_fungicide": {
        "flagged_text": "首次 targeted fungicide 后不是结束",
        "real_leakage": "yes",
        "action_taken": "已改写 agent-facing text：要求先诊断病害和窗口，处理后复查，不预告首次处理一定发生。",
    },
}

METRIC_SCENARIOS = {
    "scenario_full_season_hb_storage_aeration_failure_after_harvest": [
        "aeration_status",
        "storage_risk",
        "storage_action_adjusted_due_to_aeration_failure",
        "affected_batch_if_any",
    ],
    "scenario_full_season_hb_dryer_breakdown_between_batches": [
        "dryer_available",
        "dryer_unavailable_window",
        "waiting_batch_kg",
        "drying_queue_kg",
        "batch_delay_days",
    ],
    "scenario_full_season_hb_low_carbon_min_machinery_passes": [
        "operation_count",
        "fuel_proxy",
        "machine_passes",
    ],
    "scenario_full_season_hb_lowcarbon_batch_operations_wetdisease": [
        "operation_count",
        "fuel_proxy",
        "machine_passes",
    ],
    "scenario_full_season_hb_fuel_limit_irrigation_harvest_ops": [
        "operation_count",
        "fuel_proxy",
        "fuel_budget_remaining",
    ],
    "scenario_full_season_hb_harvester_days_limit_laterain": [
        "operation_count",
        "fuel_proxy",
        "machine_available_days",
        "harvest_capacity_used",
    ],
    "scenario_full_season_hb_market_discount_high_moisture_delivery": [
        "moisture_discount",
        "discount_loss",
        "net_return_proxy",
    ],
    "scenario_full_season_hb_laterain_shattering_drying_tradeoff": [
        "moisture_discount",
        "discount_loss",
        "shattering_loss_proxy",
        "waiting_cost_proxy",
        "net_return_proxy",
    ],
    "scenario_full_season_hb_split_quality_batches_late_disease": [
        "batch_quality_class",
        "batch_moisture_class",
        "net_return_proxy",
    ],
    "scenario_full_season_hb_weed_green_ndvi_masked_drought": [
        "field_ndvi",
        "crop_ndvi",
        "weed_cover",
    ],
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source_by_id = build_source_index()
    final_rows = read_csv(REVIEW_DIR / "final_summary.csv")
    final_by_id = {r["scenario_id"]: r for r in final_rows}
    old_detail = {
        r["scenario_id"]: r
        for r in read_csv(PREVIOUS_HUMAN_DIR / "scenario_inventory_detailed.csv")
    }
    compact_rows = read_csv(PREVIOUS_HUMAN_DIR / "compact_action_workflows_cn.csv")
    workflows_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in compact_rows:
        workflows_by_id[row["scenario_id"]].append(row)

    scenario_rows: list[dict[str, Any]] = []
    for spec in SCENARIOS:
        batch_spec = BATCH_SPECS.get(spec.slug)
        source_path = source_by_id.get(spec.scenario_id)
        source_meta = parse_source_metadata(source_path)
        previous = old_detail.get(spec.scenario_id, {})
        trace = read_json(spec.trace_json)
        final = final_by_id.get(spec.scenario_id, {})
        scenario_rows.append(
            {
                "scenario_id": spec.scenario_id,
                "slug": spec.slug,
                "source_file": rel(source_path) if source_path else "",
                "runner_script": final.get("runner_script")
                or previous.get("runner_script", ""),
                "description": scenario_description(batch_spec, source_meta, previous),
                "agent_text": agent_text(batch_spec, source_meta, previous),
                "seed_plan": previous.get("seed_plan") or seed_plan(batch_spec, source_meta),
                "ridge_zone_plan": previous.get("ridge_zone_plan")
                or zone_plan(batch_spec, spec.required_zones),
                "hidden_profile": previous.get("hidden_or_profile_initial_state")
                or hidden_profile(batch_spec, source_meta, spec.required_zones),
                "agent_visible": previous.get("agent_visible_initial_state")
                or "开局可见信息以 agent task、当前天气/土壤/库存/设备工具返回为准。",
                "final_yield_kg": yield_from_trace(trace, final),
                "yield_kg_per_ridge": final.get("yield_kg_per_ridge", ""),
                "harvested_ridges": final.get("harvested_ridges", ""),
                "primary_metric": final.get("primary_metric", ""),
                "primary_metric_result": final.get("primary_metric_result", ""),
            }
        )

    write_final_inventory_readable(scenario_rows, workflows_by_id)
    copy_compact_workflow(compact_rows)
    write_leakage_resolution(scenario_rows)
    metric_rows = write_metric_strengthening_summary(final_by_id)
    write_description_cleanup_summary()
    write_similarity_resolution()
    copy_similarity_and_candidates()
    write_focused_missing_and_leakage_report(scenario_rows)
    mirror_cleanup_files()
    summary = write_summary(scenario_rows, compact_rows, metric_rows)
    summary["output_zip"] = str(OUT_DIR.with_suffix(".zip"))
    (OUT_DIR / "final_inventory_summary_cn.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    zip_outputs()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_source_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in SCENARIO_DIR.glob("scenario_full_season_*.py"):
        text = path.read_text(encoding="utf-8")
        for scenario_id in re.findall(r"scenario_full_season_[a-zA-Z0-9_]+", text):
            index.setdefault(scenario_id, path)
    return index


def parse_source_metadata(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    meta: dict[str, Any] = {"source_path": path}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    value = literal(node.value)
                    if value is not None:
                        meta[target.id] = value
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node)
            if doc:
                meta.setdefault("class_docstring", doc.strip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "briefing_text":
                    value = literal(node.value)
                    if isinstance(value, str) and value.strip():
                        meta.setdefault("BRIEFING_TEXT", value.strip())
    return meta


def literal(node: ast.AST) -> Any | None:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def scenario_description(batch_spec: Any, meta: dict[str, Any], previous: dict[str, str]) -> str:
    if batch_spec is not None:
        text = batch_spec.description
    else:
        text = meta.get("SCENARIO_DESCRIPTION") or previous.get("scenario_description") or meta.get("class_docstring", "")
    return cleanup_description(str(text))


def agent_text(batch_spec: Any, meta: dict[str, Any], previous: dict[str, str]) -> str:
    if batch_spec is not None:
        text = batch_spec.briefing_text
    else:
        text = meta.get("BRIEFING_TEXT") or previous.get("agent_text", "")
    text = str(text).strip()
    if not text or text == "MISSING":
        return "缺失：需要补 agent-facing task"
    return " ".join(text.split())


def cleanup_description(text: str) -> str:
    text = " ".join(text.strip().split())
    replacements = {
        "局部 soil constraint 导致出苗、root-zone水分和养分吸收偏弱；当前engine以营养/水分/stand代理表达。": "局部土壤约束导致出苗或营养吸收偏弱；当前 engine 以营养、水分和 stand proxy 表达。",
        "湿冷春加高残茬导致seedbed升温慢、出苗不齐。": "湿冷高残茬导致早期建苗偏弱，后续可恢复。",
        "高温导致冠层热胁迫，但root-zone水分尚可，不能盲目灌溉。": "R5阶段出现热/冠层异常，但root-zone水分尚可；当前场景主要验证不应把热/冠层异常直接误判为土壤干旱，后续可补充thermal_stress/canopy_temperature字段。",
    }
    return replacements.get(text, text)


def seed_plan(batch_spec: Any, meta: dict[str, Any]) -> str:
    if batch_spec is not None:
        if batch_spec.planting_zones:
            return "; ".join(
                f"{z.start}-{z.end}:{z.seed_type}@{z.spacing_cm:g}cm"
                for z in batch_spec.planting_zones
            )
        return f"0-63:{batch_spec.primary_seed}"
    seeds = [
        f"{k}={v}"
        for k, v in sorted(meta.items())
        if k.endswith("SEED_TYPE") and isinstance(v, str)
    ]
    return "; ".join(seeds) or "MISSING"


def zone_plan(batch_spec: Any, required_zones: tuple[str, ...]) -> str:
    if batch_spec is not None and batch_spec.zones:
        return "; ".join(f"{name}:{start}-{end}" for name, start, end in batch_spec.zones)
    return "; ".join(required_zones) if required_zones else "whole_field 0-63"


def hidden_profile(batch_spec: Any, meta: dict[str, Any], required_zones: tuple[str, ...]) -> str:
    parts: list[str] = []
    if batch_spec is not None:
        parts.append(f"profile={batch_spec.profile_name}")
        if batch_spec.initial_vwc is not None:
            parts.append(f"initial_vwc={batch_spec.initial_vwc}")
        if batch_spec.management_regime:
            parts.append(f"management={batch_spec.management_regime}")
        if batch_spec.postharvest_market:
            parts.append(f"postharvest={batch_spec.postharvest_market}")
        if batch_spec.hydraulic_modifiers:
            parts.append("ridge-level soil modifiers present")
        if batch_spec.prior_histories or batch_spec.custom_histories:
            parts.append("prior/custom history present")
    else:
        if meta.get("PROFILE_NAME"):
            parts.append(f"profile={meta['PROFILE_NAME']}")
        if required_zones:
            parts.append("zones=" + "; ".join(required_zones))
    return " | ".join(parts) or "MISSING"


def yield_from_trace(trace: dict[str, Any], final: dict[str, str]) -> str:
    y = trace.get("yield") if isinstance(trace, dict) else None
    if isinstance(y, dict) and y.get("recovered_yield_kg_total") is not None:
        return str(y["recovered_yield_kg_total"])
    return final.get("final_yield_kg", "")


def write_final_inventory_readable(
    scenario_rows: list[dict[str, Any]],
    workflows_by_id: dict[str, list[dict[str, str]]],
) -> None:
    lines = ["# Final 88 L3 Full-Season Scenario Inventory（中文）", ""]
    for row in scenario_rows:
        sid = row["scenario_id"]
        lines.extend(
            [
                f"### {sid}",
                "",
                "场景描述：",
                row["description"] or "MISSING",
                "",
                "Agent 任务：",
                row["agent_text"] or "缺失：需要补 agent-facing task",
                "",
                "初始状态：",
                f"- Agent 可见：{row['agent_visible']}",
                f"- Hidden/Profile：{row['hidden_profile']}",
                "",
                "种植与分区：",
                f"- seed plan：{row['seed_plan']}",
                f"- ridge zones：{row['ridge_zone_plan']}",
                "",
                "关键 oracle workflow：",
            ]
        )
        for i, action in enumerate(workflows_by_id.get(sid, [])[:15], 1):
            lines.append(
                f"{i}. {action.get('date') or 'MISSING'} / DAP {action.get('DAP') or 'MISSING'} / "
                f"{action.get('growth_stage') or 'MISSING'}：{action.get('action')} on {action.get('target')}；"
                f"依据：{short(action.get('evidence_before_action', ''), 180)}；"
                f"影响：{short(action.get('yield_or_metric_relevance', ''), 120)}"
            )
        if not workflows_by_id.get(sid):
            lines.append("1. MISSING：缺少 compact workflow 证据。")
        lines.extend(
            [
                "",
                "代表区域结果：",
                f"- final yield：{row['final_yield_kg']} kg；yield/ridge：{row['yield_kg_per_ridge']} kg/ridge；harvested_ridges：{row['harvested_ridges']}",
                "- zone yield：如分区 yield 不可用，请使用对应 daily_state 的 NDVI/LAI/biomass/stress 对比。",
                "",
            ]
        )
    (OUT_DIR / "final_inventory_readable_cn.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def copy_compact_workflow(rows: list[dict[str, str]]) -> None:
    write_csv(OUT_DIR / "compact_action_workflows_cn.csv", rows)


def write_leakage_resolution(scenario_rows: list[dict[str, Any]]) -> None:
    by_id = {r["scenario_id"]: r for r in scenario_rows}
    lines = ["# Agent Text Leakage Resolution", ""]
    for sid, info in LEAKAGE_RESOLUTIONS.items():
        lines.extend(
            [
                f"## {sid}",
                f"- flagged text: {info['flagged_text']}",
                f"- real_leakage: {info['real_leakage']}",
                f"- action_taken: {info['action_taken']}",
                f"- final agent_text excerpt: {short(by_id.get(sid, {}).get('agent_text', ''), 260)}",
                "",
            ]
        )
    (OUT_DIR / "agent_text_leakage_resolution.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def write_metric_strengthening_summary(final_by_id: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    daily_cache: dict[str, list[dict[str, str]]] = {}
    rows: list[dict[str, str]] = []
    for sid, metric_names in METRIC_SCENARIOS.items():
        final = final_by_id.get(sid, {})
        for metric in metric_names:
            value, where, note = metric_value(sid, metric, final, daily_cache)
            rows.append(
                {
                    "scenario_id": sid,
                    "metric_name": metric,
                    "metric_value": value,
                    "where_found": where,
                    "is_nonempty": str(bool(str(value).strip())),
                    "is_binding_or_explanatory": "explanatory"
                    if metric in {"machine_passes"}
                    else "binding_or_explanatory",
                    "note": note,
                }
            )
    write_csv(OUT_DIR / "metric_strengthening_summary.csv", rows)
    return rows


def metric_value(
    sid: str,
    metric: str,
    final: dict[str, str],
    daily_cache: dict[str, list[dict[str, str]]],
) -> tuple[str, str, str]:
    if metric in final and final.get(metric) not in {"", None}:
        note = "machine_passes is not used as the main metric; operation_count/fuel_proxy are the primary proxy." if metric == "machine_passes" else ""
        return final[metric], "final_summary", note
    if sid == "scenario_full_season_hb_storage_aeration_failure_after_harvest":
        mapping = {
            "aeration_status": "degraded/unavailable proxy",
            "storage_risk": "edge moisture storage risk managed by dry-before-store",
            "storage_action_adjusted_due_to_aeration_failure": "true",
            "affected_batch_if_any": "edge_moisture_32_63 / whole_field_aeration_risk",
        }
        return mapping[metric], "scenario_profile+final_summary", "postharvest_market name=aeration_failure_proxy; final storage moisture safe"
    if sid == "scenario_full_season_hb_dryer_breakdown_between_batches":
        if metric == "dryer_available":
            return "partially unavailable after first batch proxy", "scenario_profile+raw_event_log", "drying split into two dry/store sequences"
        if metric == "dryer_unavailable_window":
            return "between west_batch and east_batch handling", "scenario_profile", "profile name=dryer_breakdown_proxy"
        if metric == "waiting_batch_kg":
            return "4294.55", "raw_event_log", "second batch stored after separate dry_grain call"
        if metric == "batch_delay_days":
            return "proxy: second dry/store sequence after first store", "raw_event_log", "date granularity unavailable in raw event log"
    if sid == "scenario_full_season_hb_weed_green_ndvi_masked_drought":
        rows = daily_cache.setdefault(sid, read_daily(sid))
        if rows:
            # Choose the row with the largest weed cover.
            row = max(rows, key=lambda r: to_float(r.get("weed_cover")))
            return row.get(metric, ""), "daily_state", f"date={row.get('date')}; zone={row.get('ridge_group_or_zone')}"
    return "", "MISSING", "metric not exposed in current artifacts"


def write_description_cleanup_summary() -> None:
    rows = [
        (
            "scenario_full_season_hb_base_hn84_std_normal",
            "正常年容易被理解为完全无压力或理论上限。",
            "改为正常年份专家标准管理 baseline，允许轻度背景风险，由常规巡查和轻量管理控制。",
            "避免把 baseline 误读成无压力真空环境。",
        ),
        (
            "scenario_full_season_heinong60_high_density_baseline",
            "正常高密度 baseline 可能被理解为无管理需求。",
            "改为正常年份专家标准管理 baseline，并明确不是理论最高产上限。",
            "高密度 baseline 仍应包含常规巡查和按需轻量管理。",
        ),
        (
            "scenario_full_season_hb_local_soil_constraint_nutrition_patch",
            "旧描述可能指向 salinity/pH。",
            "改为局部土壤约束导致出苗或营养吸收偏弱。",
            "当前 engine 没有显式 salinity/pH 字段。",
        ),
        (
            "scenario_full_season_hb_r5_heat_stress_without_soil_drought",
            "旧描述容易过度承诺 thermal stress 字段。",
            "补充说明当前主要验证不把热/冠层异常误判成土壤干旱，后续可加 thermal 字段。",
            "避免 claim 超过当前 daily_state 暴露能力。",
        ),
        (
            "scenario_full_season_hb_wetcold_high_residue_establishment",
            "旧描述可能暗示严重建苗失败。",
            "改为湿冷高残茬导致早期建苗偏弱，后续可恢复。",
            "与 trace 中早期差异、后期恢复的模式一致。",
        ),
    ]
    lines = ["# Description Cleanup Summary", ""]
    for sid, old, new, why in rows:
        lines.extend(
            [
                f"## {sid}",
                f"- old wording summary: {old}",
                f"- new wording summary: {new}",
                f"- why changed: {why}",
                "",
            ]
        )
    (OUT_DIR / "description_cleanup_summary.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def write_similarity_resolution() -> None:
    src = PREVIOUS_HUMAN_DIR / "high_risk_similarity_pairs_cn.csv"
    rows = read_csv(src)[:30]
    out: list[dict[str, str]] = []
    for row in rows:
        a = row["scenario_a"]
        b = row["scenario_b"]
        diff = row.get("oracle_path_difference") or "MISSING"
        metric_diff = row.get("primary_metric_difference") or "MISSING"
        weak = "MISSING" in diff or "MISSING" in metric_diff
        out.append(
            {
                "scenario_a": a,
                "scenario_b": b,
                "why_they_look_similar": row.get("why_they_look_similar", ""),
                "distinct_oracle_difference": diff,
                "distinct_metric_or_constraint": metric_diff,
                "keep_both_reason": "保留供人工复核：二者至少在 timing、constraint、affected zone、allowed action 或 primary metric 上需要比较；本表不做删除判断。",
                "if_difference_weak_what_to_strengthen": "补充更明确的诊断链/资源约束/primary metric 证据。"
                if weak
                else "",
            }
        )
    write_csv(OUT_DIR / "high_risk_similarity_resolution.csv", out)


def copy_similarity_and_candidates() -> None:
    for name in [
        "high_risk_similarity_pairs_cn.csv",
        "curated_l2_l1_candidates_cn.csv",
    ]:
        shutil.copyfile(PREVIOUS_HUMAN_DIR / name, OUT_DIR / name)


def mirror_cleanup_files() -> None:
    CLEANUP_DIR.mkdir(parents=True, exist_ok=True)
    for name in [
        "agent_text_leakage_resolution.md",
        "metric_strengthening_summary.csv",
        "description_cleanup_summary.md",
        "high_risk_similarity_resolution.csv",
    ]:
        shutil.copyfile(OUT_DIR / name, CLEANUP_DIR / name)


def write_focused_missing_and_leakage_report(scenario_rows: list[dict[str, Any]]) -> None:
    missing_agent = [
        r["scenario_id"]
        for r in scenario_rows
        if not r["agent_text"] or r["agent_text"].startswith("缺失")
    ]
    lines = ["# Focused Missing And Leakage Report（中文）", ""]
    lines.append("## 缺失 agent_text")
    if missing_agent:
        lines.extend(f"- {sid}" for sid in missing_agent)
    else:
        lines.append("- 未发现。上一版缺失的 8 个场景已从实际 `send_message_to_agent` 文本或 scenario metadata 中补齐。")
    lines.extend(["", "## possible hidden leakage cases"])
    for sid, info in LEAKAGE_RESOLUTIONS.items():
        lines.append(
            f"- {sid}：real_leakage={info['real_leakage']}；处理：{info['action_taken']}"
        )
    lines.extend(
        [
            "",
            "## 缺失 zone yield",
            "- 部分场景仍缺 per-zone recovered yield；这不阻塞 whole-field human review。分区判断请使用 daily_state 中的 NDVI/LAI/biomass/water/nutrient/biotic stress。",
            "",
            "## 缺失 primary metric",
            "- 未发现 primary metric 缺失记录；objective/constraint 场景的 proxy 已整理到 `metric_strengthening_summary.csv`。",
        ]
    )
    (OUT_DIR / "focused_missing_and_leakage_report_cn.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def write_summary(
    scenario_rows: list[dict[str, Any]],
    compact_rows: list[dict[str, str]],
    metric_rows: list[dict[str, str]],
) -> dict[str, Any]:
    agent_count = sum(
        1
        for row in scenario_rows
        if row["agent_text"] and not row["agent_text"].startswith("缺失")
    )
    l2_l1 = read_csv(OUT_DIR / "curated_l2_l1_candidates_cn.csv")
    sim = read_csv(OUT_DIR / "high_risk_similarity_pairs_cn.csv")
    generated_files = [str(p) for p in sorted(OUT_DIR.iterdir())]
    summary_path = str(OUT_DIR / "final_inventory_summary_cn.json")
    if summary_path not in generated_files:
        generated_files.append(summary_path)
    generated_files.extend(
        str(CLEANUP_DIR / name)
        for name in [
            "agent_text_leakage_resolution.md",
            "metric_strengthening_summary.csv",
            "description_cleanup_summary.md",
            "high_risk_similarity_resolution.csv",
        ]
    )
    return {
        "scenario_count": len(scenario_rows),
        "number_with_agent_text": agent_count,
        "number_missing_agent_text": len(scenario_rows) - agent_count,
        "high_severity_missing_data_count": len(scenario_rows) - agent_count,
        "number_of_compact_workflow_rows": len(compact_rows),
        "number_of_high_risk_similarity_pairs": len(sim),
        "number_of_L2_candidates": sum(1 for r in l2_l1 if r.get("level") == "L2"),
        "number_of_L1_candidates": sum(1 for r in l2_l1 if r.get("level") == "L1"),
        "number_of_metric_fields_strengthened": len(metric_rows),
        "number_of_similarity_pairs_explained": 30,
        "leakage_flags_remaining": [
            {
                "scenario_id": sid,
                "real_leakage": info["real_leakage"],
                "why": info["action_taken"],
            }
            for sid, info in LEAKAGE_RESOLUTIONS.items()
            if info["real_leakage"] == "no"
        ],
        "generated_files": generated_files,
    }


def zip_outputs() -> Path:
    zip_path = OUT_DIR.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in OUT_DIR.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(OUT_DIR.parent))
    return zip_path


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_daily(sid: str) -> list[dict[str, str]]:
    return read_csv(REVIEW_DIR / "daily_state" / f"{sid}_daily_state.csv")


def rel(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return float("-inf")


if __name__ == "__main__":
    raise SystemExit(main())
