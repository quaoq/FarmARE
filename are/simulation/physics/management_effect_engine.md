# Management-Effect Engine

This page documents the reduced management-effect model used by Farm-ARE. The module translates agent actions into persistent ridge-level effect states that other physics modules consume.

The management-effect engine is not a crop-growth model, soil-water model, or pest model. It is the bridge between farm operations and those modules.

## Role in Farm-ARE

Agent tools create actions such as planting, irrigation, fertilization, fertigation, herbicide, insecticide, and fungicide application. The management-effect engine converts those actions into effect states.

```text
agent actions / oracle actions
        -> management_effect_engine.update_day(...)
        -> stand fraction, nutrient stress, recent irrigation, treatment residuals
        -> soil engine, canopy/biomass engine, biotic-pressure engine
```

The module supports closed-loop scenarios where actions have delayed or persistent effects rather than instant state correction.

## Modeling Basis

The model follows agronomic action-effect logic:

1. Planting quality affects stand establishment.
2. Irrigation provides water input to the soil engine and is remembered as a recent intervention.
3. Base fertilizer and fertigation modify a ridge-level nutrient index.
4. Nutrient limitation reduces crop growth through a nutrient stress multiplier.
5. Herbicide, insecticide, and fungicide applications create residual protection windows.
6. Rain and wind reduce treatment efficacy.
7. Effects persist across days and are consumed by downstream physics modules.

This is not a mechanistic nutrient, pesticide, or chemical-fate model. It is a compact state-transition model for scenario execution and agent evaluation.

## State Representation

Each ridge has a `ManagementEffectState`.

```python
ManagementEffectState(
    ridge_id: int,
    planted: bool,
    planting_date: date | None,
    seed_depth_cm: float | None,
    planting_quality: float,
    stand_fraction: float,
    nutrient_index: float,
    nutrient_stress: float,
    days_since_irrigation: int | None,
    recent_irrigation_mm: float,
    herbicide_residual_days_left: int,
    insecticide_residual_days_left: int,
    fungicide_residual_days_left: int,
    cumulative_irrigation_mm: float,
    cumulative_fertigation_amount: float,
    cumulative_base_fertilizer_amount: float,
    cumulative_pesticide_applications: int,
    tags: list[str],
)
```

Important state variables:

| Variable | Meaning |
|---|---|
| `stand_fraction` | Fraction of intended stand established after planting |
| `planting_quality` | Aggregate execution-quality score from planting |
| `nutrient_index` | Ridge-level nutrient availability proxy |
| `nutrient_stress` | Growth multiplier passed to canopy/biomass model |
| `recent_irrigation_mm` | Recent water applied, used for tracing and delayed effects |
| `*_residual_days_left` | Treatment residual windows |

## Supported Actions

```python
ManagementActionType.PLANTING
ManagementActionType.IRRIGATION
ManagementActionType.FERTIGATION
ManagementActionType.BASE_FERTILIZER
ManagementActionType.HERBICIDE
ManagementActionType.INSECTICIDE
ManagementActionType.FUNGICIDE
```

Actions are represented as:

```python
ManagementAction(
    action_type: ManagementActionType,
    amount: float = 1.0,
    quality: float = 1.0,
    metadata: dict = {},
)
```

`amount` is action-specific. For irrigation and fertigation, it represents mm water equivalent. For fertilizer or pesticide-like actions, it represents a normalized application amount unless the tool provides a more specific unit.

`quality` captures execution quality. It can represent equipment calibration, imperfect application, operator error, bad row alignment, rain wash-off, high wind, or other implementation-specific factors.

`metadata` stores action details such as:

```python
seed_depth_cm
row_alignment_quality
nutrient_amount
```

## Planting Effects

Planting creates persistent stand and quality state.

Planting quality depends on:

- tool execution quality
- seed depth relative to nominal depth
- soil readiness
- ridge/row alignment quality

Default nominal seed depth:

```python
nominal_seed_depth_cm = 4.0
seed_depth_tolerance_cm = 1.0
```

Penalties:

```python
bad_depth_stand_penalty = 0.20
poor_soil_stand_penalty = 0.20
poor_alignment_stand_penalty = 0.15
min_stand_fraction = 0.35
```

