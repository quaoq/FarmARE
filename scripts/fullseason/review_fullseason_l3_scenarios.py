"""Audit generated full-season L3 FARM traces.

This script treats CSV/JSON trace files as generated artifacts. It never
edits them. If a report flags an issue, the fix belongs in the scenario,
engine, app return values, or oracle event flow, followed by regenerating the
trace from the scenario.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_AI = REPO_ROOT / "docs" / "ai"
REPORT_SUMMARY = DOCS_AI / "fullseason-l3-review-summary.md"
SCENARIO_DIR = (
    REPO_ROOT / "are" / "simulation" / "scenarios" / "scenario_farm_world_fullseason_v2"
)


STAGE_ORDER = {
    "NOT_PLANTED": 0,
    "PLANTED_PRE_EMERGENCE": 1,
    "VE": 2,
    "VC": 3,
    "V1": 4,
    "V2": 5,
    "V3": 6,
    "V4_PLUS": 7,
    "R1": 8,
    "R1_BEGINNING_BLOOM": 8,
    "R3": 9,
    "R3_BEGINNING_POD": 9,
    "R5": 10,
    "R5_BEGINNING_SEED": 10,
    "R6": 11,
    "R6_FULL_SEED": 11,
    "R7": 12,
    "R7_BEGINNING_MATURITY": 12,
    "R8": 13,
    "R8_FULL_MATURITY": 13,
}


ACTION_FUNCTIONS = {
    "plant_seeds",
    "replant_seeds",
    "apply_fertilizer",
    "apply_fertigation",
    "irrigate",
    "apply_fungicide",
    "apply_herbicide",
    "apply_pesticide",
    "spray_pesticide",
    "mechanical_weed_control",
    "harvest",
    "unload_grain",
    "dry_grain",
    "store_grain",
    "till_soil",
    "form_ridges",
    "charge",
}

CHECK_FUNCTIONS = {
    "get_current_weather",
    "get_forecast",
    "read_soil_sensors",
    "read_canopy_sensors",
    "get_farm_overview",
    "get_ridge_range_state",
    "survey_field",
    "capture_ndvi_map",
    "capture_thermal_map",
    "inspect_ridges",
    "check_status",
    "get_status",
    "get_inventory",
    "fly_survey",
    "inspect_crop_health",
    "inspect_pests",
    "inspect_emergence",
}

KEY_DECISION_ACTIONS = {
    "plant_seeds",
    "replant_seeds",
    "apply_fertigation",
    "irrigate",
    "apply_fungicide",
    "apply_herbicide",
    "apply_pesticide",
    "spray_pesticide",
    "mechanical_weed_control",
    "harvest",
    "dry_grain",
    "store_grain",
}

RIDGE_TARGET_FUNCTIONS = {
    "plant_seeds",
    "replant_seeds",
    "apply_fertilizer",
    "apply_fertigation",
    "irrigate",
    "apply_fungicide",
    "apply_herbicide",
    "apply_pesticide",
    "spray_pesticide",
    "mechanical_weed_control",
    "harvest",
}

POSTHARVEST_FUNCTIONS = {"unload_grain", "dry_grain", "store_grain"}

TARGETED_EXPECTATIONS = {
    "wet_june_ab_zoned_disease": {
        "apply_fungicide": [(40, 55)],
    },
    "fastdraining_dry_patch_irrigation": {
        "irrigate": [(20, 31)],
    },
    "heinong84_low_chemical_wet_disease": {
        "apply_fungicide": [(22, 43)],
    },
    "hb_dryr5r6_hn58_std_waterlimit": {
        "irrigate": [(22, 43)],
    },
    "hb_poordrainage_wetjune_disease_trafficability": {
        "apply_fungicide": [(44, 53)],
    },
    "hb_soy_after_soy_wetjune_disease": {
        "apply_fungicide": [(22, 43)],
    },
}

PRIMARY_METRIC_RULES = [
    (("coolwet_flowering", "disease"), "disease_pressure_control_and_yield"),
    (("dryer", "drying"), "drying_capacity_and_batch_sequence"),
    (("storage", "aeration"), "storage_capacity_moisture_and_aeration"),
    (("market", "discount"), "quality_discount_vs_drying_cost"),
    (("sensor_failure", "moisture_sensor"), "alternate_observation_for_grain_moisture"),
    (("low_carbon",), "machine_pass_count"),
    (("fuel_limit",), "fuel_budget_and_operation_priority"),
    (
        ("spray_budget", "limited_spray", "limited_fungicide"),
        "spray_budget_and_threshold",
    ),
    (("harvester_days", "machinery_harvest"), "harvest_capacity_before_weather_risk"),
    (("waterlimit", "water_limit", "one_irrigation"), "irrigation_quota_and_priority"),
    (("organic", "lowchemical", "low_chemical"), "allowed_input_constraint"),
]

BASELINE_KG_PER_RIDGE = 132.05

DIAGNOSTIC_DECISION_ACTIONS = {
    "fly_survey",
    "inspect_crop_health",
    "inspect_pests",
    "inspect_emergence",
    "get_ridge_range_state",
}


CANONICAL_COLUMNS = {
    "date": ("date", "weather_date"),
    "ridge_id": ("ridge_id",),
    "zone": ("zone",),
    "stage": ("stage",),
    "trace_index": ("trace_index",),
    "label": ("label",),
    "top_vwc": ("top_vwc", "soil_top_vwc"),
    "root_vwc": ("root_vwc", "soil_root_vwc"),
    "water_stress": ("water_stress",),
    "nutrient_index": ("nutrient_index",),
    "nutrient_stress": ("nutrient_stress",),
    "stand_fraction": ("stand_fraction",),
    "weed_pressure": ("weed_pressure",),
    "insect_pressure": ("insect_pressure",),
    "disease_pressure": ("disease_pressure",),
    "lai": ("lai",),
    "ndvi": ("ndvi", "ndvi_proxy"),
    "canopy_temp": ("canopy_temp_proxy_c", "top_temp_c"),
    "biomass": ("aboveground_biomass", "aboveground_biomass_g_m2"),
    "yield_potential": ("yield_potential", "yield_potential_g_m2"),
    "grain_moisture": ("grain_moisture", "grain_moisture_frac"),
    "biological_yield": ("biological_yield", "biological_yield_g_m2"),
    "recovered_yield": ("recovered_yield", "recovered_yield_g_m2"),
    "action_marker": ("action_marker", "action_markers"),
}


RANGE_LIMITS = {
    "top_vwc": (0.05, 0.55),
    "root_vwc": (0.05, 0.55),
    "water_stress": (0.0, 1.0),
    "nutrient_index": (0.0, 1.25),
    "nutrient_stress": (0.0, 1.0),
    "stand_fraction": (0.0, 1.1),
    "weed_pressure": (0.0, 1.0),
    "insect_pressure": (0.0, 1.0),
    "disease_pressure": (0.0, 1.0),
    "lai": (0.0, 7.5),
    "ndvi": (0.0, 0.95),
    "grain_moisture": (0.0, 0.75),
    "biomass": (0.0, 1600.0),
    "biological_yield": (0.0, 850.0),
    "recovered_yield": (0.0, 850.0),
}


@dataclass(frozen=True)
class ScenarioSpec:
    slug: str
    scenario_id: str
    field_csv: Path
    ridge_csv: Path
    trace_json: Path
    required_zones: tuple[str, ...]


SCENARIOS = [
    ScenarioSpec(
        "heinong60_high_density_baseline",
        "scenario_full_season_heinong60_high_density_baseline",
        DOCS_AI / "heinong60_high_density_daily_field_summary.csv",
        DOCS_AI / "heinong60_high_density_daily_ridge_states.csv",
        DOCS_AI / "heinong60_high_density_oracle_trace.json",
        (),
    ),
    ScenarioSpec(
        "wet_june_ab_zoned_disease",
        "scenario_full_season_wet_june_ab_zoned_disease",
        DOCS_AI / "wet_june_ab_zoned_daily_field_summary.csv",
        DOCS_AI / "wet_june_ab_zoned_daily_ridge_states.csv",
        DOCS_AI / "wet_june_ab_zoned_oracle_trace.json",
        ("A_heinong84_standard", "B_heinong60_high_density"),
    ),
    ScenarioSpec(
        "heinong84_edge_low_fertility",
        "scenario_full_season_heinong84_edge_low_fertility",
        DOCS_AI / "heinong84-edge-low-fertility-field-summary.csv",
        DOCS_AI / "heinong84-edge-low-fertility-ridge-states.csv",
        DOCS_AI / "heinong84-edge-low-fertility-oracle-trace.json",
        ("severe_0_3", "mild_edge_4_11", "healthy_12_63"),
    ),
    ScenarioSpec(
        "fastdraining_dry_patch_irrigation",
        "scenario_full_season_fastdraining_dry_patch_irrigation",
        DOCS_AI / "fastdraining-dry-patch-field-summary.csv",
        DOCS_AI / "fastdraining-dry-patch-ridge-states.csv",
        DOCS_AI / "fastdraining-dry-patch-oracle-trace.json",
        ("affected_20_31", "reference_west_0_11", "reference_east_44_53"),
    ),
    ScenarioSpec(
        "heinong84_staggered_planting",
        "scenario_full_season_heinong84_staggered_planting",
        DOCS_AI / "heinong84-staggered-planting-field-summary.csv",
        DOCS_AI / "heinong84-staggered-planting-ridge-states.csv",
        DOCS_AI / "heinong84-staggered-planting-oracle-trace.json",
        ("early_0_20", "mid_21_42", "late_43_63"),
    ),
    ScenarioSpec(
        "heinong84_threshold_insect_limited_spray",
        "scenario_full_season_heinong84_threshold_insect_limited_spray",
        DOCS_AI / "threshold-insect-limited-spray-field-summary.csv",
        DOCS_AI / "threshold-insect-limited-spray-ridge-states.csv",
        DOCS_AI / "threshold-insect-limited-spray-oracle-trace.json",
        ("affected_18_37", "reference_west_0_17", "reference_east_38_63"),
    ),
    ScenarioSpec(
        "heinong84_low_chemical_wet_disease",
        "scenario_full_season_heinong84_low_chemical_wet_disease",
        DOCS_AI / "low-chemical-wet-disease-field-summary.csv",
        DOCS_AI / "low-chemical-wet-disease-ridge-states.csv",
        DOCS_AI / "low-chemical-wet-disease-oracle-trace.json",
        ("affected_22_43", "reference_west_0_21", "reference_east_44_63"),
    ),
    ScenarioSpec(
        "early_vs_standard_late_rain_harvest",
        "scenario_full_season_early_vs_standard_late_rain_harvest",
        DOCS_AI / "early-vs-standard-late-rain-harvest-field-summary.csv",
        DOCS_AI / "early-vs-standard-late-rain-harvest-ridge-states.csv",
        DOCS_AI / "early-vs-standard-late-rain-harvest-oracle-trace.json",
        ("a_heike71_0_31", "b_heinong84_32_63"),
    ),
    ScenarioSpec(
        "hb_base_hn84_std_normal",
        "scenario_full_season_hb_base_hn84_std_normal",
        DOCS_AI / "hb-base-hn84-std-normal-field-summary.csv",
        DOCS_AI / "hb-base-hn84-std-normal-ridge-states.csv",
        DOCS_AI / "hb-base-hn84-std-normal-oracle-trace.json",
        ("whole_field_0_63",),
    ),
    ScenarioSpec(
        "hb_dryr5r6_hn58_std_waterlimit",
        "scenario_full_season_hb_dryr5r6_hn58_std_waterlimit",
        DOCS_AI / "hb-dryr5r6-hn58-std-waterlimit-field-summary.csv",
        DOCS_AI / "hb-dryr5r6-hn58-std-waterlimit-ridge-states.csv",
        DOCS_AI / "hb-dryr5r6-hn58-std-waterlimit-oracle-trace.json",
        ("priority_22_43", "reference_west_0_10", "reference_east_54_63"),
    ),
    ScenarioSpec(
        "hb_poordrainage_wetjune_disease_trafficability",
        "scenario_full_season_hb_poordrainage_wetjune_disease_trafficability",
        DOCS_AI / "hb-poordrainage-wetjune-disease-trafficability-field-summary.csv",
        DOCS_AI / "hb-poordrainage-wetjune-disease-trafficability-ridge-states.csv",
        DOCS_AI / "hb-poordrainage-wetjune-disease-trafficability-oracle-trace.json",
        ("poor_drainage_44_53", "reference_0_10"),
    ),
    ScenarioSpec(
        "hb_soy_after_soy_wetjune_disease",
        "scenario_full_season_hb_soy_after_soy_wetjune_disease",
        DOCS_AI / "hb-soy-after-soy-wetjune-disease-field-summary.csv",
        DOCS_AI / "hb-soy-after-soy-wetjune-disease-ridge-states.csv",
        DOCS_AI / "hb-soy-after-soy-wetjune-disease-oracle-trace.json",
        ("history_affected_22_43", "reference_west_0_21", "reference_east_44_63"),
    ),
]


try:
    from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_catalog import (
        SPECS as BATCH_L3_SPECS,
    )
except Exception:
    BATCH_L3_SPECS = {}

SCENARIOS.extend(
    ScenarioSpec(
        spec.slug,
        spec.scenario_id,
        DOCS_AI / f"{spec.slug.replace('_', '-')}-field-summary.csv",
        DOCS_AI / f"{spec.slug.replace('_', '-')}-ridge-states.csv",
        DOCS_AI / f"{spec.slug.replace('_', '-')}-oracle-trace.json",
        tuple(zone[0] for zone in spec.zones),
    )
    for spec in BATCH_L3_SPECS.values()
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-ai", type=Path, default=DOCS_AI)
    parser.add_argument("--output", type=Path, default=REPORT_SUMMARY)
    args = parser.parse_args()

    reports = []
    for spec in SCENARIOS:
        report = review_scenario(spec)
        report_path = args.docs_ai / f"{spec.slug}-review-report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        reports.append((spec, report, report_path))

    args.output.write_text(render_summary(reports), encoding="utf-8")
    print(
        json.dumps(
            {
                "reports": [str(path.relative_to(REPO_ROOT)) for _, _, path in reports],
                "summary": str(args.output.relative_to(REPO_ROOT)),
                "status_counts": dict(Counter(r["status"] for _, r, _ in reports)),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def review_scenario(spec: ScenarioSpec) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    field_rows = read_csv(spec.field_csv, issues, "field_csv")
    ridge_rows = read_csv(spec.ridge_csv, issues, "ridge_csv")
    trace = read_json(spec.trace_json, issues)

    metrics: dict[str, Any] = {
        "field_rows": len(field_rows),
        "ridge_rows": len(ridge_rows),
        "required_zones": list(spec.required_zones),
    }
    if field_rows:
        metrics["field_schema"] = list(field_rows[0].keys())
    if ridge_rows:
        metrics["ridge_schema"] = list(ridge_rows[0].keys())

    normalized = [normalize_row(row) for row in ridge_rows]
    check_schema(spec, field_rows, normalized, issues, metrics)
    check_value_ranges(normalized, issues, metrics)
    check_stage_progression(normalized, issues, metrics)
    check_yield_progression(normalized, issues, metrics)
    check_harvest_window_timing(spec, field_rows, normalized, issues, metrics)
    check_agronomic_stress_semantics(spec, normalized, trace, issues, metrics)

    trace_metrics = check_trace(spec, trace, issues)
    metrics.update(trace_metrics)
    action_support = build_action_support_report(trace)
    metrics["action_support_summary"] = summarize_action_support(action_support)
    unsupported = [
        item for item in action_support if item.get("support_verdict") != "supported"
    ]
    if unsupported:
        add_issue(
            issues,
            "warn",
            "action_support_incomplete",
            "Some key actions do not have enough prior tool-return evidence",
            sample=[
                {
                    "action_event_id": item["action_event_id"],
                    "action_function": item["action_function"],
                    "missing": item["missing_evidence_groups"],
                }
                for item in unsupported[:8]
            ],
        )
    check_scenario_specific(spec, normalized, trace, issues, metrics)
    semantic_validation = validate_oracle_semantics(spec, field_rows, normalized, trace)
    metrics["semantic_validation"] = semantic_validation
    for finding in semantic_validation["findings"]:
        if finding.get("severity") != "fail":
            continue
        add_issue(
            issues,
            "fail",
            f"semantic_{finding['code']}",
            finding["message"],
            evidence=finding.get("evidence"),
        )

    status = "pass"
    if any(issue["severity"] == "fail" for issue in issues):
        status = "fail"
    elif issues:
        status = "warn"

    return {
        "scenario_id": spec.scenario_id,
        "slug": spec.slug,
        "status": status,
        "metrics": metrics,
        "issues": issues,
        "action_support": action_support,
        "semantic_validation": semantic_validation,
        "acceptance": {
            "csv_state": "pass"
            if not any(
                i["severity"] == "fail"
                for i in issues
                if i["code"] != "action_support_incomplete"
            )
            else "fail",
            "action_support": "pass" if not unsupported else "warn",
            "event_order": semantic_validation["event_order_status"],
            "target_legality": semantic_validation["target_legality_status"],
            "targeted_scope": semantic_validation["targeted_scope_status"],
            "hidden_leakage": semantic_validation["hidden_leakage_status"],
            "primary_metric": semantic_validation["primary_metric_status"],
            "yield_sanity": semantic_validation["yield_sanity_status"],
            "note": "CSV state trends and tool-return-to-action evidence are checked separately.",
        },
        "principle": (
            "Trace files are generated outputs. Fix flagged behavior in the "
            "scenario, engine, app/tool return, or oracle event flow, then regenerate."
        ),
    }


def validate_oracle_semantics(
    spec: ScenarioSpec,
    field_rows: list[dict[str, str]],
    normalized_rows: list[dict[str, Any]],
    trace: dict[str, Any],
) -> dict[str, Any]:
    events = trace.get("completed_events") or []
    findings: list[dict[str, Any]] = []
    event_order = validate_event_order(events, findings)
    target_legality = validate_target_legality(events, findings)
    targeted_scope = validate_targeted_scope(spec, trace, findings)
    hidden_leakage = validate_hidden_leakage(spec, findings)
    primary_metric = primary_metric_for_slug(spec.slug)
    yield_sanity = validate_yield_sanity(
        spec, trace, normalized_rows, primary_metric, findings
    )
    return {
        "event_order_status": event_order,
        "target_legality_status": target_legality,
        "targeted_scope_status": targeted_scope,
        "hidden_leakage_status": hidden_leakage["status"],
        "hidden_leakage_findings": hidden_leakage["findings"],
        "primary_metric_status": "configured",
        "primary_metric": primary_metric,
        "yield_sanity_status": yield_sanity["status"],
        "yield_sanity_note": yield_sanity["note"],
        "field_action_marker_rows": sum(
            1
            for row in field_rows
            if row.get("action_marker") or row.get("action_markers")
        ),
        "findings": findings,
    }


def validate_event_order(
    events: list[dict[str, Any]], findings: list[dict[str, Any]]
) -> str:
    seen_any_harvest = False
    seen_batch_harvest = False
    seen_batch_dry = False
    bad: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        fn = event.get("function")
        if fn == "harvest":
            seen_any_harvest = True
            seen_batch_harvest = True
        elif fn == "dry_grain":
            if not seen_any_harvest:
                bad.append(
                    {
                        "event_id": event.get("event_id"),
                        "index": index,
                        "reason": "dry_before_any_harvest",
                    }
                )
            seen_batch_dry = True
        elif fn == "store_grain":
            safe_direct_store = _store_event_safe_without_drying(event)
            if not seen_batch_harvest or not (seen_batch_dry or safe_direct_store):
                bad.append(
                    {
                        "event_id": event.get("event_id"),
                        "index": index,
                        "reason": "store_before_harvest_or_required_dry",
                    }
                )
            seen_batch_harvest = False
            seen_batch_dry = False
    if bad:
        findings.append(
            {
                "severity": "fail",
                "code": "event_order_invalid",
                "message": "Postharvest event order violates harvest -> optional dry when needed -> store.",
                "evidence": bad[:8],
            }
        )
        return "fail"
    return "pass"


def _store_event_safe_without_drying(event: dict[str, Any]) -> bool:
    return_value = event.get("return_value")
    if not isinstance(return_value, dict) or return_value.get("status") != "ok":
        return False
    try:
        storage_moisture = float(return_value.get("storage_grain_moisture_pct"))
        max_storage_moisture = float(
            return_value.get("max_storage_moisture_pct", 13.5)
        )
    except (TypeError, ValueError):
        return False
    return storage_moisture <= max_storage_moisture + 1e-9


def validate_target_legality(
    events: list[dict[str, Any]], findings: list[dict[str, Any]]
) -> str:
    illegal: list[dict[str, Any]] = []
    for event in events:
        fn = event.get("function")
        if fn not in RIDGE_TARGET_FUNCTIONS:
            continue
        target = action_target_range(event)
        if target.get("min_ridge") is not None and target.get("max_ridge") is not None:
            continue
        illegal.append(
            {
                "event_id": event.get("event_id"),
                "function": fn,
                "return_value": summarize_action_return(event.get("return_value")),
            }
        )
    if illegal:
        findings.append(
            {
                "severity": "fail",
                "code": "target_legality_invalid",
                "message": "Ridge action lacks a ridge target in its return value.",
                "evidence": illegal[:10],
            }
        )
        return "fail"
    return "pass"


def validate_targeted_scope(
    spec: ScenarioSpec, trace: dict[str, Any], findings: list[dict[str, Any]]
) -> str:
    expected = targeted_expectations_for(spec)
    if not expected:
        return "not_applicable"
    violations: list[dict[str, Any]] = []
    for fn, allowed_ranges in expected.items():
        for item in action_ranges(trace, {fn}):
            if item.get("status") == "error":
                continue
            if item.get("min_ridge") is None or item.get("max_ridge") is None:
                violations.append(
                    {
                        "event_id": item.get("event_id"),
                        "function": fn,
                        "reason": "missing_target",
                    }
                )
                continue
            if not any(range_within(item, start, end) for start, end in allowed_ranges):
                violations.append(
                    {
                        "event_id": item.get("event_id"),
                        "function": fn,
                        "actual": f"{item.get('min_ridge')}-{item.get('max_ridge')}",
                        "allowed": [f"{start}-{end}" for start, end in allowed_ranges],
                    }
                )
    if violations:
        findings.append(
            {
                "severity": "fail",
                "code": "targeted_scope_invalid",
                "message": "Targeted scenario core action was applied outside the expected local scope.",
                "evidence": violations[:10],
            }
        )
        return "fail"
    return "pass"


def targeted_expectations_for(spec: ScenarioSpec) -> dict[str, list[tuple[int, int]]]:
    expected = {
        fn: list(ranges)
        for fn, ranges in TARGETED_EXPECTATIONS.get(spec.slug, {}).items()
    }
    batch_spec = BATCH_L3_SPECS.get(spec.slug)
    if batch_spec is None:
        return expected
    kind_to_function = {
        "fertigation": "apply_fertigation",
        "fungicide": "apply_fungicide",
        "herbicide": "apply_herbicide",
        "mechanical_weed": "mechanical_weed_control",
        "pesticide": "apply_pesticide",
        "irrigation": "irrigate",
        "replant": "replant_seeds",
    }
    for action in batch_spec.actions:
        fn = kind_to_function.get(action.kind)
        if not fn:
            continue
        if action.start == 0 and action.end == 63:
            continue
        expected.setdefault(fn, []).append((int(action.start), int(action.end)))
    return expected


def validate_hidden_leakage(
    spec: ScenarioSpec, findings: list[dict[str, Any]]
) -> dict[str, Any]:
    briefing = scenario_briefing_text(spec)
    hits: list[dict[str, Any]] = []
    if re.search(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\b", briefing):
        hits.append({"kind": "exact_range", "sample": range_samples(briefing)})
    hidden_phrases = [
        "真实原因",
        "确定病害",
        "confirmed disease",
        "confirmed pest",
        "affected ridges",
        "初始肥力较低",
        "持水能力差",
        "fast-draining",
        "skip rows",
        "漏播",
        "施肥条带不均",
    ]
    for phrase in hidden_phrases:
        if phrase.lower() in briefing.lower():
            hits.append({"kind": "hidden_cause_phrase", "sample": phrase})
    if not hits:
        return {"status": "pass", "findings": []}
    if briefing_leakage_allowed(briefing, hits):
        return {"status": "allowed_field_history", "findings": hits}
    findings.append(
        {
            "severity": "warn",
            "code": "hidden_leakage_needs_review",
            "message": "Briefing may expose hidden cause or exact affected scope.",
            "evidence": hits[:8],
        }
    )
    return {"status": "needs_review", "findings": hits}


def scenario_briefing_text(spec: ScenarioSpec) -> str:
    batch_spec = BATCH_L3_SPECS.get(spec.slug)
    if batch_spec is not None:
        return str(batch_spec.briefing_text)
    source = source_path_for_scenario(spec.scenario_id)
    if source is None:
        return ""
    meta = parse_source_metadata(source)
    return str(meta.get("BRIEFING_TEXT") or "")


def source_path_for_scenario(scenario_id: str) -> Path | None:
    for path in SCENARIO_DIR.glob("scenario_full_season_*.py"):
        if scenario_id in path.read_text(encoding="utf-8"):
            return path
    return None


def parse_source_metadata(path: Path) -> dict[str, Any]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}
    meta: dict[str, Any] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = literal_or_none(node.value)
        if value is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                meta[target.id] = value
    return meta


def literal_or_none(node: ast.AST) -> Any | None:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def range_samples(text: str) -> list[str]:
    return re.findall(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\b", text)[:8]


def briefing_leakage_allowed(briefing: str, hits: list[dict[str, Any]]) -> bool:
    allowed_context = (
        "前茬",
        "病害历史",
        "高杂草种子库",
        "低化学",
        "有机",
        "低碳",
        "水量有限",
        "喷药次数有限",
        "收获能力有限",
        "烘干能力",
        "储藏容量",
        "传感器失效",
        "外包收获机",
        "分区播种",
        "A区",
        "B区",
    )
    if not any(token in briefing for token in allowed_context):
        return False
    hidden_kinds = {hit["kind"] for hit in hits}
    if hidden_kinds == {"exact_range"}:
        return True
    return False


def primary_metric_for_slug(slug: str) -> str:
    lowered = slug.lower()
    for needles, metric in PRIMARY_METRIC_RULES:
        if any(needle in lowered for needle in needles):
            return metric
    return "yield_and_recovered_grain"


def validate_yield_sanity(
    spec: ScenarioSpec,
    trace: dict[str, Any],
    rows: list[dict[str, Any]],
    primary_metric: str,
    findings: list[dict[str, Any]],
) -> dict[str, str]:
    kg_per_ridge = recovered_kg_per_ridge(trace, rows)
    if kg_per_ridge is None:
        return {"status": "unknown", "note": "No recovered yield found."}
    stress_like = any(
        token in spec.slug
        for token in (
            "disease",
            "insect",
            "weed",
            "dry",
            "drought",
            "stress",
            "limited",
            "low",
            "wet",
            "fault",
            "failure",
            "capacity",
        )
    )
    if stress_like and kg_per_ridge > BASELINE_KG_PER_RIDGE * 1.05:
        note = (
            f"yield={kg_per_ridge:.2f} kg/ridge is above normal baseline "
            f"{BASELINE_KG_PER_RIDGE:.2f}; primary metric is {primary_metric}, "
            "so yield alone is not treated as failure."
        )
        findings.append(
            {
                "severity": "info",
                "code": "yield_above_baseline_explained",
                "message": note,
                "evidence": {"kg_per_ridge": round(kg_per_ridge, 3)},
            }
        )
        return {"status": "explained", "note": note}
    return {
        "status": "pass",
        "note": f"yield={kg_per_ridge:.2f} kg/ridge; baseline={BASELINE_KG_PER_RIDGE:.2f}.",
    }


def recovered_kg_per_ridge(
    trace: dict[str, Any], rows: list[dict[str, Any]]
) -> float | None:
    total = 0.0
    count = 0
    for event in trace.get("completed_events") or []:
        if event.get("function") != "harvest":
            continue
        rv = event.get("return_value")
        if not isinstance(rv, dict):
            continue
        grain = to_float(rv.get("grain_kg_added"))
        ridges = rv.get("harvested_ridges") or []
        if grain is None or not ridges:
            continue
        total += grain
        count += len(ridges)
    if count:
        return total / count
    by_ridge: dict[int, float] = {}
    for row in rows:
        ridge_id = row.get("ridge_id")
        value = row.get("recovered_yield")
        if ridge_id is None or value is None:
            continue
        by_ridge[int(ridge_id)] = float(value)
    if not by_ridge:
        return None
    # CSV recovered_yield is g/m2. The exact ridge area is not needed here; this
    # fallback is only for sanity status when harvest returns are missing.
    return None


def read_csv(
    path: Path, issues: list[dict[str, Any]], role: str
) -> list[dict[str, str]]:
    if not path.exists():
        add_issue(issues, "fail", f"missing_{role}", f"Missing {path}")
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path, issues: list[dict[str, Any]]) -> dict[str, Any]:
    if not path.exists():
        add_issue(issues, "fail", "missing_trace_json", f"Missing {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        add_issue(issues, "fail", "invalid_trace_json", f"{path}: {exc}")
        return {}


def normalize_row(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    for canonical, choices in CANONICAL_COLUMNS.items():
        for name in choices:
            if name in row and row[name] not in ("", None):
                out[canonical] = row[name]
                break
    for key in RANGE_LIMITS:
        out[key] = to_float(out.get(key))
    out["ridge_id"] = to_int(out.get("ridge_id"))
    out["trace_index"] = to_int(out.get("trace_index"))
    out["stage_rank"] = STAGE_ORDER.get(str(out.get("stage") or ""), -1)
    return out


def check_schema(
    spec: ScenarioSpec,
    field_rows: list[dict[str, str]],
    rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    if not rows:
        add_issue(issues, "fail", "empty_ridge_csv", "No ridge rows to audit")
        return
    by_trace: dict[int, list[dict[str, Any]]] = defaultdict(list)
    zones = set()
    for row in rows:
        by_trace[int(row.get("trace_index") or -1)].append(row)
        if row.get("zone"):
            zones.add(str(row["zone"]))
    metrics["trace_count_from_ridge_csv"] = len(by_trace)
    metrics["observed_zones"] = sorted(zones)

    bad_counts = {
        trace: len(trace_rows)
        for trace, trace_rows in by_trace.items()
        if len({r.get("ridge_id") for r in trace_rows if r.get("ridge_id") is not None})
        != 64
    }
    if bad_counts:
        add_issue(
            issues,
            "fail",
            "ridge_count_not_64",
            "Some trace snapshots do not contain 64 distinct ridges",
            sample=dict(list(bad_counts.items())[:5]),
        )

    missing_zones = [zone for zone in spec.required_zones if zone not in zones]
    if missing_zones:
        add_issue(
            issues,
            "fail",
            "missing_required_zones",
            "Required zone labels are missing from ridge CSV",
            missing_zones=missing_zones,
        )

    dates = [coerce_date(row.get("date")) for row in rows if row.get("date")]
    dates = [d for d in dates if d is not None]
    if dates:
        metrics["date_start"] = min(dates).isoformat()
        metrics["date_end"] = max(dates).isoformat()
    if field_rows:
        metrics["field_csv_rows"] = len(field_rows)


def check_value_ranges(
    rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    ranges: dict[str, dict[str, float]] = {}
    for key, (lo, hi) in RANGE_LIMITS.items():
        values = [row[key] for row in rows if row.get(key) is not None]
        if not values:
            continue
        vmin = min(values)
        vmax = max(values)
        ranges[key] = {"min": round(vmin, 4), "max": round(vmax, 4)}
        if vmin < lo or vmax > hi:
            add_issue(
                issues,
                "fail",
                f"{key}_out_of_range",
                f"{key} range {vmin:.4f}-{vmax:.4f} outside expected {lo}-{hi}",
            )
    metrics["ranges"] = ranges

    for key, jump_limit in {
        "top_vwc": 0.18,
        "root_vwc": 0.16,
        "ndvi": 0.28,
        "lai": 1.25,
        "grain_moisture": 0.22,
    }.items():
        jumps = max_consecutive_jumps(rows, key)
        metrics[f"max_{key}_jump"] = round(jumps["max_jump"], 4)
        if jumps["max_jump"] > jump_limit:
            if key in {"top_vwc", "root_vwc"} and jumps.get("rainfall_mm", 0.0) > 0.0:
                continue
            add_issue(
                issues,
                "warn",
                f"{key}_large_jump",
                f"{key} has a large consecutive jump; inspect whether an action or stage transition explains it",
                **jumps,
            )


def check_stage_progression(
    rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    regressions = []
    first_r8: dict[int, str] = {}
    for ridge_id, ridge_rows in rows_by_ridge(rows).items():
        prev_rank = -1
        prev_stage = None
        prev_dap: int | None = None
        for row in sorted(ridge_rows, key=row_sort_key):
            rank = int(row.get("stage_rank") or -1)
            stage = row.get("stage")
            dap = to_int(row.get("days_after_planting"))
            phenology_reset = (
                prev_dap is not None
                and dap is not None
                and dap < prev_dap
                and rank < prev_rank
            )
            if phenology_reset:
                prev_rank = rank
                prev_stage = stage
                prev_dap = dap
                continue
            if prev_rank >= 0 and rank >= 0 and rank < prev_rank:
                regressions.append(
                    {
                        "ridge_id": ridge_id,
                        "from": prev_stage,
                        "to": stage,
                        "date": row.get("date"),
                        "label": row.get("label"),
                    }
                )
            if stage == "R8" and ridge_id not in first_r8:
                first_r8[ridge_id] = str(row.get("date") or row.get("label") or "")
            prev_rank = max(prev_rank, rank)
            prev_stage = stage
            prev_dap = dap if dap is not None else prev_dap
    metrics["first_r8_count"] = len(first_r8)
    if regressions:
        add_issue(
            issues,
            "fail",
            "stage_regression",
            "Phenology stage regressed for some ridges",
            sample=regressions[:8],
        )


def check_yield_progression(
    rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    r8_biomass_jumps = []
    recovered_after_harvest_jumps = []
    for ridge_id, ridge_rows in rows_by_ridge(rows).items():
        ordered = sorted(ridge_rows, key=row_sort_key)
        seen_r8 = False
        prev_bio = None
        prev_recovered = None
        for row in ordered:
            bio = row.get("biological_yield")
            if (
                seen_r8
                and prev_bio is not None
                and bio is not None
                and bio - prev_bio > 35.0
            ):
                r8_biomass_jumps.append(
                    {
                        "ridge_id": ridge_id,
                        "date": row.get("date"),
                        "label": row.get("label"),
                        "jump": round(bio - prev_bio, 3),
                    }
                )
            if int(row.get("stage_rank") or -1) >= STAGE_ORDER["R8"]:
                seen_r8 = True
            rec = row.get("recovered_yield")
            if (
                prev_recovered is not None
                and prev_recovered > 0.0
                and rec is not None
                and rec - prev_recovered > 5.0
            ):
                recovered_after_harvest_jumps.append(
                    {
                        "ridge_id": ridge_id,
                        "date": row.get("date"),
                        "label": row.get("label"),
                        "jump": round(rec - prev_recovered, 3),
                    }
                )
            if bio is not None:
                prev_bio = bio
            if rec is not None:
                prev_recovered = rec
    metrics["r8_biological_yield_jump_count"] = len(r8_biomass_jumps)
    metrics["post_harvest_recovered_yield_jump_count"] = len(
        recovered_after_harvest_jumps
    )
    if r8_biomass_jumps:
        add_issue(
            issues,
            "warn",
            "r8_biological_yield_jump",
            "Biological yield increased after a ridge was already R8",
            sample=r8_biomass_jumps[:8],
        )
    if recovered_after_harvest_jumps:
        add_issue(
            issues,
            "warn",
            "post_harvest_recovered_yield_growth",
            "Recovered yield increased after a harvest marker",
            sample=recovered_after_harvest_jumps[:8],
        )


def check_harvest_window_timing(
    spec: ScenarioSpec,
    field_rows: list[dict[str, str]],
    rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    """Flag fixed oracle harvest dates that ignore an earlier clean window."""

    if not field_rows or not rows:
        return

    harvest_row = next(
        (
            row
            for row in field_rows
            if "harvest" in str(row.get("action_markers") or "")
        ),
        None,
    )
    if harvest_row is None:
        return

    harvest_trace = to_int(harvest_row.get("trace_index"))
    harvest_date = coerce_date(harvest_row.get("date"))
    if harvest_trace is None or harvest_date is None:
        return

    rows_by_trace: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        trace_index = row.get("trace_index")
        if trace_index is not None:
            rows_by_trace[int(trace_index)].append(row)

    candidates = []
    for field_row in field_rows:
        trace_index = to_int(field_row.get("trace_index"))
        candidate_date = coerce_date(field_row.get("date"))
        if (
            trace_index is None
            or candidate_date is None
            or trace_index >= harvest_trace
        ):
            continue
        stage_counts = parse_json_object(field_row.get("stage_counts_json"))
        r8_count = int(
            stage_counts.get("R8_FULL_MATURITY") or stage_counts.get("R8") or 0
        )
        if r8_count < 64:
            continue
        grain = to_float(field_row.get("avg_grain_moisture"))
        rain = to_float(field_row.get("weather_rainfall_mm")) or 0.0
        wind = to_float(field_row.get("weather_wind_speed_ms")) or 0.0
        avg_top = to_float(field_row.get("avg_top_vwc"))
        trace_rows = rows_by_trace.get(trace_index, [])
        max_top = max(
            (
                row.get("top_vwc")
                for row in trace_rows
                if row.get("top_vwc") is not None
            ),
            default=None,
        )
        if (
            grain is None
            or avg_top is None
            or grain > 0.18
            or rain > 0.1
            or wind > 6.5
            or avg_top > 0.35
            or (max_top is not None and max_top > 0.37)
        ):
            continue
        candidates.append(
            {
                "date": candidate_date.isoformat(),
                "trace_index": trace_index,
                "avg_grain_moisture": round(grain, 4),
                "avg_top_vwc": round(avg_top, 4),
                "max_top_vwc": round(max_top, 4) if max_top is not None else None,
                "rainfall_mm": round(rain, 3),
                "wind_speed_ms": round(wind, 3),
            }
        )

    metrics["harvest_date"] = harvest_date.isoformat()
    metrics["earlier_harvestable_window_count"] = len(candidates)
    if candidates:
        first = candidates[0]
        metrics["first_harvestable_window"] = first
        gap = (harvest_date - coerce_date(first["date"])).days
        metrics["harvest_delay_after_first_window_days"] = gap
        harvest_constraint_slug = slug_mentions(
            spec.slug,
            {
                "harvest",
                "laterain",
                "dryer",
                "storage",
                "market",
                "shattering",
                "capacity",
                "moisture",
                "quality",
                "earlyfrost",
            },
        )
        allowed_gap = 14 if harvest_constraint_slug else 10
        if gap > allowed_gap:
            add_issue(
                issues,
                "warn",
                "harvest_delayed_after_ready_window",
                "Oracle harvest is delayed long after an earlier clean harvest window",
                harvest_date=harvest_date.isoformat(),
                first_ready_window=first,
                delay_days=gap,
                allowed_gap_days=allowed_gap,
            )


def check_agronomic_stress_semantics(
    spec: ScenarioSpec,
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    """Flag long non-target stress that would invalidate an expert oracle."""

    water_target = slug_mentions(
        spec.slug,
        {
            "dry",
            "drought",
            "water",
            "irrigation",
            "fastdrain",
            "heatdry",
            "compacted",
        },
    )
    nutrient_target = slug_mentions(
        spec.slug,
        {
            "nutrient",
            "fertilizer",
            "fertility",
            "lowfertility",
            "rhizobia",
            "nodulation",
            "micronutrient",
            "potassium",
            "leafcolor",
        },
    )
    weed_target = slug_mentions(
        spec.slug,
        {"weed", "herbicide", "organic", "lowdensity"},
    )
    insect_target = slug_mentions(spec.slug, {"insect", "aphid", "feeder", "pest"})
    disease_target = slug_mentions(
        spec.slug,
        {"disease", "fungicide", "wetjune", "soyhistory", "poordrainage"},
    )

    stress_metrics: dict[str, Any] = {}
    water_run = stress_run_summary(
        rows,
        "water_stress",
        mode="low",
        threshold=0.75,
        share_threshold=0.35,
        max_stage_rank=STAGE_ORDER["R6"],
    )
    nutrient_run = stress_run_summary(
        rows,
        "nutrient_stress",
        mode="low",
        threshold=0.82,
        share_threshold=0.35,
        max_stage_rank=STAGE_ORDER["R6"],
    )
    weed_run = stress_run_summary(
        rows,
        "weed_pressure",
        mode="high",
        threshold=0.35,
        share_threshold=0.25,
        max_stage_rank=STAGE_ORDER["R6"],
    )
    insect_run = stress_run_summary(
        rows,
        "insect_pressure",
        mode="high",
        threshold=0.30,
        share_threshold=0.20,
        max_stage_rank=STAGE_ORDER["R6"],
    )
    disease_run = stress_run_summary(
        rows,
        "disease_pressure",
        mode="high",
        threshold=0.30,
        share_threshold=0.20,
        max_stage_rank=STAGE_ORDER["R6"],
    )
    for name, summary in {
        "water": water_run,
        "nutrient": nutrient_run,
        "weed": weed_run,
        "insect": insect_run,
        "disease": disease_run,
    }.items():
        if summary["days"] > 0:
            stress_metrics[name] = summary
    metrics["agronomic_stress_semantics"] = stress_metrics

    irrigation_actions = action_ranges(trace, {"irrigate"})
    fertigation_actions = action_ranges(trace, {"apply_fertigation"})
    herbicide_actions = action_ranges(
        trace, {"apply_herbicide", "mechanical_weed_control"}
    )
    insecticide_actions = action_ranges(trace, {"spray_pesticide", "apply_pesticide"})
    fungicide_actions = action_ranges(trace, {"apply_fungicide"})

    if not water_target and water_run["longest_run"] >= 14 and not irrigation_actions:
        add_issue(
            issues,
            "fail",
            "non_target_water_stress_dominates",
            "Long water stress appears in a non-water scenario without irrigation or an explicit water-limited objective",
            **water_run,
        )
    if (
        water_target
        and water_run["longest_run"] >= 21
        and not irrigation_actions
        and "waterlimit" not in spec.slug
    ):
        add_issue(
            issues,
            "fail",
            "target_water_stress_unhandled",
            "Water-stress scenario has a long stress run but no irrigation or explicit water-limit handling",
            **water_run,
        )
    if (
        not nutrient_target
        and nutrient_run["longest_run"] >= 21
        and not fertigation_actions
    ):
        add_issue(
            issues,
            "fail",
            "non_target_nutrient_stress_dominates",
            "Long nutrient stress appears in a non-nutrient scenario without a nutrient-management reason",
            **nutrient_run,
        )
    if not weed_target and weed_run["longest_run"] >= 14 and not herbicide_actions:
        add_issue(
            issues,
            "fail",
            "non_target_weed_pressure_dominates",
            "Weed pressure dominates a scenario whose core is not weed management",
            **weed_run,
        )
    if (
        not insect_target
        and insect_run["longest_run"] >= 10
        and not insecticide_actions
    ):
        add_issue(
            issues,
            "fail",
            "non_target_insect_pressure_dominates",
            "Insect pressure dominates a scenario whose core is not insect management",
            **insect_run,
        )
    if (
        not disease_target
        and disease_run["longest_run"] >= 14
        and not fungicide_actions
    ):
        add_issue(
            issues,
            "fail",
            "non_target_disease_pressure_dominates",
            "Disease pressure dominates a scenario whose core is not disease management",
            **disease_run,
        )


def slug_mentions(slug: str, tokens: set[str]) -> bool:
    normalized = slug.replace("-", "_").lower()
    return any(token in normalized for token in tokens)


def stress_run_summary(
    rows: list[dict[str, Any]],
    field: str,
    *,
    mode: str,
    threshold: float,
    share_threshold: float,
    max_stage_rank: int,
) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if int(row.get("stage_rank") or -1) > max_stage_rank:
            continue
        date_value = row.get("date")
        value = row.get(field)
        if not date_value or value is None:
            continue
        by_date[str(date_value)].append(float(value))

    bad_days: list[dict[str, Any]] = []
    for day, values in sorted(by_date.items()):
        if not values:
            continue
        avg_value = sum(values) / len(values)
        min_value = min(values)
        max_value = max(values)
        if mode == "low":
            share = sum(1 for value in values if value < threshold) / len(values)
            bad = share >= share_threshold or avg_value < threshold
        else:
            share = sum(1 for value in values if value > threshold) / len(values)
            bad = share >= share_threshold or max_value > threshold + 0.18
        if bad:
            bad_days.append(
                {
                    "date": day,
                    "avg": round(avg_value, 4),
                    "min": round(min_value, 4),
                    "max": round(max_value, 4),
                    "share": round(share, 4),
                }
            )

    longest, start = longest_consecutive_date_run(
        [str(item["date"]) for item in bad_days]
    )
    worst = None
    if bad_days:
        if mode == "low":
            worst = min(bad_days, key=lambda item: item["avg"])
        else:
            worst = max(bad_days, key=lambda item: item["max"])
    return {
        "field": field,
        "days": len(bad_days),
        "longest_run": longest,
        "run_start": start,
        "worst_day": worst,
    }


def longest_consecutive_date_run(days: list[str]) -> tuple[int, str | None]:
    parsed = sorted(day for day in (coerce_date(item) for item in days) if day)
    if not parsed:
        return 0, None
    longest = current = 1
    start = best_start = parsed[0]
    previous = parsed[0]
    for day in parsed[1:]:
        if (day - previous).days == 1:
            current += 1
        else:
            if current > longest:
                longest = current
                best_start = start
            current = 1
            start = day
        previous = day
    if current > longest:
        longest = current
        best_start = start
    return longest, best_start.isoformat()


def check_trace(
    spec: ScenarioSpec,
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    events = trace.get("completed_events") or []
    failed = [event for event in events if event.get("failed")]
    tool_errors = [
        event
        for event in events
        if isinstance(event.get("return_value"), dict)
        and (
            event["return_value"].get("status") == "error"
            or "error" in event["return_value"]
        )
    ]
    if failed:
        add_issue(
            issues,
            "fail",
            "failed_events",
            "Oracle trace contains failed events",
            count=len(failed),
            sample=[event.get("event_id") for event in failed[:8]],
        )
    if tool_errors:
        add_issue(
            issues,
            "fail",
            "tool_error_returns",
            "Oracle trace contains tool-level error returns",
            count=len(tool_errors),
            sample=[event.get("event_id") for event in tool_errors[:8]],
        )

    explicit = trace.get("action_justifications") or []
    reconstructed = reconstruct_action_chains(events)
    weak_actions = [
        item
        for item in reconstructed
        if item["action_function"]
        not in {"charge", "unload_grain", "dry_grain", "store_grain"}
        and len(item["prior_check_event_ids"]) < 1
    ]
    if weak_actions:
        add_issue(
            issues,
            "warn",
            "action_without_recent_check",
            "Some actions have no recent check event in completed_events",
            sample=weak_actions[:8],
        )
    return {
        "completed_event_count": len(events),
        "failed_event_count": len(failed),
        "tool_error_return_count": len(tool_errors),
        "native_action_justification_count": len(explicit),
        "reconstructed_action_count": len(reconstructed),
        "action_functions": dict(
            Counter(item["action_function"] for item in reconstructed)
        ),
    }


def reconstruct_action_chains(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chains = []
    recent_checks: list[dict[str, Any]] = []
    for event in events:
        fn = event.get("function")
        if fn in CHECK_FUNCTIONS:
            recent_checks.append(event)
            recent_checks = recent_checks[-10:]
            continue
        if fn not in ACTION_FUNCTIONS:
            continue
        checks = recent_checks[-8:]
        chains.append(
            {
                "action_event_id": event.get("event_id"),
                "action_function": fn,
                "prior_check_event_ids": [check.get("event_id") for check in checks],
                "prior_check_functions": [check.get("function") for check in checks],
                "action_return_value": event.get("return_value"),
            }
        )
    return chains


def build_action_support_report(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Explain how recent tool returns support each key management action."""
    events = trace.get("completed_events") or []
    support: list[dict[str, Any]] = []
    recent_context: list[dict[str, Any]] = []
    for event in events:
        fn = event.get("function")
        if fn in DIAGNOSTIC_DECISION_ACTIONS:
            support.append(build_single_action_support(event, recent_context))
            recent_context.append(event)
            recent_context = recent_context[-60:]
            continue
        if fn in CHECK_FUNCTIONS or fn in {"unload_grain"}:
            recent_context.append(event)
            recent_context = recent_context[-60:]
            continue
        if fn not in KEY_DECISION_ACTIONS:
            continue

        support.append(build_single_action_support(event, recent_context))
        if fn in {"harvest", "dry_grain"}:
            recent_context.append(event)
            recent_context = recent_context[-60:]
    return support


