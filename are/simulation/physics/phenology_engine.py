from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from math import acos, cos, pi, radians, sin, tan
from typing import Mapping


class SoybeanStage(str, Enum):
    """
    Reduced soybean phenology stages.

    The stage names follow the standard soybean growth-stage convention:
      - VE/VC/Vn vegetative stages
      - R1-R8 reproductive stages

    References / modeling basis:
      - Fehr and Caviness soybean staging system divides development into
        vegetative (V) and reproductive (R) stages.
      - Extension guides describe R1 as beginning bloom and R8 as full maturity
        when approximately 95% of pods have mature color.
    """
    NOT_PLANTED = "NOT_PLANTED"
    PLANTED_PRE_EMERGENCE = "PLANTED_PRE_EMERGENCE"
    VE = "VE"
    VC = "VC"
    V1 = "V1"
    V2 = "V2"
    V3 = "V3"
    V4_PLUS = "V4_PLUS"
    R1 = "R1_BEGINNING_BLOOM"
    R3 = "R3_BEGINNING_POD"
    R5 = "R5_BEGINNING_SEED"
    R6 = "R6_FULL_SEED"
    R7 = "R7_BEGINNING_MATURITY"
    R8 = "R8_FULL_MATURITY"


class SeedType(str, Enum):
    """
    Discrete seed-type classes used by Farm-ARE.

    Some entries are generic scenario-level variety classes; named entries are
    cultivar proxies controlling maturity duration and stress sensitivity.
    """
    EARLY_COLD = "EARLY_COLD"
    STANDARD = "STANDARD"
    HIGH_DENSITY = "HIGH_DENSITY"
    STRESS_TOLERANT = "STRESS_TOLERANT"
    HEIHE43 = "HEIHE43"
    HEINONG58 = "HEINONG58"
    HEINONG60 = "HEINONG60"
    HEINONG84 = "HEINONG84"
    HEIKE71 = "HEIKE71"


@dataclass
class SeedTypeParameters:
    """
    Phenology parameters for a seed-type class.

    gdd_to_r8:
        Accumulated growing degree days required to reach R8 full maturity.
        Values are scenario parameters based on early-maturity soybean ranges.

    emergence_gdd:
        Thermal time required from planting to emergence under good conditions.

    cold_germination_tolerance:
        Multiplier reducing cold penalties during pre-emergence.

    photoperiod_sensitivity:
        Strength of long-day delay before flowering. Soybean is a short-day crop,
        and long days can delay reproductive transition. This reduced model
        applies photoperiod effects mainly before R1.

    stress_sensitivity:
        Phenology slowdown under poor soil moisture / severe stress.
    """
    gdd_to_r8: float
    emergence_gdd: float
    cold_germination_tolerance: float
    photoperiod_sensitivity: float
    stress_sensitivity: float


