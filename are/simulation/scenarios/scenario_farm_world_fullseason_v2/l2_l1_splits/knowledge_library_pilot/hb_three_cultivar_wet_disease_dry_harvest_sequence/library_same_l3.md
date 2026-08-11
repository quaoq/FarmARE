# Knowledge Library Pilot: hb_three_cultivar_wet_disease_dry_harvest_sequence

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence`.
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

### 1. three_cultivar_planting

- source_l2: `scenario_l2_three_cultivar_preplant_fullfield_planting`
- farming_group: `establishment`
- task_type: `planting`; crop_stage: ``
- belief: After common field prep, each cultivar zone must be planted with its planned seed and spacing.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - inventory
  - tractor status
  - zone seed plan
- oracle_event_template:

```json
[
  {
    "seq": "prep",
    "tools": "weather_now>forecast(3d)>soil_sensors>inventory>tractor_status>level>load_fertilizer(360kg)>base_fertilize>form_ridges(1.1m)"
  },
  {
    "seq": "plant_zones",
    "block_size": "up_to_4",
    "sequence": [
      "HEIHE50 ridges 0-20: load_seeds as needed, plant depth=4 spacing=8.2",
      "HEINONG84 ridges 21-42: load_seeds as needed, plant depth=4 spacing=7.9",
      "HEINONG58 ridges 43-63: load_seeds as needed, plant depth=4 spacing=8.4"
    ]
  },
  {
    "seq": "post_plant",
    "tools": "ridge_state(0-63)"
  }
]
```

- constraints:
  - do not mix cultivar seed plans
  - do not plant before prep
- success_checks:
  - all zones planted
  - cultivar-specific records visible

### 2. hn84_wet_disease_control

- source_l2: `scenario_l2_three_cultivar_hn84_disease_control`
- farming_group: `management`
- task_type: `fungicide`; crop_stage: ``
- belief: Treat wet-zone HN84 disease only after disease evidence is confirmed by weather/soil/canopy, drone, zone state, reference, and ground checks.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - canopy sensors
  - drone survey
  - HN84 zone state
  - reference state
  - robot crop-health check
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>ridge_state(21-42)>robot0_crop_check(start=21,end=42)>advance(5d)>weather_now>soil_sensors>canopy_sensors>ridge_state(21-42)>mavic_survey(21-42)>robot0_crop_check(start=21,end=42)>inventory>tractor_status>load_fungicide(liters=85.3)>apply_fungicide(start=21,end=30,L/ridge=3.8)>apply_fungicide(start=31,end=40,L/ridge=3.8)>apply_fungicide(start=41,end=42,L/ridge=3.8)>ridge_state(21-42)"
  }
]
```

- constraints:
  - do not use fungicide for water, weed, insect, or nutrient stress
- success_checks:
  - disease pressure reduced or stabilized

### 3. hn58_dry_water_management

- source_l2: `scenario_l2_three_cultivar_hn58_dry_water_management`
- farming_group: `management`
- task_type: `irrigation`; crop_stage: ``
- belief: Irrigate HN58 dry-zone stress only when soil moisture and canopy/thermal evidence support water stress.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - canopy/thermal signal
  - drone survey
  - HN58 zone state
  - reference state
  - ground check
  - water budget
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>overview>advance(4d)>weather_now>soil_sensors>canopy_sensors>ridge_state(43-63)>mavic_survey(43-63)>Matrice4T.fly_survey(start=43,end=63)>robot0_crop_check(start=43,end=63)>inventory>FieldOpsApp.irrigate(start=43,end=63,hours=0.8)>advance(6h)>ridge_state(43-63)"
  }
]
```

- constraints:
  - do not irrigate disease, weed, insect, or nutrient stress
- success_checks:
  - water stress improves or stabilizes
  - water use tracked

### 4. zone_harvest_sequence

- source_l2: `scenario_l2_three_cultivar_zone_harvest_sequence`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: ``
- belief: Cultivar zones can differ in maturity and grain moisture; choose harvest order from zone evidence.
- evidence_chain:
  - weather
  - forecast
  - soil sensors
  - zone maturity and grain moisture
  - inventory/capacity
  - drydown rechecks
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>soil_sensors>ridge_state(0-20)>inventory"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "0-19",
    "block_size": 4,
    "sequence": [
      "harvest(start=$block_start,end=$block_end)",
      "unload_grain"
    ]
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "20-20",
    "block_size": 1,
    "sequence": [
      "harvest(start=$block_start,end=$block_end)",
      "unload_grain"
    ]
  },
  {
    "seq": "ordered",
    "tools": "dry_grain(target=13)>store_grain>advance(14d)>weather_now>soil_sensors>ridge_state(21-42)>inventory"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "21-40",
    "block_size": 4,
    "sequence": [
      "harvest(start=$block_start,end=$block_end)",
      "unload_grain"
    ]
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "41-42",
    "block_size": 2,
    "sequence": [
      "harvest(start=$block_start,end=$block_end)",
      "unload_grain"
    ]
  },
  {
    "seq": "ordered",
    "tools": "dry_grain(target=13)>store_grain>advance(12d)>weather_now>soil_sensors>ridge_state(43-63)>inventory"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "43-62",
    "block_size": 4,
    "sequence": [
      "harvest(start=$block_start,end=$block_end)",
      "unload_grain"
    ]
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "63-63",
    "block_size": 1,
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
  - do not harvest unready zones
  - do not store wet grain directly
- success_checks:
  - ready zones harvested
  - no store warning
  - stored grain non-empty
