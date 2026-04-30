# Observation Model

This page documents the reduced observation model used by Farm-ARE. The module converts hidden ridge-level world state into noisy, delayed, and spatially incomplete observation products.

The observation model is the layer that prevents the agent from reading the simulator’s hidden truth directly. It is therefore central to closed-loop evaluation.

## Role in Farm-ARE

The physics engines maintain hidden state:

```text
soil truth
phenology truth
canopy/biomass truth
biotic-pressure truth
management-effect truth
yield truth
```

The observation model converts selected parts of that hidden state into products available to agent-facing tools.

```text
hidden world state[t]
        -> observation_model
        -> sensor readings, drone products, satellite products, ground inspection, SPAD
        -> agent-facing tools
```

This separates “what is true in the world” from “what the agent can observe.”

## Modeling Basis

The model follows a partially observable system formulation:

1. The physics engine maintains hidden ridge-level truth.
2. Sensors observe only subsets of the state.
3. Observations contain noise.
4. Observations may be spatially sparse or coarse.
5. Observations may be delayed.
6. Detection tasks can have false positives and false negatives.
7. The agent acts on observations, not on hidden state.

This is not an image simulator. It does not generate raw images, point clouds, or calibrated remote-sensing products. It generates ridge-indexed observation products with realistic uncertainty and coverage behavior.

## Sensing Modes

The module covers the sensing modes in the farm inventory.

### Fixed in-situ sensors

Fixed sensors include:

- soil temperature and moisture sensors
- canopy index sensors

These provide point measurements at installed ridge positions. They have low latency and high temporal frequency but sparse spatial coverage.

### Satellite vegetation index

Satellite NDVI is represented as a coarse vegetation-index product. It groups multiple ridges into one coarse block, because satellite pixels are wider than the 1.1 m ridge spacing.

This product is useful for broad trend monitoring, not per-ridge actuation.

### UAV multispectral

The Mavic 3M-like multispectral product produces ridge-level NDVI-like values over a selected ridge range. It has higher spatial resolution than satellite NDVI and is used for field variability and anomaly detection.

### UAV thermal

The Matrice 4T-like thermal product produces canopy-temperature observations. It supports water-stress and anomaly detection but does not directly identify cause.

### UAV LiDAR

The LiDAR product produces a canopy-height proxy derived from LAI and lodging severity. It is a structural observation product, not a point-cloud simulation.

### Ground inspection

Robot dog or rover inspection produces higher-resolution pest and disease detection products over a small number of ridges. It can produce false positives and false negatives.

### Manual SPAD

Manual SPAD provides sparse high-confidence chlorophyll-like measurements. It is modeled as a point observation derived from canopy vigor and nutrient state.

## Hidden Truth Input

Each ridge is represented by `HiddenRidgeTruth`.

```python
HiddenRidgeTruth(
    ridge_id: int,
    top_vwc: float,
    root_vwc: float,
    top_temp_c: float,
    ndvi_proxy: float,
    lai: float,
    canopy_cover: float,
    canopy_temp_c: float,
    biomass_g_m2: float,
    weed_pressure: float,
    insect_pressure: float,
    disease_pressure: float,
    nutrient_index: float,
    lodging_severity: float,
)
```

This object should not be exposed directly to the agent.

## Sensor Assets

A sensor/platform is represented by `SensorAsset`.

```python
SensorAsset(
    asset_id: str,
    modality: ObservationModality,
    fixed_ridge_id: int | None,
    support_radius_ridges: int,
    available: bool,
)
```

Fixed sensors use `fixed_ridge_id`. Mobile or aerial platforms do not require fixed ridge positions.

The default asset layout places six soil sensors and six canopy-index sensors across the 64 ridges, matching the farm inventory at the scenario level.

## Observation Products

Every output is an `ObservationProduct`.

```python
ObservationProduct(
    product_id: str,
    product_type: ObservationProductType,
    modality: ObservationModality,
    asset_id: str,
    observed_day: date,
    available_day: date,
    ridge_ids: list[int],
    values: dict[int, Any],
    uncertainty: dict[str, float],
    tags: list[str],
)
```

Important fields:

| Field | Meaning |
|---|---|
| `observed_day` | When the measurement represents |
| `available_day` | When the agent/tool layer can access it |
| `ridge_ids` | Spatial support of the product |
| `values` | Product payload |
| `uncertainty` | Noise / error metadata |
| `tags` | Diagnostic labels such as anomaly detection |

## Modalities and Products

Supported modalities:

```python
SOIL_SENSOR
CANOPY_INDEX_SENSOR
SATELLITE_NDVI
UAV_MULTISPECTRAL
UAV_THERMAL
UAV_LIDAR
GROUND_INSPECTION_RGB
GROUND_INSPECTION_LIDAR
MANUAL_SPAD
```

Supported product types:

