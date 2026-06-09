# Knowledge Library Pilot: hb_storage_capacity_limit_batching

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_storage_capacity_limit_batching`.
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

- source_l2: `scenario_l2_hb_storage_capacity_standard_planting`
- farming_group: `establishment`
- task_type: `planting`; crop_stage: ``
- belief: Establish the HEINONG84 field only after weather, soil, inventory, and tractor checks.
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
  - do not plant before prep
- success_checks:
  - 64 planted ridges

### 2. capacity_aware_first_batch

- source_l2: `scenario_l2_hb_storage_capacity_harvest_batching`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: ``
- belief: When storage/dryer capacity matters, harvest in batches after checking maturity, moisture, weather, trafficability, and available capacity.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - maturity/grain moisture
  - inventory/capacity
  - drydown rechecks
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>ridge_state(0-63)>inventory>advance(9d)>soil_sensors>ridge_state(0-31)>inventory"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "0-31",
    "block_size": 4,
    "sequence": [
      "harvest(start=$block_start,end=$block_end)",
      "unload_grain"
    ]
  },
  {
    "seq": "ordered",
    "tools": "dry_grain(target=13)>store_grain>inventory>ridge_state(32-63)"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "32-63",
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
  - do not continue without capacity check
- success_checks:
  - first batch stored
  - capacity updated

### 3. remaining_batch_after_west

- source_l2: `scenario_l2_hb_storage_capacity_east_batch_after_west`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: ``
- belief: After one batch is stored, the remaining batch needs its own readiness and capacity check.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - remaining batch maturity/moisture
  - inventory/capacity
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>ridge_state(32-63)>inventory"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "32-63",
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
  - do not assume the remaining batch is safe without recheck
- success_checks:
  - remaining ridges harvested
  - no store warning
