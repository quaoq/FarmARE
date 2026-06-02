# Same-L3 Knowledge Library Pilot: Wet June Short Spray Window

## Purpose

This library supports `detail=library` for `scenario_full_season_hb_wetjune_shortwindow_trafficability`.

## L2 Coverage

| Skill | Source L2 | Coverage |
|---|---|---|
| Standard planting | `scenario_l2_hb_wetjune_shortwindow_standard_planting` | HEINONG84 standard-density preplant and planting |
| Fungicide window | `scenario_l2_hb_wetjune_shortwindow_fungicide_window` | Wet June disease diagnosis, trafficability/spray-window wait, targeted fungicide |
| Harvest dry/store | `scenario_l2_hb_wetjune_shortwindow_harvest_dry_store` | R8 moisture wait, harvest, unload, dry, store |

## Skill Cards

### 1. Standard Planting

```text
weather -> forecast -> soil -> inventory -> tractor status
level -> base_fertilize -> form 1.1 m ridges
plant HEINONG84 in 4-ridge blocks, depth_cm=4.0, seed_spacing_cm=7.9
commit/recheck
```

### 2. Wet June Fungicide Window

Belief: wet June disease pressure requires evidence and a workable spray window. Do not spray from NDVI/canopy alone; confirm disease with drone localization, target/reference state, and ground crop-health evidence, then wait/recheck trafficability and weather.

Core action template:

```text
weather -> forecast -> soil trafficability -> canopy -> overview
target state -> reference state -> drone survey -> robot crop-health check
wait to short action window
recheck weather, soil, target state, inventory, sprayer
load fungicide -> targeted fungicide blocks -> recheck disease state
```

### 3. Harvest Dry/Store

```text
weather -> forecast -> soil -> maturity/moisture -> capacity
wait/recheck drydown until moisture is dryable/legal
harvest 0-63 in 4-ridge blocks
unload -> dry_grain(target_moisture_pct=13.0) -> store_grain -> recheck
```
