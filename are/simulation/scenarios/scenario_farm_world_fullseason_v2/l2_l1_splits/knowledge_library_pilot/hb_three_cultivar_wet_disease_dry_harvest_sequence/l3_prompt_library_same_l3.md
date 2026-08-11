# Prompt Template: Three Cultivar Library

Append this block to the normal `detail=False` prompt for `scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence`.

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support.

Skill 1: Three-cultivar planting
- Evidence: weather; forecast; soil; inventory; tractor status; visible zone seed plan.
- Action pattern: common field prep, base fertilizer, 1.1 m ridges, then plant each cultivar zone with its planned seed and spacing; commit and recheck.
- Constraints: do not mix cultivar seed plans; do not plant before prep.

Skill 2: HN84 wet-zone disease control
- Evidence: weather; forecast; soil/canopy context; drone localization; HN84 zone state; reference zone state; robot crop-health check; inventory/sprayer status.
- Action pattern: wait/recheck spray window; apply targeted fungicide to the confirmed disease zone; recheck disease pressure.
- Constraints: do not use fungicide for water, weed, insect, or nutrient stress.

Skill 3: HN58 dry-zone water management
- Evidence: weather; forecast; soil moisture; canopy/thermal signal; drone localization; HN58 dry-zone state; reference state; ground check; water budget.
- Action pattern: wait/recheck irrigation window and budget; irrigate the confirmed dry zone; recheck response.
- Constraints: do not irrigate disease, weed, insect, or nutrient stress.

Skill 4: Zone harvest sequence
- Evidence: weather; forecast; soil trafficability; each zone's maturity/harvest_allowed and grain moisture; dryer/storage capacity; drydown rechecks.
- Action pattern: harvest ready zones in 4-ridge blocks; unload; dry if moisture requires it; store and recheck inventory.
- Constraints: do not harvest unready zones; do not store wet grain directly.
```
