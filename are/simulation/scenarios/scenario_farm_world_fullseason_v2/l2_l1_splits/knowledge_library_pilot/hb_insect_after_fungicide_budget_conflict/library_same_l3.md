# Same-L3 Knowledge Library Pilot: Insect After Fungicide Budget Conflict

## Purpose

This library supports `detail=library` for `scenario_full_season_hb_insect_after_fungicide_budget_conflict`. It uses same-L3 L2 skills and is intended for mechanism testing, not fair cross-L3 evaluation.

## L2 Coverage

| Skill | Source L2 | Coverage |
|---|---|---|
| Standard-density establishment | `scenario_l2_hb_insect_budget_standard_planting` | Check weather/soil/resources, complete field prep and base fertilizer, form 1.1 m ridges, plant HEINONG84 at source-L3 standard density, commit and recheck |
| Disease control under budget | `scenario_l2_hb_insect_budget_disease_control` | Diagnose disease pressure, wait for spray window, apply budgeted fungicide, recheck |
| R5 insect manual control | `scenario_l2_hb_insect_budget_r5_manual_control` | Diagnose insect pressure after fungicide spend, respect remaining budget/trafficability, use manual insecticide path |
| R8 harvest/store | `scenario_l2_hb_insect_budget_r8_harvest_store` | Start at R8 drydown window, wait/recheck moisture and weather, harvest, unload, direct store when safe |

## Skill Cards

### 1. Standard-Density Establishment

Belief: this same-L3 source starts as an unplanted HEINONG84 standard-density field. Establishment should be done only after current weather, 3-day forecast, seedbed soil, inventory, and tractor status are checked, then the field is prepared before planting.

Core action template:

```text
weather -> forecast -> seedbed soil -> inventory -> tractor status
level field -> load 360 kg base fertilizer -> base_fertilize
form 1.1 m ridges -> recheck planting weather/forecast/soil/tractor
load HEINONG84 -> plant 0-63 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9
commit planting physics -> recheck planted field
```

### 2. Disease Control Under Budget

Belief: wet-season canopy/disease signals require an evidence chain before fungicide. Use weather, forecast, soil/canopy data, drone localization, target-vs-reference state, and ground crop-health confirmation before spending fungicide budget.

Core action template:

```text
weather -> forecast -> soil/canopy -> overview
drone survey -> target state -> reference state -> robot crop-health check
wait to spray window -> recheck weather/soil/target/inventory/sprayer
load fungicide -> apply targeted fungicide blocks
recheck disease state and budget
```

### 3. R5 Insect Manual Control

Belief: insect pressure after fungicide is a separate diagnosis. Do not treat insect damage with fungicide. If wet soil or budget conflict blocks mechanical spraying, use the manual insecticide path only after insect evidence and resource checks.

Core action template:

```text
R5 overview -> soil/canopy -> drone -> target state -> robot insect/leaf-damage check
weather/resource/budget recheck
manual insecticide on confirmed insect block
recheck insect pressure and remaining budget
```

### 4. R8 Harvest/Store

Belief: R8 starts a harvest decision window. Confirm weather, trafficability, grain moisture, harvest_allowed/R8, and storage capacity; wait if grain is too wet or weather is unsuitable.

Core action template:

```text
weather -> forecast -> soil -> whole-field maturity/moisture -> inventory
wait/recheck drydown until direct-storage moisture is safe
harvest 0-63 in 4-ridge blocks
unload during harvest -> store_grain -> recheck inventory
```

## Use

Use with the base L3 `detail=False` prompt only. The library gives skills and constraints, not a human-written full answer.
