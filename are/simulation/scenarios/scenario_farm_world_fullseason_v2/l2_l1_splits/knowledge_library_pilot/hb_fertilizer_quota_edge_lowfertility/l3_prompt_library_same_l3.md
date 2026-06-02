# Prompt Template: Fertilizer Quota Edge Low-Fertility Library

Append this block to the normal `detail=False` prompt for `scenario_full_season_hb_fertilizer_quota_edge_lowfertility`. Do not also provide the normal `detail=True` prompt.

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support: collect evidence, decide whether the skill applies, then act only if the current state supports it.

Skill 1: Severe edge nutrient recovery
- Belief: Localized severe edge weakness should be treated as nutrient stress only after soil, canopy, drone, target-vs-reference, and ground evidence support nutrient limitation rather than water, weed, disease, or insect pressure.
- Evidence: current weather; forecast; soil sensors; canopy sensors; farm overview; drone survey; target block state; reference block state; ground crop-health check; inventory and quota.
- Action pattern: wait for a suitable fertigation window; recheck weather, target state, inventory, and quota; apply targeted fertigation to the confirmed severe edge block; recheck state and quota.
- Constraints: do not use fertilizer to solve weed, disease, insect, or pure water stress.
- Success check: target nutrient stress improves or stabilizes, and quota/inventory use is consistent.

Skill 2: Severe edge gap replant
- Belief: Stand gaps are not fertilizer problems. If emergence and ground checks confirm missing plants in the edge block, use replanting.
- Evidence: weather; forecast; soil sensors; farm overview; drone stand signal; ground stand check; seed inventory.
- Action pattern: wait/recheck the replant window; replant the confirmed gap; commit and recheck establishment.
- Constraints: do not replant without stand evidence; do not replace stand repair with fertilizer.
- Success check: replanted stand is visible and seed use is consistent.

Skill 3: Mild edge quota topup
- Belief: A later mild-edge topup is justified only if R1 evidence still supports nutrient stress and quota remains.
- Evidence: weather; forecast; soil and canopy sensors; target and reference state; ground confirmation; remaining quota and inventory.
- Action pattern: wait/recheck the window; apply a smaller targeted fertigation to the confirmed mild edge block; recheck state and quota.
- Constraints: do not spend quota without nutrient evidence.
- Success check: target stress improves or stabilizes and quota accounting remains valid.

Skill 4: Harvest dry/store
- Belief: Harvest is a window decision. Check maturity/harvest_allowed, weather, trafficability, grain moisture, and capacity before harvest.
- Evidence: current weather; 3-day forecast; soil sensors; maturity and grain moisture; storage/dryer capacity; drydown rechecks.
- Action pattern: wait/recheck until harvest is legal; harvest in 4-ridge blocks; unload during harvest; dry grain if moisture requires it; store and recheck inventory.
- Constraints: do not store wet grain directly; preserve harvest -> unload -> dry if needed -> store.
- Success check: harvested ridges, no store warning, and non-empty stored/recovered grain.
```
