# Same-L3 Knowledge Library Pilot: HEINONG84 Staggered Planting

## Purpose

This is a mechanism pilot for `detail=library` on:

`scenario_full_season_heinong84_staggered_planting`

It uses L2 skills extracted from the same L3. This is intentionally not a fair cross-L3 evaluation. It is useful for testing whether a structured skill library can be retrieved, rendered into the L3 prompt, and used by the agent.

## Is The Current L2 Set Enough?

Yes, for a `library_same_l3` pilot.

The current L2 set covers each major farming phase in this L3:

| Skill | Source L2 | Coverage |
|---|---|---|
| Field prep and early-zone planting | `scenario_l2_hn84_staggered_field_prep_early_planting` | Weather/soil/resource check, field prep, base fertilizer, 1.1 m ridges, early-zone planting |
| Mid-zone planting window | `scenario_l2_hn84_staggered_mid_planting_window` | Wait for the second staggered planting window, recheck conditions, plant ridges 21-42 |
| Late-zone seedbed irrigation and planting | `scenario_l2_hn84_staggered_late_seedbed_planting` | Wait for the third window, recheck seedbed, irrigate late zone, wait infiltration, plant ridges 43-63 |
| Post-planting stage scouting | `scenario_l2_hn84_staggered_stage_scouting` | Emergence checks and later stage-split scouting across early/mid/late zones |
| Staggered harvest sequence | `scenario_l2_hn84_staggered_harvest_sequence` | Start from R8/harvest-window beginning, wait/recheck, harvest early/mid/late batches, unload, dry, store |

Known gaps:

- The library has no separate L2 for every daily wait event.
- This same-L3 library includes exact source-L3 ridge ranges, waits, and parameters. That is acceptable for mechanism testing, but it is leakage for a fair cross-L3 library.
- Observation-only management is represented as a scouting skill because this L3 has no disease, insect, weed, or nutrient intervention after planting.

## JSON vs MD Format

Use both:

- `library_same_l3.json` is the machine-readable artifact for retrieval and prompt rendering.
- This Markdown file is the reviewer-facing explanation.

The JSON should not be only an oracle event list. The minimum useful unit is:

```text
belief + evidence_chain + oracle_events + constraints + success_checks
```

## Skill Cards

### 1. Field Prep And Early-Zone Planting

Belief:

If the field is unprepared and unplanted, first confirm weather, forecast, seedbed soil, inventory, fuel, and tractor status. Complete field prep before any planting. In this staggered L3, only early ridges 0-20 are planted in the first window; do not plant mid/late zones early.

Core action template:

```text
weather -> forecast -> soil -> inventory -> tractor status
attach grader -> level -> load_fertilizer(250.0) -> base_fertilize
form_ridges(ridge_width_m=1.1) -> commit_daily_physics
weather -> forecast -> soil -> tractor status
load HEINONG84 seed
plant ridges 0-20 in 4-ridge blocks, depth_cm=4.0, seed_spacing_cm=7.9
commit and recheck early zone
```

### 2. Mid-Zone Planting Window

Belief:

After early-zone planting, mid-zone planting is not an immediate action. Wait until the configured mid-zone planting window, then recheck weather, forecast, soil, seed inventory, and planter status before planting ridges 21-42.

Core action template:

```text
start after early-zone planting
wait 7 days for the mid planting window
weather -> forecast -> soil -> inventory -> tractor status
load HEINONG84 seed
plant ridges 21-42 in 4-ridge blocks, depth_cm=4.0, seed_spacing_cm=7.9
commit and recheck mid zone
```

### 3. Late-Zone Seedbed Irrigation And Planting

Belief:

After mid-zone planting, late-zone planting requires a later window and a seedbed moisture check. If the late seedbed needs moisture support, irrigate the target zone, wait for infiltration, then recheck before planting.

Core action template:

```text
start after mid-zone planting
wait 7 days for the late planting window
weather -> forecast -> soil
irrigate ridges 43-63 for 1.2 hours
wait 6 hours for infiltration
soil recheck -> inventory -> tractor status
load HEINONG84 seed
plant ridges 43-63 in 4-ridge blocks, depth_cm=4.0, seed_spacing_cm=7.9
commit and recheck late zone
```

### 4. Post-Planting Stage Scouting

Belief:

After all three planting windows, the zones should not be treated as one uniform crop stage. Scout emergence and later stage split by zone. Do not apply fertilizer, pesticide, or irrigation unless the current evidence supports a real stressor.

Core action template:

```text
wait 16 days after late-zone planting
farm overview -> soil -> canopy
robot emergence checks: early sample, mid sample, late sample
drone whole-field NDVI
charge drone/robot
wait 42 days for stage split visibility
farm overview -> early range -> mid range -> late range
soil -> canopy -> drone survey
robot crop-health checks: early sample, mid sample, late sample
charge drone/robot
```

### 5. Staggered Harvest Sequence

Belief:

At R8/harvest-window beginning, harvest timing is a decision. The early, mid, and late zones must be checked separately for maturity, weather, soil trafficability, grain moisture, and capacity. Harvest each batch only after the current state supports it.

Core action template:

```text
start at early R8/harvest-window beginning
wait 10 days, then recheck early-zone harvest conditions
attach harvester
harvest ridges 0-20 in 4-ridge blocks, unload after each block
dry_grain(target_moisture_pct=13.0) -> store_grain
wait 7 days, then recheck mid-zone harvest conditions
harvest ridges 21-42 in 4-ridge blocks, unload after each block
dry_grain(target_moisture_pct=13.0) -> store_grain
wait 10 days, then recheck late-zone harvest conditions
harvest ridges 43-63 in 4-ridge blocks, unload after each block
dry_grain(target_moisture_pct=13.0) -> store_grain
commit and recheck inventory
```

Critical order:

```text
harvest -> unload -> dry_grain -> store_grain
```

Do not store wet grain directly. Do not wait for unnecessary 8%-10% grain moisture.

## How This Should Be Used

For `detail=library_same_l3`, feed the base L3 prompt plus the selected library skills. Do not use the full `detail=True` human-written prompt at the same time, otherwise the effect of library cannot be separated from expert detail text.

Recommended first pilot comparison:

| Condition | Prompt content |
|---|---|
| base | normal L3 `detail=False` |
| detail_true | normal L3 `detail=True` |
| library_same_l3 | normal L3 `detail=False` + the five skill cards from this library |

