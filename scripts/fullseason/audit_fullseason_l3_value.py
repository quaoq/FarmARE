"""Classify full-season V2/L3 scenario value from generated traces.

This is a second-pass audit on top of ``review_fullseason_l3_scenarios.py``.
It does not edit generated CSV/JSON. The script reads current trace outputs,
captures or loads a yield baseline, and writes a value table that separates
true L3 challenge scenarios from baseline-only, rewrite, and drop candidates.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from are.simulation.apps.farm_world.farm_world_app import (  # noqa: E402
    DEFAULT_RIDGE_WIDTH_M,
    FIELD_LENGTH_M,
)
from scripts.fullseason.review_fullseason_l3_scenarios import (  # noqa: E402
    SCENARIOS,
    KEY_DECISION_ACTIONS,
    ScenarioSpec,
    normalize_row,
    read_csv,
    review_scenario,
)

DOCS_AI = REPO_ROOT / "docs" / "ai"
DEFAULT_BASELINE = DOCS_AI / "fullseason-l3-value-yield-baseline.json"
DEFAULT_JSON = DOCS_AI / "fullseason-l3-value-audit.json"
DEFAULT_CSV = DOCS_AI / "fullseason-l3-value-audit.csv"
DEFAULT_MD = DOCS_AI / "fullseason-l3-value-audit.md"

MANAGEMENT_ACTIONS = {
    "replant_seeds",
    "apply_fertigation",
    "irrigate",
    "apply_fungicide",
    "apply_herbicide",
    "apply_pesticide",
    "spray_pesticide",
    "mechanical_weed_control",
}

HARVEST_POSTHARVEST_ACTIONS = {"harvest", "dry_grain", "store_grain"}

BASELINE_ONLY_SLUGS = {
    "hb_base_hn84_std_normal",
    "heinong60_high_density_baseline",
}

DROP_SLUGS: set[str] = set()

REWRITE_SLUGS = {
    "hb_fuel_limit_irrigation_harvest_ops",
}

WINDOW_TOKENS = {
    "establishment": {
        "seed",
        "planting",
        "emergence",
        "stand",
        "skip",
        "crusting",
        "coldspring",
        "replant",
        "compaction",
        "residue",
    },
    "nutrient": {
        "nutrient",
        "fertilizer",
        "fertility",
        "rhizobia",
        "nodulation",
        "micronutrient",
        "potassium",
        "leafcolor",
    },
    "weed": {"weed", "herbicide", "organic", "lowdensity"},
    "disease": {"disease", "fungicide", "wetjune", "soyhistory", "poordrainage"},
    "insect": {"insect", "aphid", "feeder", "pest"},
    "water": {
        "dry",
        "drought",
        "water",
        "irrigation",
        "fastdrain",
        "heatdry",
        "heat",
    },
    "harvest": {
        "harvest",
        "laterain",
        "dryer",
        "storage",
        "moisture",
        "market",
        "shattering",
        "quality",
        "earlyfrost",
        "grain",
        "lodging",
    },
}

TARGET_STRESS_BY_WINDOW = {
    "water": "water",
    "nutrient": "nutrient",
    "weed": "weed",
    "insect": "insect",
    "disease": "disease",
}


@dataclass
class YieldSummary:
    yield_kg_ha: float | None
    field_yield_kg: float | None
    ridge_count: int
    source: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-ai", type=Path, default=DOCS_AI)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    parser.add_argument(
        "--snapshot-baseline",
        action="store_true",
        help="Store current generated-trace yields as the before-yield baseline.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting an existing baseline snapshot.",
    )
    args = parser.parse_args()

    baseline = load_baseline(args.baseline)
    current_yields = {spec.slug: compute_yield(spec) for spec in SCENARIOS}
    if args.snapshot_baseline:
        if args.baseline.exists() and not args.force:
            raise SystemExit(
                f"{args.baseline} already exists; pass --force to overwrite it."
            )
        write_json(
            args.baseline,
            {
                "note": (
                    "Baseline snapshot from generated trace CSVs. Do not hand-edit; "
                    "regenerate scenario traces before taking a new snapshot."
                ),
                "scenarios": {
                    slug: asdict(summary) for slug, summary in current_yields.items()
                },
            },
        )
        baseline = {slug: asdict(summary) for slug, summary in current_yields.items()}

    reports = []
    family_counts: Counter[str] = Counter()
    for spec in SCENARIOS:
        report = review_scenario(spec)
        family = scenario_family(spec.slug)
        family_counts[family] += 1
        reports.append((spec, report, family))

    records = []
    for spec, report, family in reports:
        before = baseline.get(spec.slug)
        after = current_yields[spec.slug]
        record = build_value_record(
            spec=spec,
            report=report,
            family=family,
            family_count=family_counts[family],
            before=before,
            after=after,
        )
        records.append(record)

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    write_csv(args.csv, records)
    write_json(args.json, {"summary": summarize(records), "scenarios": records})
    args.md.write_text(render_markdown(records), encoding="utf-8")
    print(
        json.dumps(
            {
                "baseline": str(args.baseline.relative_to(REPO_ROOT)),
                "csv": str(args.csv.relative_to(REPO_ROOT)),
                "json": str(args.json.relative_to(REPO_ROOT)),
                "markdown": str(args.md.relative_to(REPO_ROOT)),
                "decision_counts": dict(Counter(r["value_decision"] for r in records)),
                "review_status_counts": dict(
                    Counter(r["review_status"] for r in records)
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def load_baseline(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if isinstance(scenarios, dict):
        return scenarios
    return {}


def compute_yield(spec: ScenarioSpec) -> YieldSummary:
    issues: list[dict[str, Any]] = []
    rows = [normalize_row(row) for row in read_csv(spec.ridge_csv, issues, "ridge_csv")]
    latest_by_ridge: dict[int, dict[str, Any]] = {}
    for row in rows:
        ridge_id = row.get("ridge_id")
        recovered = row.get("recovered_yield")
        if ridge_id is None or recovered is None:
            continue
        previous = latest_by_ridge.get(int(ridge_id))
        if previous is None or row_sort_value(row) >= row_sort_value(previous):
            latest_by_ridge[int(ridge_id)] = row
    if not latest_by_ridge:
        return YieldSummary(None, None, 0, "missing_or_unreadable_ridge_csv")

    recovered_values = [
        float(row["recovered_yield"])
        for row in latest_by_ridge.values()
        if row.get("recovered_yield") is not None
    ]
    if not recovered_values:
        return YieldSummary(None, None, len(latest_by_ridge), "no_recovered_yield")
    avg_g_m2 = sum(recovered_values) / len(recovered_values)
    ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
    field_kg = sum(value * ridge_area_m2 / 1000.0 for value in recovered_values)
    return YieldSummary(
        round(avg_g_m2 * 10.0, 2),
        round(field_kg, 2),
        len(latest_by_ridge),
        "ridge_csv_final_recovered_yield",
    )


def row_sort_value(row: dict[str, Any]) -> tuple[int, str]:
    trace_index = row.get("trace_index")
    return (int(trace_index) if trace_index is not None else -1, str(row.get("date")))


def build_value_record(
    *,
    spec: ScenarioSpec,
    report: dict[str, Any],
    family: str,
    family_count: int,
    before: dict[str, Any] | None,
    after: YieldSummary,
) -> dict[str, Any]:
    metrics = report.get("metrics", {})
    issues = report.get("issues", [])
    action_functions = metrics.get("action_functions", {})
    collapsed_actions = collapsed_management_actions(spec)
    windows = scenario_windows(spec.slug, action_functions)
    target_stresses = sorted(
        {
            stress
            for window in windows
            if (stress := TARGET_STRESS_BY_WINDOW.get(window)) is not None
        }
    )
    raw_management_action_events = {
        fn: count
        for fn, count in action_functions.items()
        if fn in MANAGEMENT_ACTIONS or fn in HARVEST_POSTHARVEST_ACTIONS
    }
    key_summary = metrics.get("action_support_summary", {})
    key_count = int(key_summary.get("key_action_count") or 0)
    supported_count = int(key_summary.get("verdict_counts", {}).get("supported") or 0)
    non_target_issues = [
        issue["code"] for issue in issues if issue["code"].startswith("non_target_")
    ]
    hard_fail_issues = [
        issue["code"] for issue in issues if issue.get("severity") == "fail"
    ]
    management_actions = {
        fn: count
        for fn, count in collapsed_actions.items()
        if fn in MANAGEMENT_ACTIONS or fn in HARVEST_POSTHARVEST_ACTIONS
    }
    has_real_decision = has_management_decision(spec.slug, management_actions)
    no_action_reason = no_action_reason_for(spec.slug, windows, has_real_decision)

    before_yield = number_or_none(before, "yield_kg_ha")
    before_field = number_or_none(before, "field_yield_kg")
    after_yield = after.yield_kg_ha
    after_field = after.field_yield_kg
    delta = None
    delta_pct = None
    field_delta = None
    if before_yield is not None and after_yield is not None:
        delta = round(after_yield - before_yield, 2)
        delta_pct = round(delta / before_yield * 100.0, 2) if before_yield else None
    if before_field is not None and after_field is not None:
        field_delta = round(after_field - before_field, 2)

    decision = classify_value(
        spec.slug,
        report.get("status"),
        hard_fail_issues,
        non_target_issues,
        has_real_decision,
        windows,
        after_yield,
    )
    yield_sanity = yield_sanity_status(after_yield)
    return {
        "slug": spec.slug,
        "scenario_id": spec.scenario_id,
        "value_decision": decision,
        "review_status": report.get("status"),
        "core_windows": ",".join(windows),
        "target_stresses": ",".join(target_stresses) or "none",
        "non_target_stress_pollution": ",".join(non_target_issues) or "none",
        "yield_sanity": yield_sanity,
        "has_real_management_decision": has_real_decision,
        "management_actions": json.dumps(management_actions, sort_keys=True),
        "raw_management_action_events": json.dumps(
            raw_management_action_events, sort_keys=True
        ),
        "no_action_reason": no_action_reason,
        "supported_key_actions": supported_count,
        "key_action_count": key_count,
        "tool_return_support": "pass"
        if supported_count == key_count
        else f"{supported_count}/{key_count}",
        "duplicate_family": family,
        "duplicate_family_count": family_count,
        "duplicate_note": duplicate_note(family_count, windows, management_actions),
        "yield_before_kg_ha": before_yield,
        "yield_after_kg_ha": after_yield,
        "yield_delta_kg_ha": delta,
        "yield_delta_pct": delta_pct,
        "field_yield_before_kg": before_field,
        "field_yield_after_kg": after_field,
        "field_yield_delta_kg": field_delta,
        "yield_change_reason": yield_change_reason(
            decision=decision,
            delta=delta,
            hard_fail_issues=hard_fail_issues,
            non_target_issues=non_target_issues,
            has_real_decision=has_real_decision,
            before_exists=before is not None,
            yield_sanity=yield_sanity,
        ),
        "hard_fail_issues": ",".join(hard_fail_issues) or "none",
    }


def scenario_windows(slug: str, action_functions: dict[str, int]) -> list[str]:
    text = slug.lower().replace("-", "_")
    windows = [
        window
        for window, tokens in WINDOW_TOKENS.items()
        if any(token in text for token in tokens)
    ]
    if any(fn in action_functions for fn in {"plant_seeds", "replant_seeds"}):
        add_unique(windows, "establishment")
    if any(fn in action_functions for fn in {"apply_fertigation", "apply_fertilizer"}):
        add_unique(windows, "nutrient")
    if any(
        fn in action_functions
        for fn in {"apply_herbicide", "mechanical_weed_control"}
    ):
        add_unique(windows, "weed")
    if any(fn in action_functions for fn in {"apply_fungicide"}):
        add_unique(windows, "disease")
    if any(fn in action_functions for fn in {"apply_pesticide", "spray_pesticide"}):
        add_unique(windows, "insect")
    if any(fn in action_functions for fn in {"irrigate"}):
        add_unique(windows, "water")
    if any(
        fn in action_functions for fn in {"harvest", "dry_grain", "store_grain"}
    ):
        add_unique(windows, "harvest")
    return windows or ["baseline"]


def collapsed_management_actions(spec: ScenarioSpec) -> dict[str, int]:
    try:
        trace = json.loads(spec.trace_json.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    groups: set[tuple[str, str]] = set()
    for event in trace.get("completed_events") or []:
        fn = event.get("function")
        if fn not in KEY_DECISION_ACTIONS:
            continue
        groups.add((str(fn), operation_group_id(event)))
    return dict(Counter(fn for fn, _ in groups))


def operation_group_id(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "")
    fn = str(event.get("function") or "")
    if fn in {"apply_fungicide", "apply_pesticide", "spray_pesticide"}:
        return re.sub(r"_spray_\d+_\d+$", "", event_id)
    if fn == "harvest":
        return re.sub(r"_harvest_\d+_\d+$", "", event_id)
    return event_id


def add_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def scenario_family(slug: str) -> str:
    windows = [
        window
        for window, tokens in WINDOW_TOKENS.items()
        if any(token in slug.lower() for token in tokens)
    ]
    return "+".join(windows or ["baseline"])


def has_management_decision(slug: str, actions: dict[str, int]) -> bool:
    if any(fn in actions for fn in MANAGEMENT_ACTIONS):
        return True
    harvestish = any(
        token in slug
        for token in {
            "harvest",
            "laterain",
            "dryer",
            "storage",
            "market",
            "shattering",
            "quality",
            "moisture",
            "earlyfrost",
        }
    )
    return harvestish and any(fn in actions for fn in HARVEST_POSTHARVEST_ACTIONS)


def no_action_reason_for(
    slug: str, windows: list[str], has_real_decision: bool
) -> str:
    if has_real_decision:
        return ""
    if slug in BASELINE_ONLY_SLUGS or windows == ["baseline"]:
        return "healthy/reference baseline; no stress-driven intervention expected"
    return "missing clear intervention or explicit no-action threshold; inspect/rewrite"


def classify_value(
    slug: str,
    review_status: str | None,
    hard_fail_issues: list[str],
    non_target_issues: list[str],
    has_real_decision: bool,
    windows: list[str],
    after_yield_kg_ha: float | None,
) -> str:
    if slug in DROP_SLUGS:
        return "drop"
    if slug in BASELINE_ONLY_SLUGS:
        return "baseline_only"
    if slug in REWRITE_SLUGS:
        return "rewrite"
    if yield_sanity_status(after_yield_kg_ha) != "ok":
        return "rewrite"
    if hard_fail_issues or non_target_issues or review_status == "fail":
        return "rewrite"
    if not has_real_decision:
        return "baseline_only" if windows == ["baseline"] else "rewrite"
    return "keep"


def duplicate_note(
    family_count: int, windows: list[str], management_actions: dict[str, int]
) -> str:
    if family_count <= 1:
        return "unique family"
    action_text = ",".join(sorted(management_actions)) or "no management action"
    return (
        f"family has {family_count} scenarios; keep only if window/action differs "
        f"({'+'.join(windows)} / {action_text})"
    )


def yield_sanity_status(yield_kg_ha: float | None) -> str:
    if yield_kg_ha is None:
        return "missing_yield"
    if yield_kg_ha <= 100.0:
        return "near_zero_yield"
    if yield_kg_ha < 1800.0:
        return "very_low_yield"
    if yield_kg_ha > 6500.0:
        return "very_high_yield"
    return "ok"


def yield_change_reason(
    *,
    decision: str,
    delta: float | None,
    hard_fail_issues: list[str],
    non_target_issues: list[str],
    has_real_decision: bool,
    before_exists: bool,
    yield_sanity: str,
) -> str:
    if yield_sanity != "ok":
        return f"yield sanity failed: {yield_sanity}; inspect harvest/stress/profile path"
    if not before_exists:
        return "no previous baseline snapshot; current yield used as reference"
    if hard_fail_issues or non_target_issues:
        return "rewrite needed before yield delta is agronomically meaningful"
    if decision == "drop":
        return "scenario premise is not considered a valid L3 soybean challenge"
    if decision == "baseline_only":
        return "reference/baseline scenario; yield delta is not an L3 value signal"
    if delta is None:
        return "yield missing from generated ridge CSV"
    if abs(delta) < 1.0:
        if has_real_decision:
            return (
                "management action exists but yield barely changed; verify quality, "
                "loss, or no-action contrast"
            )
        return "no meaningful yield change and no clear management decision"
    if delta > 0:
        return "yield improved after trace/scenario/profile fixes or better stress handling"
    return "yield decreased; inspect whether resource limits or realistic stress were added"


def number_or_none(payload: dict[str, Any] | None, key: str) -> float | None:
    if not payload:
        return None
    value = payload.get(key)
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scenario_count": len(records),
        "decision_counts": dict(Counter(r["value_decision"] for r in records)),
        "review_status_counts": dict(Counter(r["review_status"] for r in records)),
        "supported_key_actions": sum(int(r["supported_key_actions"]) for r in records),
        "key_action_count": sum(int(r["key_action_count"]) for r in records),
    }


def render_markdown(records: list[dict[str, Any]]) -> str:
    counts = Counter(r["value_decision"] for r in records)
    lines = [
        "# Full-Season L3 Value Audit",
        "",
        "Generated from scenario trace CSV/JSON outputs. Do not edit trace files directly; fix scenario/profile/app/engine behavior and regenerate.",
        "",
        f"Decision counts: {dict(counts)}",
        "",
        "| Scenario | Decision | Review | Windows | Support | Yield before | Yield after | Delta | Reason |",
        "|---|---:|---:|---|---:|---:|---:|---:|---|",
    ]
    for record in records:
        lines.append(
            "| `{scenario}` | {decision} | {review} | {windows} | {support} | {before} | {after} | {delta} | {reason} |".format(
                scenario=record["slug"],
                decision=record["value_decision"],
                review=record["review_status"],
                windows=record["core_windows"],
                support=record["tool_return_support"],
                before=format_number(record["yield_before_kg_ha"]),
                after=format_number(record["yield_after_kg_ha"]),
                delta=format_number(record["yield_delta_kg_ha"]),
                reason=record["yield_change_reason"],
            )
        )
    return "\n".join(lines) + "\n"


def format_number(value: Any) -> str:
    if value in ("", None):
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
