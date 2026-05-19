"""Run HB_POORDRAINAGE_WETJUNE_DISEASE_TRAFFICABILITY oracle and export CSVs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_poordrainage_wetjune_disease_trafficability import (  # noqa: E402
    AFFECTED_END,
    AFFECTED_START,
    REFERENCE_END,
    REFERENCE_START,
    SCENARIO_ID,
    ScenarioFullSeasonHBPoorDrainageWetJuneDiseaseTrafficability,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402


TRACE_APP_NAME = "HBPoorDrainageWetJuneDiseaseDailyTrace"
ZONES = [
    (f"poor_drainage_{AFFECTED_START}_{AFFECTED_END}", AFFECTED_START, AFFECTED_END),
    (f"reference_{REFERENCE_START}_{REFERENCE_END}", REFERENCE_START, REFERENCE_END),
]


def drainage_diagnostics(
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
        warnings.append("no successful targeted fungicide event found")
    for event in spray_events:
        ridges = event["return_value"].get("sprayed_ridges") or []
        if ridges and (min(ridges) < AFFECTED_START or max(ridges) > AFFECTED_END):
            warnings.append(f"fungicide event {event['event_id']} sprayed outside poor-drainage range")
    wet_rows = [
        float(row["top_vwc"])
        for row in ridge_rows
        if AFFECTED_START <= int(row["ridge_id"]) <= AFFECTED_END
        and "wet_june" in str(row.get("label") or "")
    ]
    if wet_rows and max(wet_rows) < 0.30:
        warnings.append("poor-drainage zone did not show wet-June high top VWC")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field-csv", type=Path, default=Path("docs/ai/hb-poordrainage-wetjune-disease-trafficability-field-summary.csv"))
    parser.add_argument("--ridge-csv", type=Path, default=Path("docs/ai/hb-poordrainage-wetjune-disease-trafficability-ridge-states.csv"))
    parser.add_argument("--trace-json", type=Path, default=Path("docs/ai/hb-poordrainage-wetjune-disease-trafficability-oracle-trace.json"))
    args = parser.parse_args()
    summary = run_trace(
        scenario_cls=ScenarioFullSeasonHBPoorDrainageWetJuneDiseaseTrafficability,
        scenario_id=SCENARIO_ID,
        trace_app_name=TRACE_APP_NAME,
        zones=ZONES,
        field_csv=args.field_csv,
        ridge_csv=args.ridge_csv,
        trace_json=args.trace_json,
        diagnostics=drainage_diagnostics,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
