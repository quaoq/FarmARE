# Knowledge Library Pilot: hb_wetjune_shortwindow_trafficability

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_wetjune_shortwindow_trafficability`.
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

- source_l2: `scenario_l2_hb_wetjune_shortwindow_standard_planting`
- farming_group: `establishment`
- task_type: `planting`; crop_stage: ``
- belief: Use normal preplant checks, field prep, 1.1 m ridges, and HEINONG84 standard-density planting.
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

### 2. wetjune_fungicide_window

- source_l2: `scenario_l2_hb_wetjune_shortwindow_fungicide_window`
- farming_group: `management`
- task_type: `fungicide`; crop_stage: ``
- belief: Wet June disease pressure should be treated only after disease evidence and a suitable spray/trafficability window are confirmed.
- evidence_chain:
  - weather
  - forecast
  - soil trafficability
  - canopy sensors
  - overview
  - drone survey
  - target state
  - reference state
  - robot crop-health check
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>overview>ridge_state(22-45)>ridge_state(0-15)>mavic_survey(22-45)>robot0_crop_check(start=22,end=45)>advance(4d)>weather_now>soil_sensors>ridge_state(22-45)>tractor_status>load_fungicide(liters=92.3)>apply_fungicide(start=22,end=31,L/ridge=3.8)>apply_fungicide(start=32,end=41,L/ridge=3.8)>apply_fungicide(start=42,end=45,L/ridge=3.8)>ridge_state(22-45)"
  }
]
```

- constraints:
  - do not spray from NDVI alone
  - do not treat insects/weeds/water/nutrients with fungicide
- success_checks:
  - disease pressure reduced or stabilized
  - no tool error

### 3. harvest_dry_store

- source_l2: `scenario_l2_hb_wetjune_shortwindow_harvest_dry_store`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: ``
- belief: Wait/recheck from R8 until moisture/weather/trafficability/capacity support harvest; dry before storage if moisture requires it.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - maturity/grain moisture
  - capacity
  - drydown rechecks
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>ridge_state(0-63)>inventory>advance(8d)>weather_now>soil_sensors>ridge_state(0-63)>inventory"
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
- success_checks:
  - 64 ridges harvested
  - no store warning
