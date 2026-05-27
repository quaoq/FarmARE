"""
Physics-profile registry for round-4 full-season scenarios.

A `PhysicsProfile` bundles the deterministic climate + biotic event schedule
for a 160-day soybean season. Each round-4 scenario references a profile by
name (e.g. ``harbin_baseline_2026_seed_101``); when
``FarmWorldApp.configure_physics_profile(profile_name=...)`` matches a
profile in this registry, the orchestrator wires:

  - the profile's ``WeatherGeneratorConfig`` and seed into a per-scenario
    ``WeatherGenerator`` attached to ``physics.weather_generator``;
  - any ``WeatherEvent`` overrides into the generator;
  - any ``BioticOutbreak`` schedule into a deferred queue consumed during
    the daily tick.

Profiles are pure data: same name + seed → identical 160-day weather
trajectory. They live in Python (not YAML) so type safety, IDE
discoverability, and SHA-pinned reproducibility are free.

Reference: Harbin/Heilongjiang single-crop soybean season, May–September,
based on a 12-month climatology that can be tuned per scenario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from are.simulation.physics.biotic_pressure_engine import TreatmentType
from are.simulation.physics.weather_engine import (
    MonthlyClimate,
    WeatherEvent,
    WeatherGeneratorConfig,
)

# ---------------------------------------------------------------------------
# Profile dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BioticOutbreak:
    """A scheduled mid-season pest/disease outbreak.

    The orchestrator's daily tick raises insect/disease/weed pressure on the
    affected ridges starting on ``start_day_offset`` for ``duration_days``.
    """

    pressure_type: TreatmentType  # which channel to elevate (INSECTICIDE→insect, FUNGICIDE→disease, HERBICIDE→weed)
    start_day_offset: int  # days from scenario start_date
    duration_days: int
    ridge_start: int = 0
    ridge_end: int = 63
    severity: float = 0.5  # target pressure on affected ridges
    label: str | None = None


@dataclass
class PhysicsProfile:
    """Named climate + event schedule for a round-4 full-season scenario."""

    name: str
    location: str
    latitude_deg: float
    monthly_climate: dict[int, MonthlyClimate]
    weather_events: list[WeatherEvent] = field(default_factory=list)
    biotic_outbreaks: list[BioticOutbreak] = field(default_factory=list)
    rng_seed: int = 0
    start_date: date = field(default_factory=lambda: date(2026, 5, 4))
    duration_days: int = 160

    def to_weather_generator_config(self) -> WeatherGeneratorConfig:
        return WeatherGeneratorConfig(monthly=dict(self.monthly_climate))


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _harbin_climate_normal() -> dict[int, MonthlyClimate]:
    """Approximate Harbin (45.7°N) monthly climatology — soybean season focus."""
    return {
        1: MonthlyClimate(
            temp_mean_c=-18.0, precip_mm=4.0, wet_day_prob=0.15, solar_rad_mj_m2=6.0
        ),
        2: MonthlyClimate(
            temp_mean_c=-13.0, precip_mm=6.0, wet_day_prob=0.15, solar_rad_mj_m2=9.0
        ),
        3: MonthlyClimate(
            temp_mean_c=-3.5, precip_mm=12.0, wet_day_prob=0.18, solar_rad_mj_m2=13.0
        ),
        4: MonthlyClimate(
            temp_mean_c=7.5, precip_mm=22.0, wet_day_prob=0.22, solar_rad_mj_m2=17.0
        ),
        5: MonthlyClimate(
            temp_mean_c=15.5, precip_mm=42.0, wet_day_prob=0.25, solar_rad_mj_m2=20.0
        ),
        6: MonthlyClimate(
            temp_mean_c=21.5, precip_mm=88.0, wet_day_prob=0.32, solar_rad_mj_m2=21.0
        ),
        7: MonthlyClimate(
            temp_mean_c=23.5, precip_mm=145.0, wet_day_prob=0.40, solar_rad_mj_m2=20.0
        ),
        8: MonthlyClimate(
            temp_mean_c=22.0, precip_mm=110.0, wet_day_prob=0.36, solar_rad_mj_m2=19.0
        ),
        9: MonthlyClimate(
            temp_mean_c=15.0, precip_mm=55.0, wet_day_prob=0.25, solar_rad_mj_m2=17.0
        ),
        10: MonthlyClimate(
            temp_mean_c=6.0, precip_mm=22.0, wet_day_prob=0.18, solar_rad_mj_m2=12.0
        ),
        11: MonthlyClimate(
            temp_mean_c=-6.0, precip_mm=8.0, wet_day_prob=0.16, solar_rad_mj_m2=8.0
        ),
        12: MonthlyClimate(
            temp_mean_c=-15.5, precip_mm=4.0, wet_day_prob=0.15, solar_rad_mj_m2=6.0
        ),
    }


def _shift_climate(
    base: dict[int, MonthlyClimate],
    months: Iterable[int],
    *,
    temp_delta: float = 0.0,
    precip_factor: float = 1.0,
    wet_prob_delta: float = 0.0,
) -> dict[int, MonthlyClimate]:
    """Build a per-scenario climate by shifting selected months."""
    out: dict[int, MonthlyClimate] = {}
    months_set = set(months)
    for m, mc in base.items():
        if m in months_set:
            out[m] = MonthlyClimate(
                temp_mean_c=mc.temp_mean_c + temp_delta,
                precip_mm=max(0.0, mc.precip_mm * precip_factor),
                wet_day_prob=min(1.0, max(0.0, mc.wet_day_prob + wet_prob_delta)),
                solar_rad_mj_m2=mc.solar_rad_mj_m2,
                wind_mean_ms=mc.wind_mean_ms,
                wind_sigma_ms=mc.wind_sigma_ms,
            )
        else:
            out[m] = mc
    return out


def _solar_shift_climate(
    base: dict[int, MonthlyClimate],
    months: Iterable[int],
    *,
    solar_factor: float,
) -> dict[int, MonthlyClimate]:
    """Build a climate variant with reduced/increased solar radiation."""
    out: dict[int, MonthlyClimate] = {}
    months_set = set(months)
    for m, mc in base.items():
        if m in months_set:
            out[m] = MonthlyClimate(
                temp_mean_c=mc.temp_mean_c,
                precip_mm=mc.precip_mm,
                wet_day_prob=mc.wet_day_prob,
                solar_rad_mj_m2=max(1.0, mc.solar_rad_mj_m2 * solar_factor),
                wind_mean_ms=mc.wind_mean_ms,
                wind_sigma_ms=mc.wind_sigma_ms,
            )
        else:
            out[m] = mc
    return out


# ---------------------------------------------------------------------------
# Profile definitions (10 round-4 scenarios)
# ---------------------------------------------------------------------------


_BASELINE_NAME = "harbin_baseline_2026_seed_101"


def _build_profiles() -> dict[str, PhysicsProfile]:
    base_climate = _harbin_climate_normal()
    profiles: dict[str, PhysicsProfile] = {}

    # 1. Balanced / baseline season.
    profiles[_BASELINE_NAME] = PhysicsProfile(
        name=_BASELINE_NAME,
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=base_climate,
        rng_seed=101,
        start_date=date(2026, 5, 4),
    )

    # 2. Cold spring → delayed planting.
    profiles["harbin_cold_spring_seed_202"] = PhysicsProfile(
        name="harbin_cold_spring_seed_202",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(base_climate, [4, 5], temp_delta=-4.0),
        weather_events=[
            WeatherEvent(
                event_type="cold_spell",
                start_date=date(2026, 5, 4),
                duration_days=8,
                temp_delta_c=-6.0,
                label="cold_spring_persistent",
            ),
        ],
        rng_seed=202,
        start_date=date(2026, 5, 1),
    )

    # 3. Wet June → disease pressure on V4-R1.
    profiles["harbin_wet_june_seed_303"] = PhysicsProfile(
        name="harbin_wet_june_seed_303",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [6], precip_factor=2.0, wet_prob_delta=0.15
        ),
        weather_events=[
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 6, 12),
                duration_days=4,
                total_rain_mm=80.0,
                label="june_rain_burst",
            ),
        ],
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.FUNGICIDE,
                start_day_offset=42,
                duration_days=14,
                severity=0.45,
                label="post_rain_disease",
            ),
        ],
        rng_seed=303,
        start_date=date(2026, 5, 5),
    )

    # 3b. Wet June + A/B zoned planting. The weather regime is close to the
    # wet-June scenario, but the disease outbreak is constrained to the high
    # density B-zone sub-block that agents must target.
    profiles["harbin_wet_june_ab_zoned_seed_313"] = PhysicsProfile(
        name="harbin_wet_june_ab_zoned_seed_313",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [6], precip_factor=1.9, wet_prob_delta=0.14
        ),
        weather_events=[
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 6, 12),
                duration_days=4,
                total_rain_mm=72.0,
                label="june_rain_burst_ab_zoned",
            ),
        ],
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.FUNGICIDE,
                start_day_offset=42,
                duration_days=1,
                ridge_start=40,
                ridge_end=55,
                severity=0.48,
                label="b_zone_high_density_post_rain_disease",
            ),
        ],
        rng_seed=313,
        start_date=date(2026, 5, 5),
    )

    # 3c. Normal Heinong84 season with an edge low-fertility patch. The profile
    # stays weather/biotic-normal; the fertility gradient is scenario state.
    profiles["harbin_heinong84_edge_low_fertility_seed_414"] = PhysicsProfile(
        name="harbin_heinong84_edge_low_fertility_seed_414",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=base_climate,
        rng_seed=414,
        start_date=date(2026, 5, 5),
    )

    # 3d. Normal Heinong84 season except an R5/R6 dry spell. The spatial soil
    # difference is supplied by the scenario through ridge-level soil modifiers.
    profiles["harbin_fastdraining_dry_patch_seed_515"] = PhysicsProfile(
        name="harbin_fastdraining_dry_patch_seed_515",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [8], precip_factor=0.32, wet_prob_delta=-0.14
        ),
        weather_events=[
            WeatherEvent(
                event_type="dry_spell",
                start_date=date(2026, 8, 1),
                duration_days=18,
                label="r5_r6_dry_spell",
            ),
            WeatherEvent(
                event_type="heat_wave",
                start_date=date(2026, 8, 6),
                duration_days=7,
                temp_delta_c=3.0,
                label="r5_r6_heat_pulse",
            ),
        ],
        rng_seed=515,
        start_date=date(2026, 5, 5),
    )

    # 3e. Normal Heinong84 season for staggered planting. Spatial differences
    # come from planting dates, not weather, fertility, disease, or soil traps.
    profiles["harbin_heinong84_staggered_planting_seed_616"] = PhysicsProfile(
        name="harbin_heinong84_staggered_planting_seed_616",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=base_climate,
        rng_seed=616,
        start_date=date(2026, 5, 5),
    )

    # 3f. Heinong84 normal field with a heat/dry midseason insect-pressure
    # rise. Chemical use is intentionally limited by the scenario; the profile
    # only creates the pressure signal that must be thresholded.
    profiles["harbin_heinong84_heat_dry_insect_seed_717"] = PhysicsProfile(
        name="harbin_heinong84_heat_dry_insect_seed_717",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [7], temp_delta=1.8, precip_factor=0.58, wet_prob_delta=-0.10
        ),
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.INSECTICIDE,
                start_day_offset=55,
                duration_days=1,
                ridge_start=18,
                ridge_end=37,
                severity=0.24,
                label="early_below_threshold_insect_signal",
            ),
            BioticOutbreak(
                pressure_type=TreatmentType.INSECTICIDE,
                start_day_offset=68,
                duration_days=1,
                ridge_start=18,
                ridge_end=37,
                severity=0.52,
                label="threshold_insect_pressure",
            ),
        ],
        rng_seed=717,
        start_date=date(2026, 5, 5),
    )

    # 3g. Low-chemical-input Heinong84 season: wetter June raises disease risk,
    # but the scenario only allows fungicide after a clear threshold is observed.
    profiles["harbin_heinong84_low_chemical_wet_disease_seed_818"] = PhysicsProfile(
        name="harbin_heinong84_low_chemical_wet_disease_seed_818",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [6], precip_factor=1.85, wet_prob_delta=0.13
        ),
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.FUNGICIDE,
                start_day_offset=38,
                duration_days=1,
                ridge_start=22,
                ridge_end=43,
                severity=0.26,
                label="wet_june_disease_risk_below_threshold",
            ),
            BioticOutbreak(
                pressure_type=TreatmentType.FUNGICIDE,
                start_day_offset=49,
                duration_days=1,
                ridge_start=22,
                ridge_end=43,
                severity=0.55,
                label="wet_june_disease_threshold",
            ),
        ],
        rng_seed=818,
        start_date=date(2026, 5, 5),
    )

    # 3h. A/B maturity split under a wet late-season prior. A zone uses
    # HEIKE71 early-maturity cultivar; B zone uses HEINONG84 standard density.
    profiles["harbin_early_standard_late_rain_seed_919"] = PhysicsProfile(
        name="harbin_early_standard_late_rain_seed_919",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [9], precip_factor=2.25, wet_prob_delta=0.24
        ),
        weather_events=[
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 9, 18),
                duration_days=6,
                total_rain_mm=72.0,
                label="post_window_late_rain",
            )
        ],
        rng_seed=919,
        start_date=date(2026, 5, 5),
    )

    # HB_BASE_HN84_STD_NORMAL: normal Harbin year, Heinong84 standard density.
    profiles["harbin_hb_base_hn84_std_normal_seed_1101"] = PhysicsProfile(
        name="harbin_hb_base_hn84_std_normal_seed_1101",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [8], precip_factor=1.65, wet_prob_delta=0.08
        ),
        weather_events=[
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 8, 2),
                duration_days=3,
                total_rain_mm=42.0,
                label="normal_podfill_rain",
            ),
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 8, 18),
                duration_days=2,
                total_rain_mm=30.0,
                label="normal_late_podfill_rain",
            ),
        ],
        rng_seed=1101,
        start_date=date(2026, 5, 5),
    )

    # HB_DRYR5R6_HN58_STD_WATERLIMIT: normal early season, R5/R6 dry spell.
    profiles["harbin_hb_dryr5r6_hn58_waterlimit_seed_1202"] = PhysicsProfile(
        name="harbin_hb_dryr5r6_hn58_waterlimit_seed_1202",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [8], precip_factor=0.16, wet_prob_delta=-0.22
        ),
        weather_events=[
            WeatherEvent(
                event_type="dry_spell",
                start_date=date(2026, 8, 1),
                duration_days=18,
                label="r5_r6_waterlimited_dry_spell",
            ),
            WeatherEvent(
                event_type="heat_wave",
                start_date=date(2026, 8, 6),
                duration_days=6,
                temp_delta_c=2.5,
                label="r5_r6_waterlimited_heat_pulse",
            ),
        ],
        rng_seed=1202,
        start_date=date(2026, 5, 5),
    )

    # HB_POORDRAINAGE_WETJUNE_DISEASE_TRAFFICABILITY: wet June plus local disease.
    # Ridge-level drainage modifiers live in the scenario so the hidden spatial
    # condition remains observable only through sensors/drone/robot.
    profiles["harbin_hb_poordrainage_wetjune_disease_seed_1303"] = PhysicsProfile(
        name="harbin_hb_poordrainage_wetjune_disease_seed_1303",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [6], precip_factor=1.95, wet_prob_delta=0.16
        ),
        weather_events=[
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 6, 14),
                duration_days=4,
                total_rain_mm=82.0,
                label="wet_june_poordrainage_rain_burst",
            ),
        ],
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.FUNGICIDE,
                start_day_offset=48,
                duration_days=1,
                ridge_start=44,
                ridge_end=53,
                severity=0.54,
                label="poordrainage_post_rain_disease",
            ),
        ],
        rng_seed=1303,
        start_date=date(2026, 5, 5),
    )

    # HB_SOY_AFTER_SOY_WETJUNE_DISEASE: standard density, higher disease
    # baseline from prior soybean/history plus wet-June trigger.
    profiles["harbin_hb_soy_after_soy_wetjune_disease_seed_1404"] = PhysicsProfile(
        name="harbin_hb_soy_after_soy_wetjune_disease_seed_1404",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [6], precip_factor=1.8, wet_prob_delta=0.14
        ),
        weather_events=[
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 6, 12),
                duration_days=1,
                total_rain_mm=74.0,
                label="wet_june_soy_history_rain_burst",
            ),
        ],
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.FUNGICIDE,
                start_day_offset=42,
                duration_days=1,
                ridge_start=22,
                ridge_end=43,
                severity=0.58,
                label="soy_after_soy_post_rain_disease",
            ),
        ],
        rng_seed=1404,
        start_date=date(2026, 5, 5),
    )

    # 4. Dry August / pod-fill drought.
    profiles["harbin_dry_august_seed_404"] = PhysicsProfile(
        name="harbin_dry_august_seed_404",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            base_climate, [8], precip_factor=0.25, wet_prob_delta=-0.18
        ),
        weather_events=[
            WeatherEvent(
                event_type="dry_spell",
                start_date=date(2026, 8, 1),
                duration_days=20,
                label="aug_drought",
            ),
            WeatherEvent(
                event_type="heat_wave",
                start_date=date(2026, 8, 5),
                duration_days=8,
                temp_delta_c=4.0,
                label="aug_heat",
            ),
        ],
        rng_seed=404,
        start_date=date(2026, 5, 4),
    )

    # 5. Aphid pressure / threshold pest.
    profiles["harbin_aphid_pressure_seed_505"] = PhysicsProfile(
        name="harbin_aphid_pressure_seed_505",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=base_climate,
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.INSECTICIDE,
                start_day_offset=55,
                duration_days=20,
                ridge_start=12,
                ridge_end=35,
                severity=0.55,
                label="aphid_outbreak",
            ),
        ],
        rng_seed=505,
        start_date=date(2026, 5, 4),
    )

    # 6. Nutrient differential / patch.
    profiles["harbin_nutrient_patch_seed_606"] = PhysicsProfile(
        name="harbin_nutrient_patch_seed_606",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=base_climate,
        rng_seed=606,
        start_date=date(2026, 5, 4),
    )

    # 7. Mixed stress trap (drought → rain → disease).
    profiles["harbin_mixed_stress_seed_707"] = PhysicsProfile(
        name="harbin_mixed_stress_seed_707",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            _shift_climate(base_climate, [7], precip_factor=0.4),
            [8],
            precip_factor=1.6,
            wet_prob_delta=0.10,
        ),
        weather_events=[
            WeatherEvent(
                event_type="dry_spell",
                start_date=date(2026, 7, 5),
                duration_days=12,
                label="july_dry",
            ),
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 8, 1),
                duration_days=5,
                total_rain_mm=120.0,
                label="aug_deluge",
            ),
        ],
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.FUNGICIDE,
                start_day_offset=95,
                duration_days=12,
                severity=0.45,
                label="post_deluge_disease",
            ),
        ],
        rng_seed=707,
        start_date=date(2026, 5, 5),
    )

    # 8. Resource limited (climate normal; constraints set by scenario).
    profiles["harbin_resource_limited_seed_808"] = PhysicsProfile(
        name="harbin_resource_limited_seed_808",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=base_climate,
        rng_seed=808,
        start_date=date(2026, 5, 4),
    )

    # 9. Late harvest / rain risk.
    profiles["harbin_late_harvest_seed_909"] = PhysicsProfile(
        name="harbin_late_harvest_seed_909",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(base_climate, [9, 10], precip_factor=1.6),
        weather_events=[
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 9, 25),
                duration_days=6,
                total_rain_mm=70.0,
                label="late_harvest_rain",
            ),
        ],
        rng_seed=909,
        start_date=date(2026, 5, 1),
    )

    # 10. Adversarial: cold spring + wet June + dry August + late rain.
    profiles["harbin_adversarial_weather_seed_1001"] = PhysicsProfile(
        name="harbin_adversarial_weather_seed_1001",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(
            _shift_climate(
                _shift_climate(
                    _shift_climate(base_climate, [4, 5], temp_delta=-3.0),
                    [6],
                    precip_factor=1.6,
                    wet_prob_delta=0.10,
                ),
                [8],
                precip_factor=0.35,
            ),
            [9, 10],
            precip_factor=1.5,
        ),
        weather_events=[
            WeatherEvent(
                event_type="cold_spell",
                start_date=date(2026, 5, 1),
                duration_days=7,
                temp_delta_c=-5.0,
                label="spring_cold",
            ),
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 6, 14),
                duration_days=4,
                total_rain_mm=70.0,
                label="june_rain",
            ),
            WeatherEvent(
                event_type="dry_spell",
                start_date=date(2026, 8, 4),
                duration_days=18,
                label="aug_drought",
            ),
            WeatherEvent(
                event_type="rain_event",
                start_date=date(2026, 9, 20),
                duration_days=5,
                total_rain_mm=55.0,
                label="harvest_rain",
            ),
        ],
        biotic_outbreaks=[
            BioticOutbreak(
                pressure_type=TreatmentType.FUNGICIDE,
                start_day_offset=45,
                duration_days=15,
                severity=0.40,
                label="june_disease",
            ),
            BioticOutbreak(
                pressure_type=TreatmentType.INSECTICIDE,
                start_day_offset=70,
                duration_days=18,
                severity=0.50,
                label="midseason_aphid",
            ),
        ],
        rng_seed=1001,
        start_date=date(2026, 5, 1),
    )

    def add_l3_profile(
        name: str,
        seed: int,
        *,
        climate: dict[int, MonthlyClimate] | None = None,
        events: list[WeatherEvent] | None = None,
        outbreaks: list[BioticOutbreak] | None = None,
        start: date = date(2026, 5, 5),
    ) -> None:
        profile_events = list(events or [])
        if seed >= 1700:
            # Additional generated L3 scenarios use fixed expert-oracle action
            # windows. Keep those windows workable while preserving the named
            # stress events outside the operation dates.
            profile_events.extend(
                [
                    WeatherEvent(
                        "dry_spell",
                        date(2026, 5, 30),
                        7,
                        label="l3_early_operation_window",
                    ),
                    WeatherEvent(
                        "dry_spell",
                        date(2026, 6, 12),
                        9,
                        label="l3_flowering_check_window",
                    ),
                    WeatherEvent(
                        "dry_spell",
                        date(2026, 7, 5),
                        7,
                        label="l3_midseason_operation_window",
                    ),
                    WeatherEvent(
                        "dry_spell",
                        date(2026, 7, 20),
                        13,
                        label="l3_r5_operation_window",
                    ),
                    WeatherEvent(
                        "dry_spell",
                        date(2026, 8, 1),
                        25,
                        label="l3_podfill_operation_window",
                    ),
                    WeatherEvent(
                        "heat_wave",
                        date(2026, 8, 18),
                        35,
                        temp_delta_c=4.0,
                        label="l3_warm_maturity_finish",
                    ),
                    WeatherEvent(
                        "dry_spell",
                        date(2026, 9, 20),
                        55,
                        label="l3_harvest_operation_window",
                    ),
                ]
            )
        profiles[name] = PhysicsProfile(
            name=name,
            location="Harbin/Heilongjiang",
            latitude_deg=45.7,
            monthly_climate=climate or base_climate,
            weather_events=profile_events,
            biotic_outbreaks=outbreaks or [],
            rng_seed=seed,
            start_date=start,
        )

    add_l3_profile(
        "harbin_l3_heihe50_coldspring_seed_1513",
        1513,
        climate=_shift_climate(base_climate, [4, 5], temp_delta=-4.0),
        events=[
            WeatherEvent(
                "cold_spell",
                date(2026, 5, 5),
                8,
                temp_delta_c=-5.5,
                label="cold_seedbed",
            )
        ],
        start=date(2026, 5, 2),
    )
    add_l3_profile(
        "harbin_l3_low_nutrient_flowering_seed_1516",
        1516,
    )
    add_l3_profile(
        "harbin_l3_low_density_weed_seed_1517",
        1517,
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 5, 22), 8, label="early_weed_control_window"
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE, 24, 2, 10, 53, 0.46, "low_density_weed_flush"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_high_weed_seedbank_seed_1518",
        1518,
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 5, 20), 10, label="seedbank_control_window"
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE, 18, 2, 0, 63, 0.50, "seedbank_weed_flush"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_wetjune_short_spray_window_seed_1521",
        1521,
        climate=_shift_climate(
            base_climate, [6], precip_factor=2.0, wet_prob_delta=0.16
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 15),
                4,
                total_rain_mm=82.0,
                label="wet_june_rain",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 6, 25),
                3,
                total_rain_mm=38.0,
                label="short_spray_window_close",
            ),
            WeatherEvent(
                "dry_spell", date(2026, 7, 1), 4, label="short_spray_open_dry"
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 7, 1),
                4,
                wind_ms=2.0,
                label="short_spray_open_low_wind",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 45, 2, 18, 45, 0.54, "wet_june_disease"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_r5_leaf_feeder_seed_1523",
        1523,
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 7, 25), 5, label="r5_leaf_feeder_spray_dry"
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 7, 25),
                5,
                wind_ms=2.0,
                label="r5_leaf_feeder_spray_low_wind",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.INSECTICIDE, 80, 3, 16, 39, 0.58, "r5_leaf_feeder"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_limited_spray_budget_seed_1524",
        1524,
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 7, 25), 5, label="limited_budget_spray_dry"
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 7, 25),
                5,
                wind_ms=2.0,
                label="limited_budget_spray_low_wind",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.INSECTICIDE, 52, 1, 12, 31, 0.24, "early_light_insect"
            ),
            BioticOutbreak(
                TreatmentType.INSECTICIDE, 76, 2, 18, 45, 0.58, "later_threshold_insect"
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn84_dryr5r6_waterlimit_seed_1526",
        1526,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.28, wet_prob_delta=-0.16
        ),
        events=[
            WeatherEvent("dry_spell", date(2026, 7, 24), 26, label="r5_r6_dry_spell"),
            WeatherEvent(
                "heat_wave", date(2026, 8, 6), 6, temp_delta_c=2.8, label="r5_r6_heat"
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn60_high_dryr5r6_seed_1528",
        1528,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.28, wet_prob_delta=-0.16
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 7, 30), 18, label="high_density_dry_spell"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_two_dry_patches_seed_1529",
        1529,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.34, wet_prob_delta=-0.14
        ),
        events=[
            WeatherEvent("dry_spell", date(2026, 8, 1), 17, label="two_patch_dry_spell")
        ],
    )
    add_l3_profile(
        "harbin_l3_staggered_dryr5r6_seed_1532",
        1532,
        climate=profiles["harbin_l3_hn84_dryr5r6_waterlimit_seed_1526"].monthly_climate,
        events=profiles["harbin_l3_hn84_dryr5r6_waterlimit_seed_1526"].weather_events,
    )
    add_l3_profile(
        "harbin_l3_staggered_wetjune_disease_seed_1533",
        1533,
        climate=_shift_climate(
            base_climate, [6], precip_factor=1.9, wet_prob_delta=0.14
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=76.0,
                label="wet_june_canopy_rain",
            ),
            WeatherEvent(
                "dry_spell", date(2026, 7, 7), 4, label="staggered_spray_open_dry"
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 7, 7),
                4,
                wind_ms=2.0,
                label="staggered_spray_open_low_wind",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 45, 2, 0, 31, 0.52, "closed_canopy_disease"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_hn60_late_grain_moisture_seed_1535",
        1535,
        climate=_shift_climate(
            base_climate, [9], precip_factor=1.9, wet_prob_delta=0.18
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 10, 11),
                6,
                total_rain_mm=72.0,
                label="late_rain_risk",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_harvester_days_laterain_seed_1536",
        1536,
        climate=_shift_climate(
            base_climate, [9], precip_factor=1.8, wet_prob_delta=0.18
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 2),
                6,
                total_rain_mm=72.0,
                label="incoming_harvest_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_shattering_waiting_drydown_seed_1539",
        1539,
        climate=_shift_climate(
            base_climate, [9], precip_factor=0.75, wet_prob_delta=-0.06
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 8, 31),
                6,
                total_rain_mm=60.0,
                label="post_window_shattering_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_adversarial_multi_light_seed_1542",
        1542,
        climate=_shift_climate(
            _shift_climate(base_climate, [5], temp_delta=-2.0),
            [6, 8, 9],
            precip_factor=1.25,
            wet_prob_delta=0.06,
        ),
        events=[
            WeatherEvent(
                "cold_spell",
                date(2026, 5, 5),
                4,
                temp_delta_c=-3.0,
                label="light_cold_spring",
            ),
            WeatherEvent(
                "dry_spell", date(2026, 7, 1), 4, label="light_disease_spray_dry"
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 7, 1),
                4,
                wind_ms=2.0,
                label="light_disease_spray_low_wind",
            ),
            WeatherEvent("dry_spell", date(2026, 8, 3), 8, label="light_podfill_dry"),
            WeatherEvent(
                "rain_event",
                date(2026, 9, 8),
                2,
                total_rain_mm=28.0,
                label="light_harvest_rain",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 46, 1, 24, 43, 0.36, "light_disease"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_wetcold_high_residue_seed_1514",
        1514,
        climate=_shift_climate(
            base_climate, [5], temp_delta=-3.8, precip_factor=1.65, wet_prob_delta=0.14
        ),
        events=[
            WeatherEvent(
                "cold_spell",
                date(2026, 5, 5),
                8,
                temp_delta_c=-5.0,
                label="residue_cold_seedbed",
            )
        ],
    )
    add_l3_profile("harbin_l3_compacted_headland_seed_1515", 1515)
    add_l3_profile(
        "harbin_l3_dryer_capacity_seed_1537",
        1537,
        climate=profiles[
            "harbin_l3_hn60_late_grain_moisture_seed_1535"
        ].monthly_climate,
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 8),
                6,
                total_rain_mm=72.0,
                label="dryer_capacity_post_window_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_storage_capacity_seed_1538",
        1538,
        climate=profiles[
            "harbin_l3_hn60_late_grain_moisture_seed_1535"
        ].monthly_climate,
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 5),
                6,
                total_rain_mm=72.0,
                label="storage_capacity_post_window_rain",
            )
        ],
    )
    add_l3_profile("harbin_l3_low_carbon_min_pass_seed_1540", 1540)
    add_l3_profile(
        "harbin_l3_organic_weed_seed_1541",
        1541,
        events=[
            WeatherEvent(
                "dry_spell",
                date(2026, 5, 20),
                10,
                label="organic_mechanical_control_window",
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE,
                20,
                2,
                0,
                15,
                0.56,
                "organic_high_weed_patch",
            ),
            BioticOutbreak(
                TreatmentType.HERBICIDE,
                20,
                2,
                16,
                31,
                0.32,
                "organic_medium_weed_patch",
            ),
        ],
    )

    add_l3_profile(
        "harbin_l3_coldspring_lateplanting_laterain_heihe50_seed_1602",
        1602,
        climate=_shift_climate(
            _shift_climate(base_climate, [5], temp_delta=-3.4, precip_factor=1.25),
            [9],
            precip_factor=1.85,
            wet_prob_delta=0.18,
        ),
        events=[
            WeatherEvent(
                "cold_spell",
                date(2026, 5, 5),
                8,
                temp_delta_c=-5.0,
                label="cold_seedbed_delay",
            ),
            WeatherEvent(
                "cold_spell",
                date(2026, 5, 18),
                3,
                temp_delta_c=-5.0,
                label="late_seedbed_cold_snap",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 10, 8),
                6,
                total_rain_mm=72.0,
                label="late_rain_harvest_risk",
            ),
        ],
        start=date(2026, 5, 3),
    )
    add_l3_profile(
        "harbin_l3_wetjune_highdensity_poordrainage_disease_seed_1603",
        1603,
        climate=_shift_climate(
            base_climate, [6], precip_factor=2.05, wet_prob_delta=0.17
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 13),
                5,
                total_rain_mm=92.0,
                label="wet_june_poor_drainage_rain",
            ),
            WeatherEvent("dry_spell", date(2026, 7, 2), 4, label="spray_window_open"),
            WeatherEvent(
                "wind_event", date(2026, 7, 2), 4, wind_ms=2.0, label="low_wind_spray"
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                46,
                2,
                40,
                55,
                0.58,
                "high_density_poordrainage_disease",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_wetjune_soyhistory_poordrainage_disease_seed_1604",
        1604,
        climate=_shift_climate(
            base_climate, [6], precip_factor=1.95, wet_prob_delta=0.16
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 12),
                4,
                total_rain_mm=84.0,
                label="soy_history_wet_june_rain",
            ),
            WeatherEvent(
                "dry_spell", date(2026, 7, 1), 4, label="soy_history_spray_window"
            ),
            WeatherEvent(
                "dry_spell", date(2026, 7, 26), 8, label="soy_history_r5_spray_window"
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 8, 2),
                1,
                wind_ms=2.0,
                label="soy_history_r5_low_wind",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                43,
                2,
                22,
                43,
                0.56,
                "soy_history_poordrainage_disease",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_wetjune_weed_disease_diagnosis_seed_1605",
        1605,
        climate=_shift_climate(
            base_climate, [6], precip_factor=1.75, wet_prob_delta=0.12
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 15),
                4,
                total_rain_mm=70.0,
                label="wet_june_mixed_diagnosis",
            ),
            WeatherEvent(
                "dry_spell",
                date(2026, 7, 15),
                5,
                label="mixed_diagnosis_spray_window",
            ),
            WeatherEvent(
                "dry_spell",
                date(2026, 9, 8),
                2,
                label="mixed_diagnosis_harvest_window",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE, 28, 2, 6, 21, 0.44, "early_weed_patch"
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 48, 2, 34, 49, 0.50, "later_disease_patch"
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_highdensity_wetjune_limited_fungicide_seed_1606",
        1606,
        climate=_shift_climate(
            base_climate, [6], precip_factor=1.9, wet_prob_delta=0.15
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=78.0,
                label="limited_fungicide_wet_june",
            ),
            WeatherEvent(
                "dry_spell", date(2026, 7, 3), 4, label="single_fungicide_window"
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 7, 6),
                1,
                wind_ms=2.0,
                label="single_fungicide_low_wind",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                38,
                1,
                32,
                63,
                0.25,
                "early_subthreshold_disease",
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                49,
                2,
                36,
                59,
                0.58,
                "threshold_disease_single_spray",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_wetjune_shortwindow_trafficability_seed_1607",
        1607,
        climate=_shift_climate(
            base_climate, [6], precip_factor=2.2, wet_prob_delta=0.19
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                5,
                total_rain_mm=98.0,
                label="trafficability_wet_rain",
            ),
            WeatherEvent("dry_spell", date(2026, 7, 1), 6, label="brief_spray_window"),
            WeatherEvent(
                "rain_event",
                date(2026, 7, 7),
                3,
                total_rain_mm=46.0,
                label="window_closes_rain",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 47, 3, 22, 45, 0.56, "short_window_disease"
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn84_hn58_dry_patch_waterlimit_seed_1610",
        1610,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.24, wet_prob_delta=-0.18
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 8, 1), 20, label="variety_patch_dry_spell"
            ),
            WeatherEvent(
                "heat_wave",
                date(2026, 8, 7),
                5,
                temp_delta_c=2.8,
                label="variety_patch_heat",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn60_high_fastdrain_dryr5r6_seed_1611",
        1611,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.22, wet_prob_delta=-0.18
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 8, 1), 22, label="high_density_fastdrain_dry"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_dryr5r6_insect_threshold_waterstress_seed_1613",
        1613,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.18, wet_prob_delta=-0.20
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 7, 30), 22, label="insect_waterstress_dry_spell"
            ),
            WeatherEvent(
                "heat_wave",
                date(2026, 8, 5),
                5,
                temp_delta_c=3.0,
                label="insect_waterstress_heat",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.INSECTICIDE,
                78,
                2,
                30,
                47,
                0.50,
                "insect_waterstress_confusion",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_heatdry_aphid_limitedspray_waterlimit_seed_1614",
        1614,
        climate=_shift_climate(
            base_climate,
            [7, 8],
            temp_delta=1.8,
            precip_factor=0.34,
            wet_prob_delta=-0.16,
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 7, 20), 18, label="heatdry_water_limit"
            ),
            WeatherEvent(
                "heat_wave", date(2026, 7, 25), 7, temp_delta_c=3.2, label="aphid_heat"
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.INSECTICIDE,
                72,
                2,
                12,
                35,
                0.56,
                "heatdry_aphid_threshold",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_lowdensity_weed_dry_competition_seed_1615",
        1615,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.45, wet_prob_delta=-0.10
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 8, 4), 12, label="weed_crop_water_competition"
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE,
                24,
                2,
                8,
                55,
                0.48,
                "low_density_weed_water_competition",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_highweedseedbank_lowchemical_seed_1616",
        1616,
        events=[
            WeatherEvent(
                "dry_spell",
                date(2026, 5, 22),
                8,
                label="lowchemical_weed_control_window",
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE,
                24,
                2,
                0,
                63,
                0.52,
                "lowchemical_seedbank_weed",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_organic_residue_weed_establishment_seed_1617",
        1617,
        climate=_shift_climate(
            base_climate, [5], temp_delta=-2.2, precip_factor=1.35, wet_prob_delta=0.08
        ),
        events=[
            WeatherEvent(
                "cold_spell",
                date(2026, 5, 5),
                5,
                temp_delta_c=-3.5,
                label="organic_residue_cold",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_lowcarbon_batch_wetdisease_seed_1618",
        1618,
        climate=_shift_climate(
            base_climate, [6], precip_factor=1.8, wet_prob_delta=0.12
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=74.0,
                label="lowcarbon_wet_disease_rain",
            ),
            WeatherEvent(
                "dry_spell",
                date(2026, 7, 5),
                4,
                label="lowcarbon_batch_spray_window",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 46, 2, 24, 47, 0.52, "lowcarbon_disease"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_limited_machinery_harvest_laterain_seed_1619",
        1619,
        climate=_shift_climate(
            base_climate, [9], precip_factor=2.0, wet_prob_delta=0.20
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 10, 12),
                6,
                total_rain_mm=72.0,
                label="limited_machinery_late_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_dryer_storage_double_capacity_seed_1620",
        1620,
        climate=_shift_climate(
            base_climate, [9], precip_factor=1.7, wet_prob_delta=0.16
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 8),
                6,
                total_rain_mm=72.0,
                label="double_capacity_late_moisture",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_laterain_shattering_drying_tradeoff_seed_1621",
        1621,
        climate=_shift_climate(
            base_climate, [9], precip_factor=1.55, wet_prob_delta=0.14
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 3),
                6,
                total_rain_mm=72.0,
                label="shattering_drying_tradeoff_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_cool_august_lategrain_laterain_seed_1622",
        1622,
        climate=_shift_climate(
            _shift_climate(base_climate, [8], temp_delta=-3.8, precip_factor=1.20),
            [9],
            precip_factor=1.9,
            wet_prob_delta=0.18,
        ),
        events=[
            WeatherEvent(
                "cold_spell",
                date(2026, 8, 5),
                14,
                temp_delta_c=-3.5,
                label="cool_august_slow_gdd",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 10, 9),
                6,
                total_rain_mm=72.0,
                label="cool_august_late_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_earlyfrost_lateplanting_hn84_seed_1623",
        1623,
        climate=_shift_climate(
            _shift_climate(base_climate, [5], precip_factor=1.35, wet_prob_delta=0.10),
            [9],
            temp_delta=-1.5,
            precip_factor=1.4,
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 5, 5),
                5,
                total_rain_mm=42.0,
                label="late_planting_rain_delay",
            ),
            WeatherEvent(
                "cold_spell",
                date(2026, 9, 15),
                4,
                temp_delta_c=-5.0,
                label="early_frost_risk",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn50_hn84_hn58_mixed_stress_seed_1624",
        1624,
        climate=_shift_climate(
            _shift_climate(base_climate, [5], temp_delta=-1.8),
            [8, 9],
            precip_factor=0.55,
            wet_prob_delta=-0.08,
        ),
        events=[
            WeatherEvent("dry_spell", date(2026, 8, 2), 13, label="mixed_variety_dry"),
            WeatherEvent(
                "rain_event",
                date(2026, 9, 9),
                3,
                total_rain_mm=42.0,
                label="mixed_variety_harvest_rain",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_density_gradient_wetdry_seed_1625",
        1625,
        climate=_shift_climate(
            _shift_climate(base_climate, [6], precip_factor=1.7, wet_prob_delta=0.12),
            [8],
            precip_factor=0.42,
            wet_prob_delta=-0.10,
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 15),
                4,
                total_rain_mm=70.0,
                label="density_gradient_wet",
            ),
            WeatherEvent(
                "dry_spell", date(2026, 8, 3), 12, label="density_gradient_dry"
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE, 24, 2, 0, 20, 0.42, "low_density_weed"
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 46, 2, 43, 63, 0.48, "high_density_disease"
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_fertilizer_quota_edge_lowfertility_seed_1626",
        1626,
    )
    add_l3_profile(
        "harbin_l3_insect_after_fungicide_budget_conflict_seed_1627",
        1627,
        climate=_shift_climate(
            base_climate, [6, 8], precip_factor=1.4, wet_prob_delta=0.08
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=72.0,
                label="fungicide_budget_rain",
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 45, 2, 18, 39, 0.50, "first_disease_budget_use"
            ),
            BioticOutbreak(
                TreatmentType.INSECTICIDE,
                82,
                2,
                24,
                47,
                0.56,
                "later_insect_budget_conflict",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_disease_then_drought_recovery_seed_1628",
        1628,
        climate=_shift_climate(
            _shift_climate(base_climate, [6], precip_factor=1.75, wet_prob_delta=0.12),
            [8],
            precip_factor=0.35,
            wet_prob_delta=-0.12,
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=72.0,
                label="disease_then_drought_rain",
            ),
            WeatherEvent(
                "dry_spell", date(2026, 8, 1), 16, label="recovery_podfill_drought"
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 45, 2, 20, 43, 0.52, "early_disease_recovery"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_weed_then_disease_canopy_confusion_seed_1629",
        1629,
        climate=_shift_climate(
            base_climate, [6], precip_factor=1.7, wet_prob_delta=0.12
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 16),
                4,
                total_rain_mm=68.0,
                label="weed_then_disease_rain",
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE, 24, 2, 4, 23, 0.48, "early_weed_green_ndvi"
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                50,
                2,
                32,
                51,
                0.52,
                "later_disease_crop_canopy",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_lowinput_waterlimited_weed_pressure_seed_1630",
        1630,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.38, wet_prob_delta=-0.12
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 8, 2), 14, label="lowinput_waterlimited_dry"
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE,
                24,
                2,
                0,
                63,
                0.50,
                "lowinput_weed_water_competition",
            )
        ],
    )

    # 1700-series: additional V2 L3 scenarios from the next agronomic batch.
    warm_l3_climate = _shift_climate(base_climate, [5, 6, 7, 8, 9], temp_delta=2.2)
    add_l3_profile(
        "harbin_l3_planter_skip_rows_stand_gap_seed_1704",
        1704,
    )
    add_l3_profile(
        "harbin_l3_replant_after_crusting_short_season_seed_1706",
        1706,
        climate=_shift_climate(
            base_climate, [5], precip_factor=1.45, wet_prob_delta=0.10
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 5, 17),
                2,
                total_rain_mm=48.0,
                label="post_plant_crusting_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_fertilizer_misapplication_strip_recovery_seed_1711",
        1711,
        climate=warm_l3_climate,
    )
    add_l3_profile(
        "harbin_l3_nutrient_vs_disease_leafcolor_diagnosis_seed_1713",
        1713,
        climate=_shift_climate(
            base_climate, [6], precip_factor=1.55, wet_prob_delta=0.10
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 16),
                3,
                total_rain_mm=58.0,
                label="leafcolor_disease_risk_rain",
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                48,
                2,
                40,
                55,
                0.46,
                "leafcolor_true_disease_block",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_weed_green_ndvi_masked_drought_seed_1714",
        1714,
        climate=_shift_climate(
            warm_l3_climate, [8], precip_factor=0.36, wet_prob_delta=-0.12
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 8, 1), 15, label="masked_drought_podfill"
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE, 26, 2, 0, 31, 0.52, "green_weed_ndvi_mask"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_herbicide_window_missed_rain_delay_seed_1717",
        1717,
        climate=_shift_climate(
            base_climate, [5, 6], precip_factor=1.55, wet_prob_delta=0.12
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 5, 25),
                5,
                total_rain_mm=64.0,
                label="herbicide_window_rain_delay",
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE, 24, 2, 12, 45, 0.54, "rain_delayed_weed_flush"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_mechanical_weed_control_soil_wetness_seed_1718",
        1718,
        climate=_shift_climate(
            base_climate, [5, 6], precip_factor=1.65, wet_prob_delta=0.15
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 5, 24),
                4,
                total_rain_mm=70.0,
                label="mechanical_weed_wet_soil_delay",
            )
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE,
                25,
                2,
                0,
                63,
                0.50,
                "organic_weed_flush_wet_soil",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_leaffeeder_vs_disease_spots_diagnosis_seed_1721",
        1721,
        climate=_shift_climate(
            base_climate, [7], precip_factor=1.25, wet_prob_delta=0.05
        ),
        outbreaks=[
            BioticOutbreak(
                TreatmentType.INSECTICIDE, 66, 2, 22, 43, 0.55, "leaf_feeder_true_block"
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                66,
                1,
                44,
                55,
                0.28,
                "spot_like_low_disease_signal",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_wetjune_disease_recheck_after_fungicide_seed_1724",
        1724,
        climate=_shift_climate(
            base_climate, [6, 7], precip_factor=1.75, wet_prob_delta=0.14
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=76.0,
                label="first_disease_rain",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 7, 1),
                3,
                total_rain_mm=42.0,
                label="recheck_disease_rain",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 45, 2, 20, 43, 0.54, "first_disease_wave"
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE, 60, 2, 20, 43, 0.42, "post_spray_recheck_wave"
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_drought_recovery_false_disease_signal_seed_1725",
        1725,
        climate=_shift_climate(
            warm_l3_climate, [7, 8], precip_factor=0.38, wet_prob_delta=-0.13
        ),
        events=[
            WeatherEvent(
                "dry_spell",
                date(2026, 7, 18),
                17,
                label="midseason_drought_recovery_slow",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_r5_heat_stress_without_soil_drought_seed_1726",
        1726,
        climate=_shift_climate(base_climate, [8], temp_delta=1.2, precip_factor=1.25),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 8, 1),
                2,
                total_rain_mm=24.0,
                label="pre_heat_rootzone_recharge",
            ),
            WeatherEvent(
                "heat_wave",
                date(2026, 8, 5),
                7,
                temp_delta_c=7.0,
                label="r5_heat_without_soil_drought",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_cloudy_wet_low_radiation_biomass_seed_1728",
        1728,
        climate=_shift_climate(
            _solar_shift_climate(base_climate, [6, 7], solar_factor=0.68),
            [6, 7],
            precip_factor=1.55,
            wet_prob_delta=0.12,
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 28),
                7,
                total_rain_mm=58.0,
                label="cloudy_wet_low_radiation",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_split_irrigation_schedule_power_limit_seed_1731",
        1731,
        climate=_shift_climate(
            warm_l3_climate, [8], precip_factor=0.32, wet_prob_delta=-0.14
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 8, 1), 16, label="split_irrigation_dry_spell"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_fuel_limit_irrigation_harvest_ops_seed_1733",
        1733,
        climate=_shift_climate(
            base_climate, [8, 9], precip_factor=0.55, wet_prob_delta=-0.06
        ),
        events=[
            WeatherEvent(
                "dry_spell",
                date(2026, 8, 1),
                13,
                label="fuel_limited_irrigation_window",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 9, 10),
                4,
                total_rain_mm=50.0,
                label="fuel_limited_harvest_rain",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_market_discount_high_moisture_delivery_seed_1739",
        1739,
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 16),
                6,
                total_rain_mm=72.0,
                label="high_moisture_discount_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_split_quality_batches_late_disease_seed_1740",
        1740,
        climate=_shift_climate(
            base_climate, [8, 9], precip_factor=1.35, wet_prob_delta=0.08
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 8, 29),
                4,
                total_rain_mm=52.0,
                label="late_quality_rain",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 9, 17),
                6,
                total_rain_mm=72.0,
                label="post_window_quality_rain",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                98,
                3,
                32,
                55,
                0.50,
                "late_disease_quality_block",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_rhizobia_nodulation_failure_nutrition_seed_1702",
        1702,
    )
    add_l3_profile(
        "harbin_l3_local_soil_constraint_nutrition_patch_seed_1708",
        1708,
    )
    add_l3_profile(
        "harbin_l3_micronutrient_deficiency_flowering_patch_seed_1709",
        1709,
    )
    add_l3_profile(
        "harbin_l3_potassium_deficit_dry_podfill_interaction_seed_1710",
        1710,
        climate=_shift_climate(
            warm_l3_climate, [8], precip_factor=0.48, wet_prob_delta=-0.10
        ),
        events=[
            WeatherEvent(
                "dry_spell", date(2026, 8, 3), 12, label="k_deficit_podfill_dry"
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_overfertilized_dense_canopy_disease_risk_seed_1712",
        1712,
        climate=_shift_climate(
            warm_l3_climate, [6, 8], precip_factor=1.35, wet_prob_delta=0.08
        ),
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                62,
                2,
                24,
                39,
                0.42,
                "dense_canopy_disease_risk",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_laterain_insect_risk_seed_1723",
        1723,
        climate=_shift_climate(
            base_climate, [8, 9], precip_factor=1.55, wet_prob_delta=0.10
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 8, 26),
                4,
                total_rain_mm=68.0,
                label="late_rain_insect_risk",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 10, 10),
                6,
                total_rain_mm=72.0,
                label="post_window_laterain_insect_harvest_rain",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.INSECTICIDE,
                94,
                3,
                36,
                55,
                0.52,
                "post_laterain_insect_risk",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_coolwet_flowering_disease_risk_seed_1727",
        1727,
        climate=_shift_climate(
            base_climate, [7], temp_delta=-2.8, precip_factor=1.65, wet_prob_delta=0.12
        ),
        events=[
            WeatherEvent(
                "cold_spell",
                date(2026, 7, 5),
                8,
                temp_delta_c=-4.0,
                label="coolwet_flowering_spell",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 7, 5),
                6,
                total_rain_mm=60.0,
                label="coolwet_flowering_rain",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                63,
                5,
                22,
                32,
                0.56,
                "coolwet_flowering_disease_threshold",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_hail_leaf_damage_recovery_seed_1729",
        1729,
        climate=warm_l3_climate,
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 7, 18),
                1,
                total_rain_mm=36.0,
                label="hail_storm_proxy",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_wind_lodging_storm_harvest_risk_seed_1730",
        1730,
        climate=_shift_climate(
            base_climate, [8, 9], precip_factor=1.40, wet_prob_delta=0.08
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 8, 25),
                3,
                total_rain_mm=72.0,
                label="storm_lodging_rain",
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 8, 26),
                2,
                wind_ms=9.0,
                label="storm_lodging_wind",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 9, 8),
                4,
                total_rain_mm=50.0,
                label="lodged_harvest_rain",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_grain_moisture_sensor_failure_harvest_seed_1736",
        1736,
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 8),
                6,
                total_rain_mm=72.0,
                label="moisture_sensor_harvest_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_storage_aeration_failure_after_harvest_seed_1737",
        1737,
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 1),
                6,
                total_rain_mm=72.0,
                label="storage_aeration_post_window_rain",
            )
        ],
    )
    add_l3_profile(
        "harbin_l3_dryer_breakdown_between_batches_seed_1738",
        1738,
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 8, 31),
                6,
                total_rain_mm=72.0,
                label="dryer_breakdown_harvest_rain",
            )
        ],
    )

    # 1800-series: final engine-grounded multi-stress additions. These avoid
    # unsupported frost/hail/lodging/pollination/salinity mechanisms and use
    # only weather, soil water, biotic pressure, nutrient, phenology, harvest,
    # and postharvest moisture signals exposed by the current FARM engine.
    add_l3_profile(
        "harbin_l3_hn58_drought_insect_waterlimit_seed_1801",
        1801,
        climate=_shift_climate(
            base_climate, [8], precip_factor=0.24, wet_prob_delta=-0.18
        ),
        events=[
            WeatherEvent(
                "dry_spell",
                date(2026, 8, 1),
                18,
                label="hn58_drought_insect_dry_spell",
            ),
            WeatherEvent(
                "heat_wave",
                date(2026, 8, 6),
                5,
                temp_delta_c=2.6,
                label="hn58_drought_insect_heat",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.INSECTICIDE,
                78,
                2,
                40,
                55,
                0.55,
                "separate_insect_pressure_zone",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn60_highdensity_wetjune_weed_disease_seed_1802",
        1802,
        climate=_shift_climate(
            base_climate, [6], precip_factor=1.85, wet_prob_delta=0.14
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=76.0,
                label="hn60_highdensity_wet_june",
            ),
            WeatherEvent(
                "dry_spell",
                date(2026, 7, 3),
                4,
                label="hn60_highdensity_spray_window",
            ),
            WeatherEvent(
                "wind_event",
                date(2026, 7, 3),
                4,
                wind_ms=2.0,
                label="hn60_highdensity_low_wind_window",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE,
                26,
                2,
                4,
                23,
                0.50,
                "weed_dominant_abnormal_zone",
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                49,
                2,
                40,
                55,
                0.54,
                "disease_dominant_abnormal_zone",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_heike71_lateplant_laterain_quality_seed_1803",
        1803,
        climate=_shift_climate(base_climate, [9], precip_factor=1.85, wet_prob_delta=0.18),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 9, 13),
                6,
                total_rain_mm=72.0,
                label="heike71_late_rain_quality_risk",
            ),
        ],
        start=date(2026, 5, 3),
    )
    add_l3_profile(
        "harbin_l3_heihe43_early_density_weed_nutrient_seed_1901",
        1901,
        events=[
            WeatherEvent(
                "dry_spell",
                date(2026, 5, 24),
                8,
                label="heihe43_early_recovery_window",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.HERBICIDE,
                23,
                2,
                44,
                55,
                0.50,
                "heihe43_early_weed_competition",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn58_resistant_biotic_water_budget_seed_1902",
        1902,
        climate=_shift_climate(
            _shift_climate(base_climate, [6], precip_factor=1.55, wet_prob_delta=0.10),
            [8],
            precip_factor=0.34,
            wet_prob_delta=-0.12,
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=64.0,
                label="resistant_biotic_wet_window",
            ),
            WeatherEvent(
                "dry_spell",
                date(2026, 8, 1),
                15,
                label="resistant_biotic_water_budget_dry",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                48,
                2,
                0,
                31,
                0.54,
                "heinong84_zone_threshold_disease",
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                48,
                1,
                32,
                63,
                0.24,
                "heinong58_zone_subthreshold_signal",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_three_cultivar_wet_disease_dry_harvest_seed_1904",
        1904,
        climate=_shift_climate(
            _shift_climate(base_climate, [6], precip_factor=1.70, wet_prob_delta=0.12),
            [8, 9],
            precip_factor=0.50,
            wet_prob_delta=-0.08,
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=74.0,
                label="three_cultivar_wet_disease_rain",
            ),
            WeatherEvent(
                "dry_spell",
                date(2026, 8, 1),
                18,
                label="three_cultivar_zone_c_dry_period",
            ),
            WeatherEvent(
                "rain_event",
                date(2026, 9, 9),
                3,
                total_rain_mm=34.0,
                label="three_cultivar_moisture_harvest_rain",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                48,
                2,
                21,
                42,
                0.56,
                "heinong84_zone_wet_june_disease",
            ),
        ],
    )

    add_l3_profile(
        "harbin_l3_hn60_highdensity_fertigation_irrigation_budget_seed_1905",
        1905,
        climate=_shift_climate(
            _shift_climate(base_climate, [6], precip_factor=1.20, wet_prob_delta=0.04),
            [8],
            precip_factor=0.34,
            wet_prob_delta=-0.12,
        ),
        events=[
            WeatherEvent(
                "dry_spell",
                date(2026, 5, 24),
                7,
                label="hn60_budget_nutrient_diagnosis_window",
            ),
            WeatherEvent(
                "dry_spell",
                date(2026, 8, 2),
                18,
                label="hn60_budget_later_water_stress",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn84_hn58_lowchemical_disease_insect_budget_seed_1906",
        1906,
        climate=_shift_climate(
            _shift_climate(base_climate, [6], precip_factor=1.55, wet_prob_delta=0.10),
            [8],
            precip_factor=0.72,
            wet_prob_delta=-0.03,
        ),
        events=[
            WeatherEvent(
                "rain_event",
                date(2026, 6, 14),
                4,
                total_rain_mm=68.0,
                label="lowchemical_cultivar_disease_window",
            ),
            WeatherEvent(
                "dry_spell",
                date(2026, 8, 1),
                8,
                label="lowchemical_later_insect_window",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                48,
                2,
                0,
                31,
                0.56,
                "heinong84_zone_disease_budget_need",
            ),
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                48,
                1,
                32,
                63,
                0.22,
                "heinong58_zone_monitor_only_disease",
            ),
            BioticOutbreak(
                TreatmentType.INSECTICIDE,
                82,
                2,
                8,
                23,
                0.57,
                "later_insect_budget_need",
            ),
        ],
    )
    add_l3_profile(
        "harbin_l3_hn58_water_chemical_priority_dual_stress_seed_1907",
        1907,
        climate=_shift_climate(
            _shift_climate(base_climate, [6], precip_factor=1.25, wet_prob_delta=0.04),
            [8],
            precip_factor=0.38,
            wet_prob_delta=-0.11,
        ),
        events=[
            WeatherEvent(
                "dry_spell",
                date(2026, 8, 2),
                17,
                label="hn58_dual_stress_water_budget_dry",
            ),
        ],
        outbreaks=[
            BioticOutbreak(
                TreatmentType.FUNGICIDE,
                78,
                2,
                40,
                55,
                0.52,
                "hn58_dual_stress_disease_priority",
            ),
        ],
    )

    return profiles


PROFILES: dict[str, PhysicsProfile] = _build_profiles()


def get_profile(name: str) -> PhysicsProfile | None:
    """Lookup a profile by name; returns None if not registered."""
    return PROFILES.get(name)


__all__ = [
    "BioticOutbreak",
    "PhysicsProfile",
    "PROFILES",
    "get_profile",
]