DEFAULT_SEED_TYPE_PARAMS: dict[SeedType, SeedTypeParameters] = {
    # Earlier type: shorter season, better near cool planting thresholds.
    # gdd_to_r8 calibrated for Harbin May-Sep cumulative GDD; baseline
    # thresholds had been too high so phenology stuck at R3 in late season.
    # Tuned so that 130 days of static-mode Harbin weather (≈1100 effective
    # GDD with stress + photoperiod) reaches R8 by day 120.
    SeedType.EARLY_COLD: SeedTypeParameters(
        gdd_to_r8=1000.0,
        emergence_gdd=85.0,
        cold_germination_tolerance=0.75,
        photoperiod_sensitivity=0.20,
        stress_sensitivity=0.85,
    ),
    # Regional baseline.
    SeedType.STANDARD: SeedTypeParameters(
        gdd_to_r8=1100.0,
        emergence_gdd=95.0,
        cold_germination_tolerance=1.00,
        photoperiod_sensitivity=0.30,
        stress_sensitivity=1.00,
    ),
    # Similar maturity duration to standard; density response belongs mainly
    # in the growth/yield model, not phenology. It is kept here for consistency.
    SeedType.HIGH_DENSITY: SeedTypeParameters(
        gdd_to_r8=1100.0,
        emergence_gdd=95.0,
        cold_germination_tolerance=1.05,
        photoperiod_sensitivity=0.30,
        stress_sensitivity=1.05,
    ),
    # Stress-tolerant type: slightly shorter than standard and less slowed by stress.
    SeedType.STRESS_TOLERANT: SeedTypeParameters(
        gdd_to_r8=1080.0,
        emergence_gdd=90.0,
        cold_germination_tolerance=0.85,
        photoperiod_sensitivity=0.25,
        stress_sensitivity=0.70,
    ),
    # Tangyan cultivar-specific type. The source workbook has no explicit
    # HH42 row, so these are converted from the same black-soybean Tangyan
    # measurements used by the base scenario: planting 2025-05-19, emergence
    # 2025-05-28, and maturity 2025-08-18.
    SeedType.HEIHE43: SeedTypeParameters(
        gdd_to_r8=1080.0,
        emergence_gdd=70.0,
        cold_germination_tolerance=0.90,
        photoperiod_sensitivity=0.25,
        stress_sensitivity=0.90,
    ),
    # 黑农60: public variety descriptions place it around 119 days and
    # suitable for about 25-30 万株/公顷. The engine's effective GDD scale is
    # lower than raw active accumulated temperature, so this is mapped slightly
    # later than STANDARD but still inside a normal Harbin full-season window.
    SeedType.HEINONG60: SeedTypeParameters(
        gdd_to_r8=1130.0,
        emergence_gdd=92.0,
        cold_germination_tolerance=0.95,
        photoperiod_sensitivity=0.28,
        stress_sensitivity=0.95,
    ),
    # 黑农58: represented as a stress-tolerant Harbin cultivar with similar
    # maturity to HEINONG60/84 but less phenology slowdown under water stress.
    SeedType.HEINONG58: SeedTypeParameters(
        gdd_to_r8=1120.0,
        emergence_gdd=90.0,
        cold_germination_tolerance=0.92,
        photoperiod_sensitivity=0.27,
        stress_sensitivity=0.72,
    ),
    # 黑农84: public variety descriptions place emergence-to-maturity around
    # 119 days and >=10C active accumulated temperature around 2400C. The
    # engine maps that to the same effective Harbin GDD window as HEINONG60.
    SeedType.HEINONG84: SeedTypeParameters(
        gdd_to_r8=1130.0,
        emergence_gdd=92.0,
        cold_germination_tolerance=0.95,
        photoperiod_sensitivity=0.28,
        stress_sensitivity=0.95,
    ),
    # 黑科71: public cultivar descriptions place it in the early/very-early
    # Heilongjiang maturity group (about 108 days emergence-to-maturity).
    # Map that to the engine's effective-GDD scale so it matures before
    # HEINONG84 but later than the generic cold-spring EARLY_COLD class.
    SeedType.HEIKE71: SeedTypeParameters(
        gdd_to_r8=1040.0,
        emergence_gdd=82.0,
        cold_germination_tolerance=0.82,
        photoperiod_sensitivity=0.22,
        stress_sensitivity=0.90,
    ),
}


