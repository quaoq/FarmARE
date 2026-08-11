# Prompt Template: Two Dry Patches, One Irrigation Library

Append this block to the normal `detail=False` prompt for `scenario_full_season_hb_two_dry_patches_one_irrigation`.

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support.

Skill 1: Standard-density HEINONG84 planting
- Evidence: current weather; forecast; soil sensors; inventory; tractor status.
- Action pattern: complete field prep, base fertilizer, and 1.1 m ridge formation; plant HEINONG84 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9; commit/recheck.
- Constraint: do not plant before prep is complete.

Skill 2: Two dry patches, one irrigation
- Belief: If two dry patches appear and water is limited, choose the confirmed priority patch rather than treating every low-NDVI area.
- Evidence: weather; forecast; soil moisture; canopy/thermal signal; drone survey; target patch states; reference state; ground check; water budget.
- Action pattern: wait/recheck action window and water budget; irrigate the confirmed priority dry patch; recheck response.
- Constraints: do not diagnose drought from NDVI alone; do not use irrigation for weed, disease, insect, or nutrient stress.

Skill 3: Harvest dry/store
- Evidence: weather; forecast; soil trafficability; maturity/R8 or harvest_allowed; grain moisture; capacity; drydown rechecks.
- Action pattern: harvest in 4-ridge blocks when legal; unload during harvest; dry if moisture requires it; store and recheck inventory.
- Constraint: do not store wet grain directly.
```
