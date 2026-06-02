# Same-L3 Knowledge Library Pilot: Three Cultivar Wet-Disease/Dry-Harvest Sequence

## Purpose

This library supports `detail=library` for `scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence`.

## L2 Coverage

| Skill | Source L2 | Coverage |
|---|---|---|
| Three-cultivar planting | `scenario_l2_three_cultivar_preplant_fullfield_planting` | Field prep and cultivar-specific full-field planting |
| HN84 disease control | `scenario_l2_three_cultivar_hn84_disease_control` | Wet-zone disease diagnosis and targeted fungicide |
| HN58 dry water management | `scenario_l2_three_cultivar_hn58_dry_water_management` | Dry-zone water-stress diagnosis and irrigation |
| Zone harvest sequence | `scenario_l2_three_cultivar_zone_harvest_sequence` | Multi-zone maturity/moisture checks, harvest, unload, dry/store |

## Skill Cards

### 1. Three-Cultivar Planting

Belief: cultivar zones must be planted with their own seed choices and density settings after common field prep.

Core action template:

```text
weather -> forecast -> soil -> inventory -> tractor status
field prep -> base fertilizer -> 1.1 m ridges
plant each cultivar zone with its planned seed and spacing
commit/recheck all zones
```

### 2. HN84 Disease Control

Belief: wet-zone disease risk needs disease evidence, not just canopy color. Confirm with weather/soil/canopy, drone localization, zone state, reference comparison, and ground crop-health check before fungicide.

Core action template:

```text
weather/forecast/soil/canopy -> overview -> drone
HN84 zone state -> reference zone state -> robot crop-health check
wait/recheck spray window
apply targeted fungicide to confirmed HN84 disease zone
recheck disease pressure
```

### 3. HN58 Dry Water Management

Belief: dry-zone stress should be irrigated only if soil moisture and canopy/thermal evidence support water stress after excluding disease, insect, weed, and nutrient explanations.

Core action template:

```text
weather/forecast -> soil -> canopy/thermal -> drone
HN58 dry zone state -> reference state -> ground check
wait/recheck irrigation window and water budget
irrigate confirmed dry zone -> recheck response
```

### 4. Zone Harvest Sequence

Belief: cultivar zones can differ in maturity and moisture. Check each zone's maturity, grain moisture, weather, trafficability, and storage/dryer capacity before choosing harvest order.

Core action template:

```text
weather -> forecast -> soil -> zone maturity/moisture states -> inventory/capacity
wait/recheck drydown by zone
harvest ready zone(s) in 4-ridge blocks
unload -> dry if needed -> store -> recheck inventory
```
