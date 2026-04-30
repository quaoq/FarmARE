# Canopy and Biomass Growth Engine

This page documents the reduced soybean canopy and biomass growth engine used by Farm-ARE. The module converts weather, phenology, soil water stress, planting density, and management/biotic stress into ridge-level canopy and biomass states.

The engine does not model full crop physiology. It implements a traceable radiation-use-efficiency model suitable for closed-loop agent scenarios.

## Role in Farm-ARE

The canopy/biomass engine sits after weather, soil, and phenology.

```text
weather[t] + phenology[t] + soil_stress[t] + management_stress[t]
        -> canopy_biomass_engine.update_day(...)
        -> LAI, canopy cover, biomass, NDVI proxy, yield-potential proxy
```

The outputs are consumed by:

- drone/satellite observation models
- pest/disease/weed models
- irrigation decision logic
- nutrient correction logic
- harvest-readiness and yield modules
- final scoring against oracle trajectories

## Modeling Basis

The implementation follows a reduced radiation-use-efficiency (RUE) or light-use-efficiency (LUE) model.

The core idea is:

```text
daily biomass increment = intercepted PAR × RUE × stress multipliers
```

This follows the Monteith-style crop growth framework widely used in crop modeling. Radiation-use efficiency is defined as biomass accumulated per unit absorbed/intercepted radiation.

The model computes intercepted radiation using a Beer-Lambert canopy interception function:

```text
fIPAR = 1 - exp(-k × LAI)
APAR = solar_radiation × PAR_fraction × fIPAR
```

Then daily biomass is:

```text
daily_biomass = APAR × RUE × stress × stage_multiplier
```

## Scientific References and Simplifications

Soybean biomass accumulation is often modeled through radiation interception and radiation-use efficiency. Soybean studies report that light interception and RUE are key drivers of biomass production, and RUE varies with cultivar, planting density, environment, and growth stage.

This module uses the RUE structure but simplifies the full plant-growth problem:

- no explicit photosynthesis model
- no respiration model
- no nitrogen fixation model
- no root biomass model
- no organ-level partitioning
- no pod-number or seed-size model
- no full CROPGRO/APSIM soybean implementation

Instead, it tracks:

- leaf area index (LAI)
- canopy cover
- fraction of intercepted PAR
- aboveground biomass
- NDVI-like proxy
- yield-potential proxy

This is sufficient for Farm-ARE because the goal is not to predict field yield from first principles, but to make agent decisions affect crop state over time.

## State Representation

Each ridge has a `CanopyBiomassState`.

```python
CanopyBiomassState(
    ridge_id: int,
    initialized: bool,
    seed_type: SeedType | None,
    lai: float,
    canopy_cover: float,
    aboveground_biomass_g_m2: float,
    yield_potential_g_m2: float,
    ndvi_proxy: float,
    cumulative_apar_mj_m2: float,
    cumulative_stress_days: float,
    tags: list[str],
)
```

State variables:

| Variable | Meaning |
|---|---|
| `lai` | Leaf area index |
| `canopy_cover` | Saturating canopy-cover proxy derived from LAI |
| `aboveground_biomass_g_m2` | Aboveground dry biomass per square meter |
| `yield_potential_g_m2` | Running grain-yield potential proxy |
| `ndvi_proxy` | Simulated NDVI-like observation derived from LAI and stress |
| `cumulative_apar_mj_m2` | Accumulated absorbed/intercepted PAR |
| `cumulative_stress_days` | Count of days with strong growth limitation |

## Inputs

### Weather Input

```python
GrowthWeatherInput(
    day: date,
    solar_rad_mj_m2: float,
    air_temp_mean_c: float,
)
```

The model currently uses solar radiation directly for RUE-based growth. Temperature effects enter mainly through the phenology engine and can later be added as an explicit heat/cold stress multiplier.

### Phenology Input

```python
PhenologyInput(
    stage: GrowthStage,
    development_fraction: float,
)
```

The phenology stage determines whether the crop has emerged and which stage-specific growth multiplier applies.

### Soil Input

```python
GrowthSoilInput(
    water_stress: float,
    root_vwc: float | None = None,
)
```

`water_stress` is a 0-1 multiplier from the soil engine.

### Management Stress Input

```python
ManagementStressInput(
    nutrient_stress: float = 1.0,
    biotic_stress: float = 1.0,
    stand_fraction: float = 1.0,
    planting_density_plants_m2: float = 40.0,
)
```

These values come from management and biotic-pressure modules.

