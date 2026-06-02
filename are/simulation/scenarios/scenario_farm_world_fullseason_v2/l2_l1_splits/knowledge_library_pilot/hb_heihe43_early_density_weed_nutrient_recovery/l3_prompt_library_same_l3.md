# Prompt Template: Feed Same-L3 Library To The L3 Agent

## Target Scenario

`scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery`

## Experimental Condition

`detail=library_same_l3`

Use the normal L3 `detail=False` task prompt as the base prompt, then append the library block below. Do not also provide the normal `detail=True` prompt in this condition.

## Library Block To Append

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support: first collect the listed evidence, then decide whether the skill applies, then perform the action only if the current state supports it.

Skill 1: HEIHE43 recommended-density planting
- Belief: If the HEIHE43 field is not planted and weather, forecast, seedbed soil, seed/fertilizer/fuel inventory, and tractor status are acceptable, complete field preparation before planting. Plant HEIHE43 at the recommended density rather than arbitrary high density.
- Evidence: current weather; 3-day forecast; soil sensors; inventory; tractor status.
- Action pattern: attach grader; level field; load 360 kg base fertilizer; base_fertilize; attach furrower; form ridges at 1.1 m; plant HEIHE43 across the whole field in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=8.1; recheck planted field.
- Constraints: do not plant before field prep, base fertilizer, and ridge formation. Do not use very high density just because more seed is available.
- Success check: planted ridges, correct depth/spacing, and inventory use are visible.

Skill 2: VC nutrient recovery
- Belief: Early weak HEIHE43 growth should be diagnosed before treatment. Use fertigation only if evidence supports nutrient stress rather than drought, weed, disease, or insect pressure.
- Evidence: current weather; 3-day forecast; soil sensors; canopy sensors; farm overview; drone survey; target block state; reference block state; robot crop-health inspection.
- Action pattern: if nutrient stress is confirmed, wait for a suitable fertigation window; recheck weather and target state; check inventory; apply targeted fertigation with nutrient_amount=0.24 and water_mm=1.2 to the confirmed nutrient-weak block; recheck the target block.
- Constraints: do not use fertigation to solve weed competition, disease, insect pressure, or pure water stress.
- Success check: target nutrient stress is reduced or stabilized and input use is consistent.

Skill 3: VC weed control
- Belief: Canopy or NDVI variability is not enough to diagnose weeds. Confirm weed competition with target-vs-reference state and ground inspection before herbicide.
- Evidence: current weather; 3-day forecast; soil trafficability; canopy sensors; farm overview; drone survey; target block state; reference block state; robot crop-health inspection.
- Action pattern: if weed competition is confirmed, wait until the weed signal is visible and a spray window is suitable; recheck weather, target state, inventory, and sprayer status; load herbicide volume; apply targeted herbicide at 2.4 L/ridge to the confirmed weed block; recheck the target block.
- Constraints: do not treat nutrient or water stress with herbicide; do not use NDVI alone; do not spray without checking weather and trafficability.
- Success check: weed pressure or weed competition is reduced and non-target reference areas are not treated.

Skill 4: R7/R8 harvest drydown, dry, and store
- Belief: Near maturity is not enough for immediate harvest. Harvest requires maturity/harvest_allowed, suitable weather, trafficable soil, known grain moisture, and capacity. Wait for a legal moisture/weather window.
- Evidence: current weather; 3-day forecast; soil sensors; whole-field state with maturity and grain moisture; inventory/storage capacity; repeated moisture rechecks after drydown waits.
- Action pattern: wait and recheck moisture until harvest is legal; recheck harvest weather and soil; recheck whole-field harvestability and capacity; harvest the whole field in 4-ridge blocks; unload after each block; if moisture is above safe direct-storage moisture, dry_grain(target_moisture_pct=13.0); then store_grain and recheck inventory.
- Constraints: preserve harvest -> unload -> dry_grain -> store_grain when drying is needed. Do not store wet grain directly. Do not wait for unnecessary 8%-10% moisture.
- Success check: harvested ridges, dried grain if needed, no store_grain warning, and non-empty stored/recovered grain.
```

## Why This Is Not The Same As Detail=True

The library gives structured skills, triggers, constraints, and action patterns. It does not narrate the entire L3 answer as one continuous expert plan. The agent must still decide when each skill applies based on tool observations.

For this same-L3 pilot, some parameters are source-specific. That is acceptable for pipeline validation. For fair cross-L3 evaluation, remove exact source-L3 ridges, dates, and wait lengths, or convert them into ranges and trigger rules.

## Minimal Runner Approach Without Code Changes

No scenario code change is needed for the first pilot. Use whichever L3 evaluation runner already lets you choose the agent prompt/detail text, and set:

```text
scenario_id = scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery
detail = false
extra_context = contents of the Library Block To Append
```

If the current runner cannot append `extra_context`, create a separate wrapper/prompt assembly script later. That wrapper should be new code only; it should not modify the existing L3 scenario.

## First Metrics To Compare

Compare against base and `detail=True` using:

- completed event sequence and failed/error tool returns
- biological yield
- recovered yield for harvest/postharvest
- path metric / full-path score
- whether planting density, nutrient treatment, weed treatment, and harvest/dry/store sequence match the intended skill logic