def build_single_action_support(
    event: dict[str, Any],
    recent_context: list[dict[str, Any]],
) -> dict[str, Any]:
    fn = str(event.get("function") or "")
    action_range = action_target_range(event)
    relevant = select_relevant_context(fn, recent_context)
    evidence = [
        summarize_context_event(ctx, action_range)
        for ctx in relevant
        if isinstance(ctx.get("return_value"), dict)
    ]
    evidence = [item for item in evidence if item]
    evidence.extend(infer_self_scope_evidence(event, action_range))
    verdict, missing = support_verdict(fn, evidence)
    return {
        "action_event_id": event.get("event_id"),
        "action_function": fn,
        "action_role": "diagnostic_check"
        if fn in DIAGNOSTIC_DECISION_ACTIONS
        else "management_action",
        "target": action_range,
        "action_return_value": summarize_action_return(event.get("return_value")),
        "support_verdict": verdict,
        "missing_evidence_groups": missing,
        "supporting_tool_returns": evidence,
        "support_statement": build_support_statement(
            fn, action_range, evidence, verdict, missing
        ),
    }


def summarize_action_support(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(item["support_verdict"] for item in items)
    by_function: dict[str, Counter[str]] = defaultdict(Counter)
    for item in items:
        by_function[item["action_function"]][item["support_verdict"]] += 1
    return {
        "key_action_count": len(items),
        "verdict_counts": dict(counts),
        "by_function": {
            fn: dict(counter) for fn, counter in sorted(by_function.items())
        },
    }


def select_relevant_context(
    action_fn: str, recent_context: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    priority = {
        "plant_seeds": {
            "get_current_weather",
            "get_forecast",
            "read_soil_sensors",
            "get_status",
            "get_inventory",
        },
        "replant_seeds": {
            "read_soil_sensors",
            "read_canopy_sensors",
            "fly_survey",
            "get_ridge_range_state",
            "inspect_crop_health",
            "inspect_pests",
            "check_status",
        },
        "apply_fertigation": {
            "read_soil_sensors",
            "read_canopy_sensors",
            "fly_survey",
            "get_ridge_range_state",
            "inspect_crop_health",
            "inspect_pests",
            "check_status",
        },
        "irrigate": {
            "get_current_weather",
            "get_forecast",
            "read_soil_sensors",
            "fly_survey",
            "inspect_crop_health",
            "inspect_pests",
        },
        "apply_fungicide": {
            "get_current_weather",
            "get_forecast",
            "read_soil_sensors",
            "get_ridge_range_state",
            "fly_survey",
            "inspect_crop_health",
            "inspect_pests",
        },
        "apply_herbicide": {
            "get_current_weather",
            "get_forecast",
            "read_soil_sensors",
            "get_ridge_range_state",
            "fly_survey",
            "inspect_crop_health",
            "inspect_pests",
        },
        "mechanical_weed_control": {
            "get_current_weather",
            "get_forecast",
            "read_soil_sensors",
            "get_ridge_range_state",
            "fly_survey",
            "inspect_crop_health",
            "inspect_pests",
        },
        "apply_pesticide": {
            "get_current_weather",
            "get_forecast",
            "read_soil_sensors",
            "get_ridge_range_state",
            "fly_survey",
            "inspect_crop_health",
            "inspect_pests",
        },
        "spray_pesticide": {
            "get_current_weather",
            "get_forecast",
            "read_soil_sensors",
            "get_ridge_range_state",
            "fly_survey",
            "inspect_crop_health",
            "inspect_pests",
        },
        "harvest": {
            "get_current_weather",
            "get_forecast",
            "read_soil_sensors",
            "get_farm_overview",
            "get_ridge_range_state",
        },
        "dry_grain": {"harvest", "unload_grain", "get_farm_overview", "get_status"},
        "store_grain": {"dry_grain", "unload_grain", "get_farm_overview", "get_status"},
        "fly_survey": {
            "read_soil_sensors",
            "read_canopy_sensors",
            "get_ridge_range_state",
            "fly_survey",
        },
        "inspect_crop_health": {
            "read_soil_sensors",
            "read_canopy_sensors",
            "fly_survey",
            "get_farm_overview",
            "get_ridge_range_state",
            "check_status",
        },
        "inspect_pests": {
            "read_soil_sensors",
            "read_canopy_sensors",
            "fly_survey",
            "inspect_crop_health",
            "check_status",
        },
        "inspect_emergence": {
            "read_soil_sensors",
            "read_canopy_sensors",
            "fly_survey",
            "check_status",
        },
        "get_ridge_range_state": {
            "get_farm_overview",
            "read_soil_sensors",
            "get_current_weather",
            "get_forecast",
        },
    }
    wanted = priority.get(action_fn, CHECK_FUNCTIONS)
    selected = [ctx for ctx in recent_context if ctx.get("function") in wanted]
    return selected[-8:]


def support_verdict(
    action_fn: str, evidence: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    groups = {group for item in evidence for group in item.get("evidence_groups", [])}
    if action_fn == "fly_survey":
        if groups & {
            "sensor_scope_basis",
            "routine_scope_basis",
            "reference_scope_basis",
            "drone_scope_basis",
        }:
            return "supported", []
        if groups:
            return "partial", ["sensor_scope_basis_or_routine_or_reference_scope"]
        return "missing", ["sensor_scope_basis_or_routine_or_reference_scope"]
    if action_fn in {"inspect_crop_health", "inspect_pests", "inspect_emergence"}:
        if groups & {
            "drone_scope_basis",
            "routine_scope_basis",
            "reference_scope_basis",
        }:
            return "supported", []
        if groups:
            return "partial", ["drone_scope_basis_or_routine_or_reference_scope"]
        return "missing", ["drone_scope_basis_or_routine_or_reference_scope"]
    requirements = {
        "plant_seeds": {"weather", "soil"},
        "replant_seeds": {"crop_diagnosis"},
        "apply_fertigation": {"crop_diagnosis"},
        "irrigate": {"soil"},
        "apply_fungicide": {"crop_diagnosis"},
        "apply_herbicide": {"crop_diagnosis"},
        "apply_pesticide": {"crop_diagnosis"},
        "spray_pesticide": {"crop_diagnosis"},
        "mechanical_weed_control": {"crop_diagnosis"},
        "harvest": {"weather", "harvest_readiness"},
        "dry_grain": {"grain_logistics"},
        "store_grain": {"grain_logistics"},
        "fly_survey": {"scope_basis"},
        "inspect_crop_health": {"scope_basis"},
        "inspect_pests": {"scope_basis"},
        "inspect_emergence": {"scope_basis"},
        "get_ridge_range_state": {"scope_basis"},
    }
    required = requirements.get(action_fn, set())
    missing = sorted(required - groups)
    if not missing:
        return "supported", []
    if groups:
        return "partial", missing
    return "missing", missing


def summarize_context_event(
    event: dict[str, Any],
    action_range: dict[str, Any],
) -> dict[str, Any]:
    fn = event.get("function")
    rv = event.get("return_value")
    if not isinstance(rv, dict):
        return {}
    target = ridge_bounds_from_target(action_range)
    base = {"event_id": event.get("event_id"), "function": fn}

    if fn == "get_current_weather":
        rain = to_float(rv.get("rainfall_mm")) or 0.0
        wind = to_float(rv.get("wind_speed_ms"))
        base.update(
            {
                "evidence_groups": ["weather"],
                "key_values": {
                    "date": rv.get("date"),
                    "rainfall_mm": rain,
                    "wind_speed_ms": round_or_none(wind),
                    "temp_c": round_or_none(to_float(rv.get("temp_c"))),
                },
                "support_text": f"current weather date={rv.get('date')}, rain={rain:.2f} mm, wind={round_or_none(wind)} m/s",
            }
        )
        return base

    if fn == "get_forecast":
        forecast = rv.get("forecast") or []
        rain_values = [
            to_float(day.get("rainfall_mm")) or 0.0
            for day in forecast
            if isinstance(day, dict)
        ]
        rainy_days = sum(1 for value in rain_values if value > 0.5)
        max_rain = max(rain_values, default=0.0)
        base.update(
            {
                "evidence_groups": ["weather"],
                "key_values": {
                    "days": len(forecast),
                    "rainy_days": rainy_days,
                    "max_rain_mm": round(max_rain, 3),
                },
                "support_text": f"forecast days={len(forecast)}, rainy_days={rainy_days}, max_rain={max_rain:.2f} mm",
            }
        )
        return base

    if fn == "read_soil_sensors":
        sensors = rv.get("soil_sensors") or []
        selected = select_range_items(sensors, target)
        vwcs = [
            to_float(item.get("vwc"))
            for item in selected
            if to_float(item.get("vwc")) is not None
        ]
        temps = [
            to_float(item.get("temp_c"))
            for item in selected
            if to_float(item.get("temp_c")) is not None
        ]
        scope = sensor_anomaly_scope(sensors, target, value_key="vwc", mode="soil_vwc")
        groups = ["soil"]
        if scope["target_covered_by_anomaly"]:
            groups.extend(["scope_basis", "sensor_scope_basis"])
        base.update(
            {
                "evidence_groups": groups,
                "key_values": {
                    "sensor_count": len(selected),
                    "avg_vwc": round_or_none(avg_numbers(vwcs)),
                    "min_vwc": round_or_none(min(vwcs) if vwcs else None),
                    "max_vwc": round_or_none(max(vwcs) if vwcs else None),
                    "avg_temp_c": round_or_none(avg_numbers(temps)),
                    "anomalous_ranges": scope["anomalous_ranges"],
                    "target_covered_by_anomaly": scope["target_covered_by_anomaly"],
                },
                "support_text": f"soil sensors overlapping target={len(selected)}, avg_vwc={round_or_none(avg_numbers(vwcs))}, anomaly_ranges={scope['anomalous_ranges']}, target_covered={scope['target_covered_by_anomaly']}",
            }
        )
        return base

    if fn == "read_canopy_sensors":
        observations = rv.get("canopy_sensors") or []
        selected = select_range_items(observations, target)
        if target and observations and not selected:
            return {}
        ndvis = [to_float(item.get("ndvi_proxy")) for item in selected]
        ndvis = [value for value in ndvis if value is not None]
        scope = sensor_anomaly_scope(
            observations, target, value_key="ndvi_proxy", mode="canopy_ndvi"
        )
        groups = ["crop_diagnosis"]
        if scope["target_covered_by_anomaly"]:
            groups.extend(["scope_basis", "sensor_scope_basis"])
        base.update(
            {
                "evidence_groups": groups,
                "key_values": {
                    "sensor_count": len(selected),
                    "avg_ndvi": round_or_none(avg_numbers(ndvis)),
                    "min_ndvi": round_or_none(min(ndvis) if ndvis else None),
                    "anomalous_ranges": scope["anomalous_ranges"],
                    "target_covered_by_anomaly": scope["target_covered_by_anomaly"],
                },
                "support_text": f"canopy sensors overlapping target={len(selected)}, avg_ndvi={round_or_none(avg_numbers(ndvis))}, anomaly_ranges={scope['anomalous_ranges']}, target_covered={scope['target_covered_by_anomaly']}",
            }
        )
        return base

    if fn == "fly_survey":
        observations = rv.get("observations") or rv.get("canopy_sensors") or []
        selected = select_range_items(observations, target)
        if target and observations and not selected:
            return {}
        ndvis = [
            to_float(item.get("ndvi") if "ndvi" in item else item.get("ndvi_proxy"))
            for item in selected
        ]
        ndvis = [value for value in ndvis if value is not None]
        temps = [
            to_float(item.get("canopy_temp_c"))
            for item in selected
            if to_float(item.get("canopy_temp_c")) is not None
        ]
        base.update(
            {
                "evidence_groups": [
                    "crop_diagnosis",
                    "scope_basis",
                    "drone_scope_basis",
                ],
                "key_values": {
                    "observation_count": len(selected),
                    "avg_ndvi": round_or_none(avg_numbers(ndvis)),
                    "min_ndvi": round_or_none(min(ndvis) if ndvis else None),
                    "avg_canopy_temp_c": round_or_none(avg_numbers(temps)),
                },
                "support_text": f"{fn} target observations={len(selected)}, avg_ndvi={round_or_none(avg_numbers(ndvis))}, min_ndvi={round_or_none(min(ndvis) if ndvis else None)}",
            }
        )
        return base

    if fn in {"inspect_crop_health", "inspect_pests", "inspect_emergence"}:
        obs = list((rv.get("observations") or {}).values())
        selected = select_range_items(obs, target)
        if target and obs and not selected:
            return {}
        stands = [
            to_float(item.get("stand_fraction"))
            for item in selected
            if to_float(item.get("stand_fraction")) is not None
        ]
        pest_count = sum(1 for item in selected if item.get("pest_present") is True)
        disease_count = sum(
            1 for item in selected if item.get("disease_present") is True
        )
        groups = ["crop_diagnosis", "ground_confirmation"]
        if fn == "inspect_pests":
            groups.append("biotic_ruleout")
        base.update(
            {
                "evidence_groups": groups,
                "key_values": {
                    "covered_count": len(selected),
                    "pest_present_count": pest_count,
                    "disease_present_count": disease_count,
                    "avg_stand_fraction": round_or_none(avg_numbers(stands)),
                    "min_stand_fraction": round_or_none(
                        min(stands) if stands else None
                    ),
                    "battery_remaining_pct": rv.get("battery_remaining_pct"),
                },
                "support_text": f"{fn} covered={len(selected)}, pest_present={pest_count}, disease_present={disease_count}, min_stand={round_or_none(min(stands) if stands else None)}",
            }
        )
        return base

    if fn == "get_ridge_range_state":
        ridges = rv.get("ridges") or []
        selected = select_range_items(ridges, target)
        if target and ridges and not selected:
            return {}
        moistures = [
            to_float(item.get("grain_moisture_pct"))
            for item in selected
            if to_float(item.get("grain_moisture_pct")) is not None
        ]
        vwcs = [
            to_float(item.get("soil_vwc"))
            for item in selected
            if to_float(item.get("soil_vwc")) is not None
        ]
        stages = Counter(
            str(item.get("growth_stage"))
            for item in selected
            if item.get("growth_stage")
        )
        ready_ridges = [
            item.get("ridge_id")
            for item in selected
            if "R8" in str(item.get("growth_stage", "")).upper()
            and (to_float(item.get("grain_moisture_pct")) is not None)
            and (to_float(item.get("grain_moisture_pct")) or 999.0) <= 18.0
            # Agent-facing ridge range state no longer exposes soil_vwc; when
            # present, use it as an additional trafficability check, otherwise
            # harvest readiness here means stage + grain moisture and soil
            # support must come from read_soil_sensors evidence.
            and (
                to_float(item.get("soil_vwc")) is None
                or (to_float(item.get("soil_vwc")) or 999.0) <= 0.35
            )
        ]
        ready_count = len(ready_ridges)
        groups = ["crop_diagnosis", "scope_basis"]
        if ready_ridges_cover_target(ready_ridges, target):
            groups.append("harvest_readiness")
        base.update(
            {
                "evidence_groups": groups,
                "key_values": {
                    "ridge_count": len(selected),
                    "stage_counts": dict(stages),
                    "avg_grain_moisture_pct": round_or_none(avg_numbers(moistures)),
                    "max_grain_moisture_pct": round_or_none(
                        max(moistures) if moistures else None
                    ),
                    "max_soil_vwc": round_or_none(max(vwcs) if vwcs else None),
                    "harvest_ready_count": ready_count,
                },
                "support_text": f"range state ridges={len(selected)}, stages={dict(stages)}, avg_moisture={round_or_none(avg_numbers(moistures))}, max_soil_vwc={round_or_none(max(vwcs) if vwcs else None)}, inferred_ready={ready_count}",
            }
        )
        return base

    if fn == "get_farm_overview":
        ridges = rv.get("ridges_overview") or []
        selected = select_range_items(ridges, target) if target else ridges
        if target and ridges and not selected:
            selected = ridges
        stage_counts = Counter(
            str(item.get("growth_stage"))
            for item in selected
            if item.get("growth_stage")
        )
        moistures = [
            to_float(item.get("grain_moisture_pct"))
            for item in selected
            if to_float(item.get("grain_moisture_pct")) is not None
        ]
        groups = ["scope_basis"]
        avg_moisture = avg_numbers(moistures)
        total_ridges = sum(stage_counts.values())
        all_r8 = total_ridges > 0 and all(
            "R8" in str(stage).upper()
            for stage in stage_counts
            if stage and stage != "None"
        )
        if all_r8 and avg_moisture is not None and avg_moisture <= 18.0:
            groups.append("harvest_readiness")
        base.update(
            {
                "evidence_groups": groups,
                "key_values": {
                    "sim_date": rv.get("sim_date"),
                    "stage_counts": dict(stage_counts),
                    "avg_grain_moisture_pct": round_or_none(avg_moisture),
                },
                "support_text": f"farm overview date={rv.get('sim_date')}, stage_counts={dict(stage_counts)}, avg_moisture={round_or_none(avg_moisture)}",
            }
        )
        return base

    if fn in {"get_status", "check_status"}:
        base.update(
            {
                "evidence_groups": ["equipment"],
                "key_values": compact_dict(
                    rv,
                    {
                        "fuel_tank_l",
                        "seed_type",
                        "seed_hopper",
                        "battery_pct",
                        "charging",
                        "attached_implement",
                        "grain_bin_kg",
                    },
                ),
                "support_text": f"{fn} equipment status {compact_dict(rv, {'fuel_tank_l', 'seed_hopper', 'battery_pct', 'attached_implement', 'grain_bin_kg'})}",
            }
        )
        return base

    if fn == "get_inventory":
        base.update(
            {
                "evidence_groups": ["inventory"],
                "key_values": compact_dict(
                    rv,
                    {
                        "pesticide_liters",
                        "fertilizer_kg",
                        "fuel_liters",
                        "harvest_grain_kg",
                        "warehouse_grain_kg",
                    },
                ),
                "support_text": f"inventory {compact_dict(rv, {'pesticide_liters', 'fertilizer_kg', 'fuel_liters'})}",
            }
        )
        return base

    if fn in {"harvest", "unload_grain", "dry_grain"}:
        base.update(
            {
                "evidence_groups": ["grain_logistics"],
                "key_values": compact_dict(
                    rv,
                    {
                        "status",
                        "grain_kg_added",
                        "unloaded_kg",
                        "warehouse_grain_kg",
                        "grain_bin_kg",
                        "target_moisture_pct",
                    },
                ),
                "support_text": f"grain logistics from {fn}: {compact_dict(rv, {'status', 'grain_kg_added', 'unloaded_kg', 'warehouse_grain_kg', 'grain_bin_kg', 'target_moisture_pct'})}",
            }
        )
        return base

    return {
        **base,
        "evidence_groups": [],
        "key_values": {},
        "support_text": f"{fn} return available but no specialized summarizer",
    }


def build_support_statement(
    action_fn: str,
    action_range: dict[str, Any],
    evidence: list[dict[str, Any]],
    verdict: str,
    missing: list[str],
) -> str:
    target = (
        action_range.get("ridge_range")
        or action_range.get("ridge_count")
        or "unknown target"
    )
    ordered = sorted(
        evidence,
        key=lambda item: (
            not bool(
                set(item.get("evidence_groups", []))
                & {
                    "sensor_scope_basis",
                    "drone_scope_basis",
                    "reference_scope_basis",
                    "routine_scope_basis",
                }
            ),
            item.get("event_id") or "",
        ),
    )
    snippets = [item["support_text"] for item in ordered[:4]]
    if missing:
        snippets.append(f"missing evidence groups: {', '.join(missing)}")
    return f"{action_fn} on {target}: {verdict}. " + " | ".join(snippets)


def infer_self_scope_evidence(
    event: dict[str, Any],
    action_range: dict[str, Any],
) -> list[dict[str, Any]]:
    fn = event.get("function")
    if fn not in DIAGNOSTIC_DECISION_ACTIONS:
        return []
    event_id = str(event.get("event_id") or "")
    ridge_count = action_range.get("ridge_count")
    ridge_range = action_range.get("ridge_range")
    if ridge_count == 64 or "whole_field" in event_id:
        return [
            {
                "event_id": event_id,
                "function": fn,
                "evidence_groups": ["scope_basis", "routine_scope_basis"],
                "key_values": {
                    "basis": "routine_whole_field_check",
                    "target_ridges": ridge_range,
                    "ridge_count": ridge_count,
                },
                "support_text": f"{fn} scope is supported as a routine whole-field check over {ridge_range}",
            }
        ]
    routine_tokens = (
        "emergence",
        "early",
        "routine",
        "stage_split",
        "wet_period",
        "midseason",
        "r1_",
        "r5",
        "r6",
        "pod",
        "density",
    )
    targeted_tokens = (
        "affected",
        "suspect",
        "threshold",
        "edge_low",
        "edge_thermal",
        "patch",
        "targeted",
    )
    if any(token in event_id for token in routine_tokens) and not any(
        token in event_id for token in targeted_tokens
    ):
        return [
            {
                "event_id": event_id,
                "function": fn,
                "evidence_groups": ["scope_basis", "routine_scope_basis"],
                "key_values": {
                    "basis": "routine_growth_or_stage_check",
                    "target_ridges": ridge_range,
                    "ridge_count": ridge_count,
                },
                "support_text": f"{fn} scope is supported as a routine growth/stage check over {ridge_range}",
            }
        ]
    if any(token in event_id for token in ("reference", "control")):
        return [
            {
                "event_id": event_id,
                "function": fn,
                "evidence_groups": ["scope_basis", "reference_scope_basis"],
                "key_values": {
                    "basis": "reference_or_control_check",
                    "target_ridges": ridge_range,
                    "ridge_count": ridge_count,
                },
                "support_text": f"{fn} scope is supported as a reference/control comparison over {ridge_range}",
            }
        ]
    return []


def action_target_range(event: dict[str, Any]) -> dict[str, Any]:
    rv = event.get("return_value")
    if not isinstance(rv, dict):
        rv = {}
    ridges = (
        rv.get("treated_ridges")
        or rv.get("sprayed_ridges")
        or rv.get("irrigated_ridges")
        or rv.get("harvested_ridges")
        or rv.get("planted_ridges")
        or rv.get("replanted_ridges")
        or rv.get("fertigated_ridges")
        or rv.get("surveyed_ridges")
        or rv.get("covered_ridges")
        or rv.get("ridges")
        or rv.get("ridge_ids")
        or []
    )
    if ridges and isinstance(ridges[0], dict):
        ridges = [
            item.get("ridge_id")
            for item in ridges
            if isinstance(item, dict) and item.get("ridge_id") is not None
        ]
    if ridges:
        return {
            "ridge_range": f"{min(ridges)}-{max(ridges)}",
            "min_ridge": min(ridges),
            "max_ridge": max(ridges),
            "ridge_count": len(ridges),
        }
    return {
        "ridge_range": None,
        "min_ridge": None,
        "max_ridge": None,
        "ridge_count": None,
    }


def ridge_bounds_from_target(target: dict[str, Any]) -> tuple[int, int] | None:
    if target.get("min_ridge") is None or target.get("max_ridge") is None:
        return None
    return int(target["min_ridge"]), int(target["max_ridge"])


def select_range_items(
    items: list[dict[str, Any]], target: tuple[int, int] | None
) -> list[dict[str, Any]]:
    if not target:
        return items
    start, end = target
    selected = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_start = item.get("ridge_start", item.get("ridge_id"))
        item_end = item.get("ridge_end", item.get("ridge_id"))
        if item_start is None or item_end is None:
            selected.append(item)
            continue
        try:
            a = int(item_start)
            b = int(item_end)
        except (TypeError, ValueError):
            selected.append(item)
            continue
        if a <= end and b >= start:
            selected.append(item)
    return selected


def ready_ridges_cover_target(
    ready_ridges: Any, target: tuple[int, int] | None
) -> bool:
    if not target:
        return bool(ready_ridges)
    if not isinstance(ready_ridges, list):
        return False
    ready_ids = {
        int(item.get("ridge_id") if isinstance(item, dict) else item)
        for item in ready_ridges
        if (item.get("ridge_id") if isinstance(item, dict) else item) is not None
    }
    start, end = target
    return all(ridge_id in ready_ids for ridge_id in range(start, end + 1))


def sensor_anomaly_scope(
    sensors: list[dict[str, Any]],
    target: tuple[int, int] | None,
    *,
    value_key: str,
    mode: str,
) -> dict[str, Any]:
    """Return sensor-derived abnormal ridge ranges.

    This is intentionally stricter than "a sensor overlaps the target".
    A targeted diagnostic flight is only scoped by sensors when the target is
    covered by sensor zones whose values are measurably different from the
    same-read field median.
    """
    values = [
        to_float(sensor.get(value_key))
        for sensor in sensors
        if to_float(sensor.get(value_key)) is not None
        and to_float(sensor.get(value_key)) != -1.0
    ]
    median = median_number(values)
    anomalous: list[tuple[int, int]] = []
    if median is not None:
        for sensor in sensors:
            value = to_float(sensor.get(value_key))
            start = sensor.get("ridge_start")
            end = sensor.get("ridge_end")
            if value is None or value == -1.0 or start is None or end is None:
                continue
            try:
                ridge_range = (int(start), int(end))
            except (TypeError, ValueError):
                continue
            if mode == "canopy_ndvi":
                is_anomaly = value <= median - 0.018
            elif mode == "soil_vwc":
                is_anomaly = abs(value - median) >= 0.025
            else:
                is_anomaly = False
            if is_anomaly:
                anomalous.append(ridge_range)

    merged = merge_ranges(anomalous)
    overlap = anomaly_overlap_fraction(target, merged)
    return {
        "field_median": round_or_none(median),
        "anomalous_ranges": [f"{start}-{end}" for start, end in merged],
        "target_anomaly_overlap_fraction": round(overlap, 3),
        "target_covered_by_anomaly": bool(
            target and (range_covered_by_ranges(target, merged) or overlap >= 0.65)
        ),
    }


def median_number(values: list[float | None]) -> float | None:
    nums = sorted(float(value) for value in values if value is not None)
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2.0


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def range_covered_by_ranges(
    target: tuple[int, int],
    ranges: list[tuple[int, int]],
) -> bool:
    if not ranges:
        return False
    uncovered_start, target_end = target
    for start, end in merge_ranges(ranges):
        if end < uncovered_start:
            continue
        if start > uncovered_start:
            return False
        uncovered_start = max(uncovered_start, end + 1)
        if uncovered_start > target_end:
            return True
    return False


def anomaly_overlap_fraction(
    target: tuple[int, int] | None,
    ranges: list[tuple[int, int]],
) -> float:
    if not target or not ranges:
        return 0.0
    start, end = target
    target_len = max(0, end - start + 1)
    if target_len == 0:
        return 0.0
    covered = 0
    for a, b in merge_ranges(ranges):
        covered += max(0, min(end, b) - max(start, a) + 1)
    return min(1.0, covered / target_len)


def summarize_action_return(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"raw": str(value)[:240]}
    keys = {
        "status",
        "error",
        "grain_kg_added",
        "grain_bin_kg",
        "pesticide_used_liters",
        "fungicide_used_liters",
        "herbicide_used_liters",
        "nutrient_amount",
        "carrier_water_mm",
        "duration_hours_per_ridge",
        "seeds_used",
        "unloaded_kg",
        "warehouse_grain_kg",
    }
    out = compact_dict(value, keys)
    target = action_target_range({"return_value": value})
    out.update({k: v for k, v in target.items() if v is not None})
    return out


def compact_dict(value: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: value.get(key) for key in sorted(keys) if key in value}


def avg_numbers(values: list[float | None]) -> float | None:
    nums = [float(value) for value in values if value is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def check_scenario_specific(
    spec: ScenarioSpec,
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    if spec.slug == "heinong84_edge_low_fertility":
        edge_low_fertility_checks(rows, issues, metrics)
    elif spec.slug == "fastdraining_dry_patch_irrigation":
        fastdraining_checks(rows, trace, issues, metrics)
    elif spec.slug == "wet_june_ab_zoned_disease":
        wet_june_disease_checks(rows, trace, issues, metrics)
    elif spec.slug == "heinong84_staggered_planting":
        staggered_checks(rows, trace, issues, metrics)
    elif spec.slug == "heinong84_threshold_insect_limited_spray":
        insect_checks(rows, trace, issues, metrics)
    elif spec.slug == "heinong84_low_chemical_wet_disease":
        low_chemical_disease_checks(rows, trace, issues, metrics)
    elif spec.slug == "early_vs_standard_late_rain_harvest":
        early_vs_standard_checks(rows, trace, issues, metrics)
    elif spec.slug == "heinong60_high_density_baseline":
        high_density_checks(rows, issues, metrics)
    elif spec.slug == "hb_base_hn84_std_normal":
        hb_normal_baseline_checks(rows, trace, issues, metrics)
    elif spec.slug == "hb_dryr5r6_hn58_std_waterlimit":
        hb_waterlimited_drought_checks(rows, trace, issues, metrics)
    elif spec.slug == "hb_poordrainage_wetjune_disease_trafficability":
        hb_poordrainage_checks(rows, trace, issues, metrics)
    elif spec.slug == "hb_soy_after_soy_wetjune_disease":
        hb_soy_history_checks(rows, trace, issues, metrics)


def hb_normal_baseline_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    stress_actions = action_ranges(
        trace, {"irrigate", "apply_fungicide", "spray_pesticide", "apply_pesticide"}
    )
    metrics["hb_normal_baseline"] = {
        "stress_actions": stress_actions,
        "max_disease_pressure": round(
            max(
                (float(row.get("disease_pressure") or 0.0) for row in rows), default=0.0
            ),
            4,
        ),
        "min_water_stress": round(
            min((float(row.get("water_stress") or 1.0) for row in rows), default=1.0), 4
        ),
    }
    if stress_actions:
        add_issue(
            issues,
            "fail",
            "baseline_has_stress_action",
            "Normal baseline should not include irrigation or pesticide/fungicide stress response",
            sample=stress_actions,
        )


def hb_waterlimited_drought_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    pre = rows_matching_label(rows, ("r5_r6_drought", "r5_r6"))
    priority = zone_values(pre, "priority_22_43")
    references = zone_values(pre, "reference_west_0_10") + zone_values(
        pre, "reference_east_54_63"
    )
    irrigation = action_ranges(trace, {"irrigate"})
    metrics["hb_waterlimited_drought"] = {
        "priority_vs_reference": compare_zone_means(
            priority, references, ["root_vwc", "water_stress", "canopy_temp"]
        ),
        "irrigation_actions": irrigation,
    }
    if not irrigation:
        add_issue(
            issues,
            "fail",
            "missing_limited_irrigation",
            "Water-limited drought scenario has no irrigation action",
        )
    bad = [item for item in irrigation if not range_within(item, 22, 43)]
    if bad:
        add_issue(
            issues,
            "fail",
            "waterlimited_irrigation_not_targeted",
            "Irrigation touched ridges outside priority 22-43",
            sample=bad,
        )
    if (
        priority
        and references
        and avg(priority, "root_vwc") >= avg(references, "root_vwc") - 0.006
    ):
        add_issue(
            issues,
            "warn",
            "waterlimited_root_vwc_signal_weak",
            "Priority R5/R6 ridges are not clearly drier in root-zone VWC than references",
        )


def hb_poordrainage_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    wet = rows_matching_label(rows, ("wet_june", "post_rain", "poordrainage"))
    affected = zone_values(wet, "poor_drainage_44_53")
    reference = zone_values(wet, "reference_0_10")
    fungicide = action_ranges(trace, {"apply_fungicide"})
    metrics["hb_poordrainage"] = {
        "affected_vs_reference": compare_zone_means(
            affected, reference, ["top_vwc", "disease_pressure", "ndvi"]
        ),
        "fungicide_actions": fungicide,
    }
    if not fungicide:
        add_issue(
            issues,
            "fail",
            "missing_poordrainage_fungicide",
            "Poor-drainage disease scenario has no targeted fungicide",
        )
    bad = [item for item in fungicide if not range_within(item, 44, 53)]
    if bad:
        add_issue(
            issues,
            "fail",
            "poordrainage_fungicide_not_targeted",
            "Fungicide touched ridges outside poor-drainage 44-53",
            sample=bad,
        )
    if (
        affected
        and reference
        and avg(affected, "top_vwc") <= avg(reference, "top_vwc") + 0.02
    ):
        add_issue(
            issues,
            "warn",
            "poordrainage_wet_signal_weak",
            "Poor-drainage zone is not clearly wetter than reference",
        )


def hb_soy_history_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    wet = rows_matching_label(rows, ("soy_history", "wet_june", "disease"))
    affected = zone_values(wet, "history_affected_22_43")
    reference = zone_values(wet, "reference_west_0_21") + zone_values(
        wet, "reference_east_44_63"
    )
    fungicide = action_ranges(trace, {"apply_fungicide"})
    metrics["hb_soy_history"] = {
        "affected_vs_reference": compare_zone_means(
            affected, reference, ["disease_pressure", "ndvi", "top_vwc"]
        ),
        "fungicide_actions": fungicide,
    }
    if not fungicide:
        add_issue(
            issues,
            "fail",
            "missing_soy_history_fungicide",
            "Soy-after-soy wet-June disease scenario has no targeted fungicide",
        )
    bad = [item for item in fungicide if not range_within(item, 22, 43)]
    if bad:
        add_issue(
            issues,
            "fail",
            "soy_history_fungicide_not_targeted",
            "Fungicide touched ridges outside history-affected 22-43",
            sample=bad,
        )
    if (
        affected
        and reference
        and avg(affected, "disease_pressure")
        <= avg(reference, "disease_pressure") + 0.05
    ):
        add_issue(
            issues,
            "warn",
            "soy_history_disease_signal_weak",
            "History-affected ridges are not clearly higher disease than reference",
        )


def edge_low_fertility_checks(
    rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    early = rows_matching_label(rows, ("emergence", "early"))
    if not early:
        early = rows[: 64 * 5]
    severe = zone_values(early, "severe_0_3")
    healthy = zone_values(early, "healthy_12_63")
    metrics["edge_low_fertility"] = compare_zone_means(
        severe, healthy, ["nutrient_index", "stand_fraction", "ndvi"]
    )
    if avg(severe, "nutrient_index") >= avg(healthy, "nutrient_index") - 0.08:
        add_issue(
            issues,
            "warn",
            "edge_nutrient_signal_weak",
            "0-3 are not clearly lower fertility than healthy reference early",
        )
    if avg(severe, "stand_fraction") >= avg(healthy, "stand_fraction") - 0.15:
        add_issue(
            issues,
            "warn",
            "edge_stand_signal_weak",
            "0-3 stand is not clearly weaker before recovery",
        )
    first_r8 = first_stage_dates_by_zone(rows, "R8")
    metrics["edge_low_fertility_first_r8_by_zone"] = first_r8
    severe_r8 = first_r8.get("severe_0_3")
    healthy_r8 = first_r8.get("healthy_12_63")
    if severe_r8 and healthy_r8 and severe_r8 <= healthy_r8:
        add_issue(
            issues,
            "fail",
            "edge_severe_not_later_maturing",
            "Severe edge ridges should mature later after full replant, but R8 is not delayed versus healthy reference",
        )
    harvested_rows = [
        row for row in rows if str(row.get("harvested", "")).upper() in {"TRUE", "1"}
    ]
    if harvested_rows:
        first_harvest: dict[str, str] = {}
        for row in harvested_rows:
            zone = str(row.get("zone") or "")
            date = str(row.get("weather_date") or "")
            if zone and date and zone not in first_harvest:
                first_harvest[zone] = date
        metrics["edge_low_fertility_first_harvest_by_zone"] = first_harvest
        severe_harvest = first_harvest.get("severe_0_3")
        healthy_harvest = first_harvest.get("healthy_12_63")
        if severe_harvest and healthy_harvest and severe_harvest <= healthy_harvest:
            add_issue(
                issues,
                "fail",
                "edge_severe_not_split_harvested_later",
                "Severe edge ridges should be harvested after the healthy/mild zones",
            )


def fastdraining_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    pre = rows_matching_label(rows, ("before_irrigation", "dry_stress", "r5_r6"))
    post = rows_matching_label(rows, ("after_targeted_irrigation", "after_irrigation"))
    aff_pre = zone_values(pre, "affected_20_31")
    ref_pre = zone_values(pre, "reference_east_44_53") + zone_values(
        pre, "reference_west_0_11"
    )
    aff_post = zone_values(post, "affected_20_31")
    metrics["fastdraining"] = {
        "pre": compare_zone_means(
            aff_pre, ref_pre, ["root_vwc", "water_stress", "canopy_temp"]
        ),
        "post_affected": means(aff_post, ["root_vwc", "water_stress", "canopy_temp"]),
        "irrigation_actions": action_ranges(trace, {"irrigate"}),
    }
    if (
        aff_pre
        and ref_pre
        and avg(aff_pre, "root_vwc") >= avg(ref_pre, "root_vwc") - 0.02
    ):
        add_issue(
            issues,
            "warn",
            "fastdraining_water_signal_weak",
            "Affected patch root VWC is not clearly worse before irrigation",
        )
    bad_irrigation = [
        item
        for item in action_ranges(trace, {"irrigate"})
        if not range_within(item, 20, 31)
    ]
    if bad_irrigation:
        add_issue(
            issues,
            "fail",
            "irrigation_not_targeted",
            "Irrigation action touched ridges outside 20-31",
            sample=bad_irrigation,
        )


def wet_june_disease_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    pre = rows_matching_label(rows, ("disease", "wet"))
    affected = range_values(pre, 40, 55)
    b_zone = [
        row
        for row in zone_values(pre, "B_heinong60_high_density")
        if row.get("ridge_id") is None or not (40 <= int(row["ridge_id"]) <= 55)
    ]
    metrics["wet_june_disease"] = {
        "affected_vs_b": compare_zone_means(
            affected, b_zone, ["disease_pressure", "ndvi", "lai"]
        ),
        "fungicide_actions": action_ranges(trace, {"apply_fungicide"}),
    }
    if (
        affected
        and b_zone
        and avg(affected, "disease_pressure") <= avg(b_zone, "disease_pressure") + 0.08
    ):
        add_issue(
            issues,
            "warn",
            "wet_june_disease_signal_weak",
            "Affected B block disease pressure is not clearly above B reference",
        )
    bad = [
        item
        for item in action_ranges(trace, {"apply_fungicide"})
        if not range_within(item, 40, 55)
    ]
    if bad:
        add_issue(
            issues,
            "fail",
            "fungicide_not_targeted",
            "Fungicide action touched ridges outside 40-55",
            sample=bad,
        )


def staggered_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    zone_r8 = first_stage_dates_by_zone(rows, "R8")
    metrics["staggered_first_r8"] = zone_r8
    mixed_stage_snapshots = snapshots_with_multiple_zone_stages(rows)
    metrics["mixed_zone_stage_snapshot_count"] = mixed_stage_snapshots
    if mixed_stage_snapshots < 1:
        add_issue(
            issues,
            "fail",
            "staggered_stage_not_offset",
            "No trace snapshot shows zones at different dominant stages",
        )
    harvests = action_ranges(trace, {"harvest"})
    metrics["harvest_actions"] = harvests
    if len(harvests) < 2:
        add_issue(
            issues,
            "warn",
            "staggered_harvest_not_batched",
            "Expected multiple harvest actions for staggered zones",
        )


def insect_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    below = zone_values(
        rows_matching_label(rows, ("below_threshold", "early_insect")), "affected_18_37"
    )
    threshold = zone_values(
        rows_matching_label(rows, ("threshold", "before_pesticide", "late_insect")),
        "affected_18_37",
    )
    post = zone_values(
        rows_matching_label(rows, ("after_targeted_pesticide", "after_pesticide")),
        "affected_18_37",
    )
    metrics["threshold_insect"] = {
        "below": means(below, ["insect_pressure", "ndvi"]),
        "threshold": means(threshold, ["insect_pressure", "ndvi"]),
        "post": means(post, ["insect_pressure", "ndvi"]),
        "pesticide_actions": action_ranges(
            trace, {"apply_pesticide", "spray_pesticide"}
        ),
    }
    below_insect = max_value(below, "insect_pressure")
    threshold_insect = max_value(threshold, "insect_pressure")
    if below and below_insect >= 0.45:
        add_issue(
            issues,
            "warn",
            "early_insect_over_threshold",
            "Early insect check is already above a plausible treatment threshold",
        )
    if threshold and threshold_insect < 0.45:
        add_issue(
            issues,
            "warn",
            "late_insect_below_threshold",
            "Late insect check does not clearly justify pesticide",
        )
    bad = [
        item
        for item in action_ranges(trace, {"apply_pesticide", "spray_pesticide"})
        if not range_within(item, 18, 37)
    ]
    if bad:
        add_issue(
            issues,
            "fail",
            "pesticide_not_targeted",
            "Pesticide action touched ridges outside 18-37",
            sample=bad,
        )


def low_chemical_disease_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    below = zone_values(
        rows_matching_label(rows, ("below_threshold",)), "affected_22_43"
    )
    threshold = zone_values(
        rows_matching_label(rows, ("threshold", "before_fungicide")), "affected_22_43"
    )
    post = zone_values(
        rows_matching_label(rows, ("after_targeted_fungicide", "after_fungicide")),
        "affected_22_43",
    )
    ref_threshold = zone_values(
        rows_matching_label(rows, ("threshold", "before_fungicide")),
        "reference_west_0_21",
    ) + zone_values(
        rows_matching_label(rows, ("threshold", "before_fungicide")),
        "reference_east_46_63",
    )
    metrics["low_chemical_disease"] = {
        "below": means(below, ["disease_pressure", "ndvi"]),
        "threshold": means(threshold, ["disease_pressure", "ndvi"]),
        "reference_threshold": means(ref_threshold, ["disease_pressure", "ndvi"]),
        "post": means(post, ["disease_pressure", "ndvi"]),
        "fungicide_actions": action_ranges(trace, {"apply_fungicide"}),
    }
    below_disease = max_value(below, "disease_pressure")
    threshold_disease = max_value(threshold, "disease_pressure")
    if below and below_disease >= 0.45:
        add_issue(
            issues,
            "warn",
            "early_disease_over_threshold",
            "Low-chemical early check is already above a plausible treatment threshold",
        )
    if threshold and threshold_disease < 0.48:
        add_issue(
            issues,
            "warn",
            "disease_threshold_signal_weak",
            "Treatment-stage disease pressure may not justify fungicide under low-chemical rules",
        )
    if (
        threshold
        and ref_threshold
        and avg(threshold, "ndvi") > avg(ref_threshold, "ndvi") - 0.04
    ):
        add_issue(
            issues,
            "warn",
            "disease_ndvi_signal_weak",
            "NDVI gap at disease threshold is weak",
        )
    bad = [
        item
        for item in action_ranges(trace, {"apply_fungicide"})
        if not range_within(item, 22, 43)
    ]
    if bad:
        add_issue(
            issues,
            "fail",
            "fungicide_not_targeted",
            "Fungicide action touched ridges outside 22-43",
            sample=bad,
        )


def early_vs_standard_checks(
    rows: list[dict[str, Any]],
    trace: dict[str, Any],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    first_r8 = first_stage_dates_by_zone(rows, "R8")
    metrics["early_vs_standard_first_r8"] = first_r8
    a_date = first_r8.get("a_heike71_0_31")
    b_date = first_r8.get("b_heinong84_32_63")
    if a_date and b_date and a_date >= b_date:
        add_issue(
            issues,
            "fail",
            "early_cultivar_not_earlier",
            "HEIKE71 A zone did not reach R8 before HEINONG84 B zone",
        )
    harvests = action_ranges(trace, {"harvest"})
    metrics["harvest_actions"] = harvests
    if len(harvests) < 2:
        add_issue(
            issues,
            "warn",
            "early_vs_standard_harvest_not_batched",
            "Expected at least two harvest batches",
        )
    if harvests and not range_within(harvests[0], 0, 31):
        add_issue(
            issues,
            "warn",
            "first_harvest_not_a_zone",
            "First harvest is not limited to the early HEIKE71 zone",
        )


def high_density_checks(
    rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    max_lai = max(
        (row["lai"] for row in rows if row.get("lai") is not None), default=0.0
    )
    max_ndvi = max(
        (row["ndvi"] for row in rows if row.get("ndvi") is not None), default=0.0
    )
    metrics["high_density"] = {
        "max_lai": round(max_lai, 4),
        "max_ndvi": round(max_ndvi, 4),
    }
    if max_lai > 6.5:
        add_issue(
            issues,
            "warn",
            "high_density_lai_high",
            "High-density baseline LAI is unusually high",
        )
    if max_ndvi > 0.92:
        add_issue(
            issues,
            "warn",
            "high_density_ndvi_high",
            "High-density baseline NDVI is near saturation",
        )


def action_ranges(trace: dict[str, Any], functions: set[str]) -> list[dict[str, Any]]:
    result = []
    for event in trace.get("completed_events") or []:
        if event.get("function") not in functions:
            continue
        rv = event.get("return_value")
        if not isinstance(rv, dict):
            rv = {}
        ridges = (
            rv.get("treated_ridges")
            or rv.get("sprayed_ridges")
            or rv.get("irrigated_ridges")
            or rv.get("harvested_ridges")
            or rv.get("planted_ridges")
            or rv.get("replanted_ridges")
            or rv.get("fertigated_ridges")
            or rv.get("ridge_ids")
            or []
        )
        if ridges:
            rmin = min(ridges)
            rmax = max(ridges)
            count = len(ridges)
        else:
            rmin = rv.get("start_ridge")
            rmax = rv.get("end_ridge")
            count = None
        result.append(
            {
                "event_id": event.get("event_id"),
                "function": event.get("function"),
                "min_ridge": rmin,
                "max_ridge": rmax,
                "count": count,
                "status": rv.get("status"),
            }
        )
    return result


def range_within(item: dict[str, Any], start: int, end: int) -> bool:
    rmin = item.get("min_ridge")
    rmax = item.get("max_ridge")
    if rmin is None or rmax is None:
        return True
    return int(rmin) >= start and int(rmax) <= end


def rows_matching_label(
    rows: list[dict[str, Any]], needles: Iterable[str]
) -> list[dict[str, Any]]:
    lowered = tuple(needle.lower() for needle in needles)
    return [
        row
        for row in rows
        if any(needle in str(row.get("label") or "").lower() for needle in lowered)
    ]


def zone_values(rows: list[dict[str, Any]], zone: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("zone") == zone]


def range_values(
    rows: list[dict[str, Any]], start: int, end: int
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("ridge_id") is not None and start <= int(row["ridge_id"]) <= end
    ]


def compare_zone_means(
    a_rows: list[dict[str, Any]],
    b_rows: list[dict[str, Any]],
    keys: list[str],
) -> dict[str, dict[str, float | None]]:
    return {
        key: {
            "a": round_or_none(avg(a_rows, key)),
            "b": round_or_none(avg(b_rows, key)),
            "delta": round_or_none(avg(a_rows, key) - avg(b_rows, key)),
        }
        for key in keys
    }


def means(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, float | None]:
    return {key: round_or_none(avg(rows, key)) for key in keys}


def avg(rows: list[dict[str, Any]], key: str) -> float:
    values = [row.get(key) for row in rows if row.get(key) is not None]
    if not values:
        return 0.0
    return sum(float(value) for value in values) / len(values)


def max_value(rows: list[dict[str, Any]], key: str) -> float:
    values = [row.get(key) for row in rows if row.get(key) is not None]
    if not values:
        return 0.0
    return max(float(value) for value in values)


def round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def first_stage_dates_by_zone(rows: list[dict[str, Any]], stage: str) -> dict[str, str]:
    by_zone = {}
    for row in sorted(rows, key=row_sort_key):
        zone = row.get("zone")
        if not zone or zone in by_zone:
            continue
        if stage == "R8":
            is_match = int(row.get("stage_rank") or -1) >= STAGE_ORDER["R8"]
        else:
            is_match = row.get("stage") == stage
        if not is_match:
            continue
        by_zone[str(zone)] = str(row.get("date") or row.get("label") or "")
    return by_zone


def snapshots_with_multiple_zone_stages(rows: list[dict[str, Any]]) -> int:
    by_trace_zone: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        trace = int(row.get("trace_index") or -1)
        zone = str(row.get("zone") or "")
        stage = str(row.get("stage") or "")
        if zone and stage:
            by_trace_zone[(trace, zone)][stage] += 1
    by_trace: dict[int, set[str]] = defaultdict(set)
    for (trace, _zone), counts in by_trace_zone.items():
        if counts:
            by_trace[trace].add(counts.most_common(1)[0][0])
    return sum(1 for stages in by_trace.values() if len(stages) > 1)


def max_consecutive_jumps(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    best = {
        "max_jump": 0.0,
        "ridge_id": None,
        "from": None,
        "to": None,
        "label": None,
        "rainfall_mm": 0.0,
    }
    for ridge_id, ridge_rows in rows_by_ridge(rows).items():
        prev = None
        prev_label = None
        prev_date = None
        for row in sorted(ridge_rows, key=row_sort_key):
            value = row.get(key)
            if value is None:
                continue
            if prev is not None:
                jump = abs(float(value) - float(prev))
                day_gap = date_gap_days(prev_date, row.get("date"))
                if day_gap is not None and day_gap > 2:
                    prev = value
                    prev_label = row.get("label")
                    prev_date = row.get("date")
                    continue
                if jump > best["max_jump"]:
                    rainfall_mm = max(
                        to_float(row.get("weather_rainfall_mm")) or 0.0,
                        to_float(row.get("rainfall_mm")) or 0.0,
                    )
                    best = {
                        "max_jump": jump,
                        "ridge_id": ridge_id,
                        "from": prev,
                        "to": value,
                        "label": row.get("label"),
                        "prev_label": prev_label,
                        "day_gap": day_gap,
                        "rainfall_mm": rainfall_mm,
                    }
            prev = value
            prev_label = row.get("label")
            prev_date = row.get("date")
    return best


def rows_by_ridge(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rid = row.get("ridge_id")
        if rid is not None:
            out[int(rid)].append(row)
    return out


def row_sort_key(row: dict[str, Any]) -> tuple[int, str, int]:
    return (
        int(row.get("trace_index") or 0),
        str(row.get("date") or ""),
        int(row.get("ridge_id") or 0),
    )


def coerce_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def date_gap_days(a: Any, b: Any) -> int | None:
    a_date = coerce_date(a)
    b_date = coerce_date(b)
    if a_date is None or b_date is None:
        return None
    return abs((b_date - a_date).days)


def to_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def add_issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    **extra: Any,
) -> None:
    issue = {"severity": severity, "code": code, "message": message}
    issue.update(extra)
    issues.append(issue)


def render_summary(reports: list[tuple[ScenarioSpec, dict[str, Any], Path]]) -> str:
    lines = [
        "# Full-Season L3 Review Summary",
        "",
        "This audit reads generated trace CSV/JSON files only. It does not edit trace outputs; any correction must be made in scenario, app/tool return, physics engine, or oracle flow and then regenerated.",
        "",
        "| Scenario | Status | Issues | Key Metrics | Report |",
        "|---|---:|---:|---|---|",
    ]
    for spec, report, path in reports:
        issues = report["issues"]
        metrics = report["metrics"]
        issue_text = f"{sum(1 for i in issues if i['severity'] == 'fail')} fail / {sum(1 for i in issues if i['severity'] == 'warn')} warn"
        key_metrics = (
            f"events={metrics.get('completed_event_count')}, "
            f"actions={metrics.get('reconstructed_action_count')}, "
            f"supported_key_actions={metrics.get('action_support_summary', {}).get('verdict_counts', {}).get('supported', 0)}/"
            f"{metrics.get('action_support_summary', {}).get('key_action_count', 0)}, "
            f"r8_jumps={metrics.get('r8_biological_yield_jump_count')}, "
            f"semantic={metrics.get('semantic_validation', {}).get('event_order_status')}/"
            f"{metrics.get('semantic_validation', {}).get('target_legality_status')}/"
            f"{metrics.get('semantic_validation', {}).get('targeted_scope_status')}, "
            f"primary_metric={metrics.get('semantic_validation', {}).get('primary_metric')}"
        )
        rel = path.relative_to(REPO_ROOT)
        lines.append(
            f"| `{spec.scenario_id}` | {report['status']} | {issue_text} | {key_metrics} | `{rel}` |"
        )
    lines.extend(["", "## Next Use", ""])
    lines.append(
        "Run `uv run python scripts/fullseason/review_fullseason_l3_scenarios.py` after regenerating any scenario trace."
    )
    lines.append(
        "A `pass` means CSV trends and reconstructed/native tool-return support chains both passed strict review."
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
