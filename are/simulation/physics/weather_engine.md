# Weather Engine

This page documents the reduced daily weather generator used by Farm-ARE. The weather engine produces seedable, parameterized exogenous weather traces for soybean-season scenarios.

The implementation is designed for scenario forcing, not numerical weather prediction. It provides realistic daily variation, controlled weather disturbances, and reproducible traces that can be shared across oracle and agent runs.

## Role in Farm-ARE

The weather engine is the upstream exogenous driver for the rest of the physics stack.

```text
weather_engine.generate(...)
        -> daily weather trace
        -> soil engine
        -> crop-growth engine
        -> pest/disease/weed pressure model
        -> operation-feasibility checks
```

Weather affects:

- soil moisture through rainfall
- soil temperature through air temperature
- crop growth through temperature and solar radiation
- irrigation need through drying demand
- drone flight feasibility through rain and wind
- spraying feasibility through rain and wind
- trafficability through rainfall and wet soil
- harvest timing through rainfall and grain dry-down

## Modeling Basis

The implementation follows the structure of WGEN/Richardson-style daily stochastic weather generators.

The relevant modeling choices are:

1. Daily timestep.
2. Monthly climate parameters as inputs.
3. Wet/dry precipitation occurrence with persistence.
4. Gamma-distributed rainfall amount on wet days.
5. Autocorrelated daily temperature anomaly.
6. Monthly solar radiation baseline with rainy-day reduction.
7. Bounded daily wind-speed sampling.
8. Deterministic scenario-level event overrides.

This is a reduced implementation. A calibrated weather generator would estimate precipitation transition probabilities, rainfall distribution parameters, and multivariate residual correlations from station records. Farm-ARE instead uses compact monthly parameters so scenarios can be easily configured for Harbin or another location.

## Scientific References and Simplifications

WGEN generates daily precipitation, maximum temperature, minimum temperature, and solar radiation from statistical weather parameters. Richardson-style generators commonly model precipitation occurrence using a first-order two-state wet/dry Markov chain and generate wet-day precipitation amounts using a gamma distribution.

This implementation keeps that structure but simplifies it:

- it uses `wet_day_prob`, `wet_persistence_bonus`, and `dry_after_dry_penalty` instead of explicit `P(W|W)` and `P(W|D)` transition probabilities
- it uses one gamma shape parameter and derives wet-day mean rainfall from monthly precipitation and expected wet days
- it generates daily mean temperature using an AR(1) anomaly around monthly means
- it derives min/max temperature from a sampled diurnal range
- it represents solar radiation using monthly baselines with wet-day multipliers
- it represents wind as a bounded daily mean value

These choices are sufficient for Farm-ARE because the goal is to drive soil, crop, and operation-feasibility modules with traceable weather, not to reproduce station-level climatology.

## Default Harbin / Heilongjiang Parameters

The default configuration is intended for May–September soybean scenarios around Harbin / Heilongjiang.

```python
monthly = {
    5: MonthlyClimate(temp_mean_c=14.5, precip_mm=55.0,  wet_day_prob=0.25, solar_rad_mj_m2=18.0),
    6: MonthlyClimate(temp_mean_c=20.0, precip_mm=90.0,  wet_day_prob=0.35, solar_rad_mj_m2=20.0),
    7: MonthlyClimate(temp_mean_c=23.5, precip_mm=135.0, wet_day_prob=0.45, solar_rad_mj_m2=19.0),
    8: MonthlyClimate(temp_mean_c=21.5, precip_mm=105.0, wet_day_prob=0.38, solar_rad_mj_m2=17.0),
    9: MonthlyClimate(temp_mean_c=15.0, precip_mm=45.0,  wet_day_prob=0.25, solar_rad_mj_m2=13.0),
}
```

The May–September precipitation total is 430 mm. July and August are configured as the wettest months. Temperature rises from May into July and declines into September. Solar radiation is highest around June/July and lower in September.

These defaults are scenario parameters, not site-calibrated observations. For another farm, replace the monthly climate dictionary.

## State Representation

Each generated day is represented as:

```python
WeatherDay(
    day: date,
    air_temp_mean_c: float,
    air_temp_min_c: float,
    air_temp_max_c: float,
    rain_mm: float,
    wind_ms: float,
    solar_rad_mj_m2: float,
    is_raining: bool,
    weather_tags: list[str],
)
```

The `weather_tags` field records deterministic scenario events such as `cold_spell`, `heavy_rain_event`, or `spraying_blocked_high_wind`.

## Generator Inputs

The generator requires:

```python
WeatherGeneratorConfig
start_date
end_date
seed
optional list[WeatherEvent]
```

A fixed seed, configuration, date range, and event list always produce the same trace.

## Background Weather Generation

### Temperature

Daily mean temperature is generated as:

```text
monthly mean temperature + AR(1) anomaly
```

The AR(1) anomaly preserves day-to-day persistence.

Default parameters:

```python
temp_ar1_phi = 0.75
temp_noise_sigma_c = 2.5
```

Daily min/max temperature are derived by sampling a diurnal temperature range around the daily mean. Rainy days reduce the diurnal range.

### Rain Occurrence

