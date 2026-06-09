# Knowledge Library Pilot: hb_fertilizer_quota_edge_lowfertility

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_fertilizer_quota_edge_lowfertility`.
It is generated from the source L2 oracle workflows and is intended for `detail=library` prompt rendering.

## L2 Sufficiency

- status: ``

## Compression Rules

- `oracle_events` is a compact prompt template, not an executable replacement for the Python oracle.
- `seq` entries preserve the source oracle action order; shortened tool names are prompt notation only.
- `plant_loop` and `harvest_loop` expand to repeated contiguous ridge blocks; intermediate actions such as `load_seeds` and `unload_grain` remain explicit in `sequence`.
- `source_wait_days` records the source oracle wait length; it is evidence from the source workflow, not a universal maximum wait for other L3 targets.
- Do not hide intermediate actions inside fake tool arguments such as `paired_after_each_block`.

## Skill Cards

### 1. severe_edge_nutrient_recovery

- source_l2: `scenario_l2_hb_fertilizer_quota_severe_edge_nutrient_recovery`
- farming_group: `management`
- task_type: `fertigation`; crop_stage: ``
- belief: Treat severe edge weakness as nutrient stress only after soil, canopy, drone, target-vs-reference, and ground evidence support nutrient limitation.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - canopy sensors
  - farm overview
  - drone survey
  - target state
  - reference state
  - ground crop-health check
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>overview>mavic_survey(0-63)>ridge_state(0-7)>ridge_state(20-31)>robot0_crop_check(start=0,end=7)>advance(3d)>weather_now>ridge_state(0-7)>inventory>fertigation(start=0,end=7,nutrient=0.32,water_mm=1.2)>ridge_state(0-7)>inventory"
  }
]
```

- constraints:
  - do not use fertigation for weed, disease, insect, or pure water stress
  - respect fertilizer quota and inventory
- success_checks:
  - target nutrient stress improves or stabilizes
  - quota/inventory use is consistent

### 2. severe_edge_gap_replant

- source_l2: `scenario_l2_hb_fertilizer_quota_severe_edge_gap_replant`
- farming_group: `management`
- task_type: `replant`; crop_stage: ``
- belief: Confirmed stand gaps require replanting rather than fertilizer or spray treatment.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - farm overview
  - drone stand signal
  - ground stand check
  - seed inventory
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>advance(6d)>weather_now>soil_sensors>canopy_sensors>mavic_survey(0-7)>ridge_state(0-7)>robot0_emergence(0-3)>inventory>tractor_status>load_seeds(seed_type=HEINONG84,count=30000)>replant_seeds(start=0,end=3,depth=4,spacing_cm=7.9,full_replant=False)>ridge_state(0-3)"
  }
]
```

- constraints:
  - do not replant without stand evidence
  - do not replace replant with fertilizer
- success_checks:
  - stand gap is replanted
  - seed use and planted status are visible

### 3. mild_edge_quota_topup

- source_l2: `scenario_l2_hb_fertilizer_quota_mild_edge_topup`
- farming_group: `management`
- task_type: `fertigation`; crop_stage: ``
- belief: Use remaining quota for a later mild-edge topup only if R1 evidence still supports nutrient stress.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - canopy sensors
  - target state
  - reference state
  - ground check
  - inventory/quota
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>overview>inventory>advance(4d)>weather_now>mavic_survey(0-63)>ridge_state(8-15)>ridge_state(20-31)>robot0_crop_check(start=8,end=15)>ridge_state(8-15)>inventory>fertigation(start=8,end=15,nutrient=0.16,water_mm=1)>ridge_state(8-15)>inventory"
  }
]
```

- constraints:
  - do not spend quota without nutrient evidence
  - do not treat unrelated stress with fertilizer
- success_checks:
  - mild edge nutrient stress improves or stabilizes
  - remaining quota is tracked

### 4. harvest_dry_store

- source_l2: `scenario_l2_hb_fertilizer_quota_harvest_dry_store`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: ``
- belief: Harvest requires maturity/harvest_allowed, workable weather/soil, known grain moisture, and capacity.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - maturity and grain moisture
  - inventory and capacity
  - drydown rechecks
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>ridge_state(0-63)>inventory>advance(4d)>ridge_state(0-63)>advance(4d)>ridge_state(0-63)>advance(5d)>weather_now>soil_sensors>ridge_state(0-63)>inventory"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "0-63",
    "block_size": 4,
    "sequence": [
      "harvest(start=$block_start,end=$block_end)",
      "unload_grain"
    ]
  },
  {
    "seq": "ordered",
    "tools": "dry_grain(target=13)>store_grain>inventory"
  }
]
```

- constraints:
  - do not store wet grain directly
  - preserve harvest>unload>dry if needed>store
- success_checks:
  - harvested ridges
  - no store warning
  - stored/recovered grain is non-empty
