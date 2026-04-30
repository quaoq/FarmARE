from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Mapping


class ManagementActionType(str, Enum):
    """
    Management actions that create delayed or persistent effects.

    The action types are intentionally coarse. Tool-level APIs can be more
    detailed, but the physics engine only needs the agronomic effect state.
    """
    PLANTING = "PLANTING"
    IRRIGATION = "IRRIGATION"
    FERTIGATION = "FERTIGATION"
    BASE_FERTILIZER = "BASE_FERTILIZER"
    HERBICIDE = "HERBICIDE"
    INSECTICIDE = "INSECTICIDE"
    FUNGICIDE = "FUNGICIDE"


class GrowthStage(str, Enum):
    """
    Growth stages consumed from phenology.

    This duplicate enum keeps the module standalone. In the full Farm-ARE
    codebase, import this from the phenology module.
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


@dataclass
class ManagementEffectParameters:
    """
    Parameters for the Farm-ARE management-effect model.

    Purpose:
        Translate agent operations into delayed and persistent effect states
        consumed by the soil, canopy/biomass, and biotic-pressure engines.

    Scientific / agronomic basis:
        Farm actions do not directly overwrite crop state. Irrigation first
        modifies soil water; fertilization modifies nutrient availability;
        pesticide/herbicide/fungicide applications reduce biotic pressure with
        residual effects; planting quality affects stand establishment and
        later canopy closure.

    Engineering simplification:
        This module does not model chemical fate, nutrient chemistry, pesticide
        degradation kinetics, or full crop nutrient uptake. It provides compact
        ridge-level effect states for closed-loop scenarios.
    """

    # Planting / stand establishment.
    nominal_seed_depth_cm: float = 4.0
    seed_depth_tolerance_cm: float = 1.0
    bad_depth_stand_penalty: float = 0.20
    poor_soil_stand_penalty: float = 0.20
    poor_alignment_stand_penalty: float = 0.15
    min_stand_fraction: float = 0.35

    # Nutrient state.
    # 1.0 means no nutrient limitation. Lower values reduce growth.
    initial_nutrient_index: float = 0.75
    max_nutrient_index: float = 1.10
    base_fertilizer_gain: float = 0.20
    fertigation_gain: float = 0.12
    daily_nutrient_decay: float = 0.0015
    nutrient_uptake_coeff: float = 0.0020
    nutrient_stress_min: float = 0.55

    # Over-application penalty.
    nutrient_excess_threshold: float = 1.05
    nutrient_excess_penalty: float = 0.08

    # Irrigation accounting.
    # The soil engine performs actual water-balance physics. This module records
    # recent irrigation and provides irrigation delay/effect tags.
    irrigation_effect_window_days: int = 2
    irrigation_overapply_mm_day: float = 18.0

    # Treatment residuals. These should match or be coordinated with the
    # biotic-pressure engine parameters.
    herbicide_residual_days: int = 18
    insecticide_residual_days: int = 10
    fungicide_residual_days: int = 14

    # Weather/application quality modifiers.
    rain_washoff_mm: float = 8.0
    high_wind_ms: float = 6.0
    rain_washoff_efficacy_factor: float = 0.50
    high_wind_efficacy_factor: float = 0.70

    # Effect-to-stress mapping.
    nutrient_index_full_growth: float = 0.95
    nutrient_index_zero_growth: float = 0.35


@dataclass
class ManagementWeatherInput:
    day: date
    rain_mm: float = 0.0
    wind_ms: float = 0.0


@dataclass
class ManagementSoilInput:
    top_vwc: float = 0.25
    root_vwc: float = 0.25
    planting_ready: bool = True
    trafficability: str = "good"


@dataclass
class ManagementCropInput:
    stage: GrowthStage = GrowthStage.NOT_PLANTED
    daily_biomass_g_m2: float = 0.0


@dataclass
class ManagementAction:
    """
    Action applied on a ridge for a given day.

    amount:
        Generic action amount:
          - irrigation/fertigation: mm water equivalent for water component
          - fertilizer: relative unit or kg/ha-equivalent normalized externally
          - pesticide/herbicide/fungicide: normalized application amount

    quality:
        Execution quality in [0, 1]. This can reflect equipment calibration,
        bad alignment, rain/wind, poor depth, or manual operation quality.

    metadata:
        Optional details such as seed_depth_cm, row_alignment_quality, etc.
    """
    action_type: ManagementActionType
    amount: float = 1.0
    quality: float = 1.0
    metadata: dict[str, float | str | bool] = field(default_factory=dict)


@dataclass
class ManagementEffectState:
    ridge_id: int

    # Planting / stand effects.
    planted: bool = False
    planting_date: date | None = None
    seed_depth_cm: float | None = None
    planting_quality: float = 1.0
    stand_fraction: float = 0.0

    # Nutrient effects.
    nutrient_index: float = 0.75
    nutrient_stress: float = 0.85

    # Recent action memory.
    days_since_irrigation: int | None = None
    recent_irrigation_mm: float = 0.0

    # Treatment residuals.
    herbicide_residual_days_left: int = 0
    insecticide_residual_days_left: int = 0
    fungicide_residual_days_left: int = 0

    # Cumulative accounting.
    cumulative_irrigation_mm: float = 0.0
    cumulative_fertigation_amount: float = 0.0
    cumulative_base_fertilizer_amount: float = 0.0
    cumulative_pesticide_applications: int = 0

    tags: list[str] = field(default_factory=list)


@dataclass
class ManagementEffectDayResult:
    day: date
    ridge_id: int
    planted: bool
    stand_fraction: float
    planting_quality: float
    nutrient_index: float
    nutrient_stress: float
    recent_irrigation_mm: float
    days_since_irrigation: int | None
    herbicide_residual_days_left: int
    insecticide_residual_days_left: int
    fungicide_residual_days_left: int
    treatment_efficacy_modifier: float
    tags: list[str]


class ManagementEffectEngine:
    """
    Reduced management-effect engine for Farm-ARE.

    Purpose:
        Convert farm actions into persistent ridge-level effect states that are
        consumed by other physics modules.

    Examples:
        - Planting creates stand_fraction and planting_quality.
        - Fertilizer/fertigation modifies nutrient_index and nutrient_stress.
        - Irrigation is recorded and passed to the soil engine as water input.
        - Herbicide/insecticide/fungicide create residual effect windows.
        - Rain/wind on application day reduce treatment efficacy.

    Non-scope:
        This module does not perform water balance, crop growth, chemical fate,
        or pest/disease reduction directly. It produces effect signals that the
        soil, growth, and biotic engines consume.
    """

    def __init__(
        self,
        num_ridges: int = 64,
        params: ManagementEffectParameters | None = None,
    ) -> None:
        self.params = params or ManagementEffectParameters()
        self.states: dict[int, ManagementEffectState] = {
            ridge_id: ManagementEffectState(
                ridge_id=ridge_id,
                nutrient_index=(params.initial_nutrient_index if params else ManagementEffectParameters().initial_nutrient_index),
                nutrient_stress=0.85,
            )
            for ridge_id in range(num_ridges)
        }

    def update_day(
        self,
        weather: ManagementWeatherInput,
        actions_by_ridge: Mapping[int, list[ManagementAction]] | None = None,
        soil_by_ridge: Mapping[int, ManagementSoilInput] | None = None,
        crop_by_ridge: Mapping[int, ManagementCropInput] | None = None,
    ) -> list[ManagementEffectDayResult]:
        """
        Advance management-effect state by one day.

        This should be called once per day after actions for that day are known.
        Other modules can consume the returned state on the same day or next day,
        depending on the scenario semantics.
        """
        actions_by_ridge = actions_by_ridge or {}
        soil_by_ridge = soil_by_ridge or {}
        crop_by_ridge = crop_by_ridge or {}

        results: list[ManagementEffectDayResult] = []
        for ridge_id, state in self.states.items():
            actions = actions_by_ridge.get(ridge_id, [])
            soil = soil_by_ridge.get(ridge_id, ManagementSoilInput())
            crop = crop_by_ridge.get(ridge_id, ManagementCropInput())
            results.append(self._update_ridge_day(state, weather, actions, soil, crop))

        return results

    def irrigation_mm_by_ridge(
        self,
        actions_by_ridge: Mapping[int, list[ManagementAction]],
    ) -> dict[int, float]:
        """
        Extract irrigation water inputs for the soil engine.

        The management engine records the effect history, but the soil engine
        performs the actual water-balance update.
        """
        out: dict[int, float] = {}
        for ridge_id, actions in actions_by_ridge.items():
            total = 0.0
            for action in actions:
                if action.action_type in {ManagementActionType.IRRIGATION, ManagementActionType.FERTIGATION}:
                    total += max(0.0, action.amount) * self._clip(action.quality, 0.0, 1.0)
            if total > 0:
                out[ridge_id] = total
        return out

    def nutrient_stress_by_ridge(self) -> dict[int, float]:
        return {ridge_id: state.nutrient_stress for ridge_id, state in self.states.items()}

    def stand_fraction_by_ridge(self) -> dict[int, float]:
        return {ridge_id: state.stand_fraction for ridge_id, state in self.states.items()}

    def get_state(self) -> dict[int, ManagementEffectState]:
        return {
            ridge_id: ManagementEffectState(**vars(state))
            for ridge_id, state in self.states.items()
        }

    def _update_ridge_day(
        self,
        state: ManagementEffectState,
        weather: ManagementWeatherInput,
        actions: list[ManagementAction],
        soil: ManagementSoilInput,
        crop: ManagementCropInput,
    ) -> ManagementEffectDayResult:
        p = self.params
        tags: list[str] = []

        treatment_efficacy_modifier = self._application_efficacy_modifier(weather)
        if treatment_efficacy_modifier < 1.0:
            tags.append("application_conditions_reduce_efficacy")

        # Age recent irrigation memory.
        if state.days_since_irrigation is not None:
            state.days_since_irrigation += 1
            if state.days_since_irrigation > p.irrigation_effect_window_days:
                state.days_since_irrigation = None
                state.recent_irrigation_mm = 0.0

        # Decay nutrient index through background depletion and crop uptake.
        uptake = p.nutrient_uptake_coeff * max(0.0, crop.daily_biomass_g_m2)
        state.nutrient_index -= p.daily_nutrient_decay + uptake
        state.nutrient_index = self._clip(state.nutrient_index, 0.0, p.max_nutrient_index)

        # Age residual treatment windows.
        if state.herbicide_residual_days_left > 0:
            state.herbicide_residual_days_left -= 1
            tags.append("herbicide_residual_active")
        if state.insecticide_residual_days_left > 0:
            state.insecticide_residual_days_left -= 1
            tags.append("insecticide_residual_active")
        if state.fungicide_residual_days_left > 0:
            state.fungicide_residual_days_left -= 1
            tags.append("fungicide_residual_active")

        for action in actions:
            quality = self._clip(action.quality, 0.0, 1.0)

            if action.action_type == ManagementActionType.PLANTING:
                self._apply_planting(state, weather, action, soil, quality, tags)

            elif action.action_type == ManagementActionType.IRRIGATION:
                self._apply_irrigation(state, action, quality, tags)

            elif action.action_type == ManagementActionType.FERTIGATION:
                self._apply_irrigation(state, action, quality, tags)
                self._apply_fertigation(state, action, quality, tags)

            elif action.action_type == ManagementActionType.BASE_FERTILIZER:
                self._apply_base_fertilizer(state, action, quality, tags)

            elif action.action_type == ManagementActionType.HERBICIDE:
                state.herbicide_residual_days_left = p.herbicide_residual_days
                state.cumulative_pesticide_applications += 1
                tags.append("herbicide_effect_registered")

            elif action.action_type == ManagementActionType.INSECTICIDE:
                state.insecticide_residual_days_left = p.insecticide_residual_days
                state.cumulative_pesticide_applications += 1
                tags.append("insecticide_effect_registered")

            elif action.action_type == ManagementActionType.FUNGICIDE:
                state.fungicide_residual_days_left = p.fungicide_residual_days
                state.cumulative_pesticide_applications += 1
                tags.append("fungicide_effect_registered")

            else:
                raise ValueError(f"Unsupported action type: {action.action_type}")

        state.nutrient_stress = self._nutrient_stress_from_index(state.nutrient_index)

        if state.nutrient_index > p.nutrient_excess_threshold:
            state.nutrient_stress = max(0.0, state.nutrient_stress - p.nutrient_excess_penalty)
            tags.append("nutrient_excess_penalty")

        if state.nutrient_stress < 0.80:
            tags.append("nutrient_stress")

        state.tags = tags

        return ManagementEffectDayResult(
            day=weather.day,
            ridge_id=state.ridge_id,
            planted=state.planted,
            stand_fraction=round(state.stand_fraction, 3),
            planting_quality=round(state.planting_quality, 3),
            nutrient_index=round(state.nutrient_index, 3),
            nutrient_stress=round(state.nutrient_stress, 3),
            recent_irrigation_mm=round(state.recent_irrigation_mm, 2),
            days_since_irrigation=state.days_since_irrigation,
            herbicide_residual_days_left=state.herbicide_residual_days_left,
            insecticide_residual_days_left=state.insecticide_residual_days_left,
            fungicide_residual_days_left=state.fungicide_residual_days_left,
            treatment_efficacy_modifier=round(treatment_efficacy_modifier, 3),
            tags=tags,
        )

    def _apply_planting(
        self,
        state: ManagementEffectState,
        weather: ManagementWeatherInput,
        action: ManagementAction,
        soil: ManagementSoilInput,
        quality: float,
        tags: list[str],
    ) -> None:
        p = self.params

        seed_depth = float(action.metadata.get("seed_depth_cm", p.nominal_seed_depth_cm))
        row_alignment_quality = float(action.metadata.get("row_alignment_quality", 1.0))

        state.planted = True
        state.planting_date = weather.day
        state.seed_depth_cm = seed_depth

        stand_fraction = quality

        # Seed depth penalty.
        depth_error = abs(seed_depth - p.nominal_seed_depth_cm)
        if depth_error > p.seed_depth_tolerance_cm:
            stand_fraction -= p.bad_depth_stand_penalty
            tags.append("planting_depth_penalty")

        # Soil condition penalty.
        if not soil.planting_ready:
            stand_fraction -= p.poor_soil_stand_penalty
            tags.append("planting_under_marginal_soil")

        # Alignment penalty.
        if row_alignment_quality < 0.85:
            stand_fraction -= p.poor_alignment_stand_penalty
            tags.append("ridge_alignment_penalty")

        state.planting_quality = self._clip(stand_fraction, p.min_stand_fraction, 1.0)
        state.stand_fraction = state.planting_quality
        tags.append("planting_effect_registered")

    def _apply_irrigation(
        self,
        state: ManagementEffectState,
        action: ManagementAction,
        quality: float,
        tags: list[str],
    ) -> None:
        p = self.params
        applied_mm = max(0.0, action.amount) * quality
        state.recent_irrigation_mm = applied_mm
        state.days_since_irrigation = 0
        state.cumulative_irrigation_mm += applied_mm
        tags.append("irrigation_effect_registered")

        if applied_mm >= p.irrigation_overapply_mm_day:
            tags.append("possible_over_irrigation")

    def _apply_fertigation(
        self,
        state: ManagementEffectState,
        action: ManagementAction,
        quality: float,
        tags: list[str],
    ) -> None:
        p = self.params
        # Default fertigation amount is normalized; metadata can override nutrient units.
        nutrient_amount = float(action.metadata.get("nutrient_amount", action.amount))
        gain = p.fertigation_gain * max(0.0, nutrient_amount) * quality
        state.nutrient_index = self._clip(state.nutrient_index + gain, 0.0, p.max_nutrient_index)
        state.cumulative_fertigation_amount += max(0.0, nutrient_amount)
        tags.append("fertigation_effect_registered")

    def _apply_base_fertilizer(
        self,
        state: ManagementEffectState,
        action: ManagementAction,
        quality: float,
        tags: list[str],
    ) -> None:
        p = self.params
        nutrient_amount = float(action.metadata.get("nutrient_amount", action.amount))
        gain = p.base_fertilizer_gain * max(0.0, nutrient_amount) * quality
        state.nutrient_index = self._clip(state.nutrient_index + gain, 0.0, p.max_nutrient_index)
        state.cumulative_base_fertilizer_amount += max(0.0, nutrient_amount)
        tags.append("base_fertilizer_effect_registered")

    def _application_efficacy_modifier(self, weather: ManagementWeatherInput) -> float:
        p = self.params
        modifier = 1.0
        if weather.rain_mm >= p.rain_washoff_mm:
            modifier *= p.rain_washoff_efficacy_factor
        if weather.wind_ms >= p.high_wind_ms:
            modifier *= p.high_wind_efficacy_factor
        return self._clip(modifier, 0.0, 1.0)

    def _nutrient_stress_from_index(self, nutrient_index: float) -> float:
        p = self.params
        if nutrient_index >= p.nutrient_index_full_growth:
            return 1.0
        if nutrient_index <= p.nutrient_index_zero_growth:
            return p.nutrient_stress_min

        frac = (
            (nutrient_index - p.nutrient_index_zero_growth)
            / (p.nutrient_index_full_growth - p.nutrient_index_zero_growth)
        )
        return p.nutrient_stress_min + frac * (1.0 - p.nutrient_stress_min)

    @staticmethod
    def _clip(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))


if __name__ == "__main__":
    from datetime import timedelta

    engine = ManagementEffectEngine(num_ridges=2)

    start = date(2026, 5, 10)
    for i in range(10):
        day = start + timedelta(days=i)
        weather = ManagementWeatherInput(day=day, rain_mm=0.0, wind_ms=3.0)
        soil = {
            0: ManagementSoilInput(top_vwc=0.25, root_vwc=0.25, planting_ready=True),
            1: ManagementSoilInput(top_vwc=0.34, root_vwc=0.30, planting_ready=False),
        }
        crop = {
            0: ManagementCropInput(stage=GrowthStage.VE, daily_biomass_g_m2=2.0),
            1: ManagementCropInput(stage=GrowthStage.VE, daily_biomass_g_m2=2.0),
        }

        actions = {}
        if i == 0:
            actions = {
                0: [
                    ManagementAction(
                        ManagementActionType.PLANTING,
                        quality=1.0,
                        metadata={"seed_depth_cm": 4.0, "row_alignment_quality": 1.0},
                    )
                ],
                1: [
                    ManagementAction(
                        ManagementActionType.PLANTING,
                        quality=0.9,
                        metadata={"seed_depth_cm": 6.0, "row_alignment_quality": 0.8},
                    )
                ],
            }
        if i == 4:
            actions = {
                0: [ManagementAction(ManagementActionType.FERTIGATION, amount=8.0, quality=0.95, metadata={"nutrient_amount": 0.8})],
                1: [ManagementAction(ManagementActionType.IRRIGATION, amount=10.0, quality=0.9)],
            }

        results = engine.update_day(weather, actions, soil, crop)
        for r in results:
            if r.tags:
                print(r)
