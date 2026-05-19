"""Run Heinong60 high-density baseline oracle and export daily engine CSVs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_heinong60_high_density_baseline import (  # noqa: E402
    SCENARIO_ID,
    ScenarioFullSeasonHeinong60HighDensityBaseline,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402


TRACE_APP_NAME = "Heinong60HighDensityDailyTrace"
ZONES = [("whole_field_0_63", 0, 63)]


def high_density_diagnostics(
    field_rows: list[dict[str, Any]],
    ridge_rows: list[dict[str, Any]],
    completed_events: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    stress_actions = {
        event.get("function")
        for event in completed_events
        if event.get("function") in {"apply_fungicide", "apply_pesticide", "spray_pesticide"}
    }
    if stress_actions:
        warnings.append(f"high-density baseline contains biotic stress actions: {sorted(stress_actions)}")
    max_lai = max((float(row["lai"]) for row in ridge_rows), default=0.0)
    max_ndvi = max((float(row["ndvi"]) for row in ridge_rows), default=0.0)
    if max_lai > 6.2:
        warnings.append(f"high-density baseline LAI unexpectedly high: {max_lai:.3f}")
    if max_ndvi > 0.96:
        warnings.append(f"high-density baseline NDVI unexpectedly saturated: {max_ndvi:.3f}")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--field-csv",
        type=Path,
        default=Path("docs/ai/heinong60_high_density_daily_field_summary.csv"),
    )
    parser.add_argument(
        "--ridge-csv",
        type=Path,
        default=Path("docs/ai/heinong60_high_density_daily_ridge_states.csv"),
    )
    parser.add_argument(
        "--trace-json",
        type=Path,
        default=Path("docs/ai/heinong60_high_density_oracle_trace.json"),
    )
    args = parser.parse_args()
    summary = run_trace(
        scenario_cls=ScenarioFullSeasonHeinong60HighDensityBaseline,
        scenario_id=SCENARIO_ID,
        trace_app_name=TRACE_APP_NAME,
        zones=ZONES,
        field_csv=args.field_csv,
        ridge_csv=args.ridge_csv,
        trace_json=args.trace_json,
        diagnostics=high_density_diagnostics,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
