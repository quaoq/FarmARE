# Knowledge Library Pilot: hb_wetcold_high_residue_establishment

## Purpose

This library is a structured L2 skill context for `scenario_full_season_hb_wetcold_high_residue_establishment`.
It is generated from the source L2 oracle workflows and is intended for `detail=library` prompt rendering.

## L2 Sufficiency

- status: `sufficient_for_same_l3_pilot`
- accepted_l2_scenarios:
  - `scenario_l2_hb_wetcold_high_residue_planting_window`
  - `scenario_l2_hb_wetcold_high_residue_replant_recovery`
  - `scenario_l2_hb_wetcold_high_residue_harvest_sequence`
- coverage:
  - wet-cold high-residue seedbed window search, three-day wait/recheck, and whole-field HEINONG84 planting
  - slow-emergence strip diagnosis, wait/recheck, drone and ground confirmation, and targeted replanting of ridges 0-15
  - staged harvest: direct-store dry ridges 16-63, then delayed direct-store harvest for replanted ridges 0-15
- known_gaps:
  - no disease, insect, weed, nutrient, or irrigation treatment occurs in this source L3
  - same-L3 exact dates, waits, parameters, and ridge ranges are useful for mechanism testing but would be leakage in cross-L3 evaluation

## Compression Rules

- `oracle_events` is a compact prompt template, not an executable replacement for the Python oracle.
- `seq` entries preserve the source oracle action order; shortened tool names are prompt notation only.
- `plant_loop` and `harvest_loop` expand to repeated contiguous ridge blocks; intermediate actions such as `load_seeds` and `unload_grain` remain explicit in `sequence`.
- `source_wait_days` records the source oracle wait length; it is evidence from the source workflow, not a universal maximum wait for other L3 targets.
- Do not hide intermediate actions inside fake tool arguments such as `paired_after_each_block`.

## Skill Cards

### 1. wetcold_residue_planting_window

- source_l2: `scenario_l2_hb_wetcold_high_residue_planting_window`
- farming_group: `establishment`
- task_type: `planting_window`; crop_stage: `NOT_PLANTED`
- belief: Wet-cold high-residue seedbeds should not be planted immediately. Complete prep, wait for the seedbed to warm/drain, recheck weather/forecast/soil/planter status, then plant HEINONG84 only when conditions support it.
- evidence_chain:
  - weather_now at start
  - forecast(5d)
  - soil_sensors at start
  - advance(3d)
  - weather_now at planting window
  - forecast(3d)
  - soil_sensors at planting window
  - tractor_status before planting
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(5d)>soil_sensors>inventory>tractor_status>attach(grader)>TractorApp.level>detach>load_fertilizer(kg=360)>base_fertilize>attach(furrower)>form_ridges(width=1.1)>detach>advance(3d)>weather_now>forecast(3d)>soil_sensors>tractor_status"
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
  - Do not plant on the wet-cold start without waiting and rechecking.
  - Use HEINONG84, depth=4.0, spacing=7.9 for this same-L3 pilot.
- success_checks:
  - all 64 ridges are planted
  - post-planting overview confirms planted status

### 2. wetcold_residue_replant_recovery

- source_l2: `scenario_l2_hb_wetcold_high_residue_replant_recovery`
- farming_group: `management`
- task_type: `stand_recovery`; crop_stage: `PLANTED_PRE_EMERGENCE`
- belief: After wet-cold residue planting, the slow-emergence strip must be diagnosed before action. Wait to the target window, recheck soil/canopy/range state, use drone and ground emergence checks, then replant only the confirmed 0-15 strip.
- evidence_chain:
  - weather_now
  - forecast(3d)
  - advance(8d)
  - soil_sensors
  - canopy_sensors
  - ridge_state(0, 15)
  - drone_survey(0, 15)
  - robot_emergence_check(0, 15)
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>advance(8d)>soil_sensors>canopy_sensors>ridge_state(0-15)>mavic_charge>advance(1h)>mavic_survey(0-15)>robot0_status>robot0_charge>advance(1h)>robot0_emergence(0-15)"
  },
  {
    "seq": "replant_loop",
    "ridge_range": "0-15",
    "block_size": 4,
    "sequence": [
      "load_seeds(seed_type=HEINONG84,count=as_needed,hcap=300000) if hopper low",
      "replant_seeds(start=$block_start,end=$block_end,depth=4)"
    ]
  },
  {
    "seq": "ordered",
    "tools": "ridge_state(0-15)"
  }
]
```

- constraints:
  - Do not treat low early canopy as nutrient, weed, disease, or insect pressure without evidence.
  - Do not expand replanting into the reference zone.
  - Replanting 0-15 resets that strip's maturity, so harvest must account for it later.
- success_checks:
  - ridges 0-15 show improved stand fraction
  - reference ridges are untouched

### 3. wetcold_residue_staged_harvest

- source_l2: `scenario_l2_hb_wetcold_high_residue_harvest_sequence`
- farming_group: `harvest`
- task_type: `harvest_postharvest`; crop_stage: `R8_FULL_MATURITY`
- belief: At R8, harvest is a staged decision because replanted ridges 0-15 are wetter than ridges 16-63. Harvest/store the dry 16-63 batch first, then wait and recheck 0-15 before harvesting and direct-storing it once moisture is safe.
- evidence_chain:
  - weather_now
  - forecast(3d)
  - soil_sensors
  - ridge_state(16, 63)
  - inventory
  - advance(18d)
  - ridge_state(0, 15)
- oracle_event_template:

```json
[
  {
    "seq": "ordered",
    "tools": "weather_now>forecast(3d)>soil_sensors>overview>ridge_state(16-63)"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "16-63",
    "block_size": 4,
    "sequence": [
      "harvest(start=$block_start,end=$block_end)",
      "unload_grain"
    ]
  },
  {
    "seq": "ordered",
    "tools": "dry_grain(target=13)>store_grain>advance(21d)>weather_now>forecast(3d)>soil_sensors>overview>ridge_state(0-15)"
  },
  {
    "seq": "harvest_loop",
    "ridge_range": "0-15",
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
  - Do not harvest 0-15 together with 16-63 if grain moisture is >18%.
  - Use harvest>unload>store only for <=13.5% grain.
  - Use harvest>unload>dry>store for 13.5%-18% grain.
- success_checks:
  - 64 ridges are harvested by the end
  - 0-15 is stored without drying at <=13.5% grain moisture
  - store_grain returns no warning
