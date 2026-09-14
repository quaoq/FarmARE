"""One prospectively declared drought candidate; no model calls or yield-based dose search."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from are.simulation.distributed.authored_specs import author_process
from are.simulation.distributed.calibration import run_drought_calibration
from are.simulation.distributed.experiments import execution_source_digest

ROOT = Path(__file__).resolve().parents[1]
REVISION = "drought_water_balance_v5"


def derive_dose(source: Path) -> dict:
    rows = []
    hashes = {}
    for seed in range(10):
        directory = source / f"world_{seed}/control"
        paths = [directory / "calibration_row.json", directory / "water_balance.json"]
        data = [json.loads(p.read_text()) for p in paths]
        for p in paths:
            hashes[str(p.relative_to(source))] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
        target = data[0]["target_records"][0]
        parameters = {
            r["ridge_id"]: r["parameters"]
            for r in data[1]["rows"]
            if r["day"] == "2026-08-02"
        }
        doses = []
        for ridge, state in target["soil_before_native_action"].items():
            p = parameters[int(ridge)]
            top_retained = (
                (
                    p["saturation_vwc"]
                    - p["top_drainage_rate"]
                    * (p["saturation_vwc"] - p["field_capacity_vwc"])
                )
                * p["top_depth_m"]
                * 1000
            )
            root_deficit = (
                max(0, p["water_stress_vwc"] + 0.02 - state["root_vwc"])
                * p["root_depth_m"]
                * 1000
            )
            doses.append(
                (
                    root_deficit
                    + top_retained
                    - state["top_vwc"] * p["top_depth_m"] * 1000
                )
                / p["irrigation_efficiency"]
            )
        rows.append({"world_seed": seed, "maximum_gross_requirement_mm": max(doses)})
    dose = 5 * math.ceil(max(r["maximum_gross_requirement_mm"] for r in rows) / 5)
    if dose != 60:
        raise ValueError(
            "saved development states differ from the prospectively declared 60 mm design"
        )
    return {
        "worlds": rows,
        "gross_pulse_mm": dose,
        "root_buffer_vwc_assumption": 0.02,
        "source_sha256": hashes,
        "uses_yield_to_choose_dose": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "results/aamas_handover/drought_calendar_v5_selected_dev0to9",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dose = derive_dose(args.source)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    digest = execution_source_digest()
    process = author_process(
        "farm_disease_drought",
        scenario_revision=REVISION,
        calibration_candidate=True,
        reference_harvest_calendar=True,
    )
    path = args.output_dir / "reference.process.json"
    path.write_text(process.model_dump_json(indent=2) + "\n")
    plan = {
        "source_digest": digest,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dose_derivation": dose,
        "worlds": list(range(10)),
        "provider_calls": 0,
        "paper_evidence": False,
    }
    (args.output_dir / "development_plan.json").write_text(
        json.dumps(plan, indent=2) + "\n"
    )
    report = run_drought_calibration(
        args.output_dir / "pairs",
        world_seeds=list(range(10)),
        candidate=True,
        scenario_revision=REVISION,
        retry_immaturity=True,
        retry_wet_grain=True,
        reference_process_path=path,
    )
    summary = {
        "source_digest": digest,
        "source_unchanged": digest == execution_source_digest(),
        "process_digest": process.digest,
        "dose_derivation": dose,
        "checks": report["checks"],
        "engineering_acceptance_passed": report["engineering_acceptance_passed"],
        "confirmation_passed": False,
        "paper_evidence": False,
        "provider_calls": 0,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "dose_derivation"}, indent=2
        )
    )


if __name__ == "__main__":
    main()