| Input | Meaning |
|---|---|
| `nutrient_stress` | Reduced growth from nutrient limitation |
| `biotic_stress` | Reduced growth from pests, disease, or weeds |
| `stand_fraction` | Fraction of intended stand successfully established |
| `planting_density_plants_m2` | Actual or intended plant density |

## Seed-Type Growth Parameters

Seed type affects growth behavior but not phenology timing in this module.

Default parameters:

| Seed type | Max LAI | RUE g/MJ APAR | Harvest index | Role |
|---|---:|---:|---:|---|
| `EARLY_COLD` | 4.6 | 1.15 | 0.42 | Shorter-season, lower ceiling |
| `STANDARD` | 5.0 | 1.25 | 0.45 | Baseline type |
| `HIGH_DENSITY` | 5.4 | 1.25 | 0.45 | Higher density tolerance |
| `STRESS_TOLERANT` | 4.9 | 1.20 | 0.44 | Lower stress sensitivity |

These are scenario parameters and should be calibrated if cultivar-specific data are available.

## Daily Update Logic

### 1. Check Crop Initialization

The module accumulates biomass only after emergence. A ridge is initialized when the phenology module reaches VE.

```python
engine.initialize_ridges([ridge_id], seed_type=SeedType.STANDARD)
```

Before emergence, the daily result is zero-growth.

### 2. Compute Stress Multiplier

Daily growth is reduced by:

```text
water_stress × nutrient_stress × biotic_stress × density_multiplier
```

Seed type controls how strongly stress affects growth.

### 3. Update LAI

LAI increases during vegetative and early reproductive stages using a logistic-like approach toward seed-type-specific maximum LAI.

During R6-R8, LAI declines through senescence and stress-induced leaf loss.

### 4. Compute Light Interception

The model computes intercepted PAR using:

```text
fIPAR = 1 - exp(-k × LAI)
PAR = solar_radiation × 0.48
APAR = PAR × fIPAR
```

Default parameters:

```python
par_fraction_of_solar = 0.48
light_extinction_coeff = 0.60
```

### 5. Accumulate Biomass

Daily aboveground biomass increment is:

```text
daily_biomass = APAR × RUE × total_stress × stage_multiplier
```

A daily cap is applied to avoid unrealistic jumps.

### 6. Estimate NDVI Proxy

The NDVI proxy is derived from LAI using a saturating response:

```text
NDVI = soil_background + (NDVI_max - soil_background) × (1 - exp(-c × LAI))
```

Stress reduces NDVI slightly.

This output supports drone/satellite observation scenarios. It is not a radiative transfer model.

### 7. Track Yield Potential

The engine computes a running `yield_potential_g_m2` only after R5. It is based on aboveground biomass and seed-type-specific harvest index.

This is not final harvested yield. The harvest module should later convert yield potential into recovered yield using grain moisture, shattering, harvest timing, and machine losses.

## Integration with Other Modules

### Phenology Engine

Phenology provides stage.

```text
phenology[t].stage -> stage-specific growth behavior
```

### Soil Engine

Soil provides water stress.

```text
soil[t].water_stress -> biomass stress multiplier
```

### Biotic Pressure Engine

Pest, disease, and weed modules provide `biotic_stress`.

```text
pest/disease/weed severity -> biotic_stress
```

### Fertilizer / Nutrient Module

Nutrient state provides `nutrient_stress`.

```text
nutrient deficiency -> nutrient_stress
```

### Observation Model

The observation model consumes LAI, canopy cover, and NDVI proxy to generate sensor and drone outputs.

## Scenario Use

Example scenario:

1. Agent plants late using `STANDARD` seed.
2. Emergence occurs under dry topsoil, reducing stand fraction.
3. Growth engine initializes with lower `stand_fraction`.
4. A dry spell reduces root-zone water stress.
5. Agent delays irrigation.
6. Biomass accumulation slows and NDVI proxy drops.
7. Drone survey detects lower NDVI on affected ridges.
8. Agent irrigates.
9. Soil stress improves, but lost biomass is not fully recovered.

This gives the agent a delayed feedback loop: action timing affects canopy and biomass state, which later affects observations and yield potential.

## Limitations

The current canopy/biomass engine does not model:

- full photosynthesis
- respiration
- organ partitioning
- root growth
- nitrogen fixation
- pod number
- seed size
- cultivar-specific allometry
- detailed thermal stress
- final harvested yield
- grain moisture

These omissions are deliberate. The module is a reduced growth engine for closed-loop scenario simulation.
