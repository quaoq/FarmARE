"""Run HB_SOY_AFTER_SOY_WETJUNE_DISEASE oracle and export daily engine CSVs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_soy_after_soy_wetjune_disease import (  # noqa: E402
    AFFECTED_END,
    AFFECTED_START,
    REFERENCE_END,
    REFERENCE_START,
    SCENARIO_ID,
    ScenarioFullSeasonHBSoyAfterSoyWetJuneDisease,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402


TRACE_APP_NAME = "HBSoyAfterSoyWetJuneDiseaseDailyTrace"
ZONES = [
    (f"history_affected_{AFFECTED_START}_{AFFECTED_END}", AFFECTED_START, AFFECTED_END),
    ("reference_west_0_21", 0, 21),
    ("reference_east_44_63", 44, 63),
]


def soy_history_diagnostics(
    field_rows: list[dict[str, Any]],
    ridge_rows: list[dict[str, Any]],
    completed_events: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    spray_events = [
        event for event in completed_events
        if event.get("function") == "apply_fungicide"
        and isinstance(event.get("return_value"), dict)
        and event["return_value"].get("status") == "ok"
    ]
    if not spray_events:
        warnings.append("no successful soy-history fungicide event found")
    for event in spray_events:
        ridges = event["return_value"].get("sprayed_ridges") or []
        if ridges and (min(ridges) < AFFECTED_START or max(ridges) > AFFECTED_END):
            warnings.append(f"fungicide event {event['event_id']} sprayed outside history-affected range")
    early_scouts = [
        event for event in completed_events
        if "soy_history" in str(event.get("event_id") or "")
        and event.get("function") in {"read_canopy_sensors", "fly_survey", "inspect_crop_health"}
    ]
    if len(early_scouts) < 3:
        warnings.append("soy-after-soy history did not produce an early wet-June scouting chain")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field-csv", type=Path, default=Path("docs/ai/hb-soy-after-soy-wetjune-disease-field-summary.csv"))
    parser.add_argument("--ridge-csv", type=Path, default=Path("docs/ai/hb-soy-after-soy-wetjune-disease-ridge-states.csv"))
    parser.add_argument("--trace-json", type=Path, default=Path("docs/ai/hb-soy-after-soy-wetjune-disease-oracle-trace.json"))
    args = parser.parse_args()
    summary = run_trace(
        scenario_cls=ScenarioFullSeasonHBSoyAfterSoyWetJuneDisease,
        scenario_id=SCENARIO_ID,
        trace_app_name=TRACE_APP_NAME,
        zones=ZONES,
        field_csv=args.field_csv,
        ridge_csv=args.ridge_csv,
        trace_json=args.trace_json,
        diagnostics=soy_history_diagnostics,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
