# Same-L3 Knowledge Library Pilot: Fertilizer Quota Edge Low-Fertility

## Purpose

This is a mechanism pilot for `detail=library` on:

`scenario_full_season_hb_fertilizer_quota_edge_lowfertility`

It uses L2 skills extracted from the same L3. This is not a fair cross-L3 evaluation because the skills may include source-L3-specific timing, ranges, and parameters.

## L2 Coverage

| Skill | Source L2 | Coverage |
|---|---|---|
| Severe edge nutrient recovery | `scenario_l2_hb_fertilizer_quota_severe_edge_nutrient_recovery` | Diagnose severe edge low-fertility stress and apply targeted fertigation only when nutrient evidence and quota support it |
| Severe edge gap replant | `scenario_l2_hb_fertilizer_quota_severe_edge_gap_replant` | Diagnose stand gaps after emergence and replant the confirmed severe edge gap |
| Mild edge quota topup | `scenario_l2_hb_fertilizer_quota_mild_edge_topup` | Recheck R1 mild edge nutrition and use remaining quota for a smaller targeted fertigation |
| Harvest dry/store | `scenario_l2_hb_fertilizer_quota_harvest_dry_store` | Wait for maturity, weather, trafficability, grain moisture, and capacity; harvest, unload, dry if needed, then store |

## Skill Cards

### 1. Severe Edge Nutrient Recovery

Belief: localized edge weakness should be treated as nutrient stress only after soil/canopy/field overview, drone localization, target-vs-reference state, and ground crop-health evidence support nutrient limitation rather than water, weed, disease, or insect pressure.

Core action template:

```text
weather -> forecast -> soil -> canopy -> overview
drone survey -> target state -> reference state -> robot crop-health check
wait/recheck weather, target state, inventory, quota
apply targeted fertigation to the confirmed severe edge block
recheck target state and remaining quota
```

### 2. Severe Edge Gap Replant

Belief: stand gaps are not fixed by fertilizer. If emergence/stand inspection confirms missing plants in the edge block, use a replant action rather than nutrient or chemical treatment.

Core action template:

```text
weather -> forecast -> soil -> overview -> drone/ground stand check
wait to replant window -> recheck soil/weather/stand and seed inventory
replant the confirmed severe edge gap
commit/recheck establishment
```

### 3. Mild Edge Quota Topup

Belief: after the severe edge actions, a later mild edge topup is justified only if R1 evidence still supports nutrient stress and quota remains.

Core action template:

```text
weather -> forecast -> soil -> canopy -> overview
target state -> reference state -> ground confirmation
wait/recheck action window and remaining quota
apply a smaller targeted fertigation to the confirmed mild edge block
recheck target state and quota
```

### 4. Harvest Dry/Store

Belief: harvest is a window decision, not a fixed date. Check maturity/harvest_allowed, weather, trafficability, grain moisture, and capacity. If grain moisture is above direct-storage moisture but within the dryable range, harvest then dry before storage.

Core action template:

```text
weather -> forecast -> soil -> maturity/moisture -> inventory/capacity
wait and recheck until legal harvest window
harvest in 4-ridge blocks, unload during harvest
dry_grain(target_moisture_pct=13.0) if needed
store_grain and recheck inventory
```

## Use

For `detail=library_same_l3`, use the normal L3 `detail=False` prompt plus these skills. Do not also provide the normal human `detail=True` prompt.
