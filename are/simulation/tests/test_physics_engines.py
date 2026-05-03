"""
Determinism / sanity tests for the seven physics engines in
`are.simulation.physics`. These are scenario-free tests: each engine is
constructed in isolation, fed fixed weather and ridge inputs, and checked
against expected directional behaviour (engine-as-blackbox).

The tests cover:
  - WeatherGenerator: same seed -> same trajectory, monthly climate scales.
  - SoilEngine: rain raises VWC, ET dries, water_stress in [0, 1].
  - ThermalTimePhenologyEngine: planted ridge accumulates GDD, eventually
    emerges, eventually reaches R8.
  - CanopyBiomassGrowthEngine: with adequate weather/stress=1.0, LAI
    monotonically grows post-emergence.
  - BioticPressureEngine: insecticide treatment lowers insect pressure.
  - ManagementEffectEngine: PLANTING action sets stand_fraction; FERTIGATION
    raises nutrient_index; INSECTICIDE opens residual window.
  - YieldRecoveryEngine: harvest with R8 + grain moisture in window
    produces non-zero recovered yield.

All tests use seed=42 for stability and only assert qualitative directional
behaviour, not exact values (engine parameters may evolve).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from are.simulation.physics import (
    BioticCropInput,
    BioticPressureEngine,
    BioticSoilInput,
    BioticWeatherInput,
    CanopyBiomassGrowthEngine,
    CanopyPhenologyInput,
    GrowthSoilInput,
    GrowthWeatherInput,
    HarvestAction,
    ManagementAction,
    ManagementActionType,
    ManagementEffectEngine,
    ManagementSoilInput,
    ManagementWeatherInput,
    MonthlyClimate,
    PhenologySoilInput,
    PhenologyWeatherInput,
    PlantingConfig,
    SeedType,
    SoilEngine,
    SoilWeatherInput,
    SoybeanStage,
    ThermalTimePhenologyEngine,
    TreatmentApplication,
    TreatmentType,
    WeatherGenerator,
    WeatherGeneratorConfig,
    YieldGrowthInput,
    YieldPhenologyInput,
    YieldRecoveryEngine,
    YieldStressInput,
    YieldWeatherInput,
)
from are.simulation.physics.biotic_pressure_engine import GrowthStage as BioticStage
from are.simulation.physics.canopy_biomass_engine import (
    GrowthStage as CanopyStage,
    SeedType as CanopySeedType,
)
from are.simulation.physics.management_effect_engine import (
    GrowthStage as ManagementStage,
)
from are.simulation.physics.yield_recovery_engine import (
    GrowthStage as YieldStage,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _build_test_weather_config() -> WeatherGeneratorConfig:
    """A sane scenario-style climate for Harbin-ish summer."""
    monthly = {
        m: MonthlyClimate(
            temp_mean_c=22.0,
            precip_mm=8.0,
            wet_day_prob=0.20,
            solar_rad_mj_m2=22.0,
        )
        for m in range(1, 13)
    }
    return WeatherGeneratorConfig(monthly=monthly)


def _soil_weather(day: date, rain_mm: float = 0.0) -> SoilWeatherInput:
    return SoilWeatherInput(
        day=day,
        air_temp_mean_c=24.0,
        air_temp_min_c=18.0,
        air_temp_max_c=30.0,
        rain_mm=rain_mm,
        solar_rad_mj_m2=22.0,
        wind_ms=2.0,
    )


# ---------------------------------------------------------------------------
# Weather generator
# ---------------------------------------------------------------------------


def test_weather_generator_seeded_determinism():
    config = _build_test_weather_config()
    gen_a = WeatherGenerator(config=config, seed=42)
    gen_b = WeatherGenerator(config=config, seed=42)
    days_a = gen_a.generate(date(2026, 5, 1), date(2026, 5, 30))
    days_b = gen_b.generate(date(2026, 5, 1), date(2026, 5, 30))
    assert len(days_a) == len(days_b) == 30
    for a, b in zip(days_a, days_b):
        assert a.air_temp_mean_c == pytest.approx(b.air_temp_mean_c)
        assert a.rain_mm == pytest.approx(b.rain_mm)


def test_weather_generator_different_seeds_diverge():
    config = _build_test_weather_config()
    gen_a = WeatherGenerator(config=config, seed=1).generate(date(2026, 5, 1), date(2026, 5, 30))
    gen_b = WeatherGenerator(config=config, seed=2).generate(date(2026, 5, 1), date(2026, 5, 30))
    diff_temps = sum(1 for a, b in zip(gen_a, gen_b) if a.air_temp_mean_c != b.air_temp_mean_c)
    assert diff_temps > 5, "different seeds should produce different daily temperatures"


# ---------------------------------------------------------------------------
# Soil engine
# ---------------------------------------------------------------------------


def test_soil_rain_raises_vwc():
    engine = SoilEngine(num_ridges=4, initial_top_vwc=0.20, initial_root_vwc=0.20)
    engine.update_day(_soil_weather(date(2026, 5, 1), rain_mm=0.0))
    initial = engine.states[0].top_vwc
    engine.update_day(_soil_weather(date(2026, 5, 2), rain_mm=15.0))
    after_rain = engine.states[0].top_vwc
    assert after_rain >= initial, "rain should not lower VWC"


def test_soil_irrigation_input_lifts_top_layer():
    engine = SoilEngine(num_ridges=4, initial_top_vwc=0.15, initial_root_vwc=0.15)
    engine.update_day(_soil_weather(date(2026, 5, 1)), irrigation_mm_by_ridge={2: 10.0})
    # Irrigation goes to the irrigated ridge only.
    assert engine.states[2].top_vwc > engine.states[0].top_vwc


def test_soil_dry_warm_day_dries_top_layer():
    engine = SoilEngine(num_ridges=2, initial_top_vwc=0.30, initial_root_vwc=0.30)
    canopy_cover = {0: 0.0, 1: 0.0}  # bare soil ET dominant
    for d in range(10):
        engine.update_day(
            _soil_weather(date(2026, 6, 1) + timedelta(days=d), rain_mm=0.0),
            canopy_cover_by_ridge=canopy_cover,
        )
    assert engine.states[0].top_vwc < 0.30


# ---------------------------------------------------------------------------
# Phenology engine
# ---------------------------------------------------------------------------


def test_phenology_planting_sets_pre_emergence():
    engine = ThermalTimePhenologyEngine(num_ridges=4)
    engine.plant_ridges(
        [0, 1],
        PlantingConfig(
            planting_date=date(2026, 5, 1),
            seed_type=SeedType.STANDARD,
        ),
    )
    assert engine.states[0].stage == SoybeanStage.PLANTED_PRE_EMERGENCE
    assert engine.states[0].planted is True
    assert engine.states[2].stage == SoybeanStage.NOT_PLANTED


def test_phenology_accumulates_gdd_and_emerges():
    engine = ThermalTimePhenologyEngine(num_ridges=2)
    engine.plant_ridges(
        [0],
        PlantingConfig(
            planting_date=date(2026, 5, 1),
            seed_type=SeedType.STANDARD,
        ),
    )
    soil = {0: PhenologySoilInput(top_temp_c=22.0, top_vwc=0.25)}
    for d in range(20):
        engine.update_day(
            PhenologyWeatherInput(
                day=date(2026, 5, 1) + timedelta(days=d),
                air_temp_min_c=18.0,
                air_temp_max_c=30.0,
                air_temp_mean_c=24.0,
            ),
            soil_by_ridge=soil,
        )
    state = engine.states[0]
    assert state.accumulated_gdd > 0.0
    assert state.emerged is True
    assert state.stage in {
        SoybeanStage.VE,
        SoybeanStage.VC,
        SoybeanStage.V1,
        SoybeanStage.V2,
        SoybeanStage.V3,
        SoybeanStage.V4_PLUS,
    }


# ---------------------------------------------------------------------------
# Canopy / biomass engine
# ---------------------------------------------------------------------------


def test_canopy_lai_grows_post_emergence():
    engine = CanopyBiomassGrowthEngine(num_ridges=2)
    engine.initialize_ridges([0], seed_type=CanopySeedType.STANDARD, initial_stand_fraction=1.0)
    initial_lai = engine.states[0].lai
    for d in range(20):
        engine.update_day(
            weather=GrowthWeatherInput(
                day=date(2026, 6, 1) + timedelta(days=d),
                solar_rad_mj_m2=22.0,
                air_temp_mean_c=24.0,
            ),
            phenology_by_ridge={
                0: CanopyPhenologyInput(stage=CanopyStage.V3, development_fraction=0.30),
            },
            soil_by_ridge={0: GrowthSoilInput(water_stress=1.0, root_vwc=0.25)},
        )
    final_lai = engine.states[0].lai
    assert final_lai > initial_lai


# ---------------------------------------------------------------------------
# Biotic pressure engine
# ---------------------------------------------------------------------------


def test_biotic_insecticide_treatment_reduces_insect_pressure():
    engine = BioticPressureEngine(num_ridges=2)
    engine.set_pressure([0], insect_pressure=0.6)
    crop = {0: BioticCropInput(stage=BioticStage.V4_PLUS, canopy_cover=0.6)}
    weather = BioticWeatherInput(day=date(2026, 7, 1), air_temp_mean_c=24.0, rain_mm=0.0)
    pre = engine.states[0].insect_pressure
    engine.update_day(
        weather=weather,
        crop_by_ridge=crop,
        treatments_by_ridge={
            0: [TreatmentApplication(treatment_type=TreatmentType.INSECTICIDE, efficacy_multiplier=1.0)]
        },
    )
    post = engine.states[0].insect_pressure
    assert post < pre, f"treatment should reduce pressure; pre={pre}, post={post}"
    assert engine.states[0].insecticide_residual_days_left > 0


# ---------------------------------------------------------------------------
# Management effect engine
# ---------------------------------------------------------------------------


def test_management_planting_action_sets_stand_fraction():
    engine = ManagementEffectEngine(num_ridges=2)
    engine.update_day(
        weather=ManagementWeatherInput(day=date(2026, 5, 1)),
        actions_by_ridge={
            0: [
                ManagementAction(
                    action_type=ManagementActionType.PLANTING,
                    amount=1.0,
                    quality=1.0,
                    metadata={"seed_depth_cm": 4.0},
                )
            ]
        },
        soil_by_ridge={
            0: ManagementSoilInput(top_vwc=0.25, root_vwc=0.25, planting_ready=True),
        },
    )
    state = engine.states[0]
    assert state.planted is True
    assert state.stand_fraction > 0.0


def test_management_fertigation_raises_nutrient_index():
    engine = ManagementEffectEngine(num_ridges=2)
    pre = engine.states[0].nutrient_index
    engine.update_day(
        weather=ManagementWeatherInput(day=date(2026, 6, 1)),
        actions_by_ridge={
            0: [
                ManagementAction(
                    action_type=ManagementActionType.FERTIGATION,
                    amount=1.0,
                    quality=1.0,
                )
            ]
        },
    )
    post = engine.states[0].nutrient_index
    assert post > pre


def test_management_insecticide_opens_residual_window():
    engine = ManagementEffectEngine(num_ridges=2)
    engine.update_day(
        weather=ManagementWeatherInput(day=date(2026, 7, 1)),
        actions_by_ridge={
            0: [
                ManagementAction(
                    action_type=ManagementActionType.INSECTICIDE,
                    amount=1.0,
                    quality=1.0,
                )
            ]
        },
    )
    assert engine.states[0].insecticide_residual_days_left > 0


# ---------------------------------------------------------------------------
# Yield recovery engine
# ---------------------------------------------------------------------------


def test_yield_recovery_at_r8_with_good_moisture():
    engine = YieldRecoveryEngine(num_ridges=2)
    state = engine.states[0]
    state.r8_reached = True
    state.grain_moisture_frac = 0.15
    state.biological_yield_g_m2 = 350.0

    weather = YieldWeatherInput(
        day=date(2026, 9, 25),
        air_temp_mean_c=18.0,
        rain_mm=0.0,
        solar_rad_mj_m2=15.0,
        wind_ms=2.0,
    )
    results = engine.update_day(
        weather=weather,
        phenology_by_ridge={
            0: YieldPhenologyInput(stage=YieldStage.R8, maturity_date=date(2026, 9, 20)),
        },
        growth_by_ridge={
            0: YieldGrowthInput(yield_potential_g_m2=350.0, aboveground_biomass_g_m2=700.0),
        },
        stress_by_ridge={0: YieldStressInput()},
        harvest_actions_by_ridge={
            0: HarvestAction(machine_quality=0.95, pass_completed=True),
        },
    )
    r0 = next(r for r in results if r.ridge_id == 0)
    assert r0.harvested is True
    assert r0.recovered_yield_g_m2_at_market_moisture > 0.0
    assert r0.machine_loss_fraction > 0.0
