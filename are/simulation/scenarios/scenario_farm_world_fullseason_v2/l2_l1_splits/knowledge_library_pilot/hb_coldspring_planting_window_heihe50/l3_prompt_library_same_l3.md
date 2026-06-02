# Prompt Template: Feed Same-L3 Library To The L3 Agent

## Target Scenario

`scenario_full_season_hb_coldspring_planting_window_heihe50`

## Experimental Condition

`detail=library_same_l3`

Use the normal L3 `detail=False` task prompt as the base prompt, then append the library block below. Do not also provide the normal `detail=True` prompt in this condition.

## Library Block To Append

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support: first collect the listed evidence, then decide whether the skill applies, then perform the action only if the current state supports it.

Skill 1: HEIHE50 cold-spring field prep
- Belief: If the field is unprepared and unplanted, confirm weather, forecast, seedbed soil, inventory, fuel, and tractor status. Complete field prep before planting.
- Evidence: current weather; 5-day forecast; soil sensors; inventory; tractor status; post-prep status recheck.
- Action pattern: attach grader; level field; load 360 kg fertilizer; base_fertilize; form ridges at ridge_width_m=1.1; commit daily physics; recheck.
- Constraints: do not plant in this field-prep-only skill. Do not skip base fertilizer before ridge formation.
- Success check: field prep complete; field remains unplanted; fertilizer/fuel use is consistent.

Skill 2: HEIHE50 cold-spring planting window
- Belief: The cold 2026-05-05 start is a window-finding task, not an immediate planting task. Complete prep, wait through cold seedbed risk, recheck, wait to the planting window, and plant only when current conditions support it.
- Evidence: current weather; 5-day forecast; seedbed soil; inventory; tractor status; weather/soil after cold wait; weather/forecast/soil/planter status at planting window.
- Action pattern: complete field prep; wait 8 days; recheck weather and soil; wait 3 days; recheck weather, 3-day forecast, soil, and planter; load HEIHE50 seed; plant ridges 0-63 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=8.2; commit and recheck.
- Constraints: do not plant on the initial cold start without waiting and rechecking. Do not plant before prep/base fertilizer/ridge forming.
- Success check: all 64 ridges planted; post-planting overview confirms planted status.

Skill 3: HEIHE50 cold-spring emergence scouting
- Belief: After cold-window planting, low early canopy or non-emergence is not automatically fertilizer, weed, disease, or insect pressure. Wait through the cold/rain risk and emergence window, then interpret remote sensing with ground checks.
- Evidence: post-plant weather; 4-day forecast; soil sensors; farm overview; weather/soil after cold-rain wait; emergence-window overview; canopy sensors; drone NDVI survey; robot emergence check; whole-field range state.
- Action pattern: wait 4 days through cold/rain risk; recheck weather/soil; wait 10 days to emergence scouting; overview and canopy check; charge drone and fly whole-field survey; charge robot and inspect emergence; confirm whole-field status.
- Constraints: do not replant, fertilize, spray, or irrigate without ground-confirmed evidence. Do not infer weed/disease/insect pressure from low early canopy alone.
- Success check: emergence and stand are documented; no unsupported treatment action is taken.

Skill 4: HEIHE50 R8 drydown and direct-storage harvest
- Belief: At R8 start, harvest timing is still a decision. Check moisture, weather, trafficability, and storage capacity. If moisture is high, wait and recheck. Harvest only when the postharvest path is legal.
- Evidence: current weather; 3-day forecast; soil sensors; whole-field grain moisture/range state; repeated drydown rechecks; inventory/storage capacity.
- Action pattern: from R8 start, wait 5 days and recheck moisture; wait 5 days and recheck weather/moisture; wait 6 days to direct-store window; recheck weather, forecast, soil, moisture, and inventory; harvest ridges 0-63 in 4-ridge blocks; unload after each block; store directly only because grain moisture is <=13.5%.
- Constraints: harvest requires R8 or harvest_allowed, suitable weather, trafficable soil, known moisture, and capacity. Use harvest -> unload -> store only for <=13.5% grain. If moisture is 13.5%-18%, dry before storing. Do not wait for unnecessary 8%-10% moisture.
- Success check: all 64 ridges harvested; grain is unloaded before storage; store_grain has no warning; recovered/stored grain is non-empty.
```

## Why This Is Not The Same As Detail=True

The library gives structured skills, triggers, constraints, and action patterns. It does not narrate the entire L3 answer as one continuous expert plan. The agent must still decide when each skill applies based on tool observations.

For this same-L3 pilot, some parameters are source-specific. That is acceptable for pipeline validation. For fair cross-L3 evaluation, remove exact source-L3 dates, waits, ridge ranges, and parameters, or convert them into ranges and trigger rules.