@dataclass
class PhenologyParameters:
    """
    Global parameters for the reduced thermal-time phenology model.

    Scientific basis:
        Soybean phenology is often represented using thermal-time accumulation
        and cultivar-specific coefficients. CROPGRO-Soybean represents
        vegetative/reproductive development as temperature- and photoperiod-
        sensitive. GDD-based models are also used to predict soybean maturity,
        including R8.

    Engineering simplification:
        This module uses a daily GDD model with optional photoperiod adjustment
        before flowering. It does not implement the full CROPGRO cultivar
        coefficient set, hourly temperature response curves, or full
        photoperiod-by-development-stage interactions.

    GDD calculation:
        Daily GDD is computed from min/max air temperature using a base
        temperature and an upper cap:
            GDD = max(0, (Tmax_adj + Tmin_adj)/2 - Tbase)
        where Tmin_adj is clipped below by Tbase and Tmax_adj is clipped above
        by Tupper.
    """
    base_temp_c: float = 10.0
    upper_temp_c: float = 30.0

    # Approximate stage thresholds as fractions of seed-type-specific GDD to R8.
    # These are reduced scenario parameters, not calibrated cultivar coefficients.
    stage_fraction_thresholds: Mapping[SoybeanStage, float] = field(default_factory=lambda: {
        SoybeanStage.VE: 0.05,
        SoybeanStage.VC: 0.08,
        SoybeanStage.V1: 0.11,
        SoybeanStage.V2: 0.15,
        SoybeanStage.V3: 0.19,
        SoybeanStage.V4_PLUS: 0.24,
        SoybeanStage.R1: 0.42,
        SoybeanStage.R3: 0.55,
        SoybeanStage.R5: 0.68,
        SoybeanStage.R6: 0.80,
        SoybeanStage.R7: 0.92,
        SoybeanStage.R8: 1.00,
    })

    # Planting-depth adjustment to emergence thermal-time target.
    nominal_seed_depth_cm: float = 4.0
    emergence_gdd_penalty_per_cm_deeper: float = 8.0
    emergence_gdd_penalty_per_cm_shallower: float = 5.0

    # Soil moisture emergence penalty.
    # Values below/above the preferred interval slow emergence.
    preferred_top_vwc_min: float = 0.20
    preferred_top_vwc_max: float = 0.30
    blocked_top_vwc_dry: float = 0.12
    blocked_top_vwc_wet: float = 0.38
    max_emergence_moisture_penalty: float = 0.50

    # Post-emergence severe stress can slow development, but does not replace
    # the biomass/yield stress model. Default lower bound keeps phenology moving.
    min_development_stress_multiplier: float = 0.60

    # Optional latitude for photoperiod calculation. Harbin is roughly 45.8 N.
    latitude_deg: float = 45.8

    # Critical day length above which flowering transition is delayed.
    # Soybean is a short-day crop; this is a reduced representation.
    critical_daylength_h: float = 14.5
    max_photoperiod_delay_fraction: float = 0.35


@dataclass
class PhenologyWeatherInput:
    day: date
    air_temp_min_c: float
    air_temp_max_c: float
    air_temp_mean_c: float | None = None


@dataclass
class PhenologySoilInput:
    """
    Soil signals consumed by phenology.

    top_temp_c:
        Seed-zone temperature, used mainly during pre-emergence.

    top_vwc:
        Seed/topsoil water content, used to slow emergence under dry/wet states.

    water_stress:
        Root-zone water stress factor in [0, 1] from the soil engine. Used as
        a weak development-rate modifier after emergence.
    """
    top_temp_c: float
    top_vwc: float
    water_stress: float = 1.0


@dataclass
class PlantingConfig:
    planting_date: date
    seed_type: SeedType
    seed_depth_cm: float = 4.0
    planting_quality: float = 1.0
    latitude_deg: float | None = None


@dataclass
class PhenologyState:
    ridge_id: int
    planted: bool = False
    planting_date: date | None = None
    seed_type: SeedType | None = None
    seed_depth_cm: float = 4.0
    planting_quality: float = 1.0

    stage: SoybeanStage = SoybeanStage.NOT_PLANTED
    days_after_planting: int = 0
    accumulated_gdd: float = 0.0
    effective_development_gdd: float = 0.0

    emerged: bool = False
    emergence_date: date | None = None
    maturity_date: date | None = None

    # Diagnostics.
    last_daily_gdd: float = 0.0
    last_effective_gdd: float = 0.0
    last_daylength_h: float = 0.0
    last_photoperiod_multiplier: float = 1.0
    last_stress_multiplier: float = 1.0
    tags: list[str] = field(default_factory=list)


@dataclass
class PhenologyDayResult:
    day: date
    ridge_id: int
    stage: SoybeanStage
    days_after_planting: int
    accumulated_gdd: float
    effective_development_gdd: float
    daily_gdd: float
    effective_daily_gdd: float
    emerged: bool
    emergence_date: date | None
    maturity_date: date | None
    daylength_h: float
    photoperiod_multiplier: float
    stress_multiplier: float
    tags: list[str]