The resulting `stand_fraction` is consumed by canopy/biomass growth. Poor planting does not immediately set yield loss directly; it reduces stand establishment and later canopy/biomass potential.

## Irrigation Effects

Irrigation is handled in two places:

1. The management-effect engine records the action.
2. The soil engine performs the actual water-balance update.

The helper method:

```python
irrigation_mm_by_ridge(actions_by_ridge)
```

extracts water inputs for the soil engine.

The management-effect state records:

```python
recent_irrigation_mm
days_since_irrigation
cumulative_irrigation_mm
```

This supports traceability and delayed-effect scenarios.

## Fertilizer and Fertigation Effects

The model tracks a ridge-level nutrient index.

```python
nutrient_index = 1.0  -> no nutrient limitation
nutrient_index < 1.0  -> possible nutrient stress
nutrient_index > 1.0  -> possible over-application
```

Default parameters:

```python
initial_nutrient_index = 0.75
max_nutrient_index = 1.10
base_fertilizer_gain = 0.20
fertigation_gain = 0.12
daily_nutrient_decay = 0.0015
nutrient_uptake_coeff = 0.0020
```

The nutrient index declines slowly each day and also declines with crop biomass production. Fertilization and fertigation increase it.

The model converts nutrient index to a growth multiplier:

```python
nutrient_stress in [nutrient_stress_min, 1.0]
```

This value is consumed by the canopy/biomass engine.

## Treatment Effects

Herbicide, insecticide, and fungicide applications register residual windows.

Default residuals:

```python
herbicide_residual_days = 18
insecticide_residual_days = 10
fungicide_residual_days = 14
```

This module records residual effect windows. The biotic-pressure engine should consume the corresponding treatment actions or residual states to reduce weed, insect, or disease pressure.

## Weather Effects on Application Quality

Application efficacy is reduced by rain or high wind.

Default thresholds:

```python
rain_washoff_mm = 8.0
high_wind_ms = 6.0
rain_washoff_efficacy_factor = 0.50
high_wind_efficacy_factor = 0.70
```

These thresholds are operational simplifications. They encode the fact that spraying during rain or high wind should be penalized or blocked by agent/oracle logic.

## Daily Update Logic

Each day, the engine:

1. Ages recent irrigation memory.
2. Decays nutrient index through background depletion and crop uptake.
3. Ages treatment residual windows.
4. Applies same-day management actions.
5. Recomputes nutrient stress.
6. Emits tags for tracing and debugging.

The module does not directly modify soil moisture, crop biomass, or pest pressure. It produces the inputs those modules use.

## Integration with Other Modules

### Soil Engine

The soil engine consumes water inputs extracted from irrigation and fertigation actions.

```text
irrigation/fertigation -> irrigation_mm_by_ridge -> soil.update_day(...)
```

### Canopy/Biomass Engine

The growth engine consumes:

```text
nutrient_stress
stand_fraction
```

Poor planting and nutrient deficiency therefore reduce growth through the same daily growth pathway.

### Biotic-Pressure Engine

The biotic engine consumes treatment applications or residual states.

```text
herbicide -> weed suppression
insecticide -> insect suppression
fungicide -> disease suppression
```

### Phenology Engine

The phenology engine may consume:

```text
seed_depth_cm
planting_quality
```

This allows poor planting to delay or reduce emergence quality.

## Scenario Use

Example:

1. The agent plants under marginal soil conditions.
2. Planting registers a lower stand fraction.
3. The phenology engine may show delayed emergence.
4. The canopy/biomass engine initializes with lower stand fraction.
5. Biomass and NDVI are reduced later.
6. The agent observes weak growth and applies fertigation.
7. Nutrient index improves, but lost canopy time is only partly recoverable.

This is the intended role of the management-effect engine: actions create persistent consequences that later modules expose through world evolution.

## Limitations

The current management-effect engine does not model:

- detailed fertilizer chemistry
- nitrogen fixation
- phosphorus/potassium pools
- pesticide degradation kinetics
- spray droplet deposition
- chemical residue limits
- crop injury from specific chemical labels
- labor constraints
- detailed machinery calibration
- cost accounting

These omissions are deliberate for the first version. The model keeps the action-effect state needed for closed-loop scenario evaluation.
