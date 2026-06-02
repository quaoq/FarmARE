# Prompt Template: Storage Capacity Limit Batching Library

Append this block to the normal `detail=False` prompt for `scenario_full_season_hb_storage_capacity_limit_batching`.

```text
You may use the following structured Knowledge Library. These are reusable task skills distilled from prior L2 practice tasks. They are not a complete action script. Use them as decision support.

Skill 1: Standard HEINONG84 planting
- Evidence: weather; forecast; soil sensors; inventory; tractor status.
- Action pattern: field prep, base fertilizer, 1.1 m ridges, plant HEINONG84 in 4-ridge blocks at depth_cm=4.0 and seed_spacing_cm=7.9, then commit/recheck.

Skill 2: Capacity-aware first harvest batch
- Belief: Storage/dryer constraints make harvest a batching decision.
- Evidence: weather; forecast; soil trafficability; maturity; grain moisture; dryer/storage capacity; drydown rechecks.
- Action pattern: after a legal window, harvest a first batch in 4-ridge blocks, unload, dry if needed, store, and recheck capacity.
- Constraints: do not store wet grain directly; do not continue without capacity evidence.

Skill 3: Remaining batch after first storage
- Belief: The remaining batch still needs its own readiness check after the first batch changes inventory/capacity.
- Evidence: weather; forecast; soil; remaining-batch maturity and grain moisture; current capacity.
- Action pattern: harvest the remaining batch in 4-ridge blocks, unload, dry if needed, store, and recheck inventory.
```
