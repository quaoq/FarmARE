# Prompt Template: Feed Same-L3 Library To The L3 Agent

## Target Scenario

`scenario_full_season_heinong84_staggered_planting`

## Experimental Condition

`detail=library_same_l3`

Use the normal L3 `detail=False` task prompt as the base prompt, then append the library block below. Do not also provide the normal `detail=True` prompt in this condition.

## Library Block To Append

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support: first collect the listed evidence, then decide whether the skill applies, then perform the action only if the current state supports it.

Skill 1: HEINONG84 field prep and early-zone planting
- Belief: If the field is unprepared and unplanted, confirm weather, forecast, seedbed soil, inventory, fuel, and tractor status. Complete field prep before planting. In this staggered planting task, plant only the early zone in the first window.
- Evidence: current weather; 5-day forecast; soil sensors; inventory; tractor status; rechecked weather/forecast/soil before planting.
- Action pattern: attach grader; level field; load 250 kg fertilizer; base_fertilize; form ridges at ridge_width_m=1.1; commit daily physics; load HEINONG84 seed; plant ridges 0-20 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9; recheck early zone.
- Constraints: do not plant before prep/base fertilizer/ridge forming. Do not plant mid or late zones in the early window.
- Success check: ridges 0-20 planted; ridges 21-63 not planted yet; seed/fertilizer/fuel use is consistent.

Skill 2: HEINONG84 mid-zone planting window
- Belief: After early-zone planting, the mid zone is a window-finding task, not an immediate continuation. Wait for the mid window and recheck field conditions before planting.
- Evidence: time wait to the mid planting window; current weather; 3-day forecast; soil sensors; inventory; tractor status.
- Action pattern: wait 7 days after early-zone planting; recheck conditions; load HEINONG84 seed; plant ridges 21-42 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9; commit and recheck mid zone.
- Constraints: do not modify early ridges. Do not plant late ridges before the late window.
- Success check: ridges 21-42 planted; ridges 43-63 remain unplanted.

Skill 3: HEINONG84 late-zone seedbed irrigation and planting
- Belief: After mid-zone planting, wait for the late window. If seedbed evidence supports moisture support, irrigate only the late zone, wait for infiltration, and recheck before planting.
- Evidence: time wait to the late planting window; current weather; 3-day forecast; soil sensors; post-irrigation soil recheck; inventory; tractor status.
- Action pattern: wait 7 days after mid-zone planting; recheck conditions; irrigate ridges 43-63 for 1.2 hours; wait 6 hours; recheck soil; load HEINONG84 seed; plant ridges 43-63 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9; commit and recheck late zone.
- Constraints: do not irrigate the whole field when the late seedbed is the target. Do not plant before soil recheck after infiltration.
- Success check: ridges 43-63 planted and all 64 ridges are planted.

Skill 4: HEINONG84 post-planting stage scouting
- Belief: After all zones are planted, manage the field by observing emergence and later stage split separately by zone. Do not treat the field unless evidence supports a stressor.
- Evidence: farm overview; soil sensors; canopy sensors; robot emergence checks by zone; drone NDVI; later early/mid/late range-state checks; robot crop-health checks by zone.
- Action pattern: wait 16 days after late planting; perform emergence overview/soil/canopy/robot/drone checks; charge drone/robot; wait 42 days; perform stage-split overview, early/mid/late range checks, soil/canopy/drone, and robot health checks.
- Constraints: do not collapse the staggered zones into one uniform stage. Do not apply fertilizer, pesticide, herbicide, fungicide, or irrigation without evidence.
- Success check: emergence and stage split are documented by zone, and no unsupported treatment action is taken.

Skill 5: HEINONG84 staggered harvest sequence
- Belief: At R8/harvest-window beginning, harvest timing is a decision. Check each zone separately for R8/harvest_allowed, weather, trafficability, grain moisture, and capacity. Harvest in early/mid/late batches only when current evidence supports it.
- Evidence: drydown waits; current weather; 3-day forecast; soil sensors; farm overview; target zone range state with moisture/harvestability; inventory/capacity.
- Action pattern: wait 10 days then recheck and harvest/store ridges 0-20; wait 7 days then recheck and harvest/store ridges 21-42; wait 10 days then recheck and harvest/store ridges 43-63. For each batch: attach harvester if needed; harvest in 4-ridge blocks; unload after each block; dry_grain(target_moisture_pct=13.0); store_grain.
- Constraints: preserve harvest -> unload -> dry_grain -> store_grain when drying is needed. Do not store wet grain directly. Do not wait for unnecessary 8%-10% moisture.
- Success check: each batch has recovered/stored grain, store_grain has no warning, and all 64 ridges are harvested by the end.
```

## Why This Is Not The Same As Detail=True

The library gives structured skills, triggers, constraints, and action patterns. It does not narrate the entire L3 answer as one continuous expert plan. The agent must still decide when each skill applies based on tool observations.

For this same-L3 pilot, some parameters are source-specific. That is acceptable for pipeline validation. For fair cross-L3 evaluation, remove exact source-L3 ridges, dates, and wait lengths, or convert them into ranges and trigger rules.

