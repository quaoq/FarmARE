# Same-L3 Knowledge Library Pilot: Two Dry Patches, One Irrigation

## Purpose

This library supports `detail=library` for `scenario_full_season_hb_two_dry_patches_one_irrigation`.

## L2 Coverage

| Skill | Source L2 | Coverage |
|---|---|---|
| Standard planting | `scenario_l2_hb_two_dry_patches_standard_planting` | Weather/soil/resource check, field prep, 1.1 m ridges, HEINONG84 planting |
| Irrigation priority | `scenario_l2_hb_two_dry_patches_irrigation_priority` | Diagnose two dry patches, compare severity/resource budget, irrigate only the priority confirmed patch |
| Harvest dry/store | `scenario_l2_hb_two_dry_patches_harvest_dry_store` | R8/drydown wait, maturity/moisture/weather/capacity checks, harvest, unload, dry if needed, store |

## Skill Cards

### 1. Standard Planting

Belief: unplanted HEINONG84 standard-density field needs weather/forecast/soil/resource and tractor checks before field prep and planting.

Core action template:

```text
weather -> forecast -> soil -> inventory -> tractor status
level -> base_fertilize -> form_ridges(ridge_width_m=1.1)
plant HEINONG84 in 4-ridge blocks, depth_cm=4.0, seed_spacing_cm=7.9
commit/recheck planted field
```

### 2. Irrigation Priority

Belief: when two dry patches exist but irrigation is limited, do not irrigate both by default. Confirm water stress with soil moisture, canopy/thermal signals, target-vs-reference state, and ground check; then choose the patch where stress and yield risk justify the limited water.

Core action template:

```text
R5 overview -> weather/forecast -> soil -> canopy -> drone survey
target dry patch states -> reference state -> ground crop-health check
wait/recheck weather, soil, target state, and water budget
irrigate the confirmed priority dry patch
recheck response
```

### 3. Harvest Dry/Store

Belief: mixed maturity/drydown after stress requires waiting and rechecking. Harvest only when maturity, grain moisture, weather, soil trafficability, and capacity support it.

Core action template:

```text
weather -> forecast -> soil -> whole-field maturity/moisture -> inventory
wait/recheck until harvest window
harvest 0-63 in 4-ridge blocks
unload during harvest -> dry if needed -> store -> recheck inventory
```
