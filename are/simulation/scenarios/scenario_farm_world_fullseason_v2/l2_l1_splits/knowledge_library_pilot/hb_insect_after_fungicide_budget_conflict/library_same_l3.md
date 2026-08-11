# Knowledge Library Pilot: hb_insect_after_fungicide_budget_conflict

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_insect_after_fungicide_budget_conflict`.
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

### 1. standard_density_establishment

- source_l2: `scenario_l2_hb_insect_budget_standard_planting`
- farming_group: `establishment`
- task_type: `planting`; crop_stage: ``
- belief: For this same-L3 pilot, start from the unplanted HEINONG84 standard-density field. Establishment requires weather/forecast/soil/resource checks, field prep, base fertilizer, 1.1 m ridges, and whole-field planting with the source L3 parameters.
- evidence_chain:
  - current weather
  - 3-day forecast
  - seedbed soil sensors
  - inventory
  - tractor status
  - planting-window weather/forecast/soil recheck
  - post-planting farm overview
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>inventory>tractor_status>attach(grader)>TractorApp.level>detach>load_fertilizer(kg=360)>base_fertilize>attach(furrower)>form_ridges(width=1.1)>detach>weather_now>forecast(3d)>soil_sensors>tractor_status"
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
  - do not plant before field prep, base fertilizer, and ridge formation
  - use HEINONG84 for this source L3
  - same-L3 exact spacing/depth/ridge-width parameters are not fair cross-L3 context
- success_checks:
  - 64 ridges planted
  - inventory/fuel/seed use changed consistently
  - farm overview confirms planted status

### 2. budgeted_disease_control

- source_l2: `scenario_l2_hb_insect_budget_disease_control`
- farming_group: `management`
- task_type: `fungicide`; crop_stage: ``
- belief: Use fungicide only after disease evidence is confirmed by weather/soil/canopy context, drone localization, target-vs-reference state, and ground crop-health check.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - canopy sensors
  - farm overview
  - drone survey
  - target state
  - reference state
  - robot crop-health check
  - inventory and sprayer status
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>overview>mavic_survey(0-63)>ridge_state(18-39)>ridge_state(0-15)>robot0_crop_check(start=18,end=39)>advance(6d)>weather_now>ridge_state(18-39)>inventory>tractor_status>load_fungicide(liters=76.3)>apply_fungicide(start=18,end=27,L/ridge=3.4)>apply_fungicide(start=28,end=37,L/ridge=3.4)>apply_fungicide(start=38,end=39,L/ridge=3.4)>inventory>ridge_state(18-39)"
  }
]
```

- constraints:
  - do not use fungicide for insect, weed, nutrient, or water stress
  - respect budget and spray weather
- success_checks:
  - disease pressure reduced or stabilized
  - budget/inventory changed consistently

### 3. r5_manual_insect_control

- source_l2: `scenario_l2_hb_insect_budget_r5_manual_control`
- farming_group: `management`
- task_type: `insecticide`; crop_stage: ``
- belief: After fungicide, insect pressure is a separate R5 diagnosis. Manual insecticide is appropriate only when insect evidence and resource checks support it.
- evidence_chain:
  - R5 overview
  - soil/canopy sensors
  - drone survey
  - target state
  - robot insect/leaf-damage check
  - weather
  - budget and inventory
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>soil_sensors>canopy_sensors>overview>inventory>mavic_survey(24-47)>ridge_state(24-47)>ridge_state(0-15)>Robot0.inspect_pests(start=24,end=47)>FieldOpsApp.apply_pesticide_manual(ridge_id=24,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=26,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=28,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=30,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=32,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=34,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=36,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=38,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=40,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=42,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=44,L/ridge=3.2,advance_time=False,ridge_count=2)>FieldOpsApp.apply_pesticide_manual(ridge_id=46,L/ridge=3.2,advance_time=False,ridge_count=2)>inventory>ridge_state(24-47)"
  }
]
```

- constraints:
  - do not treat insects with fungicide
  - do not spray without evidence
  - manual path is for this source scenario's wet/trafficability and budget context
- success_checks:
  - insect pressure reduced or stabilized
  - remaining budget is tracked

### 4. r8_harvest_dry_store

- source_l2: `scenario_l2_hb_insect_budget_r8_harvest_store`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: ``
- belief: At R8, wait/recheck until weather, trafficability, grain moisture, and storage capacity support harvest, drying, and storage.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - whole-field maturity and grain moisture
  - inventory/capacity
  - drydown rechecks
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>ridge_state(0-63)>inventory>advance(5d)>ridge_state(0-63)>advance(5d)>ridge_state(0-63)>advance(1d)>weather_now>soil_sensors>ridge_state(0-63)>inventory"
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
  - dry harvested grain to the source L3 storage target before final storage
  - do not store before harvest/unload succeeds
- success_checks:
  - 64 ridges harvested
  - stored grain is non-empty
  - no store warning
