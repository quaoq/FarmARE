"""Run a same-field biotic pressure sensitivity experiment.

This is a diagnostic script, not a production scenario. It isolates biotic
effects by keeping water and nutrient stress non-limiting, then compares
healthy, disease-only, insect-only, treated, and untreated ridge blocks across
one Heinong84 full season.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from are.simulation.physics import (  # noqa: E402
    BioticCropInput,
    BioticPressureEngine,
    BioticSoilInput,
    BioticWeatherInput,
    CanopyBiomassGrowthEngine,
    CanopyPhenologyInput,
    GrowthSoilInput,
    GrowthWeatherInput,
    HarvestAction,
    ManagementStressInput,
    PhenologySoilInput,
    PhenologyWeatherInput,
    PlantingConfig,
    SeedType,
    ThermalTimePhenologyEngine,
    TreatmentApplication,
    TreatmentType,
    WeatherGenerator,
    YieldGrowthInput,
    YieldPhenologyInput,
    YieldRecoveryEngine,
    YieldStressInput,
    YieldWeatherInput,
)
from are.simulation.physics.canopy_biomass_engine import (  # noqa: E402
    GrowthStage as CanopyStage,
    SeedType as CanopySeedType,
)
from are.simulation.physics.biotic_pressure_engine import GrowthStage as BioticStage  # noqa: E402
from are.simulation.physics.yield_recovery_engine import GrowthStage as YieldStage  # noqa: E402
from are.simulation.physics.weather_engine import default_harbin_soybean_config  # noqa: E402


SCENARIO_ID = "diagnostic_biotic_pressure_mixed_ridge_experiment"
START = date(2026, 5, 5)
END = date(2026, 9, 30)
PRESSURE_START = date(2026, 7, 1)
PRESSURE_END = date(2026, 8, 15)
DISEASE_EARLY_TREATMENT = date(2026, 7, 10)
DISEASE_LATE_TREATMENT = date(2026, 7, 25)
INSECT_TREATMENT = date(2026, 7, 20)
PRESSURE_LEVEL = 0.50


@dataclass(frozen=True)
class Zone:
    name: str
    start: int
    end: int

    def contains(self, ridge_id: int) -> bool:
        return self.start <= ridge_id <= self.end


ZONES = [
    Zone("healthy_0_9", 0, 9),
    Zone("disease_untreated_10_19", 10, 19),
    Zone("disease_treated_early_20_29", 20, 29),
    Zone("disease_treated_late_30_39", 30, 39),
    Zone("insect_untreated_40_49", 40, 49),
    Zone("insect_treated_50_59", 50, 59),
    Zone("healthy_buffer_60_63", 60, 63),
]


DAILY_COLUMNS = [
    "date",
    "ridge_id",
    "zone",
    "stage",
    "pressure_protocol",
    "treatment_marker",
    "weed_pressure",
    "insect_pressure",
    "disease_pressure",
    "biotic_stress",
    "lai",
    "canopy_cover",
    "ndvi",
    "aboveground_biomass",
    "yield_potential",
    "grain_moisture",
    "biological_yield",
    "recovered_yield",
    "harvested",
]


SUMMARY_COLUMNS = [
    "zone",
    "ridge_count",
    "final_avg_insect_pressure",
    "final_avg_disease_pressure",
    "first_ndvi_gap_0p03_vs_healthy",
    "first_ndvi_gap_0p05_vs_healthy",
    "first_biomass_gap_50_g_m2_vs_healthy",
    "pressure_window_min_avg_ndvi",
    "pressure_end_avg_ndvi",
    "final_avg_ndvi",
    "max_avg_lai",
    "final_avg_biomass_g_m2",
    "final_avg_biological_yield_g_m2",
    "final_avg_recovered_yield_g_m2",
    "final_recovered_kg_total",
    "relative_recovered_vs_healthy_pct",
]


def zone_for_ridge(ridge_id: int) -> str:
    for zone in ZONES:
        if zone.contains(ridge_id):
            return zone.name
    return "unassigned"


def forced_pressure_for(zone: str, day: date) -> tuple[float | None, float | None]:
    if not (PRESSURE_START <= day <= PRESSURE_END):
        return None, None
    if zone == "disease_untreated_10_19":
        return None, PRESSURE_LEVEL
    if zone == "disease_treated_early_20_29" and day < DISEASE_EARLY_TREATMENT:
        return None, PRESSURE_LEVEL
    if zone == "disease_treated_late_30_39" and day < DISEASE_LATE_TREATMENT:
        return None, PRESSURE_LEVEL
    if zone == "insect_untreated_40_49":
        return PRESSURE_LEVEL, None
    if zone == "insect_treated_50_59" and day < INSECT_TREATMENT:
        return PRESSURE_LEVEL, None
    return None, None


def treatment_for(zone: str, day: date) -> TreatmentType | None:
    if zone == "disease_treated_early_20_29" and day == DISEASE_EARLY_TREATMENT:
        return TreatmentType.FUNGICIDE
    if zone == "disease_treated_late_30_39" and day == DISEASE_LATE_TREATMENT:
        return TreatmentType.FUNGICIDE
    if zone == "insect_treated_50_59" and day == INSECT_TREATMENT:
        return TreatmentType.INSECTICIDE
    return None


def pressure_protocol(zone: str) -> str:
    if zone == "disease_untreated_10_19":
        return "disease_0p5_maintained_untreated"
    if zone == "disease_treated_early_20_29":
        return "disease_0p5_until_jul10_fungicide"
    if zone == "disease_treated_late_30_39":
        return "disease_0p5_until_jul25_fungicide"
    if zone == "insect_untreated_40_49":
        return "insect_0p5_maintained_untreated"
    if zone == "insect_treated_50_59":
        return "insect_0p5_until_jul20_insecticide"
    return "healthy_no_forced_biotic_pressure"


def stage_value(stage: object) -> str:
    return getattr(stage, "value", str(stage))


def biotic_stress(weed: float, insect: float, disease: float) -> float:
    return max(0.35, 1.0 - (0.28 * weed + 0.32 * insect + 0.45 * disease))


def run_experiment(daily_csv: Path, summary_csv: Path, summary_json: Path) -> dict[str, object]:
    num_ridges = 64
    weather = WeatherGenerator(config=default_harbin_soybean_config(), seed=919)
    weather_days = {w.day: w for w in weather.generate(START, END)}

    phenology = ThermalTimePhenologyEngine(num_ridges=num_ridges)
    canopy = CanopyBiomassGrowthEngine(num_ridges=num_ridges)
    biotic = BioticPressureEngine(num_ridges=num_ridges)
    yld = YieldRecoveryEngine(num_ridges=num_ridges)

    ridge_ids = list(range(num_ridges))
    phenology.plant_ridges(
        ridge_ids,
        PlantingConfig(
            planting_date=START,
            seed_type=SeedType.HEINONG84,
            seed_depth_cm=4.0,
        ),
    )

    daily_rows: list[dict[str, object]] = []
    current = START
    while current <= END:
        w = weather_days[current]

        for rid in ridge_ids:
            zone = zone_for_ridge(rid)
            insect, disease = forced_pressure_for(zone, current)
            if insect is not None:
                biotic.set_pressure([rid], insect_pressure=insect)
            if disease is not None:
                biotic.set_pressure([rid], disease_pressure=disease)

        phenology_results = phenology.update_day(
            weather=PhenologyWeatherInput(
                day=current,
                air_temp_min_c=w.air_temp_min_c,
                air_temp_max_c=w.air_temp_max_c,
                air_temp_mean_c=w.air_temp_mean_c,
            ),
            soil_by_ridge={
                rid: PhenologySoilInput(
                    top_temp_c=w.air_temp_mean_c - 1.0,
                    top_vwc=0.26,
                    water_stress=1.0,
                )
                for rid in ridge_ids
            },
        )

        for result in phenology_results:
            if result.emerged and not canopy.states[result.ridge_id].initialized:
                canopy.initialize_ridges(
                    [result.ridge_id],
                    seed_type=CanopySeedType.HEINONG84,
                    initial_stand_fraction=1.0,
                )

        canopy.update_day(
            weather=GrowthWeatherInput(
                day=current,
                solar_rad_mj_m2=w.solar_rad_mj_m2,
                air_temp_mean_c=w.air_temp_mean_c,
            ),
            phenology_by_ridge={
                rid: CanopyPhenologyInput(
                    stage=CanopyStage(phenology.states[rid].stage.value),
                    development_fraction=_development_fraction(phenology, rid),
                )
                for rid in ridge_ids
            },
            soil_by_ridge={
                rid: GrowthSoilInput(water_stress=1.0, root_vwc=0.26)
                for rid in ridge_ids
            },
            management_by_ridge={
                rid: ManagementStressInput(
                    nutrient_stress=1.0,
                    biotic_stress=biotic_stress(
                        biotic.states[rid].weed_pressure,
                        biotic.states[rid].insect_pressure,
                        biotic.states[rid].disease_pressure,
                    ),
                    stand_fraction=1.0,
                    planting_density_plants_m2=23.0,
                    weed_pressure=biotic.states[rid].weed_pressure,
                    insect_pressure=biotic.states[rid].insect_pressure,
                    disease_pressure=biotic.states[rid].disease_pressure,
                )
                for rid in ridge_ids
            },
        )

        treatments: dict[int, list[TreatmentApplication]] = {}
        treatment_markers: dict[int, str] = {}
        for rid in ridge_ids:
            zone = zone_for_ridge(rid)
            treatment = treatment_for(zone, current)
            if treatment is None:
                treatment_markers[rid] = ""
                continue
            treatments[rid] = [
                TreatmentApplication(treatment_type=treatment, efficacy_multiplier=1.0)
            ]
            treatment_markers[rid] = treatment.value.lower()

        biotic.update_day(
            weather=BioticWeatherInput(
                day=current,
                air_temp_mean_c=w.air_temp_mean_c,
                rain_mm=w.rain_mm,
                is_raining=w.is_raining,
            ),
            crop_by_ridge={
                rid: BioticCropInput(
                    stage=BioticStage(phenology.states[rid].stage.value),
                    canopy_cover=canopy.states[rid].canopy_cover,
                    seed_type=SeedType.HEINONG84.value,
                )
                for rid in ridge_ids
            },
            soil_by_ridge={rid: BioticSoilInput(top_vwc=0.26, root_vwc=0.26) for rid in ridge_ids},
            treatments_by_ridge=treatments,
        )

        harvest_actions = {}
        for rid in ridge_ids:
            ys = yld.states[rid]
            if ys.harvested:
                continue
            if (
                phenology.states[rid].stage.value == YieldStage.R8.value
                and ys.grain_moisture_frac is not None
                and 0.12 <= ys.grain_moisture_frac <= 0.18
            ):
                harvest_actions[rid] = HarvestAction(machine_quality=0.95, pass_completed=True)

        yld.update_day(
            weather=YieldWeatherInput(
                day=current,
                air_temp_mean_c=w.air_temp_mean_c,
                rain_mm=w.rain_mm,
                solar_rad_mj_m2=w.solar_rad_mj_m2,
                wind_ms=w.wind_ms,
            ),
            phenology_by_ridge={
                rid: YieldPhenologyInput(
                    stage=YieldStage(phenology.states[rid].stage.value),
                    maturity_date=phenology.states[rid].maturity_date,
                )
                for rid in ridge_ids
            },
            growth_by_ridge={
                rid: YieldGrowthInput(
                    yield_potential_g_m2=canopy.states[rid].yield_potential_g_m2,
                    aboveground_biomass_g_m2=canopy.states[rid].aboveground_biomass_g_m2,
                )
                for rid in ridge_ids
            },
            stress_by_ridge={
                rid: YieldStressInput(
                    disease_severity=biotic.states[rid].disease_pressure,
                    insect_pod_damage=biotic.states[rid].insect_pressure,
                )
                for rid in ridge_ids
            },
            harvest_actions_by_ridge=harvest_actions,
        )

        for rid in ridge_ids:
            zone = zone_for_ridge(rid)
            bs = biotic.states[rid]
            cs = canopy.states[rid]
            ps = phenology.states[rid]
            ys = yld.states[rid]
            daily_rows.append(
                {
                    "date": current.isoformat(),
                    "ridge_id": rid,
                    "zone": zone,
                    "stage": stage_value(ps.stage),
                    "pressure_protocol": pressure_protocol(zone),
                    "treatment_marker": treatment_markers.get(rid, ""),
                    "weed_pressure": round(bs.weed_pressure, 4),
                    "insect_pressure": round(bs.insect_pressure, 4),
                    "disease_pressure": round(bs.disease_pressure, 4),
                    "biotic_stress": round(
                        biotic_stress(
                            bs.weed_pressure,
                            bs.insect_pressure,
                            bs.disease_pressure,
                        ),
                        4,
                    ),
                    "lai": round(cs.lai, 4),
                    "canopy_cover": round(cs.canopy_cover, 4),
                    "ndvi": round(cs.ndvi_proxy, 4),
                    "aboveground_biomass": round(cs.aboveground_biomass_g_m2, 4),
                    "yield_potential": round(cs.yield_potential_g_m2, 4),
                    "grain_moisture": "" if ys.grain_moisture_frac is None else round(ys.grain_moisture_frac, 4),
                    "biological_yield": round(ys.biological_yield_g_m2, 4),
                    "recovered_yield": round(ys.recovered_yield_g_m2_at_market_moisture, 4),
                    "harvested": bool(ys.harvested),
                }
            )
        current += timedelta(days=1)

    final_rows = [row for row in daily_rows if row["date"] == END.isoformat()]
    summary_rows = _summary_rows(daily_rows, final_rows)

    daily_csv.parent.mkdir(parents=True, exist_ok=True)
    with daily_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DAILY_COLUMNS)
        writer.writeheader()
        writer.writerows(daily_rows)

    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(summary_rows)

    payload = {
        "scenario_id": SCENARIO_ID,
        "pressure_level": PRESSURE_LEVEL,
        "pressure_window": [PRESSURE_START.isoformat(), PRESSURE_END.isoformat()],
        "treatment_dates": {
            "disease_early": DISEASE_EARLY_TREATMENT.isoformat(),
            "disease_late": DISEASE_LATE_TREATMENT.isoformat(),
            "insect": INSECT_TREATMENT.isoformat(),
        },
        "daily_csv": str(daily_csv),
        "summary_csv": str(summary_csv),
        "summary_rows": summary_rows,
    }
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _development_fraction(phenology: ThermalTimePhenologyEngine, ridge_id: int) -> float:
    state = phenology.states[ridge_id]
    seed_type = state.seed_type
    if seed_type is None:
        return 0.0
    params = phenology.seed_type_params.get(seed_type)
    if params is None:
        return 0.0
    return max(0.0, min(1.0, state.effective_development_gdd / params.gdd_to_r8))


def _summary_rows(
    daily_rows: list[dict[str, object]],
    final_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    healthy_final = [
        float(row["recovered_yield"])
        for row in final_rows
        if row["zone"] == "healthy_0_9"
    ]
    healthy_avg = _mean(healthy_final)
    rows = []
    for zone in ZONES:
        zone_daily = [row for row in daily_rows if row["zone"] == zone.name]
        zone_final = [row for row in final_rows if row["zone"] == zone.name]
        recovered = [float(row["recovered_yield"]) for row in zone_final]
        relative = 0.0 if healthy_avg <= 0 else 100.0 * _mean(recovered) / healthy_avg
        first_ndvi_gap_0p03 = _first_gap_date(
            daily_rows=daily_rows,
            zone=zone.name,
            key="ndvi",
            gap_threshold=0.03,
        )
        first_ndvi_gap_0p05 = _first_gap_date(
            daily_rows=daily_rows,
            zone=zone.name,
            key="ndvi",
            gap_threshold=0.05,
        )
        first_biomass_gap_50 = _first_gap_date(
            daily_rows=daily_rows,
            zone=zone.name,
            key="aboveground_biomass",
            gap_threshold=50.0,
        )
        rows.append(
            {
                "zone": zone.name,
                "ridge_count": len(zone_final),
                "final_avg_insect_pressure": round(_mean(float(row["insect_pressure"]) for row in zone_final), 4),
                "final_avg_disease_pressure": round(_mean(float(row["disease_pressure"]) for row in zone_final), 4),
                "first_ndvi_gap_0p03_vs_healthy": first_ndvi_gap_0p03,
                "first_ndvi_gap_0p05_vs_healthy": first_ndvi_gap_0p05,
                "first_biomass_gap_50_g_m2_vs_healthy": first_biomass_gap_50,
                "pressure_window_min_avg_ndvi": round(
                    _min_daily_zone_mean(
                        [
                            row
                            for row in zone_daily
                            if PRESSURE_START.isoformat() <= str(row["date"]) <= PRESSURE_END.isoformat()
                        ],
                        "ndvi",
                    ),
                    4,
                ),
                "pressure_end_avg_ndvi": round(
                    _mean(
                        float(row["ndvi"])
                        for row in zone_daily
                        if row["date"] == PRESSURE_END.isoformat()
                    ),
                    4,
                ),
                "final_avg_ndvi": round(_mean(float(row["ndvi"]) for row in zone_final), 4),
                "max_avg_lai": round(_max_daily_zone_mean(zone_daily, "lai"), 4),
                "final_avg_biomass_g_m2": round(_mean(float(row["aboveground_biomass"]) for row in zone_final), 4),
                "final_avg_biological_yield_g_m2": round(_mean(float(row["biological_yield"]) for row in zone_final), 4),
                "final_avg_recovered_yield_g_m2": round(_mean(recovered), 4),
                "final_recovered_kg_total": round(_mean(recovered) * len(zone_final) * 35.2 * 1.1 / 1000.0, 3),
                "relative_recovered_vs_healthy_pct": round(relative, 2),
            }
        )
    return rows


def _mean(values: object) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _daily_zone_means(rows: list[dict[str, object]], key: str) -> list[float]:
    by_date: dict[str, list[float]] = {}
    for row in rows:
        by_date.setdefault(str(row["date"]), []).append(float(row[key]))
    return [_mean(values) for values in by_date.values()]


def _first_gap_date(
    daily_rows: list[dict[str, object]],
    zone: str,
    key: str,
    gap_threshold: float,
) -> str:
    if zone == "healthy_0_9":
        return ""
    by_date_zone: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in daily_rows:
        by_date_zone.setdefault((str(row["date"]), str(row["zone"])), []).append(row)
    for day in sorted({str(row["date"]) for row in daily_rows}):
        healthy = by_date_zone.get((day, "healthy_0_9"), [])
        current = by_date_zone.get((day, zone), [])
        if not healthy or not current:
            continue
        gap = _mean(float(row[key]) for row in healthy) - _mean(float(row[key]) for row in current)
        if gap >= gap_threshold:
            return day
    return ""


def _min_daily_zone_mean(rows: list[dict[str, object]], key: str) -> float:
    means = _daily_zone_means(rows, key)
    return min(means) if means else 0.0


def _max_daily_zone_mean(rows: list[dict[str, object]], key: str) -> float:
    means = _daily_zone_means(rows, key)
    return max(means) if means else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--daily-csv",
        type=Path,
        default=Path("docs/ai/biotic-pressure-mixed-ridge-daily.csv"),
    )
    parser.add_argument(
        "--summary-csv",
        type=Path,
        default=Path("docs/ai/biotic-pressure-mixed-ridge-summary.csv"),
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("docs/ai/biotic-pressure-mixed-ridge-summary.json"),
    )
    args = parser.parse_args()
    payload = run_experiment(args.daily_csv, args.summary_csv, args.summary_json)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