class ThermalTimePhenologyEngine:
    """
    Reduced thermal-time soybean phenology engine for Farm-ARE.

    Purpose:
        Track soybean development stage as a function of temperature, seed type,
        planting depth, soil moisture/temperature during emergence, and optional
        photoperiod delay before flowering.

    Scope:
        This module only models phenological stage progression. It does not
        compute biomass, canopy cover, yield, pest pressure, or final harvest
        recovery. Those modules should consume the stage output.

    Non-scope:
        Full CROPGRO-Soybean phenology, cultivar coefficient calibration,
        hourly temperature response curves, vernalization, detailed leaf-number
        dynamics, or genotype-specific photoperiod functions.
    """

    def __init__(
        self,
        num_ridges: int = 64,
        params: PhenologyParameters | None = None,
        seed_type_params: Mapping[SeedType, SeedTypeParameters] | None = None,
    ) -> None:
        self.params = params or PhenologyParameters()
        self.seed_type_params = dict(seed_type_params or DEFAULT_SEED_TYPE_PARAMS)
        self.states: dict[int, PhenologyState] = {
            ridge_id: PhenologyState(ridge_id=ridge_id)
            for ridge_id in range(num_ridges)
        }

    def plant_ridges(
        self,
        ridge_ids: list[int],
        config: PlantingConfig,
    ) -> None:
        """
        Initialize planting state for selected ridges.

        This does not guarantee emergence. Emergence is produced later by
        daily thermal-time accumulation and soil conditions.
        """
        if not 0.0 <= config.planting_quality <= 1.0:
            raise ValueError("planting_quality must be in [0, 1]")

        for ridge_id in ridge_ids:
            state = self.states[ridge_id]
            state.planted = True
            state.planting_date = config.planting_date
            state.seed_type = config.seed_type
            state.seed_depth_cm = config.seed_depth_cm
            state.planting_quality = config.planting_quality
            state.stage = SoybeanStage.PLANTED_PRE_EMERGENCE
            state.days_after_planting = 0
            state.accumulated_gdd = 0.0
            state.effective_development_gdd = 0.0
            state.emerged = False
            state.emergence_date = None
            state.maturity_date = None
            state.tags = ["planted"]

    def update_day(
        self,
        weather: PhenologyWeatherInput,
        soil_by_ridge: Mapping[int, PhenologySoilInput] | None = None,
    ) -> list[PhenologyDayResult]:
        """
        Advance all planted ridges by one day.

        soil_by_ridge may omit ridges. Missing ridges use neutral/default soil
        inputs, so the model can be run from weather alone if necessary.
        """
        soil_by_ridge = soil_by_ridge or {}

        results: list[PhenologyDayResult] = []
        for ridge_id, state in self.states.items():
            soil = soil_by_ridge.get(
                ridge_id,
                PhenologySoilInput(top_temp_c=weather.air_temp_min_c, top_vwc=0.25, water_stress=1.0),
            )
            results.append(self._update_ridge_day(state, weather, soil))

        return results

    def get_state(self) -> dict[int, PhenologyState]:
        return {
            ridge_id: PhenologyState(**vars(state))
            for ridge_id, state in self.states.items()
        }

    def _update_ridge_day(
        self,
        state: PhenologyState,
        weather: PhenologyWeatherInput,
        soil: PhenologySoilInput,
    ) -> PhenologyDayResult:
        tags: list[str] = []

        if not state.planted:
            return self._result(weather.day, state, 0.0, 0.0, 0.0, 1.0, 1.0, ["not_planted"])

        if state.seed_type is None or state.planting_date is None:
            # Defensive: scenarios that mark ridges harvested/planted without
            # populating seed_type or planting_date should not crash the whole
            # daily tick. Treat the ridge as inert for phenology purposes.
            return self._result(
                weather.day, state, 0.0, 0.0, 0.0, 1.0, 1.0, ["incomplete_planting_metadata"]
            )

        seed_params = self.seed_type_params[state.seed_type]

        state.days_after_planting = max(0, (weather.day - state.planting_date).days + 1)

        daily_gdd = self.compute_daily_gdd(weather.air_temp_min_c, weather.air_temp_max_c)
        state.accumulated_gdd += daily_gdd

        latitude = self.params.latitude_deg
        daylength = self.daylength_hours(weather.day, latitude)

        stress_multiplier = self._stress_multiplier(state, soil, seed_params)
        photoperiod_multiplier = self._photoperiod_multiplier(state, daylength, seed_params)

        effective_daily_gdd = daily_gdd * stress_multiplier * photoperiod_multiplier
        state.effective_development_gdd += effective_daily_gdd

        # Emergence uses seed-type emergence target modified by seed depth,
        # soil moisture, soil temperature, and planting quality.
        emergence_target = self._emergence_target_gdd(state, soil, seed_params)

        if not state.emerged:
            if soil.top_temp_c < self.params.base_temp_c:
                tags.append("emergence_slow_cold_soil")
            if soil.top_vwc < self.params.preferred_top_vwc_min:
                tags.append("emergence_slow_dry_topsoil")
            if soil.top_vwc > self.params.preferred_top_vwc_max:
                tags.append("emergence_slow_wet_topsoil")

            if state.effective_development_gdd >= emergence_target:
                state.emerged = True
                state.emergence_date = weather.day
                state.stage = SoybeanStage.VE
                tags.append("emerged")
            else:
                state.stage = SoybeanStage.PLANTED_PRE_EMERGENCE
        else:
            state.stage = self._stage_from_effective_gdd(state, seed_params)
            if state.stage == SoybeanStage.R1:
                tags.append("reproductive_started")
            if state.stage == SoybeanStage.R8 and state.maturity_date is None:
                state.maturity_date = weather.day
                tags.append("full_maturity_reached")

        if photoperiod_multiplier < 0.999:
            tags.append("photoperiod_delay")
        if stress_multiplier < 0.999:
            tags.append("stress_slowdown")

        state.last_daily_gdd = daily_gdd
        state.last_effective_gdd = effective_daily_gdd
        state.last_daylength_h = daylength
        state.last_photoperiod_multiplier = photoperiod_multiplier
        state.last_stress_multiplier = stress_multiplier
        state.tags = tags

        return self._result(
            weather.day,
            state,
            daily_gdd,
            effective_daily_gdd,
            daylength,
            photoperiod_multiplier,
            stress_multiplier,
            tags,
        )

    def compute_daily_gdd(self, tmin_c: float, tmax_c: float) -> float:
        """
        Compute daily GDD using base and upper temperature caps.

        The default base temperature is 10 C. The default upper cap is 30 C.
        """
        p = self.params
        tmin_adj = max(tmin_c, p.base_temp_c)
        tmax_adj = min(max(tmax_c, p.base_temp_c), p.upper_temp_c)
        return max(0.0, ((tmin_adj + tmax_adj) / 2.0) - p.base_temp_c)

    def _emergence_target_gdd(
        self,
        state: PhenologyState,
        soil: PhenologySoilInput,
        seed_params: SeedTypeParameters,
    ) -> float:
        p = self.params

        target = seed_params.emergence_gdd

        depth_delta = state.seed_depth_cm - p.nominal_seed_depth_cm
        if depth_delta > 0:
            target += depth_delta * p.emergence_gdd_penalty_per_cm_deeper
        elif depth_delta < 0:
            target += abs(depth_delta) * p.emergence_gdd_penalty_per_cm_shallower

        # Poor topsoil moisture increases emergence thermal-time target.
        moisture_penalty = 0.0
        if soil.top_vwc < p.preferred_top_vwc_min:
            denom = max(1e-6, p.preferred_top_vwc_min - p.blocked_top_vwc_dry)
            moisture_penalty = (p.preferred_top_vwc_min - soil.top_vwc) / denom
        elif soil.top_vwc > p.preferred_top_vwc_max:
            denom = max(1e-6, p.blocked_top_vwc_wet - p.preferred_top_vwc_max)
            moisture_penalty = (soil.top_vwc - p.preferred_top_vwc_max) / denom

        moisture_penalty = min(p.max_emergence_moisture_penalty, max(0.0, moisture_penalty))
        target *= 1.0 + moisture_penalty

        # Cold seed-zone temperature increases the emergence target.
        if soil.top_temp_c < p.base_temp_c:
            cold_gap = p.base_temp_c - soil.top_temp_c
            target *= 1.0 + 0.08 * cold_gap * seed_params.cold_germination_tolerance

        # Poor planting quality slows or prevents uniform emergence.
        # Use a bounded penalty instead of setting infinite target.
        quality = max(0.30, min(1.0, state.planting_quality))
        target /= quality

        return target

    def _stress_multiplier(
        self,
        state: PhenologyState,
        soil: PhenologySoilInput,
        seed_params: SeedTypeParameters,
    ) -> float:
        p = self.params

        if not state.emerged:
            # Pre-emergence stress is handled through the emergence target.
            return 1.0

        water_stress = max(0.0, min(1.0, soil.water_stress))
        slowdown = (1.0 - water_stress) * seed_params.stress_sensitivity
        multiplier = 1.0 - slowdown
        return max(p.min_development_stress_multiplier, min(1.0, multiplier))

    def _photoperiod_multiplier(
        self,
        state: PhenologyState,
        daylength_h: float,
        seed_params: SeedTypeParameters,
    ) -> float:
        p = self.params

        # Before emergence, it is not affected by photoperiod.

        if not state.emerged:
            return 1.0

        # Apply only before flowering. After R1, stage progression is driven
        # mainly by thermal time in this reduced model.
        if state.stage.value.startswith("R"):
            return 1.0

        if daylength_h <= p.critical_daylength_h:
            return 1.0

        excess_h = daylength_h - p.critical_daylength_h
        delay = seed_params.photoperiod_sensitivity * excess_h / 2.0
        delay = min(p.max_photoperiod_delay_fraction, max(0.0, delay))
        return 1.0 - delay

    def _stage_from_effective_gdd(
        self,
        state: PhenologyState,
        seed_params: SeedTypeParameters,
    ) -> SoybeanStage:
        frac = min(1.0, state.effective_development_gdd / seed_params.gdd_to_r8)

        # Return latest stage whose threshold has been met.
        current = SoybeanStage.VE
        for stage, threshold in self.params.stage_fraction_thresholds.items():
            if frac >= threshold:
                current = stage
            else:
                break
        return current

    def _result(
        self,
        day: date,
        state: PhenologyState,
        daily_gdd: float,
        effective_daily_gdd: float,
        daylength: float,
        photoperiod_multiplier: float,
        stress_multiplier: float,
        tags: list[str],
    ) -> PhenologyDayResult:
        return PhenologyDayResult(
            day=day,
            ridge_id=state.ridge_id,
            stage=state.stage,
            days_after_planting=state.days_after_planting,
            accumulated_gdd=round(state.accumulated_gdd, 2),
            effective_development_gdd=round(state.effective_development_gdd, 2),
            daily_gdd=round(daily_gdd, 2),
            effective_daily_gdd=round(effective_daily_gdd, 2),
            emerged=state.emerged,
            emergence_date=state.emergence_date,
            maturity_date=state.maturity_date,
            daylength_h=round(daylength, 2),
            photoperiod_multiplier=round(photoperiod_multiplier, 3),
            stress_multiplier=round(stress_multiplier, 3),
            tags=list(tags),
        )

    @staticmethod
    def daylength_hours(day: date, latitude_deg: float) -> float:
        """
        Approximate astronomical daylength in hours.

        This is used only for a reduced soybean photoperiod modifier.
        """
        lat = radians(latitude_deg)
        doy = day.timetuple().tm_yday

        # Solar declination approximation.
        decl = radians(23.44) * sin(2.0 * pi * (284 + doy) / 365.0)

        x = -tan(lat) * tan(decl)
        x = max(-1.0, min(1.0, x))
        hour_angle = acos(x)

        return 24.0 * hour_angle / pi


if __name__ == "__main__":
    from datetime import timedelta

    engine = ThermalTimePhenologyEngine(num_ridges=2)

    planting_date = date(2026, 5, 10)
    engine.plant_ridges(
        ridge_ids=[0, 1],
        config=PlantingConfig(
            planting_date=planting_date,
            seed_type=SeedType.STANDARD,
            seed_depth_cm=4.0,
            planting_quality=1.0,
        ),
    )

    day = planting_date
    for i in range(130):
        weather = PhenologyWeatherInput(
            day=day,
            air_temp_min_c=12.0,
            air_temp_max_c=24.0,
            air_temp_mean_c=18.0,
        )
        soil = {
            0: PhenologySoilInput(top_temp_c=14.0, top_vwc=0.25, water_stress=1.0),
            1: PhenologySoilInput(top_temp_c=11.0, top_vwc=0.18, water_stress=0.85),
        }
        results = engine.update_day(weather, soil)

        if results[0].tags or results[0].stage in {SoybeanStage.R1, SoybeanStage.R5, SoybeanStage.R8}:
            print(results[0])

        if results[0].stage == SoybeanStage.R8:
            break

        day += timedelta(days=1)