```python
SOIL_MOISTURE_POINT
SOIL_TEMPERATURE_POINT
CANOPY_INDEX_POINT
NDVI_MAP
CANOPY_TEMP_MAP
CANOPY_HEIGHT_MAP
SPAD_POINT
PEST_DETECTION
DISEASE_DETECTION
ANOMALY_MAP
```

## Noise and Latency Parameters

Default noise parameters:

```python
soil_vwc_noise_std = 0.015
soil_temp_noise_std_c = 0.6
canopy_index_noise_std = 0.025
spad_noise_std = 2.0

satellite_ndvi_noise_std = 0.050
uav_ndvi_noise_std = 0.025
thermal_noise_std_c = 1.0
lidar_height_noise_std_m = 0.05
```

Default latency:

```python
fixed_sensor_latency_days = 0
satellite_latency_days = 1
uav_latency_days = 0
ground_inspection_latency_days = 0
manual_spad_latency_days = 0
```

Latency can be increased in scenarios where map processing or reporting delays matter.

## Satellite NDVI

Satellite NDVI is coarse by construction.

```python
satellite_min_ridges_per_pixel = 8
```

A satellite product averages hidden NDVI over ridge blocks, adds noise, and assigns the same observed value to each ridge in the block.

This prevents the agent from using satellite data as if it were per-ridge truth.

## UAV Multispectral

UAV multispectral products observe an NDVI-like variable at ridge-level resolution.

```python
uav_ndvi_noise_std = 0.025
```

The model tags an NDVI anomaly if a ridge is sufficiently lower than the surveyed mean.

```python
ndvi_anomaly_drop_threshold = 0.08
```

The anomaly tag is a product-level diagnostic and does not reveal the cause.

## UAV Thermal

UAV thermal products observe canopy temperature.

```python
thermal_noise_std_c = 1.0
thermal_anomaly_threshold_c = 2.5
```

A ridge can be tagged as thermally anomalous if it is sufficiently hotter than the surveyed mean.

Thermal anomalies can indicate water stress, disease, or other issues, but the product does not directly classify the cause.

## UAV LiDAR

UAV LiDAR products observe canopy-height proxy.

Height is approximated from LAI and reduced by lodging severity.

```text
height = max_height × (1 - exp(-c × LAI)) × lodging_modifier
```

This is a structural proxy, not a point-cloud model.

## Ground Inspection

Ground inspection emits pest and disease detection products.

The model uses sensitivity and specificity:

```python
pest_detection_sensitivity = 0.85
pest_detection_specificity = 0.90
disease_detection_sensitivity = 0.80
disease_detection_specificity = 0.88
```

Hidden pressure is converted into a binary “present” condition using thresholds:

```python
pest_presence_pressure_threshold = 0.35
disease_presence_pressure_threshold = 0.30
```

Detection can miss true pressure or produce false positives. This gives the agent a reason to verify or cross-check observations.

## Manual SPAD

SPAD is modeled as a sparse point measurement derived from NDVI proxy and nutrient state.

```text
SPAD = f(NDVI proxy, nutrient index) + noise
```

Default range:

```python
spad_min = 25
spad_max = 50
```

This is a chlorophyll-like diagnostic, not a leaf-level biochemical model.

## Integration with Other Modules

### Soil Engine

The observation model consumes:

```text
top_vwc
root_vwc
top_temp_c
```

It emits soil moisture and soil temperature point observations.

### Canopy/Biomass Engine

The observation model consumes:

```text
ndvi_proxy
lai
canopy_cover
biomass
```

It emits multispectral NDVI, canopy index, LiDAR height, and SPAD-like products.

### Biotic Pressure Engine

The observation model consumes:

```text
weed_pressure
insect_pressure
disease_pressure
```

It emits pest/disease detection products only through ground inspection and indirect anomaly products through UAV/satellite signals.

### Management-Effect Engine

The observation model consumes:

```text
nutrient_index
```

This contributes to SPAD-like observations.

## Scenario Use

Example:

1. The hidden biotic-pressure engine increases insect pressure on ridge 10.
2. The canopy/biomass engine lowers NDVI proxy on that ridge.
3. A UAV multispectral survey detects an NDVI anomaly over ridges 8-12.
4. The agent dispatches a robot dog to inspect those ridges.
5. Ground inspection detects pest presence with some probability.
6. The agent decides whether to spray or continue monitoring.

The hidden insect pressure is never exposed directly. The agent sees only noisy products.

## Limitations

The current observation model does not implement:

- raw imagery
- radiative transfer
- atmospheric correction
- georeferencing errors
- point-cloud simulation
- camera geometry
- object detector training
- sensor drift
- weather-dependent image quality
- cloud masking for satellite imagery
- GCP/calibration-panel workflow
- multi-day data fusion

These can be added later if needed. The first version focuses on observable state products needed for closed-loop agent evaluation.
