# Knowledge Library Pilot: hb_heihe43_early_density_weed_nutrient_recovery

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery`.
It is generated from the source L2 oracle workflows and is intended for `detail=library` prompt rendering.

## L2 Sufficiency

- status: `sufficient_for_same_l3_pilot`
- accepted_l2_scenarios:
  - `scenario_l2_hb_heihe43_recommended_density_planting`
  - `scenario_l2_hb_heihe43_nutrient_recovery`
  - `scenario_l2_hb_heihe43_early_weed_control`
  - `scenario_l2_hb_heihe43_r7_harvest_drydown_store`
- coverage:
  - preplant field preparation and HEIHE43 recommended-density planting
  - early VC nutrient-weak block diagnosis and fertigation recovery
  - early VC weed-competition diagnosis and targeted herbicide
  - late maturity drydown, harvest, drying, and storage
- known_gaps:
  - routine checks without a management action are not represented as standalone skills
  - this pilot does not include a separate L2 for every L3 observation-only waypoint
  - same-L3 exact target ridges and parameters are useful for mechanism testing but would be leakage in cross-L3 evaluation

## Compression Rules

- `oracle_events` is a compact prompt template, not an executable replacement for the Python oracle.
- `seq` entries preserve the source oracle action order; shortened tool names are prompt notation only.
- `plant_loop` and `harvest_loop` expand to repeated contiguous ridge blocks; intermediate actions such as `load_seeds` and `unload_grain` remain explicit in `sequence`.
- `source_wait_days` records the source oracle wait length; it is evidence from the source workflow, not a universal maximum wait for other L3 targets.
- Do not hide intermediate actions inside fake tool arguments such as `paired_after_each_block`.

## Skill Cards

### 1. heihe43_recommended_density_planting

- source_l2: `scenario_l2_hb_heihe43_recommended_density_planting`
- farming_group: `establishment`
- task_type: `planting`; crop_stage: `NOT_PLANTED`
- belief: If the HEIHE43 field is not planted and weather, seedbed soil, seed inventory, fertilizer, fuel, and tractor status are acceptable, complete field prep before planting. HEIHE43 should be planted at the recommended spacing rather than arbitrary high density.
- evidence_chain:
  - get_current_weather
  - get_forecast(3d)
  - read_soil_sensors
  - get_inventory
  - get_status
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
      "load_seeds(seed_type=HEIHE43,count=as_needed,hcap=300000) if hopper low",
      "plant_seeds(start=$block_start,end=$block_end,depth=4,spacing=8.1)"
    ]
  },
  {
    "seq": "ordered",
    "tools": "overview"
  }
]
```

- constraints:
  - Do not plant before field prep, base fertilizer, and ridge formation are complete.
  - Use spacing=8.1 for this same-L3 pilot.
  - Do not substitute very high density such as 4.05 cm; current physics penalizes large density deviation from the HEIHE43 optimum.
- success_checks:
  - farm overview shows planted ridges
  - inventory reflects seed/fuel/fertilizer use
  - planting record shows depth 4.0 cm and seed spacing 8.1 cm

### 2. heihe43_vc_nutrient_recovery

- source_l2: `scenario_l2_hb_heihe43_nutrient_recovery`
- farming_group: `management`
- task_type: `fertigation`; crop_stage: `VC`
- belief: If early HEIHE43 vigor is weak in a localized block, diagnose before acting. Treat as nutrient stress only after checking soil/water context, canopy, drone map, target-vs-reference ridge states, and ground crop-health evidence; do not confuse nutrient weakness with drought, weed, disease, or insect pressure.
- evidence_chain:
  - get_current_weather
  - get_forecast(3d)
  - read_soil_sensors
  - read_canopy_sensors
  - get_farm_overview
  - drone_survey(0, 63)
  - ridge_state(8, 19)
  - ridge_state(24, 35)
  - robot_crop_check(8, 19)
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>overview>mavic_survey(0-63)>ridge_state(8-19)>ridge_state(24-35)>robot0_crop_check(start=8,end=19)>advance(4d)>weather_now>ridge_state(8-19)>inventory>fertigation(start=8,end=19,nutrient=0.24,water_mm=1.2)>ridge_state(8-19)"
  }
]
```

- constraints:
  - Use fertigation only for nutrient-supported weakness, not for weed, disease, insect, or pure water stress.
  - Keep the action targeted to the confirmed nutrient-weak block in this same-L3 pilot.
  - Recheck weather and target state after waiting.
- success_checks:
  - nutrient stress in the target block is reduced or stabilized
  - input inventory changed consistently with fertigation
  - reference block remains untreated

### 3. heihe43_vc_weed_control

- source_l2: `scenario_l2_hb_heihe43_early_weed_control`
- farming_group: `management`
- task_type: `herbicide`; crop_stage: `VC`
- belief: If early HEIHE43 canopy/vigor variability persists, distinguish weed competition from nutrient or water stress. Use weather, trafficability, canopy, drone, target-vs-reference state, and ground inspection before targeted herbicide.
- evidence_chain:
  - get_current_weather
  - get_forecast(3d)
  - read_soil_sensors
  - read_canopy_sensors
  - get_farm_overview
  - drone_survey(0, 63)
  - advance(8d) for signal visibility
  - ridge_state(44, 55)
  - ridge_state(24, 35)
  - robot_crop_check(44, 55)
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>canopy_sensors>overview>mavic_survey(0-63)>advance(8d)>ridge_state(44-55)>ridge_state(24-35)>robot0_crop_check(start=44,end=55)>advance(4d)>weather_now>ridge_state(44-55)>inventory>tractor_status>TractorApp.load_pesticide(liters=29.4)>apply_herbicide(start=44,end=53,L/ridge=2.4)>apply_herbicide(start=54,end=55,L/ridge=2.4)>ridge_state(44-55)"
  }
]
```

- constraints:
  - Do not diagnose weed pressure from NDVI alone.
  - Do not use fertilizer or irrigation to solve weed competition.
  - Confirm spray weather and tractor/sprayer state before herbicide.
- success_checks:
  - weed pressure or weed competition in the target block is reduced
  - herbicide inventory changed consistently
  - non-target reference block is not treated

### 4. heihe43_r7_r8_harvest_dry_store

- source_l2: `scenario_l2_hb_heihe43_r7_harvest_drydown_store`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: `R7_TO_R8`
- belief: Near maturity is not enough for immediate harvest. From R7/R8, repeatedly check weather, forecast, soil trafficability, harvest_allowed/R8, grain moisture, and storage capacity. Wait for a legal harvest window; if moisture is above 13.5% but within the dryable range, harvest then dry before storage.
- evidence_chain:
  - get_current_weather
  - get_forecast(3d)
  - read_soil_sensors
  - ridge_state(0, 63)
  - inventory
  - repeated drydown waits and moisture rechecks
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>ridge_state(0-63)>inventory>advance(4d)>ridge_state(0-63)>advance(4d)>ridge_state(0-63)>advance(4d)>weather_now>soil_sensors>ridge_state(0-63)>inventory"
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
  - Harvest requires R8 or harvest_allowed, suitable weather, trafficable soil, known grain moisture, and capacity.
  - Do not store grain above safe moisture without drying.
  - Do not wait for unnecessary 8%-10% grain moisture.
  - Preserve the sequence harvest>unload>dry_grain>store when drying is needed.
- success_checks:
  - 64 ridges harvested
  - grain dried to target=13.0 before storage
  - store_grain returns no warning
  - recovered/stored grain is non-empty
