# Same-L3 Knowledge Library Pilot: Wet-Cold High-Residue Establishment

## Purpose

This is a mechanism pilot for `detail=library` on:

`scenario_full_season_hb_wetcold_high_residue_establishment`

It uses L2 skills extracted from the same L3. This is intentionally not a fair cross-L3 evaluation.

## Is The Current L2 Set Enough?

Yes, for a `library_same_l3` pilot.

| Skill | Source L2 | Coverage |
|---|---|---|
| Wet-cold residue field prep | `scenario_l2_hb_wetcold_high_residue_field_prep` | Weather/soil/resource check, field prep, base fertilizer, 1.1 m ridges |
| Wet-cold seedbed planting window | `scenario_l2_hb_wetcold_high_residue_planting_window` | Wait 3 days, recheck seedbed, plant HEINONG84 whole field |
| Slow-emergence replant recovery | `scenario_l2_hb_wetcold_high_residue_replant_recovery` | Wait/recheck, drone and robot confirmation, targeted replant ridges 0-15 |
| Staged harvest | `scenario_l2_hb_wetcold_high_residue_harvest_sequence` | Harvest dry ridges 16-63 first, then wait and direct-store replanted 0-15 once moisture is safe |

Known gaps:

- There is no disease, insect, weed, nutrient, or irrigation action in this source L3.
- Exact ridge ranges and wait lengths are same-L3 leakage and must be generalized for fair cross-L3 library use.

## Skill Cards

### 1. Wet-Cold Residue Field Prep

```text
weather -> forecast -> soil -> inventory -> tractor status
attach grader -> level -> load_fertilizer(360.0) -> base_fertilize
form_ridges(ridge_width_m=1.1) -> commit_daily_physics
```

### 2. Wet-Cold Seedbed Planting Window

```text
start unplanted
complete field prep
wait 3 days for wet-cold residue seedbed
weather -> 3-day forecast -> soil -> planter status
load HEINONG84 seed
plant ridges 0-63, depth_cm=4.0, seed_spacing_cm=7.9
commit and recheck
```

### 3. Slow-Emergence Replant Recovery

```text
start after emergence routine check
wait 8 days for target action window
soil -> canopy -> target range state
drone survey 0-15
robot emergence check 0-15
load HEINONG84 seed once
replant ridges 0-15 in 4-ridge blocks
recheck 0-15
```

### 4. Staged Harvest

```text
start at R8 with moisture split
check 16-63 weather/soil/moisture/capacity
harvest 16-63 -> unload -> store
wait 18 days
check 0-15 weather/soil/moisture/capacity
harvest 0-15 -> unload -> store
```

Critical point: this scenario is not a whole-field same-day harvest after full 0-15 replanting. The replanted strip matures later and needs its own direct-storage harvest window.
