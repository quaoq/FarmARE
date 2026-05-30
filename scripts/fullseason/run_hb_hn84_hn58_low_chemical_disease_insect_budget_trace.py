"""Run hb_hn84_hn58_low_chemical_disease_insect_budget oracle trace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_hn84_hn58_low_chemical_disease_insect_budget import (  # noqa: E402
    SCENARIO_ID,
    SPEC,
    ScenarioFullSeasonHBHn84Hn58LowChemicalDiseaseInsectBudget,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace  # noqa: E402

TRACE_APP_NAME = "HbHn84Hn58LowChemicalDiseaseInsectBudgetDailyTrace"
ZONES = list(SPEC.zones)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--field-csv",
        type=Path,
        default=Path(
            "docs/ai/hb-hn84-hn58-low-chemical-disease-insect-budget-field-summary.csv"
        ),
    )
    parser.add_argument(
        "--ridge-csv",
        type=Path,
        default=Path(
            "docs/ai/hb-hn84-hn58-low-chemical-disease-insect-budget-ridge-states.csv"
        ),
    )
    parser.add_argument(
        "--trace-json",
        type=Path,
        default=Path(
            "docs/ai/hb-hn84-hn58-low-chemical-disease-insect-budget-oracle-trace.json"
        ),
    )
    args = parser.parse_args()
    summary = run_trace(
        scenario_cls=ScenarioFullSeasonHBHn84Hn58LowChemicalDiseaseInsectBudget,
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
