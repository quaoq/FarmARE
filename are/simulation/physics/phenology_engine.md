# Thermal-Time Phenology Engine

This page documents the reduced soybean phenology engine used by Farm-ARE. The module tracks the crop development stage for each ridge as a function of temperature, seed type, planting depth, soil conditions during emergence, and a reduced photoperiod effect.

The phenology engine does not compute biomass, canopy cover, pest pressure, yield, or harvest recovery. It provides the crop stage state that those downstream modules consume.

## Role in Farm-ARE

The phenology engine converts planting decisions and daily weather into crop-stage progression.

```text
planting event + seed type + seed depth
        + daily temperature
        + seed-zone soil state
        + optional water stress
        -> phenology_engine.update_day(...)
        -> VE / Vn / R1-R8 stage
```

The stage output affects:

- emergence assessment
- timing of weed control
- timing of pest scouting
- sensitivity to water stress
- transition into flowering and pod filling
- harvest-readiness monitoring
- yield-penalty weighting by stage

## Modeling Basis

The implementation is a reduced thermal-time soybean phenology model.

The relevant modeling choices are:

1. Soybean development is represented through standard vegetative and reproductive stages.
2. Daily development is driven by growing degree days (GDD).
3. GDD uses a base temperature of 10°C and an upper cap of 30°C by default.
4. Seed type controls the thermal-time target to reach R8 maturity.
5. Planting depth and seed-zone soil conditions modify the emergence target.
6. Optional photoperiod delay is applied before flowering.
7. Severe water stress can slow stage progression after emergence.

This follows the same broad structure used by soybean crop models: development is temperature-driven, cultivar/seed-type dependent, and sensitive to photoperiod. It is not a full implementation of CROPGRO-Soybean or APSIM-Soybean.

## Scientific References and Simplifications

The stage labels follow the standard soybean growth-stage system developed by Fehr and Caviness and used in extension guides. The system separates vegetative stages (VE, VC, V1, V2, etc.) and reproductive stages (R1-R8). R8 corresponds to full maturity when most pods have reached mature color.

CROPGRO-Soybean represents vegetative and reproductive development as driven by temperature and photoperiod with cultivar coefficients. This implementation keeps that causal structure but collapses it into a daily GDD model with seed-type-specific thermal-time targets.

GDD-based soybean maturity models are used in practice to predict R8 maturity. Reported models commonly use a base temperature around 10°C and upper temperature caps around 30°C. This module uses:

```text
Tbase = 10°C
Tupper = 30°C
```

Daily GDD is computed as:

```text
Tmin_adj = max(Tmin, Tbase)
Tmax_adj = min(max(Tmax, Tbase), Tupper)
GDD = max(0, (Tmin_adj + Tmax_adj) / 2 - Tbase)
```

Photoperiod is simplified. Soybean is a short-day crop, and long days can delay flowering. The module computes astronomical daylength from date and latitude, then applies a small delay before R1 when daylength exceeds a critical value. This is not a cultivar-calibrated photoperiod response.

## Seed-Type Classes

The engine uses four seed-type classes:

```python
SeedType.EARLY_COLD
SeedType.STANDARD
SeedType.HIGH_DENSITY
SeedType.STRESS_TOLERANT
```

These are scenario-level variety classes rather than commercial cultivar names.

Default parameters:

| Seed type | GDD to R8 | Emergence GDD | Role |
|---|---:|---:|---|
| `EARLY_COLD` | 1650 | 85 | Shorter-season type with better cold-start tolerance |
| `STANDARD` | 1850 | 95 | Baseline regional type |
| `HIGH_DENSITY` | 1850 | 95 | Similar phenology to standard; density response belongs mostly in growth/yield |
| `STRESS_TOLERANT` | 1800 | 90 | Slightly shorter duration and less slowed by stress |

The GDD-to-R8 values are simplified maturity targets. They are designed to approximate early-maturity soybean behavior suitable for short growing seasons. They should be calibrated or replaced when cultivar-specific data are available.

## State Representation

Each ridge has a `PhenologyState`.

```python
PhenologyState(
    ridge_id: int,
    planted: bool,
    planting_date: date | None,
    seed_type: SeedType | None,
    seed_depth_cm: float,
    planting_quality: float,
    stage: SoybeanStage,
    days_after_planting: int,
    accumulated_gdd: float,
    effective_development_gdd: float,
    emerged: bool,
    emergence_date: date | None,
    maturity_date: date | None,
    tags: list[str],
)
```

Important state variables:

| Variable | Meaning |
|---|---|
| `accumulated_gdd` | Raw thermal time accumulated from planting |
| `effective_development_gdd` | Thermal time after stress and photoperiod modifiers |
| `stage` | Current soybean stage |
| `emerged` | Whether VE has occurred |
| `emergence_date` | First date when emergence is reached |
| `maturity_date` | First date when R8 is reached |
| `planting_quality` | Execution-quality scalar from planting operation |

## Stage Thresholds

Stage progression is defined as fractions of the seed-type-specific GDD-to-R8 target.

Default thresholds:

