# Prompt Template: Feed Same-L3 Library To The L3 Agent

## Target Scenario

`scenario_full_season_hb_wetcold_high_residue_establishment`

## Experimental Condition

`detail=library_same_l3`

Use the normal L3 `detail=False` task prompt as the base prompt, then append the library block below. Do not also provide the normal `detail=True` prompt.

## Library Block To Append

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support: first collect the listed evidence, then decide whether the skill applies, then perform the action only if the current state supports it.

Skill 1: Wet-cold high-residue field prep
- Belief: If the field is unprepared and unplanted, confirm weather, forecast, seedbed soil, inventory, and tractor status. Complete field prep before planting.
- Evidence: current weather; 5-day forecast; soil sensors; inventory; tractor status.
- Action pattern: attach grader; level; load 360 kg fertilizer; base_fertilize; form 1.1 m ridges; commit and recheck.
- Constraints: do not plant in this field-prep-only step.
- Success check: field prep complete and field remains unplanted.

Skill 2: Wet-cold seedbed planting window
- Belief: Wet-cold high-residue seedbeds should not be planted immediately. Wait and recheck before planting.
- Evidence: current weather; 5-day forecast; soil sensors; wait; current weather; 3-day forecast; soil sensors; planter status.
- Action pattern: complete field prep; wait 3 days; recheck; load HEINONG84 seed; plant ridges 0-63 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9; commit and recheck.
- Constraints: do not plant on the wet-cold start without waiting and rechecking.
- Success check: all 64 ridges are planted.

Skill 3: Slow-emergence replant recovery
- Belief: Low early canopy in this scenario is a stand/emergence problem, not automatically fertilizer, weed, disease, or insect pressure. Confirm with drone and ground checks before replanting.
- Evidence: weather; forecast; wait 8 days; soil; canopy; range state 0-15; drone survey 0-15; robot emergence check 0-15.
- Action pattern: replant ridges 0-15 in 4-ridge blocks using HEINONG84 at depth_cm=4.0 and spacing_cm=7.9.
- Constraints: do not expand into the reference zone. Replanting 0-15 delays maturity, so harvest must be staged later.
- Success check: ridges 0-15 show improved stand fraction.

Skill 4: Staged harvest after replanting
- Belief: At R8, ridges 16-63 and replanted ridges 0-15 have different grain moisture. Do not harvest the whole field on the same day unless both batches are legal.
- Evidence: weather; forecast; soil trafficability; range state/maturity/moisture for 16-63; inventory/capacity; wait; range state/maturity/moisture for 0-15.
- Action pattern: harvest 16-63 -> unload -> store; wait 18 days; harvest 0-15 -> unload -> store.
- Constraints: direct store only for <=13.5% grain. For 13.5%-18%, dry before store. Do not harvest >18% grain.
- Success check: 64 ridges harvested, both batches stored without drying because grain moisture is <=13.5%, and store_grain has no warning.
```

## Why This Is Not The Same As Detail=True

The library gives structured skills, triggers, constraints, and action patterns. It does not narrate the whole L3 answer as one continuous expert plan. The agent must still decide when each skill applies based on tool observations.
