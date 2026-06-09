# Knowledge Library Pilot: hb_wetjune_disease_recheck_after_fungicide

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_wetjune_disease_recheck_after_fungicide`.
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

### 1. establishment_planting

- source_l2: `scenario_l2_hb_wetjune_recheck_establishment_planting`
- farming_group: `establishment`
- task_type: `planting`; crop_stage: `NOT_PLANTED`
- belief: Start from field opening state; verify weather, soil, inventory and equipment before field prep and planting.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - inventory
  - tractor status
  - post-planting overview
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(5d)>soil_sensors>inventory>tractor_status>attach(grader)>TractorApp.level>detach>load_fertilizer(kg=360)>base_fertilize>attach(furrower)>form_ridges(width=1.1)>detach>weather_now>forecast(3d)>soil_sensors>tractor_status"
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
  - complete prep/base fertilizer/ridge formation before planting
  - use visible seed plan and spacing
- success_checks:
  - planted ridges visible
  - inventory and field state updated

### 2. mid_fungicide_20_43

- source_l2: `scenario_l2_hb_wetjune_recheck_mid_fungicide_20_43`
- farming_group: `management`
- task_type: `fungicide`; crop_stage: `MID`
- belief: Use observation and ground evidence before disease-control fungicide application; do not treat only from NDVI or a guessed target.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - canopy sensors
  - range state
  - drone survey
  - ground confirmation
  - inventory/status
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "overview>weather_now>forecast(3d)>advance(3d)>weather_now>forecast(3d)>soil_sensors>canopy_sensors>ridge_state(20-43)>mavic_charge>advance(1h)>mavic_survey(20-43)>robot0_status>robot0_charge>advance(1h)>robot0_crop_check(start=20,end=43)>inventory>tractor_status>load_fungicide(liters=88.128)>apply_fungicide(start=20,end=29,L/ridge=3.6)>apply_fungicide(start=30,end=39,L/ridge=3.6)>apply_fungicide(start=40,end=43,L/ridge=3.6)>ridge_state(20-43)"
  }
]
```

- constraints:
  - target source cause must match action type
  - wait/recheck before action when the window is not immediately ready
  - do not blindly copy ridges/rates to another L3
- success_checks:
  - target range treated
  - post-action range state rechecked
  - resource use visible

### 3. r5_fungicide_20_43

- source_l2: `scenario_l2_hb_wetjune_recheck_r5_fungicide_20_43`
- farming_group: `management`
- task_type: `fungicide`; crop_stage: `R5`
- belief: Use observation and ground evidence before disease-control fungicide application; do not treat only from NDVI or a guessed target.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - canopy sensors
  - range state
  - drone survey
  - ground confirmation
  - inventory/status
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "overview>weather_now>forecast(3d)>advance(3d)>weather_now>forecast(3d)>soil_sensors>canopy_sensors>ridge_state(20-43)>mavic_charge>advance(1h)>mavic_survey(20-43)>robot0_status>robot0_charge>advance(1h)>robot0_crop_check(start=20,end=43)>inventory>tractor_status>load_fungicide(liters=68.544)>apply_fungicide(start=20,end=29,L/ridge=2.8)>apply_fungicide(start=30,end=39,L/ridge=2.8)>apply_fungicide(start=40,end=43,L/ridge=2.8)>ridge_state(20-43)"
  }
]
```

- constraints:
  - target source cause must match action type
  - wait/recheck before action when the window is not immediately ready
  - do not blindly copy ridges/rates to another L3
- success_checks:
  - target range treated
  - post-action range state rechecked
  - resource use visible

### 4. harvest_window

- source_l2: `scenario_l2_hb_wetjune_recheck_harvest_window`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: `R8_WINDOW`
- belief: After R8, harvest timing should be based on maturity, grain moisture, weather, trafficability and postharvest resources.
- evidence_chain:
  - weather
  - forecast
  - farm overview
  - soil sensors
  - range state
  - grain moisture
  - storage/dryer state
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>overview>soil_sensors>advance(34d)>weather_now>forecast(3d)>ridge_state(0-63)"
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
    "tools": "dry_grain(target=13)>store_grain"
  }
]
```

- constraints:
  - do not harvest before maturity/window
  - do not store wet grain without drying when drying is required
  - harvest>unload>dry/store order
- success_checks:
  - harvested ridges visible
  - grain unloaded
  - grain stored or dried then stored
