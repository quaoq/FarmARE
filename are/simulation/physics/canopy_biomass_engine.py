from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from math import exp
from typing import Mapping


class GrowthStage(str, Enum):
    """
    Growth stages consumed from the phenology engine.

    This duplicate enum keeps the module standalone. In the full Farm-ARE codebase,
    import the stage enum from the phenology module instead.
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
    EARLY_COLD = "EARLY_COLD"
    STANDARD = "STANDARD"
    HIGH_DENSITY = "HIGH_DENSITY"
    STRESS_TOLERANT = "STRESS_TOLERANT"


@dataclass
class SeedGrowthParameters:
    """
    Seed-type-specific growth parameters.

    These parameters affect canopy expansion, maximum LAI, stress sensitivity,
    and harvest index. They do not control phenology timing; phenology timing is
    handled by the thermal-time phenology engine.

    high_density_tolerance:
        Reduces penalty when planting density is high.

    stress_sensitivity:
        Controls how strongly water/nutrient/biotic stress reduces daily biomass.
    """
    max_lai: float
    canopy_growth_rate: float
    rue_g_mj_apar: float
    harvest_index: float
    stress_sensitivity: float
    density_opt_plants_m2: float
    high_density_tolerance: float


DEFAULT_SEED_GROWTH_PARAMS: dict[SeedType, SeedGrowthParameters] = {
    SeedType.EARLY_COLD: SeedGrowthParameters(
        max_lai=4.6,
        canopy_growth_rate=0.055,
        rue_g_mj_apar=1.15,
        harvest_index=0.42,
        stress_sensitivity=0.90,
        density_opt_plants_m2=35.0,
        high_density_tolerance=0.85,
    ),
    SeedType.STANDARD: SeedGrowthParameters(
        max_lai=5.0,
        canopy_growth_rate=0.060,
        rue_g_mj_apar=1.25,
        harvest_index=0.45,
        stress_sensitivity=1.00,
        density_opt_plants_m2=40.0,
        high_density_tolerance=1.00,
    ),
    SeedType.HIGH_DENSITY: SeedGrowthParameters(
        max_lai=5.4,
        canopy_growth_rate=0.065,
        rue_g_mj_apar=1.25,
        harvest_index=0.45,
        stress_sensitivity=1.05,
        density_opt_plants_m2=50.0,
        high_density_tolerance=0.60,
    ),
    SeedType.STRESS_TOLERANT: SeedGrowthParameters(
        max_lai=4.9,
        canopy_growth_rate=0.058,
        rue_g_mj_apar=1.20,
        harvest_index=0.44,
        stress_sensitivity=0.70,
        density_opt_plants_m2=38.0,
        high_density_tolerance=0.90,
    ),
}


@dataclass
class CanopyBiomassParameters:
    """
    Parameters for the Farm-ARE reduced canopy/biomass model.

    Scientific basis:
        The model follows the radiation-use-efficiency (RUE) / light-use-efficiency
        framework introduced by Monteith and used broadly in crop modeling:
            daily biomass increment = intercepted PAR * RUE * stress modifiers.
        Intercepted radiation is computed from LAI using a Beer-Lambert canopy
        interception curve:
            fIPAR = 1 - exp(-k * LAI)

    Soybean-specific simplification:
        Soybean studies report RUE values that vary by cultivar, population,
        stage, and environment. This module uses a scenario-level RUE value of
        roughly 1.15-1.25 g dry matter per MJ intercepted PAR, consistent with
        reported soybean ranges used for simplified crop modeling.

    Engineering simplification:
        This module is not CROPGRO-Soybean or APSIM-Soybean. It does not model
        organ-level carbon allocation, nitrogen fixation, pod number, or seed
        composition. It tracks LAI, canopy cover, aboveground biomass, and a
        yield-potential proxy.
    """

    # Radiation terms.
    par_fraction_of_solar: float = 0.48
    light_extinction_coeff: float = 0.60

    # LAI dynamics.
    initial_lai_at_emergence: float = 0.05
    senescence_rate_default: float = 0.035
    senescence_rate_after_r7: float = 0.075
    lai_stress_loss_rate: float = 0.025

    # Biomass and yield.
    initial_biomass_g_m2_at_emergence: float = 2.0
    max_daily_biomass_g_m2: float = 35.0

    # Stress multipliers.
    min_daily_growth_multiplier: float = 0.0
    nutrient_stress_default: float = 1.0
    biotic_stress_default: float = 1.0

    # Density response.
    # Population values are plants/m2; 30-50 plants/m2 corresponds to about
    # 300k-500k plants/ha.
    density_low_penalty_strength: float = 0.60
    density_high_penalty_strength: float = 0.35

    # Observation bridge.
    # NDVI is approximated from LAI and stress. This is an observation proxy,
    # not a radiative transfer model.
    ndvi_soil_background: float = 0.18
    ndvi_max: float = 0.88
    ndvi_lai_saturation_coeff: float = 0.65
    ndvi_stress_penalty: float = 0.10


@dataclass
class GrowthWeatherInput:
    day: date
    solar_rad_mj_m2: float
    air_temp_mean_c: float


@dataclass
class GrowthSoilInput:
    """
    Soil/crop-water input from the soil engine.

    water_stress:
        0-1 multiplier where 1 is no water limitation and 0 is severe stress.

    root_vwc:
        Optional diagnostic, not required by the core biomass calculation.
    """
    water_stress: float = 1.0
    root_vwc: float | None = None


@dataclass
class PhenologyInput:
    """
    Stage/progress input from the phenology engine.

    stage:
        Current soybean stage.

    development_fraction:
        Effective development progress from 0 to 1, typically
        effective_development_gdd / gdd_to_r8. If not available, this module
        can still update using stage-only rules, but the LAI curve is cleaner
        with a continuous progress variable.
    """
    stage: GrowthStage
    development_fraction: float


@dataclass
class ManagementStressInput:
    """
    Stress multipliers from management and biotic modules.

    All values are in [0, 1]. They are multiplicative terms reducing daily
    biomass accumulation.

    nutrient_stress:
        1 = no nutrient limitation, lower values reduce growth.

    biotic_stress:
        1 = no pest/disease/weed damage, lower values reduce growth.

    stand_fraction:
        Fraction of intended stand successfully established. This captures
        emergence gaps and bad planting outcomes. It affects canopy expansion
        and final biomass capacity.

    planting_density_plants_m2:
        Actual established or intended planting density in plants/m2.
    """
    nutrient_stress: float = 1.0
    biotic_stress: float = 1.0
    stand_fraction: float = 1.0
    planting_density_plants_m2: float = 40.0


@dataclass
class CanopyBiomassState:
    ridge_id: int
    initialized: bool = False
    seed_type: SeedType | None = None
    lai: float = 0.0
    canopy_cover: float = 0.0
    aboveground_biomass_g_m2: float = 0.0
    yield_potential_g_m2: float = 0.0
    ndvi_proxy: float = 0.18
    cumulative_apar_mj_m2: float = 0.0
    cumulative_stress_days: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass
class CanopyBiomassDayResult:
    day: date
    ridge_id: int
    stage: GrowthStage
    lai: float
    canopy_cover: float
    fipar: float
    apar_mj_m2: float
    daily_biomass_g_m2: float
    aboveground_biomass_g_m2: float
    yield_potential_g_m2: float
    ndvi_proxy: float
    total_stress_multiplier: float
    water_stress: float
    nutrient_stress: float
    biotic_stress: float
    density_multiplier: float
    tags: list[str]


class CanopyBiomassGrowthEngine:
    """
    Reduced soybean canopy and biomass growth engine for Farm-ARE.

    Purpose:
        Convert daily radiation, phenology stage, soil water stress, and
        management/biotic stress into LAI, canopy cover, biomass, and NDVI-like
        state variables.

    Scope:
        This is a simple RUE-based growth module for scenario simulation and
        agent evaluation. It provides directional realism and traceable state
        transitions rather than site-calibrated soybean physiology.

    Non-scope:
        No explicit photosynthesis, respiration, nitrogen fixation, root growth,
        organ partitioning, pod number, seed size, or final grain moisture.
    """

    def __init__(
        self,
        num_ridges: int = 64,
        params: CanopyBiomassParameters | None = None,
        seed_params: Mapping[SeedType, SeedGrowthParameters] | None = None,
    ) -> None:
        self.params = params or CanopyBiomassParameters()
        self.seed_params = dict(seed_params or DEFAULT_SEED_GROWTH_PARAMS)
        self.states: dict[int, CanopyBiomassState] = {
            ridge_id: CanopyBiomassState(ridge_id=ridge_id)
            for ridge_id in range(num_ridges)
        }

    def initialize_ridges(
        self,
        ridge_ids: list[int],
        seed_type: SeedType,
        initial_stand_fraction: float = 1.0,
    ) -> None:
        """
        Initialize growth state after emergence.

        This should usually be called when the phenology engine reaches VE.
        It may also be called directly during scenario initialization.
        """
        stand_fraction = self._clip(initial_stand_fraction, 0.0, 1.0)

        for ridge_id in ridge_ids:
            state = self.states[ridge_id]
            state.initialized = True
            state.seed_type = seed_type
            state.lai = self.params.initial_lai_at_emergence * stand_fraction
            state.canopy_cover = self._canopy_cover_from_lai(state.lai)
            state.aboveground_biomass_g_m2 = self.params.initial_biomass_g_m2_at_emergence * stand_fraction
            state.yield_potential_g_m2 = 0.0
            state.ndvi_proxy = self._ndvi_from_lai(state.lai, stress_multiplier=1.0)
            state.cumulative_apar_mj_m2 = 0.0
            state.cumulative_stress_days = 0.0
            state.tags = ["growth_initialized"]

    def update_day(
        self,
        weather: GrowthWeatherInput,
        phenology_by_ridge: Mapping[int, PhenologyInput],
        soil_by_ridge: Mapping[int, GrowthSoilInput] | None = None,
        management_by_ridge: Mapping[int, ManagementStressInput] | None = None,
    ) -> list[CanopyBiomassDayResult]:
        """
        Advance canopy/biomass state by one day.

        Ridges not initialized or not emerged do not accumulate biomass.
        """
        soil_by_ridge = soil_by_ridge or {}
        management_by_ridge = management_by_ridge or {}

        results: list[CanopyBiomassDayResult] = []
        for ridge_id, state in self.states.items():
            phen = phenology_by_ridge.get(
                ridge_id,
                PhenologyInput(stage=GrowthStage.NOT_PLANTED, development_fraction=0.0),
            )
            soil = soil_by_ridge.get(ridge_id, GrowthSoilInput())
            mgmt = management_by_ridge.get(ridge_id, ManagementStressInput())
            results.append(self._update_ridge_day(state, weather, phen, soil, mgmt))

        return results

    def get_state(self) -> dict[int, CanopyBiomassState]:
        return {
            ridge_id: CanopyBiomassState(**vars(state))
            for ridge_id, state in self.states.items()
        }

    def _update_ridge_day(
        self,
        state: CanopyBiomassState,
        weather: GrowthWeatherInput,
        phen: PhenologyInput,
        soil: GrowthSoilInput,
        mgmt: ManagementStressInput,
    ) -> CanopyBiomassDayResult:
        tags: list[str] = []

        if not state.initialized or phen.stage in {GrowthStage.NOT_PLANTED, GrowthStage.PLANTED_PRE_EMERGENCE}:
            return self._result(
                weather.day, state, phen.stage, 0.0, 0.0, 0.0, 1.0, soil, mgmt, 1.0,
                ["not_emerged_or_not_initialized"],
            )

        if state.seed_type is None:
            raise ValueError("Initialized growth state is missing seed_type")

        sp = self.seed_params[state.seed_type]
        p = self.params

        water_stress = self._clip(soil.water_stress, 0.0, 1.0)
        nutrient_stress = self._clip(mgmt.nutrient_stress, 0.0, 1.0)
        biotic_stress = self._clip(mgmt.biotic_stress, 0.0, 1.0)
        stand_fraction = self._clip(mgmt.stand_fraction, 0.0, 1.0)
        density_multiplier = self._density_multiplier(mgmt.planting_density_plants_m2, sp)

        # Stress multiplier. Seed-type stress sensitivity controls how strongly
        # non-ideal conditions reduce biomass growth.
        raw_stress = water_stress * nutrient_stress * biotic_stress * density_multiplier
        total_stress = self._clip(raw_stress ** sp.stress_sensitivity, p.min_daily_growth_multiplier, 1.0)

        # Update LAI before computing interception. During early/mid growth, LAI
        # follows a logistic-like approach toward max_lai. During late maturity,
        # senescence reduces LAI.
        self._update_lai(state, phen, sp, total_stress, stand_fraction)

        fipar = self._fipar_from_lai(state.lai)
        par = max(0.0, weather.solar_rad_mj_m2) * p.par_fraction_of_solar
        apar = par * fipar

        stage_growth_multiplier = self._stage_growth_multiplier(phen.stage)
        daily_biomass = apar * sp.rue_g_mj_apar * total_stress * stage_growth_multiplier
        daily_biomass = min(daily_biomass, p.max_daily_biomass_g_m2)

        # After R7, biomass accumulation is mostly stopped in this reduced model.
        if phen.stage in {GrowthStage.R7, GrowthStage.R8}:
            daily_biomass *= 0.20

        state.aboveground_biomass_g_m2 += daily_biomass
        state.cumulative_apar_mj_m2 += apar
        state.canopy_cover = self._canopy_cover_from_lai(state.lai)
        state.ndvi_proxy = self._ndvi_from_lai(state.lai, total_stress)

        if total_stress < 0.85:
            state.cumulative_stress_days += 1.0
            tags.append("growth_stress")
        if water_stress < 0.85:
            tags.append("water_limited_growth")
        if nutrient_stress < 0.85:
            tags.append("nutrient_limited_growth")
        if biotic_stress < 0.85:
            tags.append("biotic_limited_growth")

        # Yield potential is not final harvested yield. It is a running proxy
        # based on biomass and harvest-index potential, updated mainly after R5.
        state.yield_potential_g_m2 = self._yield_potential(state, phen, sp)

        state.tags = tags
        return self._result(
            weather.day,
            state,
            phen.stage,
            fipar,
            apar,
            daily_biomass,
            total_stress,
            soil,
            mgmt,
            density_multiplier,
            tags,
        )

    def _update_lai(
        self,
        state: CanopyBiomassState,
        phen: PhenologyInput,
        sp: SeedGrowthParameters,
        total_stress: float,
        stand_fraction: float,
    ) -> None:
        p = self.params

        max_lai = sp.max_lai * max(0.05, stand_fraction)

        if phen.stage in {
            GrowthStage.VE,
            GrowthStage.VC,
            GrowthStage.V1,
            GrowthStage.V2,
            GrowthStage.V3,
            GrowthStage.V4_PLUS,
            GrowthStage.R1,
            GrowthStage.R3,
            GrowthStage.R5,
        }:
            # Logistic-like expansion toward max LAI.
            growth_rate = sp.canopy_growth_rate * (0.35 + 0.65 * total_stress)
            delta_lai = growth_rate * state.lai * max(0.0, 1.0 - state.lai / max_lai)
            if state.lai < 0.10:
                delta_lai = max(delta_lai, 0.015 * total_stress)
            state.lai += delta_lai

        if phen.stage == GrowthStage.R6:
            # Near full seed, maintain canopy but allow stress-induced loss.
            state.lai -= p.lai_stress_loss_rate * (1.0 - total_stress) * state.lai

        if phen.stage == GrowthStage.R7:
            state.lai -= p.senescence_rate_default * state.lai
            state.lai -= p.lai_stress_loss_rate * (1.0 - total_stress) * state.lai

        if phen.stage == GrowthStage.R8:
            state.lai -= p.senescence_rate_after_r7 * state.lai

        state.lai = self._clip(state.lai, 0.0, max_lai)

    def _yield_potential(
        self,
        state: CanopyBiomassState,
        phen: PhenologyInput,
        sp: SeedGrowthParameters,
    ) -> float:
        """
        Running yield-potential proxy in g/m2.

        Grain yield is not finalized here. This proxy starts contributing after
        beginning seed (R5) and increases as biomass accumulates through seed fill.
        A later harvest module should convert this to recovered yield using grain
        moisture, shattering, and harvest losses.
        """
        if phen.stage in {
            GrowthStage.NOT_PLANTED,
            GrowthStage.PLANTED_PRE_EMERGENCE,
            GrowthStage.VE,
            GrowthStage.VC,
            GrowthStage.V1,
            GrowthStage.V2,
            GrowthStage.V3,
            GrowthStage.V4_PLUS,
            GrowthStage.R1,
            GrowthStage.R3,
        }:
            return 0.0

        # Seed-fill fraction from R5 to R8.
        if phen.stage == GrowthStage.R5:
            fill_fraction = 0.35
        elif phen.stage == GrowthStage.R6:
            fill_fraction = 0.70
        elif phen.stage == GrowthStage.R7:
            fill_fraction = 0.90
        else:
            fill_fraction = 1.00

        return state.aboveground_biomass_g_m2 * sp.harvest_index * fill_fraction

    def _density_multiplier(self, density_plants_m2: float, sp: SeedGrowthParameters) -> float:
        """
        Reduced planting-density response.

        Low density reduces canopy closure and biomass capture. Excessively high
        density causes a smaller penalty, reduced for HIGH_DENSITY-like types.
        """
        p = self.params
        density = max(0.0, density_plants_m2)
        opt = max(1e-6, sp.density_opt_plants_m2)

        if density < opt:
            deficit = (opt - density) / opt
            return self._clip(1.0 - p.density_low_penalty_strength * deficit, 0.2, 1.0)

        excess = (density - opt) / opt
        penalty = p.density_high_penalty_strength * sp.high_density_tolerance * excess
        return self._clip(1.0 - penalty, 0.65, 1.0)

    def _stage_growth_multiplier(self, stage: GrowthStage) -> float:
        """
        Stage-specific biomass accumulation multiplier.

        Early emergence has low biomass growth; vegetative and early reproductive
        stages carry most canopy/biomass accumulation; late maturity slows.
        """
        if stage in {GrowthStage.VE, GrowthStage.VC}:
            return 0.45
        if stage in {GrowthStage.V1, GrowthStage.V2, GrowthStage.V3}:
            return 0.75
        if stage in {GrowthStage.V4_PLUS, GrowthStage.R1, GrowthStage.R3, GrowthStage.R5}:
            return 1.00
        if stage == GrowthStage.R6:
            return 0.75
        if stage == GrowthStage.R7:
            return 0.35
        if stage == GrowthStage.R8:
            return 0.10
        return 0.0

    def _fipar_from_lai(self, lai: float) -> float:
        return 1.0 - exp(-self.params.light_extinction_coeff * max(0.0, lai))

    def _canopy_cover_from_lai(self, lai: float) -> float:
        # Canopy cover proxy using the same saturating form as light interception.
        return self._clip(1.0 - exp(-0.75 * max(0.0, lai)), 0.0, 1.0)

    def _ndvi_from_lai(self, lai: float, stress_multiplier: float) -> float:
        p = self.params
        ndvi = p.ndvi_soil_background + (p.ndvi_max - p.ndvi_soil_background) * (
            1.0 - exp(-p.ndvi_lai_saturation_coeff * max(0.0, lai))
        )
        ndvi -= p.ndvi_stress_penalty * (1.0 - self._clip(stress_multiplier, 0.0, 1.0))
        return self._clip(ndvi, p.ndvi_soil_background, p.ndvi_max)

    def _result(
        self,
        day: date,
        state: CanopyBiomassState,
        stage: GrowthStage,
        fipar: float,
        apar: float,
        daily_biomass: float,
        total_stress: float,
        soil: GrowthSoilInput,
        mgmt: ManagementStressInput,
        density_multiplier: float,
        tags: list[str],
    ) -> CanopyBiomassDayResult:
        return CanopyBiomassDayResult(
            day=day,
            ridge_id=state.ridge_id,
            stage=stage,
            lai=round(state.lai, 3),
            canopy_cover=round(state.canopy_cover, 3),
            fipar=round(fipar, 3),
            apar_mj_m2=round(apar, 3),
            daily_biomass_g_m2=round(daily_biomass, 3),
            aboveground_biomass_g_m2=round(state.aboveground_biomass_g_m2, 3),
            yield_potential_g_m2=round(state.yield_potential_g_m2, 3),
            ndvi_proxy=round(state.ndvi_proxy, 3),
            total_stress_multiplier=round(total_stress, 3),
            water_stress=round(self._clip(soil.water_stress, 0.0, 1.0), 3),
            nutrient_stress=round(self._clip(mgmt.nutrient_stress, 0.0, 1.0), 3),
            biotic_stress=round(self._clip(mgmt.biotic_stress, 0.0, 1.0), 3),
            density_multiplier=round(density_multiplier, 3),
            tags=list(tags),
        )

    @staticmethod
    def _clip(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))


if __name__ == "__main__":
    from datetime import timedelta

    engine = CanopyBiomassGrowthEngine(num_ridges=1)
    engine.initialize_ridges([0], seed_type=SeedType.STANDARD, initial_stand_fraction=0.95)

    start = date(2026, 5, 25)
    stages = [
        GrowthStage.VE, GrowthStage.VC, GrowthStage.V1, GrowthStage.V2, GrowthStage.V3,
        GrowthStage.V4_PLUS, GrowthStage.R1, GrowthStage.R3, GrowthStage.R5,
        GrowthStage.R6, GrowthStage.R7, GrowthStage.R8,
    ]

    day = start
    for i in range(100):
        # Simple demonstration stage schedule.
        stage = stages[min(len(stages) - 1, i // 8)]
        phen = {0: PhenologyInput(stage=stage, development_fraction=min(1.0, i / 100.0))}
        soil = {0: GrowthSoilInput(water_stress=0.75 if 50 <= i <= 60 else 1.0)}
        mgmt = {0: ManagementStressInput(nutrient_stress=1.0, biotic_stress=1.0, stand_fraction=0.95)}
        weather = GrowthWeatherInput(day=day, solar_rad_mj_m2=18.0, air_temp_mean_c=22.0)

        result = engine.update_day(weather, phen, soil, mgmt)[0]
        if i % 10 == 0 or result.tags:
            print(result)

        day += timedelta(days=1)
