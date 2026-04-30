# Yield and Recovered-Yield Engine

This page documents the reduced yield and recovered-yield model used by Farm-ARE. The module converts biological yield potential into harvestable and recovered yield by accounting for grain moisture, harvest timing, field losses, and machine losses.

The model is intended for scenario evaluation. It is not a detailed grain-quality, combine-mechanics, storage, or market-pricing simulator.

## Role in Farm-ARE

The yield/recovery engine runs after the canopy/biomass and phenology modules.

```text
phenology[t] + growth_yield_potential[t] + weather[t] + harvest_action[t]
        -> yield_recovery_engine.update_day(...)
        -> grain moisture, harvest readiness, field loss, machine loss, recovered yield
```

The module supports scenarios such as:

- harvest timing under rain risk
- harvest delay and shattering loss
- grain moisture dry-down after R8
- harvesting too wet and requiring drying
- harvesting too dry and losing saleable mass or shattering
- lodging increasing machine losses
- combine/header quality affecting recovered yield
- comparing oracle and agent final yield under the same weather trace

## Modeling Basis

The model separates three yield quantities:

1. biological yield potential
2. harvestable yield after field losses
3. recovered yield after machine/header losses and moisture conversion

This separation is important for agent evaluation. A good growth season can still produce poor recovered yield if the agent delays harvest, harvests under unsuitable moisture, or operates with poor machine conditions.

## Agronomic Anchors

Soybeans are commonly marketed around 13% moisture. Extension guidance often treats roughly 13% as optimal or near-optimal for minimizing mechanical damage and avoiding elevator/storage penalties. Harvesting too wet can require drying or cause discounts, while harvesting too dry reduces saleable weight and increases shattering risk.

Delayed harvest increases the risk of pod shattering, especially after dry-down below roughly 11% moisture or repeated wetting/drying cycles. Several extension sources recommend harvesting soon after soybean reaches acceptable moisture and avoiding excessive delay after maturity.

The model encodes these operational facts with configurable thresholds:

```python
market_moisture_frac = 0.13
ideal_harvest_moisture_min = 0.12
ideal_harvest_moisture_max = 0.15
wet_harvest_threshold = 0.18
dry_shatter_threshold = 0.11
```

## State Representation

Each ridge has a `YieldRecoveryState`.

```python
YieldRecoveryState(
    ridge_id: int,
    r8_reached: bool,
    maturity_date: date | None,
    grain_moisture_frac: float | None,
    wet_dry_cycles_after_r8: int,
    biological_yield_g_m2: float,
    field_loss_fraction: float,
    machine_loss_fraction: float,
    recovered_yield_g_m2_at_market_moisture: float,
    harvested: bool,
    harvest_date: date | None,
    drying_required: bool,
    quality_discount_fraction: float,
    tags: list[str],
)
```

Important state variables:

| Variable | Meaning |
|---|---|
| `biological_yield_g_m2` | Yield potential from the growth model before harvest losses |
| `grain_moisture_frac` | Current field grain moisture after R8 |
| `field_loss_fraction` | Loss before harvest, including shattering/lodging/biotic damage |
| `machine_loss_fraction` | Loss during combine/header recovery |
| `recovered_yield_g_m2_at_market_moisture` | Final recovered yield converted to 13% moisture basis |
| `drying_required` | Whether harvested grain exceeds drying threshold |
| `quality_discount_fraction` | Simple quality/discount proxy |

## Inputs

### Weather Input

```python
YieldWeatherInput(
    day: date,
    air_temp_mean_c: float,
    rain_mm: float,
    solar_rad_mj_m2: float,
    wind_ms: float,
)
```

Weather drives grain dry-down and wetting/drying cycles after maturity.

### Phenology Input

```python
YieldPhenologyInput(
    stage: GrowthStage,
    maturity_date: date | None,
)
```

R8 starts the harvest-readiness process, but R8 is not identical to immediate harvest readiness. Grain moisture must dry down into an acceptable range.

### Growth Input

```python
YieldGrowthInput(
    yield_potential_g_m2: float,
    aboveground_biomass_g_m2: float = 0.0,
)
```

`yield_potential_g_m2` is the biological yield ceiling produced by the canopy/biomass module.

### Stress Input

```python
YieldStressInput(
    lodging_severity: float = 0.0,
    disease_severity: float = 0.0,
    insect_pod_damage: float = 0.0,
)
```

These late-season states increase field and machine losses.

### Harvest Action

```python
HarvestAction(
    machine_quality: float = 1.0,
    pass_completed: bool = True,
)
```

`machine_quality` captures combine/header setup, operator execution, and general harvest execution quality. Lower values increase machine loss.

## Daily Update Logic

### 1. Update Biological Yield Potential

Before harvest, the engine stores the maximum biological yield potential received from the growth model.

```text
biological_yield_g_m2 = max(previous, growth_yield_potential)
```

