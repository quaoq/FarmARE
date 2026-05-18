"""Run early-vs-standard late-rain L3 oracle and export daily engine CSVs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_early_vs_standard_late_rain_harvest import (  # noqa: E402
    A_END,
    A_SEED_TYPE,
    A_START,
    B_END,
    B_SEED_TYPE,
    B_START,
    SCENARIO_ID,
    ScenarioFullSeasonEarlyVsStandardLateRainHarvest,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402


TRACE_APP_NAME = "EarlyVsStandardLateRainHarvestDailyTrace"
ZONES = [
    (f"a_{A_SEED_TYPE.lower()}_{A_START}_{A_END}", A_START, A_END),
    (f"b_{B_SEED_TYPE.lower()}_{B_START}_{B_END}", B_START, B_END),
]


def harvest_diagnostics(
    field_rows: list[dict[str, Any]],
    ridge_rows: list[dict[str, Any]],
    completed_events: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    harvest_events = [
        event
        for event in completed_events
        if event.get("function") == "harvest"
        and isinstance(event.get("return_value"), dict)
        and event["return_value"].get("status") == "ok"
    ]
    if not harvest_events:
        warnings.append("no successful harvest events found")
        return warnings
    first_harvest_ridges = harvest_events[0]["return_value"].get("harvested_ridges", [])
    if first_harvest_ridges and max(first_harvest_ridges) > A_END:
        warnings.append("first harvest event was not limited to the early HEIKE71 zone")
    a_r8_trace = _first_trace_index_for_stage(ridge_rows, A_START, A_END, "R8_FULL_MATURITY")
    b_r8_trace = _first_trace_index_for_stage(ridge_rows, B_START, B_END, "R8_FULL_MATURITY")
    if a_r8_trace is None:
        warnings.append("A zone never reached R8 in trace")
    if b_r8_trace is None:
        warnings.append("B zone never reached R8 in trace")
    if a_r8_trace is not None and b_r8_trace is not None and a_r8_trace >= b_r8_trace:
        warnings.append("HEIKE71 A zone did not reach R8 before HEINONG84 B zone")
    for event in harvest_events:
        ridges = event["return_value"].get("harvested_ridges", [])
        if ridges and min(ridges) <= A_END < max(ridges):
            warnings.append(f"harvest event {event['event_id']} crossed A/B zone boundary")
    return warnings


def _first_trace_index_for_stage(
    rows: list[dict[str, Any]],
    start: int,
    end: int,
    stage: str,
) -> int | None:
    matching = [
        int(row["trace_index"])
        for row in rows
        if start <= int(row["ridge_id"]) <= end and row.get("stage") == stage
    ]
    return min(matching) if matching else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--field-csv",
        type=Path,
        default=Path("docs/ai/early-vs-standard-late-rain-harvest-field-summary.csv"),
    )
    parser.add_argument(
        "--ridge-csv",
        type=Path,
        default=Path("docs/ai/early-vs-standard-late-rain-harvest-ridge-states.csv"),
    )
    parser.add_argument(
        "--trace-json",
        type=Path,
        default=Path("docs/ai/early-vs-standard-late-rain-harvest-oracle-trace.json"),
    )
    args = parser.parse_args()

    summary = run_trace(
        scenario_cls=ScenarioFullSeasonEarlyVsStandardLateRainHarvest,
        scenario_id=SCENARIO_ID,
        trace_app_name=TRACE_APP_NAME,
        zones=ZONES,
        field_csv=args.field_csv,
        ridge_csv=args.ridge_csv,
        trace_json=args.trace_json,
        diagnostics=harvest_diagnostics,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
