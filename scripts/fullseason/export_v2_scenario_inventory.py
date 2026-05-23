"""Export a human-readable inventory for reviewed full-season V2 scenarios.

The source of truth is the same path used by the review step:
`review_fullseason_l3_scenarios.SCENARIOS` points to each generated trace
CSV/JSON. This script reads those generated artifacts and the scenario metadata
from Python specs/source files, then writes a compact table for manual review.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from are.simulation.apps.farm_world.farm_world_app import (
    DEFAULT_RIDGE_WIDTH_M,
    FIELD_LENGTH_M,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_catalog import (
    SPECS as BATCH_SPECS,
)
from scripts.fullseason.review_fullseason_l3_scenarios import (
    DOCS_AI,
    REPO_ROOT,
    SCENARIOS,
)

SCENARIO_DIR = (
    REPO_ROOT / "are" / "simulation" / "scenarios" / "scenario_farm_world_fullseason_v2"
)
CSV_OUTPUT = DOCS_AI / "fullseason-v2-scenario-inventory.csv"
MD_OUTPUT = DOCS_AI / "fullseason-v2-scenario-inventory.md"

RIDGE_AREA_M2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M

YIELD_ACTIONS = {
    "base_fertilize",
    "form_ridges",
    "plant_seeds",
    "replant_seeds",
    "apply_fertigation",
    "apply_fungicide",
    "apply_herbicide",
    "apply_pesticide",
    "spray_pesticide",
    "mechanical_weed_control",
    "irrigate",
    "harvest",
    "dry_grain",
    "store_grain",
}

ACTION_CN = {
    "base_fertilize": "底肥",
    "base_fertilizer": "底肥",
    "form_ridges": "起垄",
    "planting": "播种",
    "plant_seeds": "播种",
    "replant": "补种",
    "replanting": "补种",
    "replant_seeds": "补种",
    "fertigation": "补肥/水肥",
    "apply_fertigation": "补肥/水肥",
    "fungicide": "杀菌",
    "apply_fungicide": "杀菌",
    "herbicide": "除草剂",
    "apply_herbicide": "除草剂",
    "pesticide": "杀虫",
    "insecticide": "杀虫",
    "apply_pesticide": "杀虫",
    "spray_pesticide": "杀虫",
    "mechanical_weed": "机械除草",
    "mechanical_weed_control": "机械除草",
    "irrigation": "灌溉",
    "irrigate": "灌溉",
    "harvest": "收获",
    "dry": "烘干",
    "dry_grain": "烘干",
    "store": "入库",
    "store_grain": "入库",
}

ACTION_TIMELINE_PRIORITY = {
    "底肥": 10,
    "起垄": 15,
    "播种": 20,
    "补种": 25,
    "补肥/水肥": 40,
    "除草剂": 45,
    "机械除草": 45,
    "杀菌": 50,
    "杀虫": 50,
    "灌溉": 55,
    "收获": 80,
    "烘干": 90,
    "入库": 100,
}

RIDGE_ACTION_TYPES = {
    "base_fertilizer",
    "planting",
    "replant",
    "replanting",
    "fertigation",
    "fungicide",
    "herbicide",
    "pesticide",
    "insecticide",
    "mechanical_weed",
    "irrigation",
    "harvest",
}

POSTHARVEST_ACTION_TYPES = {"dry_grain", "store_grain"}

CANONICAL_COLUMNS = {
    "trace_index": ("trace_index",),
    "date": ("date", "weather_date"),
    "ridge_id": ("ridge_id",),
    "zone": ("zone",),
    "stage": ("stage",),
    "top_vwc": ("top_vwc", "soil_top_vwc"),
    "root_vwc": ("root_vwc", "soil_root_vwc"),
    "water_stress": ("water_stress",),
    "nutrient_index": ("nutrient_index",),
    "nutrient_stress": ("nutrient_stress",),
    "stand_fraction": ("stand_fraction",),
    "weed_pressure": ("weed_pressure",),
    "insect_pressure": ("insect_pressure",),
    "disease_pressure": ("disease_pressure",),
    "recovered_yield": ("recovered_yield", "recovered_yield_g_m2"),
    "harvested": ("harvested",),
}


def main() -> int:
    source_by_id = build_source_index()
    rows: list[dict[str, Any]] = []
    for review_spec in SCENARIOS:
        batch_spec = BATCH_SPECS.get(review_spec.slug)
        source_path = source_by_id.get(review_spec.scenario_id)
        source_meta = parse_source_metadata(source_path) if source_path else {}
        trace = read_json(review_spec.trace_json)
        field_rows = read_csv(review_spec.field_csv)
        ridge_rows = read_csv(review_spec.ridge_csv)

        row = {
            "scenario_id": review_spec.scenario_id,
            "slug": review_spec.slug,
            "scenario_description": scenario_description(batch_spec, source_meta),
            "agent_text": agent_text(batch_spec, source_meta),
            "initial_state": initial_state_summary(
                batch_spec, source_meta, ridge_rows, tuple(review_spec.required_zones)
            ),
            "action_timeline": action_timeline_summary(ridge_rows, field_rows, trace),
            "yield_affecting_workflow": workflow_summary(
                batch_spec, trace, ridge_rows, field_rows
            ),
            "seed_plan": seed_plan(batch_spec, source_meta),
            **yield_summary(trace, ridge_rows),
            "ridge_csv": str(review_spec.ridge_csv.relative_to(REPO_ROOT)),
            "trace_json": str(review_spec.trace_json.relative_to(REPO_ROOT)),
            "source_file": str(source_path.relative_to(REPO_ROOT))
            if source_path
            else "",
        }
        rows.append(row)

    write_csv(rows)
    write_markdown(rows)
    print(
        json.dumps(
            {
                "scenario_count": len(rows),
                "csv": str(CSV_OUTPUT.relative_to(REPO_ROOT)),
                "markdown": str(MD_OUTPUT.relative_to(REPO_ROOT)),
                "ridge_area_m2": round(RIDGE_AREA_M2, 2),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def build_source_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in SCENARIO_DIR.glob("scenario_full_season_*.py"):
        text = path.read_text(encoding="utf-8")
        for scenario_id in re.findall(r"scenario_full_season_[a-zA-Z0-9_]+", text):
            index.setdefault(scenario_id, path)
    return index


def parse_source_metadata(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    meta: dict[str, Any] = {"source_path": path}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    value = literal_or_none(node.value)
                    if value is not None:
                        meta[target.id] = value
        elif isinstance(node, ast.ClassDef):
            if "class_docstring" not in meta:
                doc = ast.get_docstring(node)
                if doc:
                    meta["class_docstring"] = doc.strip()
            for item in ast.walk(node):
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "briefing_text"
                        ):
                            value = literal_or_none(item.value)
                            if value:
                                meta.setdefault("BRIEFING_TEXT", value)
    return meta


def literal_or_none(node: ast.AST) -> Any | None:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def scenario_description(batch_spec: Any | None, source_meta: dict[str, Any]) -> str:
    if batch_spec is not None:
        return clean_text(batch_spec.description)
    return clean_text(
        source_meta.get("SCENARIO_DESCRIPTION")
        or source_meta.get("class_docstring")
        or ""
    )


def agent_text(batch_spec: Any | None, source_meta: dict[str, Any]) -> str:
    if batch_spec is not None:
        return clean_text(batch_spec.briefing_text)
    return clean_text(source_meta.get("BRIEFING_TEXT") or "")


def seed_plan(batch_spec: Any | None, source_meta: dict[str, Any]) -> str:
    if batch_spec is not None:
        if batch_spec.planting_zones:
            parts = [
                f"{zone.start}-{zone.end}:{zone.seed_type}@{zone.spacing_cm:g}cm"
                for zone in batch_spec.planting_zones
            ]
        else:
            parts = [f"0-63:{batch_spec.primary_seed}"]
        return "; ".join(parts)

    seeds = []
    for key, value in sorted(source_meta.items()):
        if key.endswith("SEED_TYPE") and isinstance(value, str):
            seeds.append(f"{key}={value}")
    if not seeds and isinstance(source_meta.get("SEED_TYPE"), str):
        seeds.append(str(source_meta["SEED_TYPE"]))
    spacings = [
        f"{key}={value:g}cm"
        for key, value in sorted(source_meta.items())
        if key.endswith("SPACING_CM") and isinstance(value, (int, float))
    ]
    return "; ".join(seeds + spacings)


def initial_state_summary(
    batch_spec: Any | None,
    source_meta: dict[str, Any],
    ridge_rows: list[dict[str, str]],
    required_zones: tuple[str, ...],
) -> str:
    first = first_snapshot(ridge_rows)
    pieces: list[str] = []
    if batch_spec is not None:
        pieces.append(f"profile={batch_spec.profile_name}")
        pieces.append(f"initial_vwc={batch_spec.initial_vwc:g}")
        if batch_spec.prior_histories:
            pieces.append(
                "prior="
                + "; ".join(
                    f"{preset}:{start}-{end}"
                    for preset, start, end in batch_spec.prior_histories
                )
            )
        if batch_spec.custom_histories:
            pieces.append(
                "custom_history="
                + "; ".join(
                    f"{history.name}:{start}-{end}"
                    for history, start, end in batch_spec.custom_histories
                )
            )
        if batch_spec.hydraulic_modifiers:
            pieces.append(
                "soil_modifiers="
                + "; ".join(
                    f"{start}-{end}" for start, end, _ in batch_spec.hydraulic_modifiers
                )
            )
        if batch_spec.management_regime:
            pieces.append(f"constraints={compact_dict(batch_spec.management_regime)}")
        if batch_spec.postharvest_market:
            pieces.append(f"postharvest={compact_dict(batch_spec.postharvest_market)}")
    else:
        if source_meta.get("PROFILE_NAME"):
            pieces.append(f"profile={source_meta['PROFILE_NAME']}")
        if required_zones:
            pieces.append("zones=" + "; ".join(required_zones))

    if first:
        pieces.append(
            "first_trace="
            + ", ".join(
                [
                    f"date={first.get('date') or 'n/a'}",
                    f"stage={stage_counts(first['rows'])}",
                    f"top_vwc_avg={avg_col(first['rows'], 'top_vwc')}",
                    f"root_vwc_avg={avg_col(first['rows'], 'root_vwc')}",
                    f"nutrient_avg={avg_col(first['rows'], 'nutrient_index')}",
                    f"stand_minmax={minmax_col(first['rows'], 'stand_fraction')}",
                    f"max_pressure(w/i/d)={max_col(first['rows'], 'weed_pressure')}/"
                    f"{max_col(first['rows'], 'insect_pressure')}/"
                    f"{max_col(first['rows'], 'disease_pressure')}",
                ]
            )
        )
    return " | ".join(piece for piece in pieces if piece)


def workflow_summary(
    batch_spec: Any | None,
    trace: dict[str, Any],
    ridge_rows: list[dict[str, str]],
    field_rows: list[dict[str, str]],
) -> str:
    completed = trace.get("completed_events") or []
    events = [
        event
        for event in completed
        if event.get("function") in YIELD_ACTIONS
        and not event.get("failed")
        and status_ok(event.get("return_value"))
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get("function"))].append(event)

    parts = []
    for fn in [
        "base_fertilize",
        "form_ridges",
        "plant_seeds",
        "replant_seeds",
        "apply_fertigation",
        "mechanical_weed_control",
        "apply_herbicide",
        "apply_fungicide",
        "apply_pesticide",
        "spray_pesticide",
        "irrigate",
        "harvest",
        "dry_grain",
        "store_grain",
    ]:
        if fn not in grouped:
            continue
        parts.append(summarize_function(fn, grouped[fn]))

    if batch_spec is not None and batch_spec.actions:
        planned = "; ".join(
            f"{action.window}:{ACTION_CN.get(action.kind, action.kind)} "
            f"{action.start}-{action.end}"
            for action in batch_spec.actions
        )
        parts.append(f"计划管理窗口={planned}")
    timeline = action_timeline_summary(ridge_rows, field_rows, trace)
    if timeline:
        parts.insert(0, f"时间线={timeline}")
    return " | ".join(parts)


def action_timeline_summary(
    rows: list[dict[str, str]],
    field_rows: list[dict[str, str]] | None = None,
    trace: dict[str, Any] | None = None,
) -> str:
    """Summarize dated yield-affecting actions from generated daily trace rows."""

    field_items = timeline_items_from_field_rows(field_rows or [])
    if field_items:
        return render_timeline_items(field_items)
    trace_items = timeline_items_from_trace(trace or {}, rows)
    if trace_items:
        return render_timeline_items(trace_items)

    dated: dict[tuple[str, str], set[int]] = defaultdict(set)
    zones: dict[tuple[str, str], set[str]] = defaultdict(set)
    label_fallback: dict[tuple[str, str], set[int]] = defaultdict(set)
    label_zones: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in rows:
        date = row.get("date") or row.get("weather_date")
        ridge_id = to_int(row.get("ridge_id"))
        if not date or ridge_id is None:
            continue
        zone = row.get("zone") or ""
        marker = row.get("action_marker") or row.get("action_markers")
        if marker:
            if str(row.get("label") or "").lower().startswith("o_wait"):
                continue
            for token in str(marker).split("|"):
                token = token.strip()
                if not token:
                    continue
                key = (date, ACTION_CN.get(token, token))
                dated[key].add(ridge_id)
                if zone:
                    zones[key].add(zone)
            continue

        inferred = infer_action_from_label(row.get("label") or "")
        if inferred:
            key = (date, inferred)
            label_fallback[key].add(ridge_id)
            if zone:
                label_zones[key].add(zone)

    return render_timeline(
        dated if dated else label_fallback, zones if dated else label_zones
    )


def timeline_items_from_trace(
    trace: dict[str, Any], rows: list[dict[str, str]]
) -> list[dict[str, Any]]:
    events = trace.get("completed_events") or []
    if not events:
        return []
    date_hints = timeline_date_hints(rows)
    grouped: dict[tuple[str, str, bool], dict[str, Any]] = {}
    sequence = 0
    for event in events:
        fn = event.get("function")
        if fn not in YIELD_ACTIONS:
            continue
        rv = event.get("return_value")
        if not isinstance(rv, dict):
            rv = {}
        action_type = trace_action_type(fn)
        label = ACTION_CN.get(action_type, ACTION_CN.get(str(fn), str(fn)))
        is_postharvest = fn in {"dry_grain", "store_grain"}
        ridge_ids = trace_ridges_from_return_value(rv)
        if not is_postharvest and not ridge_ids:
            continue
        date = infer_trace_event_date(event, label, date_hints)
        key = (date, label, is_postharvest)
        item = grouped.setdefault(
            key,
            {
                "date": date,
                "action": label,
                "ridges": set(),
                "postharvest": is_postharvest,
                "sequence": sequence,
            },
        )
        item["ridges"].update(ridge_ids)
        sequence += 1
    return sorted(
        grouped.values(),
        key=lambda item: (
            item["date"],
            ACTION_TIMELINE_PRIORITY.get(item["action"], 999),
            item["sequence"],
        ),
    )


def timeline_date_hints(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    hints: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        date = row.get("date") or row.get("weather_date")
        label = str(row.get("label") or "")
        if not date or not label:
            continue
        inferred = infer_action_from_label(label)
        if inferred:
            hints[inferred].add(date)
        lowered = label.lower()
        if "targeted_irrigation" in lowered:
            hints["灌溉"].add(date)
        if "fungicide" in lowered:
            hints["杀菌"].add(date)
        if "after_main_harvest" in lowered or "after_all_harvest" in lowered:
            hints["收获"].add(date)
    return {key: sorted(values) for key, values in hints.items()}


def infer_trace_event_date(
    event: dict[str, Any], label: str, date_hints: dict[str, list[str]]
) -> str:
    event_id = str(event.get("event_id") or "").lower()
    hints = date_hints.get(label) or []
    if label == "收获" and hints:
        if "affected" in event_id and len(hints) > 1:
            return hints[-1]
        return hints[0]
    if hints:
        return hints[0]
    return "unknown-date"


def trace_action_type(fn: str) -> str:
    return {
        "plant_seeds": "planting",
        "replant_seeds": "replanting",
        "apply_fertigation": "fertigation",
        "apply_fungicide": "fungicide",
        "apply_herbicide": "herbicide",
        "apply_pesticide": "pesticide",
        "spray_pesticide": "pesticide",
        "mechanical_weed_control": "mechanical_weed",
        "irrigate": "irrigation",
        "base_fertilize": "base_fertilizer",
    }.get(fn, fn)


def trace_ridges_from_return_value(rv: dict[str, Any]) -> set[int]:
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
    out: set[int] = set()
    for ridge_id in ridges:
        value = to_int(ridge_id)
        if value is not None:
            out.add(value)
    return out


def timeline_items_from_field_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Rebuild timeline from real recent actions, not marker text alone.

    Field summary action markers are coarse and may contain several labels for
    one row. The authoritative scope for an action is the recent action payload:
    it carries action_type plus ridge_ids in the same order the scenario emitted
    them. This avoids displaying ridge operations as ``postharvest/field`` when
    a marker has no matching action payload.
    """

    grouped: dict[tuple[str, str, bool], dict[str, Any]] = {}
    sequence = 0
    for row in rows:
        date = row.get("date") or row.get("weather_date")
        if not date:
            continue
        marker_tokens = {
            token.strip()
            for token in str(
                row.get("action_marker") or row.get("action_markers") or ""
            ).split("|")
            if token.strip()
        }
        recent = parse_recent_actions(row.get("recent_actions_json"))
        row_had_harvest_or_store = False
        for action in recent:
            action_type = str(action.get("action_type") or "")
            if action_type not in RIDGE_ACTION_TYPES | POSTHARVEST_ACTION_TYPES:
                continue
            label = ACTION_CN.get(action_type, action_type)
            is_postharvest = action_type in POSTHARVEST_ACTION_TYPES
            ridge_ids = {
                int(ridge_id)
                for ridge_id in action.get("ridge_ids") or []
                if isinstance(ridge_id, int)
            }
            if not is_postharvest and not ridge_ids:
                continue
            row_had_harvest_or_store = row_had_harvest_or_store or action_type in {
                "harvest",
                "store_grain",
            }
            key = (date, label, is_postharvest)
            item = grouped.setdefault(
                key,
                {
                    "date": date,
                    "action": label,
                    "ridges": set(),
                    "postharvest": is_postharvest,
                    "sequence": sequence,
                },
            )
            item["ridges"].update(ridge_ids)
            sequence += 1

        # Some traces mark drying in the field summary but do not include it in
        # recent_actions_json because dry_grain is a postharvest operation. Keep
        # that marker, but only as an explicit postharvest step.
        if "dry" in marker_tokens and row_had_harvest_or_store:
            key = (date, ACTION_CN["dry"], True)
            grouped.setdefault(
                key,
                {
                    "date": date,
                    "action": ACTION_CN["dry"],
                    "ridges": set(),
                    "postharvest": True,
                    "sequence": sequence,
                },
            )
            sequence += 1

    return sorted(
        grouped.values(),
        key=lambda item: (
            item["date"],
            ACTION_TIMELINE_PRIORITY.get(item["action"], 999),
            item["sequence"],
        ),
    )


