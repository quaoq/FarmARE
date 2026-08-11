# Knowledge Library Pilot: heinong84_staggered_planting

## Purpose

This library is a structured L2 skill context for `scenario_full_season_heinong84_staggered_planting`.
It is generated from the source L2 oracle workflows and is intended for `detail=library` prompt rendering.

## L2 Sufficiency

- status: `sufficient_for_same_l3_pilot`
- accepted_l2_scenarios:
  - `scenario_l2_hn84_staggered_field_prep_early_planting`
  - `scenario_l2_hn84_staggered_mid_planting_window`
  - `scenario_l2_hn84_staggered_late_seedbed_planting`
  - `scenario_l2_hn84_staggered_stage_scouting`
  - `scenario_l2_hn84_staggered_harvest_sequence`
- coverage:
  - field preparation, base fertilizer, ridge formation, and early-zone planting
  - mid-zone planting-window wait, recheck, and planting
  - late-zone planting-window wait, seedbed irrigation, infiltration wait, and planting
  - post-planting emergence and stage-split scouting across staggered zones
  - R8 harvest-window decision, staggered harvest, unloading, drying, and storage
- known_gaps:
  - daily wait events are compressed into skill patterns rather than separate skills
  - observation-only management is represented as a scouting skill because the source L3 has no treatment action in the management window
  - same-L3 exact target ridges and parameters are useful for mechanism testing but would be leakage in cross-L3 evaluation

## Compression Rules

- `oracle_events` is a compact prompt template, not an executable replacement for the Python oracle.
- `seq` entries preserve the source oracle action order; shortened tool names are prompt notation only.
- `plant_loop` and `harvest_loop` expand to repeated contiguous ridge blocks; intermediate actions such as `load_seeds` and `unload_grain` remain explicit in `sequence`.
- `source_wait_days` records the source oracle wait length; it is evidence from the source workflow, not a universal maximum wait for other L3 targets.
- Do not hide intermediate actions inside fake tool arguments such as `paired_after_each_block`.

## Skill Cards

### 1. hn84_field_prep_early_zone_planting

- source_l2: `scenario_l2_hn84_staggered_field_prep_early_planting`
- farming_group: `establishment`
- task_type: `field_prep_planting`; crop_stage: `NOT_PLANTED`
- belief: If the field is unprepared and unplanted, confirm weather, forecast, seedbed soil, inventory, and tractor status. Complete field prep before planting. In this staggered L3, only early ridges 0-20 are planted in the first window.
- evidence_chain:
  - weather_now
  - forecast(5d)
  - soil_sensors
  - inventory
  - tractor_status
  - weather_now
  - forecast(3d)
  - soil_sensors
  - tractor_status
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(5d)>soil_sensors>inventory>tractor_status>attach(grader)>TractorApp.level>load_fertilizer(kg=250)>base_fertilize>form_ridges(width=1.1)>commit_physics>weather_now>forecast(3d)>soil_sensors>tractor_status"
  },
  {
    "seq": "plant_loop",
    "ridge_range": "0-19",
    "block_size": 4,
    "sequence": [
      "load_seeds(seed_type=HEINONG84,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4,spacing=7.9)"
    ]
  },
  {
    "seq": "plant_loop",
    "ridge_range": "20-20",
    "block_size": 1,
    "sequence": [
      "load_seeds(seed_type=HEINONG84,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4,spacing=7.9)"
    ]
  },
  {
    "seq": "ordered",
    "tools": "commit_physics>ridge_state(0-20)"
  }
]
```

- constraints:
  - Do not plant before field prep, base fertilizer, and ridge formation.
  - Do not plant mid or late zones in the early window.
  - Use HEINONG84, depth=4.0, spacing=7.9 for this same-L3 pilot.
- success_checks:
  - ridges 0-20 show planted status
  - mid and late zones remain unplanted
  - seed/fertilizer/fuel inventory changed consistently

### 2. hn84_mid_zone_planting_window

- source_l2: `scenario_l2_hn84_staggered_mid_planting_window`
- farming_group: `establishment`
- task_type: `planting_window`; crop_stage: `EARLY_ZONE_PLANTED`
- belief: After early-zone planting, wait for the configured mid-zone planting window rather than immediately planting all remaining ridges. Recheck weather, forecast, soil, seed inventory, and planter status before planting ridges 21-42.
- evidence_chain:
  - advance(7d) for the mid planting window
  - weather_now
  - forecast(3d)
  - soil_sensors
  - inventory
  - tractor_status
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "advance(7d)>weather_now>forecast(3d)>soil_sensors>inventory>tractor_status"
  },
  {
    "seq": "plant_loop",
    "ridge_range": "21-40",
    "block_size": 4,
    "sequence": [
      "load_seeds(seed_type=HEINONG84,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4,spacing=7.9)"
    ]
  },
  {
    "seq": "plant_loop",
    "ridge_range": "41-42",
    "block_size": 2,
    "sequence": [
      "load_seeds(seed_type=HEINONG84,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4,spacing=7.9)"
    ]
  },
  {
    "seq": "ordered",
    "tools": "commit_physics>ridge_state(21-42)"
  }
]
```

- constraints:
  - Do not plant ridges 21-42 before the mid-zone window.
  - Recheck conditions after waiting.
  - Do not modify already planted early ridges.
