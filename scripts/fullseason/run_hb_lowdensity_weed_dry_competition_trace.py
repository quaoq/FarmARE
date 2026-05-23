"""Run hb_lowdensity_weed_dry_competition oracle and export daily engine CSVs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_lowdensity_weed_dry_competition import (  # noqa: E402
    SCENARIO_ID,
    SPEC,
    ScenarioFullSeasonHBLowdensityWeedDryCompetition,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402

TRACE_APP_NAME = "HbLowdensityWeedDryCompetitionDailyTrace"
ZONES = list(SPEC.zones)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--field-csv",
        type=Path,
        default=Path("docs/ai/hb-lowdensity-weed-dry-competition-field-summary.csv"),
    )
    parser.add_argument(
        "--ridge-csv",
        type=Path,
        default=Path("docs/ai/hb-lowdensity-weed-dry-competition-ridge-states.csv"),
    )
    parser.add_argument(
        "--trace-json",
        type=Path,
        default=Path("docs/ai/hb-lowdensity-weed-dry-competition-oracle-trace.json"),
    )
    args = parser.parse_args()
    summary = run_trace(
        scenario_cls=ScenarioFullSeasonHBLowdensityWeedDryCompetition,
        scenario_id=SCENARIO_ID,
        trace_app_name=TRACE_APP_NAME,
        zones=ZONES,
        field_csv=args.field_csv,
        ridge_csv=args.ridge_csv,
        trace_json=args.trace_json,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
