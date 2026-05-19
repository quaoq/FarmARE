"""Run HB_BASE_HN84_STD_NORMAL oracle and export daily engine CSVs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_base_hn84_std_normal import (  # noqa: E402
    SCENARIO_ID,
    ScenarioFullSeasonHBBaseHN84StdNormal,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402


TRACE_APP_NAME = "HBBaseHN84StdNormalDailyTrace"
ZONES = [("whole_field_0_63", 0, 63)]


def baseline_diagnostics(
    field_rows: list[dict[str, Any]],
    ridge_rows: list[dict[str, Any]],
    completed_events: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    stress_actions = {
        event.get("function")
        for event in completed_events
        if event.get("function") in {"irrigate", "apply_fungicide", "apply_pesticide", "spray_pesticide"}
    }
    if stress_actions:
        warnings.append(f"baseline contains stress-response actions: {sorted(stress_actions)}")
    max_disease = max((float(row["disease_pressure"]) for row in ridge_rows), default=0.0)
    min_water = min((float(row["water_stress"]) for row in ridge_rows), default=1.0)
    if max_disease > 0.38:
        warnings.append(f"baseline disease pressure unexpectedly high: {max_disease:.3f}")
    if min_water < 0.35:
        warnings.append(f"baseline water stress unexpectedly severe: min={min_water:.3f}")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field-csv", type=Path, default=Path("docs/ai/hb-base-hn84-std-normal-field-summary.csv"))
    parser.add_argument("--ridge-csv", type=Path, default=Path("docs/ai/hb-base-hn84-std-normal-ridge-states.csv"))
    parser.add_argument("--trace-json", type=Path, default=Path("docs/ai/hb-base-hn84-std-normal-oracle-trace.json"))
    args = parser.parse_args()
    summary = run_trace(
        scenario_cls=ScenarioFullSeasonHBBaseHN84StdNormal,
        scenario_id=SCENARIO_ID,
        trace_app_name=TRACE_APP_NAME,
        zones=ZONES,
        field_csv=args.field_csv,
        ridge_csv=args.ridge_csv,
        trace_json=args.trace_json,
        diagnostics=baseline_diagnostics,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
