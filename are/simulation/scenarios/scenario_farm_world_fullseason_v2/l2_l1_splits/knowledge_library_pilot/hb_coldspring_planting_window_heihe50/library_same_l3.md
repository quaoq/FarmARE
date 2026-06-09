# Knowledge Library Pilot: hb_coldspring_planting_window_heihe50

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_coldspring_planting_window_heihe50`.
It is generated from the source L2 oracle workflows and is intended for `detail=library` prompt rendering.

## L2 Sufficiency

- status: `sufficient_for_same_l3_pilot`
- accepted_l2_scenarios:
  - `scenario_l2_hb_coldspring_heihe50_planting_window`
  - `scenario_l2_hb_coldspring_heihe50_cold_emergence_scouting`
  - `scenario_l2_hb_coldspring_heihe50_harvest_drydown_store`
- coverage:
  - cold-spring planting-window search from 2026-05-05, field prep, wait/recheck, and whole-field HEIHE50 planting
  - post-planting cold/rain emergence-risk monitoring, remote sensing, and ground stand confirmation
  - R8 harvest-window search, drydown waits, weather/soil/moisture rechecks, whole-field harvest, unloading, and direct storage
- known_gaps:
  - management has no treatment action in the source L3, so it is represented as a scouting/stand-confirmation L2
  - daily waits are compressed into agronomic window-finding patterns rather than one skill per day
  - same-L3 exact dates, waits, parameters, and ridge ranges are useful for mechanism testing but would be leakage in cross-L3 evaluation

## Compression Rules

- `oracle_events` is a compact prompt template, not an executable replacement for the Python oracle.
- `seq` entries preserve the source oracle action order; shortened tool names are prompt notation only.
- `plant_loop` and `harvest_loop` expand to repeated contiguous ridge blocks; intermediate actions such as `load_seeds` and `unload_grain` remain explicit in `sequence`.
- `source_wait_days` records the source oracle wait length; it is evidence from the source workflow, not a universal maximum wait for other L3 targets.
- Do not hide intermediate actions inside fake tool arguments such as `paired_after_each_block`.

## Skill Cards

### 1. heihe50_coldspring_planting_window

- source_l2: `scenario_l2_hb_coldspring_heihe50_planting_window`
- farming_group: `establishment`
- task_type: `planting_window`; crop_stage: `NOT_PLANTED`
- belief: The 2026-05-05 cold-spring start is a window-finding task, not an immediate planting task. Complete prep, wait out the cold seedbed spell, recheck weather/soil, wait to the planting window, then plant whole-field HEIHE50 only when the current state supports it.
- evidence_chain:
  - weather_now at start
  - forecast(5d) at start
  - soil_sensors at start
  - inventory
  - tractor_status
  - advance(8d)
  - weather_now after cold wait
  - soil_sensors after cold wait
  - advance(3d)
  - weather_now at planting window
  - forecast(3d) at planting window
  - soil_sensors at planting window
  - tractor_status before planting
- oracle_event_template:

```json
[
  {
    "seq": "start_observe",
    "tools": "weather_now>forecast(5d)>soil_sensors>inventory>tractor_status"
  },
  {
    "seq": "field_prep",
    "tools": "attach_implement(grader)>level>detach>load_fertilizer(360kg)>base_fertilize>attach_implement(furrower)>form_ridges(1.1m)>detach"
  },
  {
    "seq": "wait_recheck",
    "source_wait_days": 8,
    "tools": "weather_now>soil_sensors"
  },
  {
    "seq": "wait_recheck",
    "source_wait_days": 3,
    "tools": "weather_now>forecast(3d)>soil_sensors>tractor_status"
  },
  {
    "seq": "plant_loop",
    "ridge_range": "0-63",
    "block_size": 4,
    "sequence": [
      "load_seeds(seed_type=HEIHE50,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4.0,spacing=8.2)"
    ]
  },
  {
    "seq": "post_plant",
    "tools": "commit_physics>overview"
  }
]
```

- constraints:
  - Do not plant on the 2026-05-05 cold start without waiting and rechecking.
  - Do not plant before field prep, base fertilizer, and ridge formation.
  - Use HEIHE50, depth=4.0, spacing=8.2 for this same-L3 pilot.
- success_checks:
  - all 64 ridges are planted
  - post-planting overview confirms planted status
  - seed/fertilizer/fuel use is consistent with whole-field planting

### 2. heihe50_coldspring_emergence_scouting

- source_l2: `scenario_l2_hb_coldspring_heihe50_cold_emergence_scouting`
- farming_group: `management`
- task_type: `management_scouting`; crop_stage: `PLANTED_PRE_EMERGENCE`
- belief: After cold-window HEIHE50 planting, low NDVI or non-emergence should not be treated as fertilizer, weed, disease, or insect pressure. Monitor cold/rain emergence risk, wait through the risk period, then use overview, canopy, drone, and ground checks to confirm stand before deciding whether action is needed.
- evidence_chain:
  - weather_now after planting
  - forecast(4d)
  - soil_sensors
  - overview
  - advance(4d)
  - weather_now after cold/rain wait
  - soil_sensors after cold/rain wait
  - advance(10d)
  - overview at emergence window
  - canopy_sensors
  - mavic_survey(0, 63)
  - robot0_emergence_check(0, 15)
  - ridge_state(0, 63)
- oracle_event_template:

```json
[
  {
    "seq": "postplant_start_check",
    "tools": "weather_now>forecast(4d)>soil_sensors>overview"
  },
  {
    "seq": "wait_recheck",
    "source_wait_days": 4,
    "tools": "weather_now>soil_sensors"
  },
  {
    "seq": "wait_to_emergence_window",
    "source_wait_days": 10,
    "tools": "overview>canopy_sensors"
  },
  {
    "seq": "drone_survey",
    "tools": "mavic_charge>advance(1h)>mavic_survey(0-63)"
  },
  {
    "seq": "ground_stand_check",
    "tools": "robot0_status>robot0_charge>advance(1h)>robot0_emergence_check(0-15)"
  },
  {
    "seq": "confirm_no_action",
    "tools": "ridge_state(0-63)"
  }
]
```

- constraints:
  - Do not diagnose fertilizer, weed, disease, or insect pressure from low early canopy alone.
  - Do not replant, fertilize, spray, or irrigate without ground-confirmed evidence.
  - Use drone and ground checks to interpret cold-spring emergence signals.
- success_checks:
  - emergence/stand status is checked after the cold/rain risk period
  - whole-field status is documented
  - no unsupported treatment action is taken

### 3. heihe50_coldspring_harvest_drydown_store

- source_l2: `scenario_l2_hb_coldspring_heihe50_harvest_drydown_store`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: `R8_WINDOW_BEGINNING`
- belief: At R8 start, harvest is still a timing decision. Check weather, forecast, soil trafficability, whole-field grain moisture, and capacity. If moisture is too high, wait and recheck. Harvest only once moisture reaches direct-store safety and the field is workable.
- evidence_chain:
  - weather_now at R8 start
  - forecast(3d) at R8 start
  - soil_sensors at R8 start
  - ridge_state(0, 63) for initial moisture
  - advance(5d)
  - ridge_state(0, 63) after first drydown
  - advance(5d)
  - weather_now after second drydown
  - ridge_state(0, 63) after second drydown
  - advance(6d)
  - weather_now at direct-store window
  - forecast(3d) at direct-store window
  - soil_sensors at direct-store window
  - ridge_state(0, 63) at direct-store window
  - inventory for storage capacity
- oracle_event_template:

```json
[
  {
    "seq": "r8_start_check",
    "tools": "weather_now>forecast(3d)>soil_sensors>ridge_state(0-63)"
  },
  {
    "seq": "wait_recheck",
    "source_wait_days": 5,
    "tools": "ridge_state(0-63)"
  },
  {
    "seq": "wait_recheck",
    "source_wait_days": 5,
    "tools": "weather_now>ridge_state(0-63)"
  },
  {
    "seq": "wait_recheck",
    "source_wait_days": 6,
    "tools": "weather_now>forecast(3d)>soil_sensors>ridge_state(0-63)>inventory"
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
    "seq": "direct_store_recheck",
    "tools": "store_grain>inventory"
  }
]
```

- constraints:
  - Harvest requires R8 or harvest_allowed, suitable weather, trafficable soil, known grain moisture, and storage capacity.
  - Use harvest>unload>store only when grain moisture is <= 13.5%.
  - If grain moisture is 13.5%-18%, dry before storing.
  - Do not wait for unnecessary 8%-10% grain moisture.
- success_checks:
  - 64 ridges are harvested
  - harvested grain is unloaded before storage
  - store_grain returns no warning
  - final recovered/stored grain is non-empty
