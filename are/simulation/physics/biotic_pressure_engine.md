# Biotic Pressure Engine

This page documents the reduced biotic-pressure model used by Farm-ARE. The module tracks latent weed, insect, and disease pressure for each ridge and converts these pressures into a biotic stress multiplier consumed by the canopy/biomass growth engine.

The module is designed for closed-loop agent scenarios. It does not attempt to simulate detailed insect ecology, pathogen biology, or herbicide/pesticide chemistry.

## Role in Farm-ARE

The biotic-pressure engine sits alongside the soil and crop-growth modules.

```text
weather[t] + crop_stage[t] + canopy_cover[t] + soil_wetness[t] + treatments[t]
        -> biotic_pressure_engine.update_day(...)
        -> weed pressure, insect pressure, disease pressure
        -> biotic stress multiplier
        -> canopy/biomass growth model
```

The module supports scenarios such as:

- early-season weed control
- pest scouting and treatment
- drone anomaly followed by ground verification
- disease risk after wet weather
- delayed or mistimed pesticide application
- treatment wash-off after rain
- residual protection after herbicide, insecticide, or fungicide application

## Modeling Basis

The model follows integrated pest management logic instead of species-level simulation.

The main modeling choices are:

1. Weed, insect, and disease pressure are latent ridge-level states in `[0, 1]`.
2. Pressure increases when weather and crop stage are favorable.
3. Pressure decreases naturally when conditions are unfavorable.
4. Treatments reduce current pressure and create residual suppression for a limited number of days.
5. Rain can reduce treatment efficacy through wash-off.
6. The crop-growth model receives a biotic stress multiplier derived from weighted pressures.
7. The observation model should expose noisy or delayed indicators, not the hidden true pressure directly.

## Scientific Anchors and Simplifications

### Insect pressure

Soybean aphid is a useful reference insect because extension guidance defines a concrete management threshold. The common economic threshold is approximately 250 aphids per plant with 80% of plants infested and populations increasing. The model does not simulate individual aphids by default. Instead, normalized insect pressure is mapped to an aphid-equivalent diagnostic so that scenarios can express threshold-like decisions.

Soybean aphid development is favored by moderate warm temperatures around 25-28°C and suppressed by high temperatures near or above 35°C. The model uses a bell-shaped temperature suitability function centered near 27°C and suppresses growth under very high temperatures.

### Disease pressure

Soybean disease risk is often associated with favorable temperature, wet soil, high humidity, rainfall, and leaf wetness. The model uses rainfall and top-layer soil moisture as simple moisture proxies. Disease pressure increases when temperatures are moderate and moisture/rain conditions are favorable.

This is not a model of a specific disease such as white mold, SDS, or frogeye leaf spot. It is a generic fungal/disease pressure state for scenario generation.

### Weed pressure

Weed competition is most important early in soybean development. Extension sources often describe the first four to six weeks after planting, or early vegetative stages, as the critical weed-control period. The model therefore gives weeds high impact during early vegetative stages and suppresses weed growth as canopy cover increases.

The model does not distinguish weed species or herbicide modes of action.

## State Representation

Each ridge has a `BioticPressureState`.

```python
BioticPressureState(
    ridge_id: int,
    weed_pressure: float,
    insect_pressure: float,
    disease_pressure: float,
    herbicide_residual_days_left: int,
    insecticide_residual_days_left: int,
    fungicide_residual_days_left: int,
    cumulative_weed_pressure: float,
    cumulative_insect_pressure: float,
    cumulative_disease_pressure: float,
    tags: list[str],
)
```

Pressure variables are normalized:

```text
0.0 = no pressure
1.0 = severe pressure
```

Cumulative pressure variables support later yield-penalty or evaluation modules.

## Inputs

### Weather Input

```python
BioticWeatherInput(
    day: date,
    air_temp_mean_c: float,
    rain_mm: float,
    is_raining: bool = False,
)
```

Weather affects insect development, disease suitability, treatment wash-off, and moisture-driven risk.

### Soil Input

```python
BioticSoilInput(
    top_vwc: float = 0.25,
    root_vwc: float = 0.25,
)
```

Top-layer VWC is used as a wetness proxy for disease risk.

### Crop Input

```python
BioticCropInput(
    stage: GrowthStage,
    canopy_cover: float = 0.0,
)
```

Crop stage affects weed, insect, and disease suitability. Canopy cover suppresses weed growth.

### Treatments

```python
TreatmentApplication(
    treatment_type: TreatmentType,
    efficacy_multiplier: float = 1.0,
)
```

Supported treatments:

