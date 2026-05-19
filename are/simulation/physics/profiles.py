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
from datetime import date, timedelta
from typing import Iterable

from are.simulation.physics.biotic_pressure_engine import TreatmentType
from are.simulation.physics.weather_engine import (
    MonthlyClimate,
    WeatherEvent,
    WeatherEventType,
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
        1: MonthlyClimate(temp_mean_c=-18.0, precip_mm=4.0, wet_day_prob=0.15, solar_rad_mj_m2=6.0),
        2: MonthlyClimate(temp_mean_c=-13.0, precip_mm=6.0, wet_day_prob=0.15, solar_rad_mj_m2=9.0),
        3: MonthlyClimate(temp_mean_c=-3.5, precip_mm=12.0, wet_day_prob=0.18, solar_rad_mj_m2=13.0),
        4: MonthlyClimate(temp_mean_c=7.5, precip_mm=22.0, wet_day_prob=0.22, solar_rad_mj_m2=17.0),
        5: MonthlyClimate(temp_mean_c=15.5, precip_mm=42.0, wet_day_prob=0.25, solar_rad_mj_m2=20.0),
        6: MonthlyClimate(temp_mean_c=21.5, precip_mm=88.0, wet_day_prob=0.32, solar_rad_mj_m2=21.0),
        7: MonthlyClimate(temp_mean_c=23.5, precip_mm=145.0, wet_day_prob=0.40, solar_rad_mj_m2=20.0),
        8: MonthlyClimate(temp_mean_c=22.0, precip_mm=110.0, wet_day_prob=0.36, solar_rad_mj_m2=19.0),
        9: MonthlyClimate(temp_mean_c=15.0, precip_mm=55.0, wet_day_prob=0.25, solar_rad_mj_m2=17.0),
        10: MonthlyClimate(temp_mean_c=6.0, precip_mm=22.0, wet_day_prob=0.18, solar_rad_mj_m2=12.0),
        11: MonthlyClimate(temp_mean_c=-6.0, precip_mm=8.0, wet_day_prob=0.16, solar_rad_mj_m2=8.0),
        12: MonthlyClimate(temp_mean_c=-15.5, precip_mm=4.0, wet_day_prob=0.15, solar_rad_mj_m2=6.0),
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
        monthly_climate=_shift_climate(base_climate, [6], precip_factor=2.0, wet_prob_delta=0.15),
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
        monthly_climate=_shift_climate(base_climate, [6], precip_factor=1.9, wet_prob_delta=0.14),
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
        monthly_climate=_shift_climate(base_climate, [8], precip_factor=0.32, wet_prob_delta=-0.14),
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
        monthly_climate=_shift_climate(base_climate, [7], temp_delta=1.8, precip_factor=0.58, wet_prob_delta=-0.10),
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
        monthly_climate=_shift_climate(base_climate, [6], precip_factor=1.85, wet_prob_delta=0.13),
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
        monthly_climate=_shift_climate(base_climate, [9], precip_factor=2.25, wet_prob_delta=0.24),
        rng_seed=919,
        start_date=date(2026, 5, 5),
    )

    # HB_BASE_HN84_STD_NORMAL: normal Harbin year, Heinong84 standard density.
    profiles["harbin_hb_base_hn84_std_normal_seed_1101"] = PhysicsProfile(
        name="harbin_hb_base_hn84_std_normal_seed_1101",
        location="Harbin/Heilongjiang",
        latitude_deg=45.7,
        monthly_climate=_shift_climate(base_climate, [8], precip_factor=1.65, wet_prob_delta=0.08),
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
        monthly_climate=_shift_climate(base_climate, [8], precip_factor=0.28, wet_prob_delta=-0.16),
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
        monthly_climate=_shift_climate(base_climate, [6], precip_factor=1.95, wet_prob_delta=0.16),
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
        monthly_climate=_shift_climate(base_climate, [6], precip_factor=1.8, wet_prob_delta=0.14),
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
        monthly_climate=_shift_climate(base_climate, [8], precip_factor=0.25, wet_prob_delta=-0.18),
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
