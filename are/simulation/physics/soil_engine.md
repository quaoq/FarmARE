# Soil Engine

This page documents the reduced soil physics engine used by Farm-ARE. The soil engine converts daily weather forcing and farm interventions into ridge-level soil moisture, soil temperature, trafficability, and crop water-stress state.

The implementation is intended for closed-loop agent scenarios, not site-calibrated hydrology. It provides traceable, parameterized state transitions that can be shared across oracle and agent runs under the same exogenous weather trace.

## Role in Farm-ARE

The soil engine sits between the weather engine, crop-growth engine, and farm-operation tools.

```text
weather[t] + irrigation[t] + canopy_cover[t]
        -> soil_engine.update_day(...)
        -> soil moisture, soil temperature, trafficability, water stress
        -> planting, irrigation, crop growth, spraying, harvest feasibility
```

The soil state affects several downstream decisions:

- planting readiness depends on top-layer soil temperature and moisture
- irrigation decisions depend on root-zone moisture
- crop growth depends on water stress
- tractor, robot dog, spraying, and harvesting feasibility depend on trafficability
- heavy rainfall changes both soil moisture and operation feasibility

## Scientific Basis and Simplification

The model follows the standard daily soil-water-balance structure used in crop simulators such as DSSAT and APSIM-SoilWat. Water enters through rainfall and irrigation, then exits through runoff, drainage, soil evaporation, and plant transpiration.

The evapotranspiration logic follows a reduced FAO-56-style structure. Atmospheric demand is approximated from solar radiation, air temperature, and wind. Canopy cover partitions water loss between exposed-soil evaporation and crop transpiration.

The implementation is intentionally simplified. It does not solve Richards' equation, does not represent a full multi-layer soil profile, and does not model lateral flow between ridges. Instead, it uses two conceptual soil layers:

- top layer: seed-zone moisture/temperature, planting readiness, surface wetness, trafficability, evaporation
- root-zone layer: crop-accessible water, transpiration, growth-stage water stress

This split is important because planting readiness and crop water stress should not be represented by the same scalar moisture value.

## State Representation

Each ridge has an independent `RidgeSoilState`.

```python
RidgeSoilState(
    ridge_id: int,
    top_vwc: float,
    root_vwc: float,
    top_temp_c: float,
    root_temp_c: float,
    cumulative_runoff_mm: float,
    cumulative_drainage_mm: float,
    cumulative_evap_mm: float,
    cumulative_transpiration_mm: float,
    tags: list[str],
)
```

Default state variables:

| Variable | Meaning |
|---|---|
| `top_vwc` | Volumetric water content in the seed/topsoil layer |
| `root_vwc` | Volumetric water content in the root-zone layer |
| `top_temp_c` | Soil temperature in the top layer |
| `root_temp_c` | Soil temperature in the root zone |
| `cumulative_runoff_mm` | Accumulated runoff from excess inflow |
| `cumulative_drainage_mm` | Accumulated drainage below the modeled root zone |
| `cumulative_evap_mm` | Accumulated soil evaporation |
| `cumulative_transpiration_mm` | Accumulated crop transpiration |
| `tags` | Operational labels generated during update |

The engine assumes independent ridge-level states. This matches the current 1D ridge-indexed Farm-ARE representation and avoids adding lateral soil-water movement.

## Inputs

The soil engine consumes daily weather forcing from the weather generator/playback module.

```python
WeatherInput(
    day: date,
    air_temp_mean_c: float,
    air_temp_min_c: float,
    air_temp_max_c: float,
    rain_mm: float,
    solar_rad_mj_m2: float,
    wind_ms: float,
)
```

It also consumes management inputs:

```python
irrigation_mm_by_ridge: dict[int, float]
canopy_cover_by_ridge: dict[int, float]
```

`irrigation_mm_by_ridge` represents water applied at the ridge level in mm water equivalent.

`canopy_cover_by_ridge` is a value in `[0, 1]` and is used to partition evapotranspiration between soil evaporation and crop transpiration.

## Outputs

Each daily update returns one `SoilDayResult` per ridge.

```python
SoilDayResult(
    day: date,
    ridge_id: int,
    top_vwc: float,
    root_vwc: float,
    top_temp_c: float,
    root_temp_c: float,
    water_stress: float,
    irrigation_recommended: bool,
    planting_ready: bool,
    trafficability: str,
    runoff_mm: float,
    drainage_mm: float,
    evap_mm: float,
    transpiration_mm: float,
    tags: list[str],
)
```

Important derived outputs:

| Output | Use |
|---|---|
| `planting_ready` | Determines whether planting is allowed or blocked |
| `trafficability` | Determines whether tractor or ground-robot operations are feasible |
| `water_stress` | Feeds the crop-growth model |
| `irrigation_recommended` | Supports irrigation decision tools |
| `tags` | Exposes traceable state labels for scenario debugging |

## Default Parameters

The default parameters are scenario-generation values, not calibrated measurements.

```python
top_depth_m = 0.10
root_depth_m = 0.40

wilting_point_vwc = 0.12
field_capacity_vwc = 0.30
saturation_vwc = 0.40

max_infiltration_mm_day = 35.0
top_drainage_rate = 0.65
root_drainage_rate = 0.45

irrigation_efficiency = 0.90
rainfall_capture_efficiency = 0.85
```

Planting thresholds:

```python
planting_temp_min_c = 10.0
planting_vwc_min = 0.20
planting_vwc_max = 0.30
planting_vwc_too_dry = 0.15
planting_vwc_too_wet = 0.35
```

Growth water-stress thresholds:

```python
water_stress_vwc = 0.18
irrigation_trigger_vwc = 0.17
```

Soil temperature response:

```python
top_temp_response = 0.40
root_temp_response = 0.18
rain_cooling_coeff_c_per_10mm = 0.25
```

Reduced ET approximation:

```python
radiation_et_coeff = 0.55
min_temp_factor = 0.40
max_temp_factor = 1.25
bare_soil_evap_fraction = 0.65
max_crop_coefficient = 1.05
```

These values should be treated as tunable hyperparameters. They can be adjusted for different soil types, field locations, or scenario regimes.

## Daily Update Logic

Each day, each ridge is updated independently.

### 1. Inflow

Rainfall and irrigation are converted into effective water entering the ridge.

```text
effective_rain = rain_mm * rainfall_capture_efficiency
effective_irrigation = irrigation_mm * irrigation_efficiency
incoming = effective_rain + effective_irrigation
```

Incoming water is capped by `max_infiltration_mm_day`. Excess water becomes runoff.

### 2. Top-Layer Storage

Infiltrated water enters the top layer first.

If the top layer exceeds saturation, excess water is moved downward. If the top layer exceeds field capacity, a fraction drains to the root zone.

```text
top layer -> percolation -> root zone
```

This gives rainfall and irrigation a delayed effect on the root zone.

### 3. Evapotranspiration

The engine estimates daily evaporative demand from solar radiation, temperature, and wind.

Canopy cover controls how demand is split:

- low canopy cover: more exposed-soil evaporation
- high canopy cover: more crop transpiration

Soil evaporation removes water from the top layer. Crop transpiration removes water from the root zone.

### 4. Root-Zone Storage

The root zone receives percolated water from the top layer. It loses water through crop transpiration and drainage below the modeled root zone.

Root-zone water stress is computed from root-zone VWC. Stress is 1.0 above the stress threshold and decreases toward 0.0 near wilting point.

### 5. Soil Temperature

Soil temperature is represented as a lagged response to daily mean air temperature.

The top layer responds faster than the root zone. Rainfall produces a small cooling effect.

This is a practical approximation for planting-window and early-growth scenarios.

### 6. Derived Operational States

The engine computes:

- planting readiness
- irrigation recommendation
- water-stress factor
- trafficability category
- debug / trace tags

These outputs are used by agent-facing tools and downstream physics modules.

## Planting Readiness

Planting readiness is computed from the top layer.

Default conditions:

```text
top_temp_c >= 10°C
0.20 <= top_vwc <= 0.30
```

Blocked or marginal conditions:

```text
top_vwc < 0.15        -> too dry
0.15 <= top_vwc < 0.20 -> marginal dry
0.30 < top_vwc <= 0.35 -> marginal wet
top_vwc > 0.35        -> too wet
```

This is an operational planting rule, not a full germination model.

## Trafficability

Trafficability is an operational state used to decide whether ground operations can proceed.

Default categories:

```text
good
limited
blocked
```

The model marks trafficability as blocked if top-layer moisture is too high or daily rainfall is heavy. It marks trafficability as limited if the field is wet but not fully blocked.

This affects:

- planting
- tractor-mounted spraying
- fertilizer application
- harvesting
- robot dog / ground rover inspection

## Irrigation Recommendation

Irrigation is recommended when root-zone VWC drops below the irrigation trigger threshold.

Default:

```text
root_vwc <= 0.17 -> irrigation recommended
```

Water stress begins slightly above this threshold:

```text
root_vwc <= 0.18 -> water stress tag
```

This separates the physical stress state from the operational decision threshold.

## Integration with Other Modules

### Weather Engine

The weather engine provides daily forcing:

```text
temperature
rainfall
solar radiation
wind
```

Rainfall directly changes soil water. Temperature and solar radiation affect drying. Wind increases evaporative demand.

### Crop-Growth Engine

The crop-growth engine consumes:

```text
water_stress
root_vwc
soil temperature
canopy cover
```

Crop growth also returns canopy cover, which affects the next soil update.

### Irrigation Tools

Irrigation actions are written as ridge-level water inputs:

```python
irrigation_mm_by_ridge = {
    12: 8.0,
    13: 8.0,
    14: 8.0,
}
```

The soil engine then updates the moisture state and reports whether stress was reduced.

### Planting Tools

Planting tools should query or consume `planting_ready`, `top_vwc`, and `top_temp_c`.

The agent should not plant if the soil is too cold, too dry, or too wet.

### Operation Feasibility Tools

Ground operations should consume `trafficability`.

Examples:

```text
trafficability = blocked -> tractor cannot enter
trafficability = limited -> operation possible but risky or delayed
trafficability = good -> operation allowed
```

## Scenario Use

The soil engine supports closed-loop scenarios where agent actions affect later outcomes.

Example:

1. Weather engine produces dry period.
2. Soil engine lowers root-zone VWC over several days.
3. Agent observes moisture stress through sensors.
4. Agent irrigates selected ridges.
5. Soil engine updates VWC and water-stress state.
6. Crop-growth model uses updated stress to determine growth penalty.

The same weather trace can be used for both oracle and agent runs. The only difference is the management sequence applied to the soil engine.

## Limitations

The current soil engine does not model:

- lateral water movement between ridges
- detailed multi-layer soil profiles
- capillary rise
- snow accumulation or freeze-thaw dynamics
- groundwater
- soil compaction mechanics
- full Penman-Monteith evapotranspiration
- site-calibrated hydraulic parameters

These omissions are deliberate for the first implementation. The goal is a traceable and parameterized state-transition model for agent evaluation, not a complete agronomic simulator.