| Stage | Fraction of GDD-to-R8 |
|---|---:|
| VE | 0.05 |
| VC | 0.08 |
| V1 | 0.11 |
| V2 | 0.15 |
| V3 | 0.19 |
| V4+ | 0.24 |
| R1 | 0.42 |
| R3 | 0.55 |
| R5 | 0.68 |
| R6 | 0.80 |
| R7 | 0.92 |
| R8 | 1.00 |

These thresholds are scenario parameters, not calibrated cultivar coefficients. They provide a compact way to expose stage-specific behavior to other modules.

## Inputs

### Planting Input

Planting is initialized through:

```python
PlantingConfig(
    planting_date: date,
    seed_type: SeedType,
    seed_depth_cm: float = 4.0,
    planting_quality: float = 1.0,
)
```

Planting does not guarantee emergence. The model accumulates thermal time and checks soil conditions before reaching VE.

### Weather Input

Daily weather input:

```python
PhenologyWeatherInput(
    day: date,
    air_temp_min_c: float,
    air_temp_max_c: float,
    air_temp_mean_c: float | None = None,
)
```

### Soil Input

Daily soil input:

```python
PhenologySoilInput(
    top_temp_c: float,
    top_vwc: float,
    water_stress: float = 1.0,
)
```

`top_temp_c` and `top_vwc` affect pre-emergence development. `water_stress` is used after emergence as a weak phenology slowdown.

## Emergence Logic

Emergence is modeled as a seed-type-specific thermal-time target modified by planting depth, seed-zone moisture, seed-zone temperature, and planting quality.

Default nominal seed depth:

```python
nominal_seed_depth_cm = 4.0
```

If seeds are planted deeper than nominal, the emergence target increases. If seeds are planted shallower than nominal, the target also increases, but with a smaller penalty. This represents delayed or less reliable emergence from non-ideal placement.

Seed-zone moisture penalty:

```text
preferred top-layer VWC: 0.20-0.30
dry penalty below 0.20
wet penalty above 0.30
```

Cold seed-zone temperature below 10°C increases the emergence target.

Planting quality is a scalar in `[0, 1]`. Lower quality increases the effective emergence target, representing poor seed-to-soil contact, uneven depth, or mechanical placement issues.

## Photoperiod Logic

The module computes daylength from latitude and date.

Default latitude:

```python
latitude_deg = 45.8
```

This approximates Harbin.

Default critical daylength:

```python
critical_daylength_h = 14.5
```

When daylength exceeds this threshold before flowering, development is slowed according to the seed type's photoperiod sensitivity.

This is a reduced short-day soybean response. It is included so the model does not treat temperature as the only driver of flowering timing.

## Stress Logic

After emergence, root-zone water stress can slow development.

The soil engine provides:

```python
water_stress in [0, 1]
```

The phenology engine converts this into a development multiplier. The default lower bound prevents phenology from stopping completely:

```python
min_development_stress_multiplier = 0.60
```

Biomass and yield penalties should be handled by the growth/yield model, not by phenology alone.

## Outputs

Each daily update returns one result per ridge:

```python
PhenologyDayResult(
    day: date,
    ridge_id: int,
    stage: SoybeanStage,
    days_after_planting: int,
    accumulated_gdd: float,
    effective_development_gdd: float,
    daily_gdd: float,
    effective_daily_gdd: float,
    emerged: bool,
    emergence_date: date | None,
    maturity_date: date | None,
    daylength_h: float,
    photoperiod_multiplier: float,
    stress_multiplier: float,
    tags: list[str],
)
```

The output is consumed by:

- crop-growth model
- pest/disease/weed model
- irrigation episode logic
- harvest-readiness model
- observation model

## Integration with Other Modules

### Weather Engine

The phenology engine consumes daily min/max temperatures from the weather trace.

```text
weather[t] -> daily GDD
```

### Soil Engine

The phenology engine consumes seed-zone temperature, top-layer VWC, and water-stress factor.

```text
soil[t] -> emergence modifier and stress multiplier
```

### Crop-Growth Engine

The crop-growth engine consumes stage and effective development progress.

```text
phenology[t] -> stage-specific growth sensitivity
```

### Harvest Module

Harvest readiness begins after R8, but grain dry-down and harvestable moisture are handled separately.

R8 is full maturity, not necessarily immediate harvest readiness.

## Scenario Use

Example scenario:

1. Agent plants on May 10 using `STANDARD` seed.
2. A cold spell occurs on May 12-14.
3. Soil temperature remains near the lower threshold.
4. Emergence is delayed.
5. Later stand assessment sees delayed or uneven emergence.
6. Growth and yield modules receive lower-quality initial state.

The same weather and soil traces can be used for oracle and agent runs. Differences in planting date, seed depth, or seed type produce different phenology trajectories.

## Limitations

The current phenology engine does not model:

- cultivar-specific CROPGRO coefficients
- full photoperiod response functions
- hourly temperature response
- detailed leaf-number dynamics
- determinacy / indeterminacy
- soybean maturity groups as named cultivars
- seed mortality
- stand density
- biomass or yield
- harvest grain moisture

These are deliberate omissions. The module is a reduced stage-tracking engine for scenario evaluation.
