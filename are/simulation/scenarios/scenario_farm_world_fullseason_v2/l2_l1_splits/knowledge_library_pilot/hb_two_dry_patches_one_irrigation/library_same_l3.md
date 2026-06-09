# Knowledge Library Pilot: hb_two_dry_patches_one_irrigation

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_two_dry_patches_one_irrigation`.
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

### 1. standard_density_planting

- source_l2: `scenario_l2_hb_two_dry_patches_standard_planting`
- farming_group: `establishment`
- task_type: `planting`; crop_stage: ``
- belief: If the HEINONG84 field is unplanted, check weather, soil, resources, and tractor state before prep and planting.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - inventory
  - tractor status
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>inventory>tractor_status>attach(grader)>TractorApp.level>detach>load_fertilizer(kg=360)>base_fertilize>attach(furrower)>form_ridges(width=1.1)>detach"
  },
  {
    "seq": "plant_loop",
    "ridge_range": "0-63",
    "block_size": 4,
    "sequence": [
      "load_seeds(seed_type=HEINONG84,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4,spacing=7.9)"
    ]
  },
  {
    "seq": "ordered",
    "tools": "commit_physics>overview"
  }
]
```

- constraints:
  - do not plant before field prep
  - use the source scenario standard-density planting parameters for same-L3 pilot
- success_checks:
  - 64 planted ridges
  - correct seed/depth/spacing visible

### 2. two_patch_irrigation_priority

- source_l2: `scenario_l2_hb_two_dry_patches_irrigation_priority`
- farming_group: `management`
- task_type: `irrigation`; crop_stage: ``
- belief: With limited water, confirm and prioritize the dry patch with stronger water-stress and yield-risk evidence rather than irrigating every low-NDVI area.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - canopy sensors
  - drone survey
  - target patch states
  - reference state
  - ground check
  - water budget
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>overview>ridge_state(42-53)>ridge_state(24-35)>mavic_survey(42-53)>Matrice300.fly_survey(start=42,end=53)>robot0_crop_check(start=42,end=53)>advance(2d)>weather_now>ridge_state(42-53)>inventory>FieldOpsApp.irrigate(start=42,end=53,hours=1.25)>advance(6h)>ridge_state(42-53)"
  }
]
```

- constraints:
  - do not use irrigation for nutrient, weed, disease, or insect stress
  - do not use NDVI alone
- success_checks:
  - target water stress improves or stabilizes
  - water budget use is consistent

### 3. harvest_dry_store

- source_l2: `scenario_l2_hb_two_dry_patches_harvest_dry_store`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: ``
- belief: Wait/recheck maturity, grain moisture, weather, soil trafficability, and capacity before harvest.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - whole-field maturity/moisture
  - inventory/capacity
  - drydown rechecks
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>ridge_state(0-63)>inventory>advance(38d)>weather_now>soil_sensors>ridge_state(0-63)>inventory"
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
  - do not harvest before R8/harvest_allowed
- success_checks:
  - 64 ridges harvested
  - no store warning
  - stored grain non-empty