- success_checks:
  - ridges 21-42 show planted status
  - ridges 43-63 remain unplanted until the late window

### 3. hn84_late_seedbed_irrigation_planting

- source_l2: `scenario_l2_hn84_staggered_late_seedbed_planting`
- farming_group: `establishment`
- task_type: `irrigation_planting_window`; crop_stage: `EARLY_AND_MID_PLANTED`
- belief: Late-zone planting requires a later window and seedbed evidence. If the late seedbed needs moisture support, irrigate ridges 43-63, wait for infiltration, then recheck before planting.
- evidence_chain:
  - advance(7d) for the late planting window
  - weather_now
  - forecast(3d)
  - soil_sensors
  - FieldOpsApp.irrigate
  - advance(6h)
  - soil_sensors
  - inventory
  - tractor_status
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "advance(7d)>weather_now>forecast(3d)>soil_sensors>FieldOpsApp.irrigate(start=43,end=63,hours=1.2)>advance(6h)>soil_sensors>inventory>tractor_status"
  },
  {
    "seq": "plant_loop",
    "ridge_range": "43-62",
    "block_size": 4,
    "sequence": [
      "load_seeds(seed_type=HEINONG84,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4,spacing=7.9)"
    ]
  },
  {
    "seq": "plant_loop",
    "ridge_range": "63-63",
    "block_size": 1,
    "sequence": [
      "load_seeds(seed_type=HEINONG84,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4,spacing=7.9)"
    ]
  },
  {
    "seq": "ordered",
    "tools": "commit_physics>ridge_state(43-63)"
  }
]
```

- constraints:
  - Do not plant ridges 43-63 before the late-zone window.
  - Do not irrigate the whole field when only the late seedbed needs support.
  - Recheck soil after infiltration before planting.
- success_checks:
  - ridges 43-63 show planted status
  - all 64 ridges are planted after the late action
  - water and seed use are consistent with targeted action

### 4. hn84_stage_scouting

- source_l2: `scenario_l2_hn84_staggered_stage_scouting`
- farming_group: `management`
- task_type: `management_scouting`; crop_stage: `POST_PLANTING_TO_R3`
- belief: After all zones are planted, manage the crop by scouting emergence and later stage split separately by zone. Do not treat the field unless evidence supports a real stressor.
- evidence_chain:
  - advance(16d)
  - overview
  - soil_sensors
  - canopy_sensors
  - robot_emergence_check for early/mid/late samples
  - drone_survey(0, 63)
  - advance(42d)
  - ridge_state for early/mid/late zones
  - robot_crop_check for early/mid/late samples
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "advance(16d)>overview>soil_sensors>canopy_sensors>robot0_status>robot0_emergence(0-7)>robot0_emergence(21-28)>robot0_emergence(43-50)>mavic_survey(0-63)>mavic_charge>robot0_charge>advance(42d)>overview>ridge_state(0-20)>ridge_state(21-42)>ridge_state(43-63)>soil_sensors>canopy_sensors>mavic_survey(0-63)>robot0_status>robot0_crop_check(start=0,end=7)>robot0_crop_check(start=21,end=28)>robot0_crop_check(start=43,end=50)>robot0_charge>mavic_charge"
  }
]
```

- constraints:
  - Do not collapse the three staggered zones into one uniform stage assumption.
  - Do not apply fertilizer, pesticide, herbicide, fungicide, or irrigation without direct evidence.
  - Use ground checks to confirm what drone/canopy signals mean.
- success_checks:
  - emergence status is checked by zone
  - stage split across zones is visible in range-state checks
  - no unsupported treatment action is taken

### 5. hn84_staggered_harvest_sequence

- source_l2: `scenario_l2_hn84_staggered_harvest_sequence`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: `R8_WINDOW_BEGINNING`
- belief: At R8/harvest-window beginning, harvest timing is still a decision. Check each zone for maturity, harvest_allowed, weather, soil trafficability, grain moisture, and capacity. Harvest batches in early/mid/late order only when current evidence supports it.
- evidence_chain:
  - advance(10d)
  - weather_now
  - forecast(3d)
  - soil_sensors
  - overview
  - ridge_state(0, 20)
  - advance(7d)
  - ridge_state(21, 42)
  - advance(10d)
  - ridge_state(43, 63)
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "advance(11d)>weather_now>forecast(3d)>soil_sensors>overview>ridge_state(0-20)>attach(harvester)"
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
    "tools": "dry_grain(target=13)>store_grain>advance(1d)>weather_now>forecast(3d)>soil_sensors>overview>ridge_state(21-42)"
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
    "tools": "dry_grain(target=13)>store_grain>advance(14d)>weather_now>forecast(3d)>soil_sensors>overview>ridge_state(43-63)"
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
    "tools": "dry_grain(target=13)>store_grain>commit_physics>inventory"
  }
]
```

- constraints:
  - Harvest requires R8 or harvest_allowed, suitable weather, trafficable soil, known grain moisture, and storage/dryer capacity.
  - Preserve harvest>unload>dry_grain>store_grain when drying is needed.
  - Do not store wet grain directly.
  - Do not wait for unnecessary 8%-10% grain moisture.
- success_checks:
  - 64 ridges harvested by the end of the sequence
  - each batch has non-empty recovered grain
  - store_grain returns no warning
  - final recovered_yield_kg_total matches the source L3 scale
