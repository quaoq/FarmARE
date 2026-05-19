"""Run threshold-insect L3 oracle and export daily engine CSVs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_heinong84_threshold_insect_limited_spray import (  # noqa: E402
    AFFECTED_END,
    AFFECTED_START,
    SCENARIO_ID,
    ScenarioFullSeasonHeinong84ThresholdInsectLimitedSpray,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402


TRACE_APP_NAME = "ThresholdInsectLimitedSprayDailyTrace"
ZONES = [
    (f"affected_{AFFECTED_START}_{AFFECTED_END}", AFFECTED_START, AFFECTED_END),
    ("reference_west_0_17", 0, 17),
    ("reference_east_38_63", 38, 63),
]


def insect_diagnostics(
    field_rows: list[dict[str, Any]],
    ridge_rows: list[dict[str, Any]],
    completed_events: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    spray_events = [
        event
        for event in completed_events
        if event.get("function") in {"spray_pesticide", "apply_pesticide"}
        and isinstance(event.get("return_value"), dict)
        and event["return_value"].get("status") == "ok"
    ]
    if not spray_events:
        warnings.append("no successful pesticide spray event found")
    for event in spray_events:
        ridges = event["return_value"].get("sprayed_ridges", [])
        if not ridges:
            ridges = event["return_value"].get("ridge_ids", [])
        if ridges and (min(ridges) < AFFECTED_START or max(ridges) > AFFECTED_END):
            warnings.append(f"pesticide event {event['event_id']} sprayed outside affected range")
    affected_rows = [
        row
        for row in ridge_rows
        if AFFECTED_START <= int(row["ridge_id"]) <= AFFECTED_END
    ]
    pre = [
        float(row["insect_pressure"])
        for row in affected_rows
        if "below_threshold" in str(row.get("label") or "")
    ]
    threshold = [
        float(row["insect_pressure"])
        for row in affected_rows
        if "after_targeted_insecticide" in str(row.get("label") or "")
    ]
    if pre and max(pre) >= 0.45:
        warnings.append("below-threshold insect check already reached high treatment pressure")
    if threshold and max(threshold) > 0.40:
        warnings.append("post-pesticide affected insect pressure remains high")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--field-csv",
        type=Path,
        default=Path("docs/ai/threshold-insect-limited-spray-field-summary.csv"),
    )
    parser.add_argument(
        "--ridge-csv",
        type=Path,
        default=Path("docs/ai/threshold-insect-limited-spray-ridge-states.csv"),
    )
    parser.add_argument(
        "--trace-json",
        type=Path,
        default=Path("docs/ai/threshold-insect-limited-spray-oracle-trace.json"),
    )
    args = parser.parse_args()

    summary = run_trace(
        scenario_cls=ScenarioFullSeasonHeinong84ThresholdInsectLimitedSpray,
        scenario_id=SCENARIO_ID,
        trace_app_name=TRACE_APP_NAME,
        zones=ZONES,
        field_csv=args.field_csv,
        ridge_csv=args.ridge_csv,
        trace_json=args.trace_json,
        diagnostics=insect_diagnostics,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
