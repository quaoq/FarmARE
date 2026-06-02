# Same-L3 Knowledge Library Pilot: Storage Capacity Limit Batching

## Purpose

This library supports `detail=library` for `scenario_full_season_hb_storage_capacity_limit_batching`.

## L2 Coverage

| Skill | Source L2 | Coverage |
|---|---|---|
| Standard planting | `scenario_l2_hb_storage_capacity_standard_planting` | Normal HEINONG84 standard-density establishment |
| Harvest capacity batching | `scenario_l2_hb_storage_capacity_harvest_batching` | R8 drydown and capacity-aware first batch harvest/dry/store |
| East batch after west | `scenario_l2_hb_storage_capacity_east_batch_after_west` | Continue from post-west checkpoint, verify remaining east batch, harvest/dry/store |

## Skill Cards

### 1. Standard Planting

```text
weather -> forecast -> soil -> inventory -> tractor status
field prep -> base fertilizer -> 1.1 m ridges
plant HEINONG84 in 4-ridge blocks, depth_cm=4.0, seed_spacing_cm=7.9
commit/recheck
```

### 2. Capacity-Aware First Batch

Belief: storage/dryer constraints require batch decisions. At R8, check grain moisture, weather, trafficability, dryer/storage capacity, and expected recovered mass before choosing a batch.

Core action template:

```text
weather -> forecast -> soil -> whole-field maturity/moisture -> inventory/capacity
wait/recheck drydown
harvest first batch in 4-ridge blocks
unload -> dry_grain(target_moisture_pct=13.0) -> store_grain
recheck capacity before continuing
```

### 3. Remaining Batch After First Storage

Belief: after the first batch is stored, the remaining batch still needs its own weather, moisture, and capacity check. Do not assume the rest is automatically safe.

Core action template:

```text
weather -> forecast -> soil -> remaining-batch state -> inventory/capacity
harvest remaining batch in 4-ridge blocks
unload -> dry if needed -> store -> recheck
```
