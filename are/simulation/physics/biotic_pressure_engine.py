from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from math import exp
from typing import Mapping


class GrowthStage(str, Enum):
    """
    Growth stages consumed from the phenology engine.

    This duplicate enum keeps the module standalone. In the full Farm-ARE
    codebase, import the stage enum from the phenology module instead.
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


class TreatmentType(str, Enum):
    HERBICIDE = "HERBICIDE"
    INSECTICIDE = "INSECTICIDE"
    FUNGICIDE = "FUNGICIDE"


@dataclass
class BioticPressureParameters:
    """
    Parameters for the Farm-ARE reduced biotic-pressure model.

    Scientific basis:
        The module follows integrated pest-management logic rather than a
        species-level ecological simulator. It tracks latent weed, insect, and
        disease pressure as severity states in [0, 1], where pressure increases
        under favorable weather/stage conditions and decreases after treatments.

        Relevant agronomic anchors:
          - Soybean aphid management commonly uses an economic threshold of
            about 250 aphids/plant with 80% plants infested and increasing
            populations. This module does not simulate aphids/plant directly by
            default, but maps insect severity to an aphid-equivalent diagnostic.
          - Soybean aphid development is favored by moderate warm temperatures,
            roughly 25-28 C, and strongly reduced by high temperatures near/above
            35 C.
          - Soybean disease risk is often driven by favorable temperature plus
            moisture/leaf-wetness/rainfall conditions.
          - Weed competition is most important early in soybean growth; the
            critical weed-free period is often described as the first several
            weeks after planting or early vegetative stages.

    Engineering simplification:
        This is a ridge-level latent-pressure model. It does not model individual
        species, insect life cycles, dispersal kernels, pathogen inoculum
        dynamics, or herbicide label chemistry. It provides realistic closed-loop
        pressure dynamics for agent scenarios.
    """

    # Generic severity bounds.
    min_pressure: float = 0.0
    max_pressure: float = 1.0

    # Daily baseline growth rates for pressures.
    weed_base_growth: float = 0.035
    insect_base_growth: float = 0.025
    disease_base_growth: float = 0.020

    # Natural daily decay when conditions are unfavorable.
    weed_decay: float = 0.010
    insect_decay: float = 0.012
    disease_decay: float = 0.010

    # Temperature response for insect pressure.
    insect_opt_temp_c: float = 27.0
    insect_temp_sigma_c: float = 7.0
    insect_high_temp_suppression_c: float = 35.0

    # Disease response: moisture/rain and moderate temperature favor disease.
    disease_opt_temp_c: float = 22.0
    disease_temp_sigma_c: float = 8.0
    disease_rain_mm_scale: float = 12.0
    disease_vwc_threshold: float = 0.28

    # Weed response: early crop stages are most sensitive.
    weed_early_stage_multiplier: float = 1.4
    weed_late_stage_multiplier: float = 0.35
    weed_canopy_suppression_strength: float = 0.70

    # Treatment effect parameters.
    # Treatments are delayed and persistent rather than instantaneous deletion.
    herbicide_initial_reduction: float = 0.55
    herbicide_residual_days: int = 18
    herbicide_residual_suppression: float = 0.60

    insecticide_initial_reduction: float = 0.65
    insecticide_residual_days: int = 10
    insecticide_residual_suppression: float = 0.55

    fungicide_initial_reduction: float = 0.45
    fungicide_residual_days: int = 14
    fungicide_residual_suppression: float = 0.45

    # Bad application / weather wash-off.
    rain_washoff_mm: float = 8.0
    wash_off_penalty: float = 0.50

    # Stress conversion.
    # Growth stress multiplier = 1 - weighted pressure, clipped.
    weed_growth_weight: float = 0.28
    insect_growth_weight: float = 0.22
    disease_growth_weight: float = 0.30
    min_biotic_stress_multiplier: float = 0.35

    # Aphid-equivalent diagnostic mapping.
    # severity 0.5 maps near 250 aphids/plant.
    aphid_equiv_at_severity_half: float = 250.0


@dataclass
class BioticWeatherInput:
    day: date
    air_temp_mean_c: float
    rain_mm: float
    is_raining: bool = False


@dataclass
class BioticSoilInput:
    top_vwc: float = 0.25
    root_vwc: float = 0.25


@dataclass
class BioticCropInput:
    stage: GrowthStage
    canopy_cover: float = 0.0


@dataclass
class TreatmentApplication:
    """
    Treatment applied to a ridge on a given day.

    efficacy_multiplier:
        1.0 means nominal efficacy. Lower values model poor timing,
        under-application, rain wash-off, or equipment error.
    """
    treatment_type: TreatmentType
    efficacy_multiplier: float = 1.0


@dataclass
class BioticPressureState:
    ridge_id: int
    weed_pressure: float = 0.05
    insect_pressure: float = 0.02
    disease_pressure: float = 0.02

    herbicide_residual_days_left: int = 0
    insecticide_residual_days_left: int = 0
    fungicide_residual_days_left: int = 0

    cumulative_weed_pressure: float = 0.0
    cumulative_insect_pressure: float = 0.0
    cumulative_disease_pressure: float = 0.0

    tags: list[str] = field(default_factory=list)


@dataclass
class BioticPressureDayResult:
    day: date
    ridge_id: int
    weed_pressure: float
    insect_pressure: float
    disease_pressure: float
    biotic_stress_multiplier: float
    aphid_equivalent_per_plant: float
    insect_treatment_recommended: bool
    herbicide_residual_days_left: int
    insecticide_residual_days_left: int
    fungicide_residual_days_left: int
    tags: list[str]


class BioticPressureEngine:
    """
    Reduced weed / insect / disease pressure engine for Farm-ARE.

    Purpose:
        Provide latent biotic pressure states that evolve with weather, crop
        stage, canopy cover, and treatments. The growth model consumes the
        resulting biotic stress multiplier.

    Scope:
        Ridge-level severity dynamics for weeds, insects, and disease.

    Non-scope:
        Species-level ecology, insect reproduction stages, pathogen inoculum
        transport, chemical label restrictions, resistance evolution, and
        detailed pesticide fate.
    """

    def __init__(
        self,
        num_ridges: int = 64,
        params: BioticPressureParameters | None = None,
        initial_weed_pressure: float = 0.05,
        initial_insect_pressure: float = 0.02,
        initial_disease_pressure: float = 0.02,
    ) -> None:
        self.params = params or BioticPressureParameters()
        self.states: dict[int, BioticPressureState] = {
            ridge_id: BioticPressureState(
                ridge_id=ridge_id,
                weed_pressure=initial_weed_pressure,
                insect_pressure=initial_insect_pressure,
                disease_pressure=initial_disease_pressure,
            )
            for ridge_id in range(num_ridges)
        }

    def update_day(
        self,
        weather: BioticWeatherInput,
        crop_by_ridge: Mapping[int, BioticCropInput],
        soil_by_ridge: Mapping[int, BioticSoilInput] | None = None,
        treatments_by_ridge: Mapping[int, list[TreatmentApplication]] | None = None,
    ) -> list[BioticPressureDayResult]:
        """
        Advance all ridge biotic-pressure states by one day.
        """
        soil_by_ridge = soil_by_ridge or {}
        treatments_by_ridge = treatments_by_ridge or {}

        results: list[BioticPressureDayResult] = []
        for ridge_id, state in self.states.items():
            crop = crop_by_ridge.get(
                ridge_id,
                BioticCropInput(stage=GrowthStage.NOT_PLANTED, canopy_cover=0.0),
            )
            soil = soil_by_ridge.get(ridge_id, BioticSoilInput())
            treatments = treatments_by_ridge.get(ridge_id, [])
            results.append(self._update_ridge_day(state, weather, crop, soil, treatments))

        return results

    def get_state(self) -> dict[int, BioticPressureState]:
        return {
            ridge_id: BioticPressureState(**vars(state))
            for ridge_id, state in self.states.items()
        }

    def set_pressure(
        self,
        ridge_ids: list[int],
        weed_pressure: float | None = None,
        insect_pressure: float | None = None,
        disease_pressure: float | None = None,
    ) -> None:
        """
        Scenario utility for initializing or forcing local outbreaks.
        """
        for ridge_id in ridge_ids:
            state = self.states[ridge_id]
            if weed_pressure is not None:
                state.weed_pressure = self._clip(weed_pressure)
            if insect_pressure is not None:
                state.insect_pressure = self._clip(insect_pressure)
            if disease_pressure is not None:
                state.disease_pressure = self._clip(disease_pressure)

    def _update_ridge_day(
        self,
        state: BioticPressureState,
        weather: BioticWeatherInput,
        crop: BioticCropInput,
        soil: BioticSoilInput,
        treatments: list[TreatmentApplication],
    ) -> BioticPressureDayResult:
        p = self.params
        tags: list[str] = []

        # Apply treatments at the start of the day. If it rains enough on the
        # same day, reduce efficacy to represent wash-off / poor application.
        for treatment in treatments:
            efficacy = self._clip(treatment.efficacy_multiplier)
            if weather.rain_mm >= p.rain_washoff_mm:
                efficacy *= (1.0 - p.wash_off_penalty)
                tags.append("treatment_washoff_risk")

            if treatment.treatment_type == TreatmentType.HERBICIDE:
                reduction = p.herbicide_initial_reduction * efficacy
                state.weed_pressure *= (1.0 - reduction)
                state.herbicide_residual_days_left = p.herbicide_residual_days
                tags.append("herbicide_applied")

            elif treatment.treatment_type == TreatmentType.INSECTICIDE:
                reduction = p.insecticide_initial_reduction * efficacy
                state.insect_pressure *= (1.0 - reduction)
                state.insecticide_residual_days_left = p.insecticide_residual_days
                tags.append("insecticide_applied")

            elif treatment.treatment_type == TreatmentType.FUNGICIDE:
                reduction = p.fungicide_initial_reduction * efficacy
                state.disease_pressure *= (1.0 - reduction)
                state.fungicide_residual_days_left = p.fungicide_residual_days
                tags.append("fungicide_applied")

            else:
                raise ValueError(f"Unsupported treatment type: {treatment.treatment_type}")

        # Compute daily suitability factors.
        weed_suitability = self._weed_suitability(crop)
        insect_suitability = self._insect_suitability(weather, crop)
        disease_suitability = self._disease_suitability(weather, soil, crop)

        # Residual treatments suppress new growth pressure for a limited time.
        if state.herbicide_residual_days_left > 0:
            weed_suitability *= (1.0 - p.herbicide_residual_suppression)
            state.herbicide_residual_days_left -= 1
            tags.append("herbicide_residual_active")

        if state.insecticide_residual_days_left > 0:
            insect_suitability *= (1.0 - p.insecticide_residual_suppression)
            state.insecticide_residual_days_left -= 1
            tags.append("insecticide_residual_active")

        if state.fungicide_residual_days_left > 0:
            disease_suitability *= (1.0 - p.fungicide_residual_suppression)
            state.fungicide_residual_days_left -= 1
            tags.append("fungicide_residual_active")

        # Logistic-like pressure growth under favorable conditions; decay under
        # unfavorable conditions. Pressures remain in [0, 1].
        state.weed_pressure = self._update_pressure(
            pressure=state.weed_pressure,
            suitability=weed_suitability,
            growth_rate=p.weed_base_growth,
            decay_rate=p.weed_decay,
        )
        state.insect_pressure = self._update_pressure(
            pressure=state.insect_pressure,
            suitability=insect_suitability,
            growth_rate=p.insect_base_growth,
            decay_rate=p.insect_decay,
        )
        state.disease_pressure = self._update_pressure(
            pressure=state.disease_pressure,
            suitability=disease_suitability,
            growth_rate=p.disease_base_growth,
            decay_rate=p.disease_decay,
        )

        state.cumulative_weed_pressure += state.weed_pressure
        state.cumulative_insect_pressure += state.insect_pressure
        state.cumulative_disease_pressure += state.disease_pressure

        if state.weed_pressure > 0.35:
            tags.append("weed_pressure_high")
        if state.insect_pressure > 0.50:
            tags.append("insect_pressure_high")
        if state.disease_pressure > 0.40:
            tags.append("disease_pressure_high")

        biotic_stress = self._biotic_stress_multiplier(state)
        aphid_equiv = self._aphid_equivalent(state.insect_pressure)

        # Treatment threshold diagnostic. This is not an automatic treatment rule.
        # The agent/oracle should still verify scouting, stage, weather, and trend.
        insect_treatment_recommended = (
            aphid_equiv >= 250.0
            and crop.stage in {
                GrowthStage.V4_PLUS,
                GrowthStage.R1,
                GrowthStage.R3,
                GrowthStage.R5,
            }
        )
        if insect_treatment_recommended:
            tags.append("aphid_threshold_like_condition")

        state.tags = tags

        return BioticPressureDayResult(
            day=weather.day,
            ridge_id=state.ridge_id,
            weed_pressure=round(state.weed_pressure, 3),
            insect_pressure=round(state.insect_pressure, 3),
            disease_pressure=round(state.disease_pressure, 3),
            biotic_stress_multiplier=round(biotic_stress, 3),
            aphid_equivalent_per_plant=round(aphid_equiv, 1),
            insect_treatment_recommended=insect_treatment_recommended,
            herbicide_residual_days_left=state.herbicide_residual_days_left,
            insecticide_residual_days_left=state.insecticide_residual_days_left,
            fungicide_residual_days_left=state.fungicide_residual_days_left,
            tags=tags,
        )

    def _weed_suitability(self, crop: BioticCropInput) -> float:
        p = self.params

        if crop.stage in {GrowthStage.NOT_PLANTED, GrowthStage.PLANTED_PRE_EMERGENCE}:
            return 0.6

        if crop.stage in {GrowthStage.VE, GrowthStage.VC, GrowthStage.V1, GrowthStage.V2, GrowthStage.V3}:
            stage_factor = p.weed_early_stage_multiplier
        elif crop.stage in {GrowthStage.V4_PLUS, GrowthStage.R1}:
            stage_factor = 1.0
        else:
            stage_factor = p.weed_late_stage_multiplier

        canopy = self._clip(crop.canopy_cover)
        canopy_suppression = 1.0 - p.weed_canopy_suppression_strength * canopy
        return self._clip(stage_factor * canopy_suppression, 0.0, 1.5)

    def _insect_suitability(self, weather: BioticWeatherInput, crop: BioticCropInput) -> float:
        p = self.params

        if crop.stage in {
            GrowthStage.NOT_PLANTED,
            GrowthStage.PLANTED_PRE_EMERGENCE,
            GrowthStage.VE,
            GrowthStage.VC,
            GrowthStage.V1,
            GrowthStage.V2,
        }:
            stage_factor = 0.35
        elif crop.stage in {GrowthStage.V3, GrowthStage.V4_PLUS, GrowthStage.R1, GrowthStage.R3, GrowthStage.R5}:
            stage_factor = 1.0
        else:
            stage_factor = 0.45

        temp_factor = exp(-((weather.air_temp_mean_c - p.insect_opt_temp_c) ** 2) / (2.0 * p.insect_temp_sigma_c ** 2))

        if weather.air_temp_mean_c >= p.insect_high_temp_suppression_c:
            temp_factor *= 0.25

        # Heavy rain can mechanically suppress small insects in this reduced model.
        rain_factor = 0.75 if weather.rain_mm >= 15.0 else 1.0

        return self._clip(stage_factor * temp_factor * rain_factor, 0.0, 1.2)

    def _disease_suitability(
        self,
        weather: BioticWeatherInput,
        soil: BioticSoilInput,
        crop: BioticCropInput,
    ) -> float:
        p = self.params

        if crop.stage in {GrowthStage.NOT_PLANTED, GrowthStage.PLANTED_PRE_EMERGENCE}:
            stage_factor = 0.45
        elif crop.stage in {GrowthStage.VE, GrowthStage.VC, GrowthStage.V1, GrowthStage.V2, GrowthStage.V3}:
            stage_factor = 0.70
        elif crop.stage in {GrowthStage.V4_PLUS, GrowthStage.R1, GrowthStage.R3, GrowthStage.R5, GrowthStage.R6}:
            stage_factor = 1.0
        else:
            stage_factor = 0.60

        temp_factor = exp(-((weather.air_temp_mean_c - p.disease_opt_temp_c) ** 2) / (2.0 * p.disease_temp_sigma_c ** 2))
        rain_factor = 1.0 - exp(-max(0.0, weather.rain_mm) / p.disease_rain_mm_scale)
        wet_soil_factor = 1.0 if soil.top_vwc >= p.disease_vwc_threshold else 0.45

        return self._clip(stage_factor * temp_factor * max(rain_factor, wet_soil_factor), 0.0, 1.2)

    def _update_pressure(
        self,
        pressure: float,
        suitability: float,
        growth_rate: float,
        decay_rate: float,
    ) -> float:
        if suitability > 0.35:
            # Logistic-like growth. Suitability modulates growth rate.
            pressure += growth_rate * suitability * pressure * (1.0 - pressure)
            # Low initial pressure should still be able to increase under favorable
            # conditions, so add a small recruitment term.
            pressure += 0.01 * growth_rate * suitability
        else:
            pressure -= decay_rate * (1.0 - suitability) * pressure

        return self._clip(pressure)

    def _biotic_stress_multiplier(self, state: BioticPressureState) -> float:
        p = self.params
        penalty = (
            p.weed_growth_weight * state.weed_pressure
            + p.insect_growth_weight * state.insect_pressure
            + p.disease_growth_weight * state.disease_pressure
        )
        return self._clip(1.0 - penalty, p.min_biotic_stress_multiplier, 1.0)

    def _aphid_equivalent(self, insect_pressure: float) -> float:
        """
        Map normalized insect pressure to an aphid-equivalent diagnostic.

        This does not claim that all insect pressure is aphid pressure. It gives
        scenarios an interpretable proxy for threshold-like decisions.
        """
        p = self.params
        # severity=0.5 -> 250 aphids/plant by construction.
        return (insect_pressure / 0.5) * p.aphid_equiv_at_severity_half

    def _clip(self, x: float, lo: float | None = None, hi: float | None = None) -> float:
        p = self.params
        lo = p.min_pressure if lo is None else lo
        hi = p.max_pressure if hi is None else hi
        return max(lo, min(hi, x))


if __name__ == "__main__":
    from datetime import timedelta

    engine = BioticPressureEngine(num_ridges=1)

    start = date(2026, 6, 15)
    for i in range(45):
        day = start + timedelta(days=i)
        weather = BioticWeatherInput(
            day=day,
            air_temp_mean_c=26.0,
            rain_mm=12.0 if i in {10, 11, 12} else 0.0,
            is_raining=i in {10, 11, 12},
        )
        crop = {
            0: BioticCropInput(
                stage=GrowthStage.R1 if i > 20 else GrowthStage.V4_PLUS,
                canopy_cover=0.55 + min(0.35, i * 0.01),
            )
        }
        soil = {0: BioticSoilInput(top_vwc=0.30 if i in {10, 11, 12, 13} else 0.24)}
        treatments = {}
        if i == 30:
            treatments = {0: [TreatmentApplication(TreatmentType.INSECTICIDE)]}

        result = engine.update_day(weather, crop, soil, treatments)[0]
        if i % 5 == 0 or result.tags:
            print(result)
