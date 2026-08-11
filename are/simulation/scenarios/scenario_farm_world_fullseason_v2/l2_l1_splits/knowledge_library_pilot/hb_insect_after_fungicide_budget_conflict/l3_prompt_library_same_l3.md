# Prompt Template: Insect After Fungicide Budget Conflict Library

Append this block to the normal `detail=False` prompt for `scenario_full_season_hb_insect_after_fungicide_budget_conflict`.

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support: collect evidence, decide whether the skill applies, then act only if the current state supports it.

Skill 1: HEINONG84 standard-density establishment
- Belief: This same-L3 source starts as an unplanted HEINONG84 standard-density field. Establishment requires checking current weather, 3-day forecast, seedbed soil, inventory, and tractor status before field prep and planting.
- Evidence: current weather; 3-day forecast; seedbed soil sensors; inventory; tractor status; planting-window weather/forecast/soil recheck; post-planting farm overview.
- Action pattern: level field; load 360 kg base fertilizer; base_fertilize; form ridges at ridge_width_m=1.1; recheck planting weather, forecast, soil, and tractor; load HEINONG84 seed; plant ridges 0-63 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9; commit planting physics; recheck planted field.
- Constraints: do not plant before field prep, base fertilizer, and ridge formation. The exact spacing/depth/ridge-width values are source-specific same-L3 pilot context.
- Success check: all 64 ridges are planted and inventory/seed/fuel use is consistent.

Skill 2: Budgeted disease control
- Belief: Wet-season disease pressure requires an evidence chain before fungicide.
- Evidence: current weather; forecast; soil and canopy sensors; farm overview; drone survey; target and reference state; robot crop-health check; inventory and sprayer status.
- Action pattern: wait/recheck the spray window; load fungicide; apply targeted fungicide to the confirmed disease block; recheck disease state and budget.
- Constraints: do not use fungicide for insect, weed, nutrient, or water stress; respect spray weather and budget.
- Success check: disease pressure is reduced or stabilized and input use is consistent.

Skill 3: R5 manual insect control
- Belief: Insect pressure after fungicide is a separate diagnosis. If mechanical spraying is unsuitable in the current wet/budget context, manual insecticide can be used after confirmation.
- Evidence: R5 overview; soil/canopy sensors; drone localization; target state; robot insect/leaf-damage check; weather; budget and inventory.
- Action pattern: recheck readiness; apply manual insecticide to the confirmed insect block; recheck insect pressure and budget.
- Constraints: do not treat insects with fungicide; do not spray without insect evidence.
- Success check: insect pressure is reduced or stabilized and remaining budget is tracked.

Skill 4: R8 harvest/store
- Belief: R8 begins a harvest window, not an automatic harvest date.
- Evidence: weather; 3-day forecast; soil trafficability; whole-field maturity and grain moisture; inventory/storage capacity; drydown rechecks.
- Action pattern: wait/recheck until harvest and storage are legal; harvest in 4-ridge blocks; unload during harvest; direct store only when moisture is safe, otherwise dry first.
- Constraints: preserve harvest -> unload -> store for safe direct-storage grain, or harvest -> unload -> dry -> store for wet-but-dryable grain.
- Success check: harvested ridges, non-empty stored/recovered grain, and no store warning.
```