def render_timeline_items(items: list[dict[str, Any]]) -> str:
    rendered = []
    for item in items:
        target = ranges_text(sorted(item["ridges"]))
        if target:
            rendered.append(f"{item['date']}:{item['action']}({target})")
        elif item["postharvest"]:
            rendered.append(f"{item['date']}:{item['action']}(postharvest/field)")
    return "; ".join(rendered)


def render_timeline(
    source: dict[tuple[str, str], set[int]],
    source_zones: dict[tuple[str, str], set[str]],
) -> str:
    if not source:
        return ""

    items = []
    for (date, action), ridge_ids in sorted(
        source.items(),
        key=lambda item: (
            item[0][0],
            ACTION_TIMELINE_PRIORITY.get(item[0][1], 999),
            item[0][1],
        ),
    ):
        zone_text = ",".join(sorted(source_zones.get((date, action), set())))
        target = ranges_text(sorted(ridge_ids))
        if zone_text and target:
            items.append(f"{date}:{action}({target}; {zone_text})")
        elif target:
            items.append(f"{date}:{action}({target})")
        else:
            items.append(f"{date}:{action}(postharvest/field)")
    return "; ".join(items)


def parse_recent_actions(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def ridges_for_action_token(token: str, recent: list[dict[str, Any]]) -> set[int]:
    aliases = {
        "base_fertilizer": {"base_fertilizer"},
        "planting": {"planting"},
        "fertigation": {"fertigation"},
        "fungicide": {"fungicide"},
        "herbicide": {"herbicide"},
        "pesticide": {"pesticide", "insecticide"},
        "insecticide": {"pesticide", "insecticide"},
        "mechanical_weed": {"mechanical_weed"},
        "irrigation": {"irrigation"},
        "harvest": {"harvest"},
        "dry": {"dry_grain"},
        "store": {"store_grain"},
        "store_grain": {"store_grain"},
        "replant": {"replant", "replanting"},
        "replanting": {"replant", "replanting"},
    }
    wanted = aliases.get(token, {token})
    ridges: set[int] = set()
    for action in recent:
        if action.get("action_type") not in wanted:
            continue
        for ridge_id in action.get("ridge_ids") or []:
            if isinstance(ridge_id, int):
                ridges.add(ridge_id)
    return ridges


def infer_action_from_label(label: str) -> str:
    text = label.lower()
    if text.startswith("o_wait"):
        return ""
    if "base_fertilizer" in text or "prep" in text:
        return "底肥/整地"
    if "planting" in text:
        return "播种"
    if "replant" in text:
        return "补种"
    if "targeted_recovery" in text:
        return "补肥/补种恢复"
    if "nutrient" in text or "fertigation" in text:
        return "补肥/水肥"
    if "fungicide" in text:
        return "杀菌"
    if "pesticide" in text or "insect" in text:
        return "杀虫"
    if "herbicide" in text or "weed" in text:
        return "控草"
    if "irrigation" in text or "irrigate" in text:
        return "灌溉"
    if "harvest" in text:
        return "收获"
    return ""


def summarize_function(fn: str, events: list[dict[str, Any]]) -> str:
    label = ACTION_CN.get(fn, fn)
    if fn in {"plant_seeds", "harvest"}:
        ridges: set[int] = set()
        total_kg = 0.0
        spacings = set()
        for event in events:
            rv = event.get("return_value") or {}
            if isinstance(rv, dict):
                ridges.update(
                    int(x) for x in rv.get("planted_ridges", []) if x is not None
                )
                ridges.update(
                    int(x) for x in rv.get("harvested_ridges", []) if x is not None
                )
                if rv.get("grain_kg_added") is not None:
                    total_kg += float(rv["grain_kg_added"])
                if rv.get("seed_spacing_cm") is not None:
                    spacings.add(float(rv["seed_spacing_cm"]))
        extra = ""
        if spacings:
            extra = f", spacing={format_number_list(spacings)}cm"
        if total_kg:
            extra += f", grain={total_kg:.1f}kg"
        return f"{label}:{len(events)}批 {ranges_text(sorted(ridges))}{extra}"

    targets = []
    amounts = []
    for event in events:
        rv = event.get("return_value") or {}
        if isinstance(rv, dict):
            targets.append(ridges_from_return_value(rv))
            for key in (
                "nutrient_amount",
                "carrier_water_mm",
                "water_mm",
                "hours",
                "liters_used",
                "moved_kg",
                "target_moisture_pct",
            ):
                if key in rv and rv[key] not in (None, ""):
                    amounts.append(f"{key}={rv[key]}")
    target_text = ", ".join(sorted(set(t for t in targets if t)))
    amount_text = ", ".join(sorted(set(amounts)))[:160]
    detail = "; ".join(x for x in (target_text, amount_text) if x)
    if detail:
        return f"{label}:{len(events)}次 ({detail})"
    return f"{label}:{len(events)}次"


def ridges_from_return_value(rv: dict[str, Any]) -> str:
    for key in (
        "fertigated_ridges",
        "irrigated_ridges",
        "treated_ridges",
        "sprayed_ridges",
        "weed_controlled_ridges",
        "ridges_dried",
        "stored_ridges",
    ):
        value = rv.get(key)
        if isinstance(value, list):
            return ranges_text([int(x) for x in value])
        if isinstance(value, int):
            return f"{value} ridges"
    return ""


def yield_summary(
    trace: dict[str, Any], ridge_rows: list[dict[str, str]]
) -> dict[str, Any]:
    harvest_ridges: set[int] = set()
    total_grain_kg = 0.0
    for event in trace.get("completed_events") or []:
        if event.get("function") != "harvest" or event.get("failed"):
            continue
        rv = event.get("return_value") or {}
        if not isinstance(rv, dict) or rv.get("status") != "ok":
            continue
        harvest_ridges.update(int(x) for x in rv.get("harvested_ridges", []))
        total_grain_kg += float(rv.get("grain_kg_added") or 0.0)

    final = final_snapshot(ridge_rows)
    recovered_g_m2 = [
        to_float(row.get("recovered_yield"))
        for row in final
        if to_float(row.get("recovered_yield")) is not None
    ]
    csv_total_kg = (
        sum(value * RIDGE_AREA_M2 / 1000.0 for value in recovered_g_m2)
        if recovered_g_m2
        else 0.0
    )
    harvested_count = len(harvest_ridges) or len(recovered_g_m2) or 64
    total = total_grain_kg or csv_total_kg
    return {
        "yield_kg_per_ridge": round(total / harvested_count, 2)
        if harvested_count
        else "",
        "total_yield_kg": round(total, 2),
        "harvested_ridges": harvested_count,
        "csv_recovered_kg_per_ridge": round(csv_total_kg / len(recovered_g_m2), 2)
        if recovered_g_m2
        else "",
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append(normalize_row(row))
        return rows


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    for canonical, names in CANONICAL_COLUMNS.items():
        for name in names:
            if name in row and row[name] not in ("", None):
                out[canonical] = row[name]
                break
    return out


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def first_snapshot(rows: list[dict[str, str]]) -> dict[str, Any]:
    if not rows:
        return {}
    trace_values = [to_int(row.get("trace_index")) for row in rows]
    trace_values = [value for value in trace_values if value is not None]
    if trace_values:
        first_trace = min(trace_values)
        snap = [row for row in rows if to_int(row.get("trace_index")) == first_trace]
    else:
        first_date = rows[0].get("date")
        snap = [row for row in rows if row.get("date") == first_date]
    return {"date": snap[0].get("date"), "rows": snap}


def final_snapshot(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if not rows:
        return []
    trace_values = [to_int(row.get("trace_index")) for row in rows]
    trace_values = [value for value in trace_values if value is not None]
    if trace_values:
        final_trace = max(trace_values)
        return [row for row in rows if to_int(row.get("trace_index")) == final_trace]
    final_date = rows[-1].get("date")
    return [row for row in rows if row.get("date") == final_date]


def avg_col(rows: list[dict[str, str]], key: str) -> str:
    values = [to_float(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return "n/a"
    return f"{sum(values) / len(values):.3f}"


def max_col(rows: list[dict[str, str]], key: str) -> str:
    values = [to_float(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return "n/a"
    return f"{max(values):.3f}"


def minmax_col(rows: list[dict[str, str]], key: str) -> str:
    values = [to_float(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return "n/a"
    return f"{min(values):.2f}-{max(values):.2f}"


def stage_counts(rows: list[dict[str, str]]) -> str:
    counts = Counter(row.get("stage") or "unknown" for row in rows)
    return "/".join(f"{stage}:{count}" for stage, count in sorted(counts.items()))


def write_csv(rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "scenario_id",
        "slug",
        "scenario_description",
        "agent_text",
        "initial_state",
        "action_timeline",
        "yield_affecting_workflow",
        "seed_plan",
        "yield_kg_per_ridge",
        "total_yield_kg",
        "harvested_ridges",
        "csv_recovered_kg_per_ridge",
        "ridge_csv",
        "trace_json",
        "source_file",
    ]
    with CSV_OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(rows: list[dict[str, Any]]) -> None:
    seed_counts = Counter()
    for row in rows:
        seed_text = str(row.get("seed_plan") or "")
        for seed in ("HEINONG84", "HEINONG60", "HEINONG58", "HEIHE50", "HEIKE71"):
            if seed in seed_text:
                seed_counts[seed] += 1
    lines = [
        "# Full-Season V2 Scenario Inventory",
        "",
        f"Generated from `scripts/fullseason/review_fullseason_l3_scenarios.py` scenario list and generated trace CSV/JSON. Ridge yield uses harvest-event grain kg when available; fallback is final `recovered_yield` g/m2 × ridge area ({RIDGE_AREA_M2:.1f} m2).",
        "",
        "Seed coverage: "
        + ", ".join(f"`{seed}` {count}" for seed, count in seed_counts.most_common())
        + f" across {len(rows)} scenarios. A scenario can count toward multiple seeds when it is zoned by cultivar.",
        "",
        f"Full CSV with complete agent text and initialization details: `{CSV_OUTPUT.relative_to(REPO_ROOT)}`.",
        "",
        "| Scenario ID | Seed | Yield kg/ridge | Description | Yield-affecting workflow |",
        "|---|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['scenario_id']}`",
                    markdown_cell(shorten(row["seed_plan"], 72)),
                    str(row["yield_kg_per_ridge"]),
                    markdown_cell(shorten(row["scenario_description"], 110)),
                    markdown_cell(shorten(row["yield_affecting_workflow"], 160)),
                ]
            )
            + " |"
        )
    MD_OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def compact_dict(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def status_ok(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("status", "ok") == "ok"
    return True


def ranges_text(values: list[int]) -> str:
    if not values:
        return ""
    values = sorted(set(values))
    ranges = []
    start = prev = values[0]
    for value in values[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append((start, prev))
        start = prev = value
    ranges.append((start, prev))
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in ranges)


def format_number_list(values: set[float]) -> str:
    return ",".join(f"{value:g}" for value in sorted(values))


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|")


def shorten(value: Any, limit: int) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
