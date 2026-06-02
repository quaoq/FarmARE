# Same-L3 Knowledge Library Pilot: HEIHE50 Cold-Spring Planting Window

## Purpose

This is a mechanism pilot for `detail=library` on:

`scenario_full_season_hb_coldspring_planting_window_heihe50`

It uses L2 skills extracted from the same L3. This is intentionally not a fair cross-L3 evaluation. It is useful for testing whether a structured skill library can be retrieved, rendered into the L3 prompt, and used by the agent.

## Is The Current L2 Set Enough?

Yes, for a `library_same_l3` pilot.

The current L2 set covers each major farming phase in this L3:

| Skill | Source L2 | Coverage |
|---|---|---|
| Cold-spring field prep | `scenario_l2_hb_coldspring_heihe50_field_prep` | Weather/forecast/soil/resource check, field prep, base fertilizer, 1.1 m ridges, and post-prep verification |
| Cold-spring planting window | `scenario_l2_hb_coldspring_heihe50_planting_window` | Start from 2026-05-05, prep, wait out cold seedbed risk, recheck, wait to planting window, and plant HEIHE50 whole field |
| Emergence and stand scouting | `scenario_l2_hb_coldspring_heihe50_cold_emergence_scouting` | Post-planting cold/rain risk monitoring, delayed emergence scouting, drone survey, and ground stand confirmation |
| R8 drydown and direct storage harvest | `scenario_l2_hb_coldspring_heihe50_harvest_drydown_store` | Start at R8, wait/recheck moisture and field conditions, harvest whole field, unload, and direct store when moisture is safe |

Known gaps:

- There is no disease, insect, weed, nutrient, irrigation, or replant treatment in the source L3. The management phase is therefore represented as a scouting/stand-confirmation skill.
- Daily waits are compressed into window-finding patterns rather than separate skills.
- This same-L3 library includes exact source-L3 dates, waits, ridge ranges, and parameters. That is acceptable for mechanism testing, but it is leakage for a fair cross-L3 library.

## JSON vs MD Format

Use both:

- `library_same_l3.json` is the machine-readable artifact for retrieval and prompt rendering.
- This Markdown file is the reviewer-facing explanation.

The JSON should not be only an oracle event list. The minimum useful unit is:

```text
belief + evidence_chain + oracle_events + constraints + success_checks
```

## Skill Cards

### 1. Cold-Spring Field Prep

Belief:

If the cold-spring HEIHE50 field is unprepared and unplanted, first confirm weather, forecast, seedbed soil, fertilizer/fuel inventory, and tractor status. Complete field prep before any planting.

Core action template:

```text
weather -> 5-day forecast -> soil -> inventory -> tractor status
attach grader -> level -> load_fertilizer(360.0) -> base_fertilize
form_ridges(ridge_width_m=1.1) -> commit_daily_physics
recheck tractor/field state
```

### 2. Cold-Spring Planting Window

Belief:

The 2026-05-05 start is not an immediate planting window. Complete field prep, wait through the cold seedbed spell, recheck, wait to the planting window, recheck again, then plant HEIHE50 only when current conditions support it.

Core action template:

```text
start 2026-05-05, NOT_PLANTED
weather -> forecast -> seedbed soil -> inventory -> tractor status
field prep sequence
wait 8 days for cold seedbed risk
weather/soil recheck
wait 3 days to planting window
weather -> 3-day forecast -> seedbed soil -> planter status
load HEIHE50 seed
plant ridges 0-63 in 4-ridge blocks, depth_cm=4.0, seed_spacing_cm=8.2
commit and recheck planted field
```

### 3. Emergence And Stand Scouting

Belief:

After cold-window planting, early low canopy/NDVI should not be treated as fertilizer, weed, disease, or insect pressure. Wait through the cold/rain risk and emergence window, then use remote sensing plus ground checks before deciding whether any action is justified.

Core action template:

```text
start after HEIHE50 planting, PLANTED_PRE_EMERGENCE
weather -> 4-day forecast -> soil -> farm overview
wait 4 days through cold/rain risk
weather/soil recheck
wait 10 days to emergence scouting window
farm overview -> canopy sensors
charge drone -> whole-field NDVI survey
charge robot -> inspect_emergence sample
whole-field range-state check
no treatment unless evidence supports it
```

### 4. R8 Drydown And Direct Storage Harvest

Belief:

At R8 start, harvest timing is still a decision. Check weather, trafficability, grain moisture, and storage capacity. If grain moisture is too high, wait and recheck. Harvest only when the field is workable and moisture is safe for the intended postharvest path.

Core action template:

```text
start at R8/harvest-window beginning
weather -> 3-day forecast -> soil -> whole-field moisture state
wait 5 days, then recheck moisture
wait 5 days, then recheck weather and moisture
wait 6 days to direct-store window
weather -> 3-day forecast -> soil -> whole-field moisture -> inventory/capacity
attach harvester
harvest ridges 0-63 in 4-ridge blocks, unload after each block
store_grain directly because split-window grain moisture is <= 13.5%
recheck inventory
```

Critical order:

```text
harvest -> unload -> store_grain
```

Direct storage is only valid when grain moisture is `<=13.5%`. If moisture is `13.5%-18%`, use:

```text
harvest -> unload -> dry_grain -> store_grain
```

Do not wait for unnecessary 8%-10% grain moisture.

## How This Should Be Used

For `detail=library_same_l3`, feed the base L3 prompt plus the selected library skills. Do not use the full `detail=True` human-written prompt at the same time, otherwise the effect of library cannot be separated from expert detail text.

Recommended first pilot comparison:

| Condition | Prompt content |
|---|---|
| base | normal L3 `detail=False` |
| detail_true | normal L3 `detail=True` |
| library_same_l3 | normal L3 `detail=False` + the four skill cards from this library |