### 2. Initialize R8 / Maturity State

When the phenology module reaches R8, the yield module initializes grain moisture.

Default:

```python
initial_r8_grain_moisture = 0.30
```

This represents mature but not yet harvest-dry soybeans.

### 3. Grain Moisture Dry-Down

After R8, grain moisture changes daily.

Dry-down increases with:

- solar radiation
- wind
- background dry-down rate

Rain reduces dry-down and can rewet the crop.

Default parameters:

```python
base_drydown_per_day = 0.018
solar_drydown_coeff = 0.0006
wind_drydown_coeff = 0.0012
rain_rewetting_per_mm = 0.0015
max_daily_rewetting = 0.035
```

This is a simple field dry-down model, not a mechanistic grain-moisture model.

### 4. Field Loss

Field loss occurs before harvest. It includes:

- delayed-harvest shattering
- very low moisture shattering risk
- repeated wet/dry cycles after R8
- lodging
- disease/quality loss
- insect pod damage

Default delayed-harvest logic:

```python
shatter_delay_grace_days = 7
shatter_loss_per_day_after_grace = 0.006
```

Very dry grain increases field loss:

```python
dry_shatter_threshold = 0.11
shatter_loss_dry_bonus = 0.015
```

Repeated wet/dry cycles add additional shattering risk:

```python
wet_dry_cycle_loss = 0.010
```

### 5. Harvest Action and Machine Loss

When a harvest action is applied, the engine computes machine/header loss.

Base machine loss:

```python
base_machine_loss_fraction = 0.025
```

Loss increases under:

- low moisture
- high moisture
- lodging
- poor machine quality

The engine then computes recovered yield:

```text
harvestable_yield = biological_yield × (1 - field_loss)
recovered_as_harvested = harvestable_yield × (1 - machine_loss)
```

### 6. Market-Moisture Conversion

Recovered yield is converted to a 13% moisture basis.

```text
dry_matter = recovered_as_harvested × (1 - field_moisture)
market_mass = dry_matter / (1 - 0.13)
```

This accounts for the fact that overdry soybeans contain less saleable water mass relative to the market moisture basis.

### 7. Drying / Quality Flags

If grain moisture exceeds the drying threshold, the engine marks drying as required.

```python
drying_required_moisture = 0.15
```

Quality-discount flags can be added for wet harvest or damage risk. This is a proxy for downstream storage/quality handling, not a full pricing module.

## Outputs

Each daily update returns one result per ridge.

```python
YieldRecoveryDayResult(
    day: date,
    ridge_id: int,
    stage: GrowthStage,
    grain_moisture_frac: float | None,
    biological_yield_g_m2: float,
    field_loss_fraction: float,
    machine_loss_fraction: float,
    harvestable_yield_g_m2: float,
    recovered_yield_g_m2_at_market_moisture: float,
    recovered_yield_kg_ha_at_market_moisture: float,
    recovered_yield_bu_ac_at_market_moisture: float,
    harvested: bool,
    drying_required: bool,
    quality_discount_fraction: float,
    harvest_ready: bool,
    tags: list[str],
)
```

The module reports both physical units and agronomic units:

```text
g/m2
kg/ha
bu/ac
```

## Integration with Other Modules

### Phenology Engine

The yield module starts grain-moisture tracking at R8.

```text
phenology.stage == R8 -> initialize grain moisture
```

### Canopy/Biomass Engine

The growth engine provides biological yield potential.

```text
growth.yield_potential_g_m2 -> biological_yield_g_m2
```

### Weather Engine

Weather controls dry-down and rewetting.

```text
solar + wind -> dry-down
rain -> rewetting and wet/dry cycles
```

### Biotic and Management Modules

Late-season lodging, disease, and insect pod damage increase field and machine loss.

### Harvest / Operations Module

The harvest action triggers final recovery. Machine quality and pass completion affect recovered yield.

## Scenario Use

Example:

1. Soybeans reach R8 on September 15.
2. Grain moisture starts near 30%.
3. The crop dries down over several days.
4. Rain events rewet the crop and create wet/dry cycles.
5. The agent delays harvest beyond the safe window.
6. Field loss increases through shattering and wet/dry cycles.
7. The agent harvests at low moisture.
8. Machine loss increases and recovered yield drops.
9. The oracle harvests earlier at 13-15% moisture and recovers more yield.

This creates a final metric that reflects the timing of earlier decisions rather than only whether the harvest tool was eventually called.

## Limitations

The current yield/recovery engine does not model:

- detailed grain filling physiology
- seed size and seed number
- full grain moisture physics
- combine-specific mechanics
- real elevator pricing schedules
- drying energy/cost
- storage spoilage
- detailed quality grading
- spatially explicit pod shattering
- market timing

These omissions are deliberate. The module provides a compact recovered-yield model for closed-loop agent evaluation.
