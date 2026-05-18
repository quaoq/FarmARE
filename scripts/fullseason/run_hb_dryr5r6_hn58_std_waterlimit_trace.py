"""Run HB_DRYR5R6_HN58_STD_WATERLIMIT oracle and export daily engine CSVs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_dryr5r6_hn58_std_waterlimit import (  # noqa: E402
    PRIORITY_END,
    PRIORITY_START,
    REFERENCE_EAST_END,
    REFERENCE_EAST_START,
    REFERENCE_WEST_END,
    REFERENCE_WEST_START,
    SCENARIO_ID,
    ScenarioFullSeasonHBDryR5R6HN58StdWaterLimit,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402


TRACE_APP_NAME = "HBDryR5R6HN58WaterLimitDailyTrace"
ZONES = [
    (f"priority_{PRIORITY_START}_{PRIORITY_END}", PRIORITY_START, PRIORITY_END),
    (f"reference_west_{REFERENCE_WEST_START}_{REFERENCE_WEST_END}", REFERENCE_WEST_START, REFERENCE_WEST_END),
    (f"reference_east_{REFERENCE_EAST_START}_{REFERENCE_EAST_END}", REFERENCE_EAST_START, REFERENCE_EAST_END),
]


def drought_diagnostics(
    field_rows: list[dict[str, Any]],
    ridge_rows: list[dict[str, Any]],
    completed_events: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    irrigation_events = [
        event for event in completed_events
        if event.get("function") == "irrigate"
        and isinstance(event.get("return_value"), dict)
        and event["return_value"].get("status") in {"ok", "irrigation_started"}
    ]
    if len(irrigation_events) != 1:
        warnings.append(f"expected one limited irrigation event, found {len(irrigation_events)}")
    for event in irrigation_events:
        ridges = event["return_value"].get("irrigated_ridges") or []
        if ridges and (min(ridges) < PRIORITY_START or max(ridges) > PRIORITY_END):
            warnings.append(f"irrigation event {event['event_id']} treated outside priority range")
        if len(ridges) >= 64:
            warnings.append("water-limited scenario irrigated whole field")
    priority_pre = [
        float(row["root_vwc"])
        for row in ridge_rows
        if PRIORITY_START <= int(row["ridge_id"]) <= PRIORITY_END
        and "r5_r6_drought_window" in str(row.get("label") or "")
    ]
    reference_pre = [
        float(row["root_vwc"])
        for row in ridge_rows
        if (
            REFERENCE_WEST_START <= int(row["ridge_id"]) <= REFERENCE_WEST_END
            or REFERENCE_EAST_START <= int(row["ridge_id"]) <= REFERENCE_EAST_END
        )
        and "r5_r6_drought_window" in str(row.get("label") or "")
    ]
    if priority_pre and reference_pre:
        priority_avg = sum(priority_pre) / len(priority_pre)
        reference_avg = sum(reference_pre) / len(reference_pre)
        if priority_avg >= reference_avg - 0.006:
            warnings.append("priority ridges did not show a clear R5/R6 root-zone VWC gap")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field-csv", type=Path, default=Path("docs/ai/hb-dryr5r6-hn58-std-waterlimit-field-summary.csv"))
    parser.add_argument("--ridge-csv", type=Path, default=Path("docs/ai/hb-dryr5r6-hn58-std-waterlimit-ridge-states.csv"))
    parser.add_argument("--trace-json", type=Path, default=Path("docs/ai/hb-dryr5r6-hn58-std-waterlimit-oracle-trace.json"))
    args = parser.parse_args()
    summary = run_trace(
        scenario_cls=ScenarioFullSeasonHBDryR5R6HN58StdWaterLimit,
        scenario_id=SCENARIO_ID,
        trace_app_name=TRACE_APP_NAME,
        zones=ZONES,
        field_csv=args.field_csv,
        ridge_csv=args.ridge_csv,
        trace_json=args.trace_json,
        diagnostics=drought_diagnostics,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