```python
TreatmentType.HERBICIDE
TreatmentType.INSECTICIDE
TreatmentType.FUNGICIDE
```

## Daily Update Logic

### 1. Apply Treatments

Treatments are applied at the start of the day. Each treatment reduces the corresponding pressure immediately and creates a residual suppression period.

Default effects:

| Treatment | Initial reduction | Residual days | Residual suppression |
|---|---:|---:|---:|
| Herbicide | 0.55 | 18 | 0.60 |
| Insecticide | 0.65 | 10 | 0.55 |
| Fungicide | 0.45 | 14 | 0.45 |

If rainfall exceeds the wash-off threshold on the application day, efficacy is reduced.

Default wash-off threshold:

```python
rain_washoff_mm = 8.0
wash_off_penalty = 0.50
```

### 2. Compute Suitability

Each pressure has a daily suitability value.

Weed suitability depends on crop stage and canopy cover.

Insect suitability depends on crop stage, mean temperature, and heavy rain.

Disease suitability depends on crop stage, mean temperature, rainfall, and top-layer VWC.

### 3. Update Pressures

Pressures follow logistic-like growth under favorable conditions and decay under unfavorable conditions.

```text
if suitability high:
    pressure increases toward 1.0
else:
    pressure decays toward 0.0
```

This keeps states bounded and avoids discontinuous pressure jumps unless the scenario explicitly initializes an outbreak.

### 4. Compute Biotic Stress

The growth model consumes:

```python
biotic_stress_multiplier
```

This is derived from weighted weed, insect, and disease pressure.

Default weights:

```python
weed_growth_weight = 0.28
insect_growth_weight = 0.22
disease_growth_weight = 0.30
```

The multiplier is clipped to avoid negative growth:

```python
min_biotic_stress_multiplier = 0.35
```

### 5. Generate Diagnostics

The model emits tags such as:

```text
weed_pressure_high
insect_pressure_high
disease_pressure_high
aphid_threshold_like_condition
herbicide_residual_active
treatment_washoff_risk
```

These tags are useful for debugging and scenario validation. They should not all be exposed directly to the agent.

## Aphid-Equivalent Diagnostic

The model maps normalized insect pressure to an aphid-equivalent diagnostic.

Default mapping:

```text
insect_pressure = 0.5 -> 250 aphids/plant
```

This does not mean all insect pressure is soybean aphid. It provides an interpretable threshold proxy for scenarios where the agent must decide whether pest pressure warrants treatment.

The module marks `insect_treatment_recommended` when:

```text
aphid_equivalent_per_plant >= 250
and crop stage is late vegetative through R5
```

The agent or oracle should still verify weather, trend, stage, and treatment feasibility.

## Scenario Use

Example scenario:

1. A warm period during V4-R1 increases insect suitability.
2. Insect pressure grows gradually over several days.
3. Drone observations show an NDVI anomaly but do not reveal true pressure directly.
4. Ground inspection confirms insect pressure in selected ridges.
5. The agent applies insecticide.
6. If rain occurs on the application day, treatment efficacy is reduced.
7. Residual suppression reduces insect pressure growth for several days.
8. The growth model receives a higher or lower biotic stress multiplier depending on timing.

This supports closed-loop evaluation: delayed scouting or mistimed treatment produces a different crop trajectory than the oracle.

## Integration with Other Modules

### Weather Engine

The biotic-pressure engine consumes mean temperature and rainfall.

```text
temperature -> insect/disease suitability
rainfall -> disease risk and treatment wash-off
```

### Soil Engine

Top-layer VWC is used as a wetness proxy for disease risk.

```text
wet topsoil -> higher disease suitability
```

### Phenology Engine

Crop stage determines whether weed, insect, and disease pressure are relevant.

```text
stage -> suitability and treatment-recommendation logic
```

### Canopy/Biomass Growth Engine

The growth engine consumes the biotic stress multiplier.

```text
biotic_pressure -> biotic_stress_multiplier -> daily biomass reduction
```

### Observation Model

The observation model should convert hidden pressures into noisy signals.

Examples:

```text
weed pressure -> canopy competition / visual weed detection
insect pressure -> local leaf damage or pest confirmation
disease pressure -> thermal/NDVI anomaly or lesion classification
```

## Limitations

The current biotic-pressure engine does not model:

- individual pest species
- insect life stages
- explicit aphid reproduction and migration
- pathogen-specific infection cycles
- leaf wetness duration
- pesticide chemistry
- pesticide resistance
- herbicide modes of action
- weed species
- spatial spread between ridges
- natural enemy populations

These omissions are deliberate. The goal is a compact latent-pressure model for closed-loop agent evaluation.