Rain occurrence uses a compact wet/dry persistence process.

```text
base probability = monthly wet_day_prob
if previous day was wet: add wet_persistence_bonus
if previous day was dry: subtract dry_after_dry_penalty
```

Default parameters:

```python
wet_persistence_bonus = 0.15
dry_after_dry_penalty = 0.05
```

This approximates a first-order wet/dry Markov process without requiring calibrated transition probabilities.

### Rain Amount

If a day is wet, rainfall amount is sampled from a gamma distribution.

The mean wet-day rainfall is derived from monthly precipitation:

```text
mean_wet_day_rain = monthly_precip_mm / expected_wet_days
expected_wet_days = wet_day_prob * days_in_month
```

Default gamma shape:

```python
rain_gamma_shape = 1.6
```

### Solar Radiation

Solar radiation is sampled around a monthly baseline. Rainy days reduce solar radiation using a random multiplier.

Default rainy-day multipliers:

```python
rainy_solar_min_multiplier = 0.45
rainy_solar_max_multiplier = 0.75
```

### Wind

Wind is represented as bounded daily mean wind speed.

Default bounds:

```python
wind_min_ms = 0.0
wind_max_ms = 14.0
```

This is sufficient for operation feasibility checks, such as drone flight and pesticide spraying.

## Scenario Event Overrides

Background weather can be modified by deterministic scenario events.

Supported event types:

```python
rain_event
cold_spell
heat_wave
wind_event
dry_spell
```

Example:

```python
WeatherEvent(
    event_type="rain_event",
    start_date=date(2026, 6, 25),
    duration_days=2,
    total_rain_mm=35.0,
    label="heavy_rain_event",
)
```

Event semantics:

| Event | Effect |
|---|---|
| `rain_event` | Adds specified total rainfall over event duration, reduces solar radiation, lowers max temperature |
| `cold_spell` | Shifts mean/min/max temperature downward |
| `heat_wave` | Shifts mean/min/max temperature upward |
| `wind_event` | Forces wind speed to a specified value |
| `dry_spell` | Sets rainfall to zero and slightly increases solar radiation |

Events are applied after stochastic background generation. This allows scenario authors to force specific disturbances while preserving the rest of the season.

## Example Usage

```python
from datetime import date

config = default_harbin_soybean_config()
generator = WeatherGenerator(config=config, seed=42)

trace = generator.generate(
    start_date=date(2026, 5, 1),
    end_date=date(2026, 9, 30),
    events=[
        WeatherEvent(
            event_type="cold_spell",
            start_date=date(2026, 5, 12),
            duration_days=3,
            temp_delta_c=5.0,
            label="post_planting_cold_spell",
        ),
        WeatherEvent(
            event_type="dry_spell",
            start_date=date(2026, 8, 1),
            duration_days=8,
            label="pod_fill_dry_spell",
        ),
    ],
)
```

## Integration with Farm-ARE

The weather trace is shared across oracle and agent runs.

```text
same weather trace + oracle actions -> oracle world evolution
same weather trace + agent actions  -> agent world evolution
```

This makes outcome differences attributable to action sequences rather than different exogenous conditions.

The agent should not directly observe hidden future weather. It should access weather through scenario-specific tools, such as:

- current weather
- rain status
- wind status
- short-term forecast
- weather alert feed

## Downstream Consumption

### Soil Engine

The soil engine consumes:

```text
air_temp_mean_c
rain_mm
solar_rad_mj_m2
wind_ms
```

Rain increases soil moisture. Temperature, solar radiation, and wind increase drying demand.

### Crop-Growth Engine

The crop-growth engine consumes:

```text
temperature
solar radiation
cold/heat event tags
```

Temperature drives thermal-time accumulation. Radiation affects biomass accumulation. Extreme conditions introduce stress penalties.

### Pest / Disease / Weed Pressure

Biotic pressure can consume:

```text
temperature
rainfall
humidity proxy from rainy days
crop stage
```

For example, dry spells may increase water stress and pest risk, while heavy rain may suppress some insects but increase disease risk.

### Operation Feasibility

Operation tools consume:

```text
is_raining
rain_mm
wind_ms
weather_tags
```

Examples:

- drone flight blocked by rain or high wind
- pesticide spraying blocked by rain or high wind
- field trafficability affected by rainfall through the soil engine
- harvest delayed by rain or wet field conditions

## Limitations

The current weather generator does not model:

- sub-daily weather
- humidity
- dew point
- vapor pressure deficit
- station-calibrated precipitation transition matrices
- multivariate residual correlation among temperature, radiation, and precipitation
- extreme-event return periods
- typhoons or regional storm systems
- snow/freeze-thaw processes

These omissions are deliberate in the first version. The module is intended to provide a reproducible and parameterized weather driver for Farm-ARE scenarios.

## Implementation Notes

The generator is deterministic under fixed seed/config/events.

Use `weather_tags` to track programmed scenario disturbances.

Use `summarize_weather(trace)` to inspect whether a generated trace is in the intended range before running scenario comparisons.

For new locations, create a new `WeatherGeneratorConfig` with monthly climate parameters. The rest of the engine does not need to change.
