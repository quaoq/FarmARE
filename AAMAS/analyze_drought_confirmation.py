"""Read-only diagnosis of the frozen drought pairs; never changes acceptance."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


def analyze(root: Path, output: Path) -> None:
    report_path = root / "calibration_report.json"
    report = json.loads(report_path.read_text())
    rows = []
    for pair, check in zip(report["pairs"], report["checks"], strict=True):
        assert pair["world_seed"] == check["world_seed"]
        control, omission = pair["control"], pair["omission"]
        target = control["target_records"][0]
        treatment_day = datetime.fromtimestamp(
            target["world_time"], timezone.utc
        ).date().isoformat()
        before, after = (
            target["soil_before_native_action"],
            target["soil_after_native_action"],
        )
        row = {
            "world_seed": pair["world_seed"],
            "screening_passed": check["passed"],
            "marketable_loss_fraction": check["shortfall"],
            "biological_loss_fraction": 1
            - omission["biological_yield_kg"] / control["biological_yield_kg"],
            "control_marketable_kg": control["marketable_yield_kg"],
            "omission_marketable_kg": omission["marketable_yield_kg"],
            "accepted_irrigation": target["accepted"],
            "stressed_fraction": target["stressed_fraction"],
            "paired_exogenous_world_equal": control["exogenous_world_digest"]
            == omission["exogenous_world_digest"],
            "paired_target_time_equal": target["world_time"]
            == omission["target_records"][0]["world_time"],
            "treatment_day": treatment_day,
            "mean_root_vwc_before": target["mean_root_vwc"],
            "mean_root_vwc_immediately_after": mean(
                after[r]["root_vwc"] for r in after
            ),
            "stressed_fraction_immediately_after": mean(
                after[r]["root_vwc"] < target["stress_threshold_by_ridge"][r]
                for r in after
            ),
            "immediate_root_vwc_delta": mean(
                after[r]["root_vwc"] - before[r]["root_vwc"] for r in before
            ),
            "immediate_top_vwc_delta": mean(
                after[r]["top_vwc"] - before[r]["top_vwc"] for r in before
            ),
            "immediate_drainage_mm": mean(
                after[r]["cumulative_drainage_mm"]
                - before[r]["cumulative_drainage_mm"]
                for r in before
            ),
        }
        for arm in ("control", "omission"):
            water = json.loads(
                (root / f"world_{pair['world_seed']}" / arm / "water_balance.json")
                .read_text()
            )["rows"]
            assert len({(r["day"], r["ridge_id"]) for r in water}) == len(water)
            on_day = [r for r in water if r["day"] == treatment_day]
            row[f"{arm}_stage_at_daily_tick"] = json.dumps(
                Counter(r["phenology_before_crop_tick"]["stage"] for r in on_day),
                sort_keys=True,
            )
            # Rain is a world quantity; count each day once, not once per ridge.
            rain = {r["day"]: r["rain_mm"] for r in water}
            row[f"{arm}_dry_spell_rain_mm"] = sum(
                v for day, v in rain.items() if "2026-07-10" <= day < "2026-08-17"
            )
            last_day = max(r["day"] for r in water)
            row[f"{arm}_last_daily_mean_stress_days"] = mean(
                r["canopy_before_crop_tick"]["cumulative_stress_days"]
                for r in water if r["day"] == last_day
            )
            row[f"{arm}_complete"] = (
                pair[arm]["harvest_complete"] and pair[arm]["storage_complete"]
            )
        rows.append(row)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "drought_confirmation_pairs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "drought_confirmation_diagnosis.json").write_text(json.dumps({
        "source_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "engineering_acceptance_passed": report["engineering_acceptance_passed"],
        "criteria_unchanged": True,
        "units": {"vwc": "m3/m3", "rain_and_drainage": "mm", "yield": "kg"},
        "sampling": "Native daily soil tick; crop state before daily crop tick. "
                    "Immediate irrigation response comes from native action snapshots.",
        "interpretation": "Complete fixed-workflow paired outcomes. Biological and "
                          "marketable yield remain separate. A stressed root zone and "
                          "accepted irrigation do not guarantee the prespecified "
                          "marketable-yield effect. This report does not change the "
                          "scenario, threshold, cohort, or failed screening result.",
        "pairs": rows,
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.source, args.output_dir)
