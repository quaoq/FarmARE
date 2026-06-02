# Same-L3 Knowledge Library Pilot: HEIHE43 Density, Weed, Nutrient Recovery

## Purpose

This is a mechanism pilot for `detail=library` on:

`scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery`

It uses L2 skills extracted from the same L3. This is intentionally not a fair cross-L3 evaluation. It is useful for testing whether a structured skill library can be retrieved, rendered into the L3 prompt, and used by the agent.

## Is The Current L2 Set Enough?

Yes, for a `library_same_l3` pilot.

The current L2 set covers the main management decisions in this L3:

| Skill | Source L2 | Coverage |
|---|---|---|
| HEIHE43 recommended-density planting | `scenario_l2_hb_heihe43_recommended_density_planting` | Weather/soil/resource check, field prep, base fertilizer, ridging, HEIHE43 planting at `seed_spacing_cm=8.1` |
| VC nutrient recovery | `scenario_l2_hb_heihe43_nutrient_recovery` | Diagnose nutrient-weak block, compare reference block, wait for window, targeted fertigation |
| VC weed control | `scenario_l2_hb_heihe43_early_weed_control` | Diagnose weed competition separately from nutrient/water stress, wait for visibility and spray window, targeted herbicide |
| R7/R8 harvest drydown | `scenario_l2_hb_heihe43_r7_harvest_drydown_store` | Wait for maturity/moisture/weather/trafficability window, harvest, unload, dry, store |

Known gaps:

- Observation-only routine checks are not separate skills.
- The same checkpoint can feed multiple skills, so the library should be treated as a decision aid, not as a simple concatenation of scenarios.
- Same-L3 exact target ridges, dates, wait lengths, and parameters are acceptable for this pilot, but they are leakage for a fair cross-L3 library.

## JSON vs MD Format

Use both:

- `library_same_l3.json` is the machine-readable artifact for retrieval and prompt rendering.
- This Markdown file is the reviewer-facing explanation.

The JSON should not be only an oracle event list. A bare oracle event list is too close to "answer replay" and does not explain when a skill applies. The minimum useful unit is:

```text
belief + evidence_chain + oracle_events + constraints + success_checks
```

For `library_same_l3`, `oracle_events` may include exact parameters such as ridges and rates because the goal is mechanism testing. For cross-L3 evaluation, those should be generalized into trigger conditions, parameter ranges, and action templates.

## Skill Cards

### 1. HEIHE43 Recommended-Density Planting

Belief:

If the field is unplanted and weather, forecast, soil, seed/fertilizer/fuel inventory, and tractor status are acceptable, complete field prep before planting. HEIHE43 should be planted at its recommended density; do not substitute arbitrary high density.

Core action template:

```text
weather -> forecast -> soil -> inventory -> tractor status
attach grader -> level -> load/base fertilizer -> attach furrower -> form ridges
plant HEIHE43 in 4-ridge blocks, depth_cm=4.0, seed_spacing_cm=8.1
recheck planted field
```

Why this matters:

The physics model uses actual `seed_spacing_cm` to compute realized planting density. For HEIHE43, large deviation from the recommended density reduces biomass/yield.

### 2. VC Nutrient Recovery

Belief:

If early HEIHE43 vigor is weak in a localized block, diagnose before acting. Treat as nutrient stress only after soil/water context, canopy, drone map, target-vs-reference state, and ground inspection support nutrient weakness rather than drought, weed, disease, or insect pressure.

Core action template:

```text
weather -> forecast -> soil -> canopy -> field overview
drone whole field -> target state 8-19 -> reference state 24-35 -> robot crop health 8-19
wait 4 days -> recheck weather/state/inventory
apply_fertigation(8, 19, nutrient_amount=0.24, water_mm=1.2)
recheck 8-19
```

### 3. VC Weed Control

Belief:

If early canopy/vigor variability persists, distinguish weed competition from nutrient or water stress. Do not use NDVI alone. Confirm with target-vs-reference state and ground inspection before herbicide.

Core action template:

```text
weather -> forecast -> soil trafficability -> canopy -> field overview
drone whole field -> wait 8 days for weed signal visibility
target state 44-55 -> reference state 24-35 -> robot crop health 44-55
wait 4 days -> recheck weather/state/inventory/sprayer
load_pesticide(29.4)
apply_herbicide(44, 53, liters_per_ridge=2.4)
apply_herbicide(54, 55, liters_per_ridge=2.4)
recheck 44-55
```

### 4. R7/R8 Harvest Drydown, Dry, Store

Belief:

Near maturity is not enough for immediate harvest. From R7/R8, repeatedly check weather, forecast, soil trafficability, harvest_allowed/R8, grain moisture, and storage capacity. Wait for a legal harvest window. If grain moisture is above safe direct-storage moisture but within the dryable range, harvest, unload, dry, then store.

Core action template:

```text
weather -> forecast -> soil -> whole-field maturity/moisture -> inventory
wait 4 days -> recheck moisture
wait 4 days -> recheck moisture
wait 4 days -> recheck weather/soil/moisture/capacity
harvest 0-63 in 4-ridge blocks
unload after each harvest block
dry_grain(target_moisture_pct=13.0)
store_grain
recheck inventory
```

Critical order:

```text
harvest -> unload -> dry_grain -> store_grain
```

Do not store wet grain directly. Do not wait for unnecessary 8%-10% moisture.

## How This Should Be Used

For `detail=library_same_l3`, feed the base L3 prompt plus the selected library skills. Do not use the full `detail=True` human-written prompt at the same time, otherwise the effect of library cannot be separated from expert detail text.

Recommended first pilot comparison:

| Condition | Prompt content |
|---|---|
| base | normal L3 `detail=False` |
| detail_true | normal L3 `detail=True` |
| library_same_l3 | normal L3 `detail=False` + the four skill cards from this library |

Expected behavior:

- `library_same_l3` may approach `detail_true` because same-L3 skills include exact targets and parameters.
- This condition proves the mechanism, not fair generalization.
- The fair paper claim should later use cross-L3 library retrieval.
