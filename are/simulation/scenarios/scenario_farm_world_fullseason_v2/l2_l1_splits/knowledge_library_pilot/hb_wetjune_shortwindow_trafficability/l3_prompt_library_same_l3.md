# Prompt Template: Wet June Short Spray Window Library

Append this block to the normal `detail=False` prompt for `scenario_full_season_hb_wetjune_shortwindow_trafficability`.

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support.

Skill 1: HEINONG84 standard-density planting
- Evidence: weather; forecast; soil sensors; inventory; tractor status.
- Action pattern: field prep, base fertilizer, 1.1 m ridges, HEINONG84 planting in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9, then commit/recheck.

Skill 2: Wet June fungicide window
- Belief: Wet canopy/disease risk needs confirmation and a workable spray window.
- Evidence: weather; forecast; soil trafficability; canopy sensors; farm overview; drone localization; target and reference state; robot crop-health check; inventory/sprayer status.
- Action pattern: wait/recheck the short spray window; load fungicide; apply targeted fungicide to the confirmed disease block; recheck disease state.
- Constraints: do not use NDVI alone; do not use fungicide for weeds, insects, nutrients, or water stress.

Skill 3: R8 harvest dry/store
- Evidence: weather; forecast; soil trafficability; maturity/harvest_allowed; grain moisture; capacity; drydown rechecks.
- Action pattern: harvest when legal in 4-ridge blocks; unload; dry if moisture requires it; store and recheck.
- Constraint: do not store wet grain directly.
```
