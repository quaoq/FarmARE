# Farm-ARE Full-Season Scenario Scaffolds
These scenarios are L3 full-season scaffolds. Each scenario hands the agent responsibility for a soybean season from planting through post-harvest storage. The oracle path is not a single fixed action such as “spray” or “harvest.” It is a long-horizon control trace that reacts to the evolving weather, soil, phenology, canopy/biomass, biotic-pressure, management-effect, observation, and recovered-yield models.

The files intentionally reference several assumed physics-aware tools. These should be implemented or mapped into existing apps during integration. The key assumed tools are `configure_physics_profile`, `advance_time`, `commit_daily_physics`, `apply_fertigation`, `load_fungicide`, `apply_fungicide`, `inspect_crop_health`, `inspect_pests`, `dry_grain`, and `store_grain`.

Each scenario uses the same general lifecycle:

1. Check weather, forecast, soil, tractor, and inventory.
2. Plant all 64 ridges with the selected seed type.
3. Commit planting effects and wait to emergence/early monitoring.
4. Run one or more mid-season monitoring/intervention episodes.
5. Check pod-fill water status and decide whether irrigation is needed.
6. Wait to maturity and assess R8/grain moisture.
7. Harvest in 4-ridge passes with refuel/unload logistics.
8. Commit recovered-yield state.
9. Dry/store grain if needed.

The intent is not to make 10 unrelated scripts. The intent is to define 10 different physics regimes and oracle policies under the same farm lifecycle so the benchmark can test whether the agent adapts to the world, instead of replaying one script.

## 1. `scenario_full_season_balanced`
File: `scenario_full_season_baseline_balanced_season.py`
Title: Balanced full-season management
Physics profile: `harbin_baseline_2026_seed_101`
Seed type: `STANDARD`
Weather / pressure regime: normal rainfall, no extreme events
Initial condition: planting-ready soil and normal inventory
Oracle intent:
- planting: plant all ridges once soil is ready
- weekly_monitoring: routine NDVI/soil checks
- minor_fertigation: small nutrient correction on ridges 28-35
- pod_fill_irrigation_check: check dry risk but only irrigate if threshold crossed
- harvest: harvest after R8 and moisture enters range
- storage: dry/store if moisture requires

Evaluation emphasis:
- balanced end-to-end lifecycle
- periodic monitoring with limited interventions
- full-season recovered-yield accounting

## 2. `scenario_full_season_cold_spring`
File: `scenario_full_season_cold_spring_delayed_planting.py`
Title: Cold spring with delayed planting
Physics profile: `harbin_cold_spring_seed_202`
Seed type: `EARLY_COLD`
Weather / pressure regime: early cold spell and wet seedbed
Initial condition: soil temp below threshold and VWC marginal wet
Oracle intent:
- wait_for_planting_window: delay planting until soil temp and VWC are acceptable
- planting: plant EARLY_COLD seed after window opens
- emergence_check: verify emergence but no replanting unless severe
- short_season_monitoring: monitor maturity because planting was delayed
- harvest_urgency: harvest once moisture is acceptable before frost/rain risk
- storage: dry/store harvested grain

Evaluation emphasis:
- waiting for the seedbed instead of wet/cold planting
- seed-type choice under short-season pressure
- maturity and harvest timing after delayed planting

## 3. `scenario_full_season_wet_june_disease`
File: `scenario_full_season_wet_june_disease_pressure.py`
Title: Wet June disease-pressure season
Physics profile: `harbin_wet_june_seed_303`
Seed type: `STANDARD`
Weather / pressure regime: normal planting, wet June and disease-favorable July
Initial condition: planting-ready, later wet disease-pressure event injected
Oracle intent:
- planting: plant after normal checks
- wet_period: physics profile injects repeated rain
- disease_diagnosis: NDVI + thermal + ground confirmation
- spray_window_wait: wait until wind/rain allow fungicide
- fungicide: apply fungicide to affected block 34-46
- harvest: normal harvest but watch wet field
- storage: dry/store if harvested wet

Evaluation emphasis:
- disease-vs-drought diagnosis after wet weather
- spray-window waiting
- fungicide timing and residual effect

## 4. `scenario_full_season_dry_pod_fill`
File: `scenario_full_season_dry_pod_fill_yield_protection.py`
Title: Dry pod-fill yield protection
Physics profile: `harbin_dry_august_seed_404`
Seed type: `STANDARD`
Weather / pressure regime: normal early season, dry R5/R6 window
Initial condition: normal planting, injected August dry spell
Oracle intent:
- planting: plant normally
- routine_growth_monitoring: periodic sensor/drone monitoring
- dry_spell: root-zone VWC drops during R5/R6
- thermal_confirmation: thermal drone confirms water stress
- irrigation: irrigate ridges 20-43 and confirm delayed response
- harvest: harvest based on maturity/moisture
- storage: store recovered yield

Evaluation emphasis:
- R5/R6 water-stress detection
- thermal confirmation before irrigation
- yield preservation from timely irrigation

## 5. `scenario_full_season_aphid_threshold`
File: `scenario_full_season_aphid_threshold_trend.py`
Title: Aphid-like outbreak with thresholded response
Physics profile: `harbin_aphid_pressure_seed_505`
Seed type: `STANDARD`
Weather / pressure regime: warm moderate June favors insect pressure
Initial condition: normal planting, latent insect pressure grows later
Oracle intent:
- planting: normal planting
- initial_pest_signal: weak signal, monitor not spray
- one_day_pressure_evolution: biotic pressure grows under weather
- threshold_confirmation: ground inspection confirms threshold-like condition
- insecticide: spray ridges 16-27
- followup_monitoring: confirm pressure reduction
- harvest_storage: complete harvest/storage

Evaluation emphasis:
- monitoring trend instead of immediate spraying
- ground threshold confirmation
- targeted treatment

## 6. `scenario_full_season_nutrient_differential`
File: `scenario_full_season_nutrient_vs_drought_differential.py`
Title: Nutrient deficiency vs drought differential diagnosis
Physics profile: `harbin_nutrient_patch_seed_606`
Seed type: `HIGH_DENSITY`
Weather / pressure regime: normal weather, localized nutrient deficiency
Initial condition: high-density seed with nutrient-sensitive patch
Oracle intent:
- planting: plant HIGH_DENSITY seed
- canopy_monitoring: detect low-NDVI block
- rule_out_drought: soil moisture normal and no thermal stress
- rule_out_pest: ground inspection no pest threshold
- fertigation: apply fertigation ridges 28-35
- delayed_response: wait 48h and recheck canopy
- harvest_storage: complete season

Evaluation emphasis:
- differential diagnosis of low NDVI
- SPAD/ground verification
- fertigation rather than irrigation/pesticide

## 7. `scenario_full_season_mixed_stress_trap`
File: `scenario_full_season_mixed_stress_wrong_action_trap.py`
Title: Mixed stress with wrong-action trap
Physics profile: `harbin_mixed_stress_seed_707`
Seed type: `STANDARD`
Weather / pressure regime: dry spell followed by rain and disease risk
Initial condition: normal planting, later two distinct anomaly periods
Oracle intent:
- planting: normal planting
- dry_anomaly: thermal + soil confirm drought; irrigate
- rain_event: rain resets irrigation need
- wet_disease_anomaly: wet period creates disease risk
- fungicide: treat disease only after confirmation and spray window
- harvest_timing: harvest after moisture window
- storage: dry/store

Evaluation emphasis:
- using different actions for different anomaly causes
- avoiding one-size-fits-all intervention
- sequential drought and disease handling

## 8. `scenario_full_season_resource_limited`
File: `scenario_full_season_resource_limited_operations.py`
Title: Resource-limited full-season operations
Physics profile: `harbin_resource_limited_seed_808`
Seed type: `STANDARD`
Weather / pressure regime: normal weather with logistics constraints
Initial condition: low fuel, limited seed/fertilizer/pesticide inventory
Oracle intent:
- planting_with_reloads: seed hopper and inventory checks
- fuel_refill_before_operations: refuel before tractor operations
- targeted_fertigation_only: avoid whole-field fertilizer waste
- pesticide_inventory_check: spray only if threshold met and inventory sufficient
- harvest_unload_logistics: harvest with grain unload cycles
- storage: dry/store

Evaluation emphasis:
- inventory and fuel-aware planning
- targeted interventions under resource limits
- harvest unload logistics

## 9. `scenario_full_season_late_harvest_rain_risk`
File: `scenario_full_season_late_harvest_rain_risk.py`
Title: Late-season harvest rain and shattering risk
Physics profile: `harbin_late_harvest_seed_909`
Seed type: `STRESS_TOLERANT`
Weather / pressure regime: stable early season, late September rain risk
Initial condition: stress-tolerant seed, later harvest moisture timing challenge
Oracle intent:
- planting: plant STRESS_TOLERANT seed
- normal_monitoring: routine monitoring
- maturity_reached_high_moisture: R8 but grain too wet
- drydown_wait: wait one drydown day
- harvest_before_rain: harvest before rain/shatter risk
- drying_if_needed: dry and store

Evaluation emphasis:
- R8 versus actual harvest readiness
- grain moisture dry-down
- rain/shattering risk

## 10. `scenario_full_season_adversarial_weather`
File: `scenario_full_season_full_adversarial_weather_season.py`
Title: Adversarial weather full-season control
Physics profile: `harbin_adversarial_weather_seed_1001`
Seed type: `EARLY_COLD`
Weather / pressure regime: cold spring, wet disease period, dry pod fill, wet harvest risk
Initial condition: multi-event full-season stress test
Oracle intent:
- cold_wet_planting_delay: wait for seedbed readiness
- plant_early_cold: plant early/cold-tolerant type
- disease_after_wet_period: diagnose and fungicide after wet window
- dry_pod_fill_irrigation: irrigate R5/R6 stress block
- harvest_moisture_decision: choose harvest window around rain risk
- postharvest_drying_storage: dry/store and close season

Evaluation emphasis:
- multi-event full-season robustness
- sequential rescheduling
- final recovered-yield preservation

## Integration notes

The `self.events` assignment currently collects all local oracle events dynamically. If the current ARE validation stack requires an explicit ordered event list, replace that scaffold with an explicit list after integration.

The scenario files intentionally keep physics configuration in `farm_world.configure_physics_profile(...)`. This keeps the individual scenario readable while allowing the weather generator, soil initial state, hidden biotic-pressure events, and stochastic seeds to be defined centrally. A profile should specify:

- weather seed and monthly climatology
- programmed weather events
- initial soil VWC / temperature distribution
- seed type and cultivar parameters
- latent nutrient, insect, disease, and weed pressure maps
- inventory and equipment constraints
- harvest moisture/dry-down parameters

For oracle/agent comparison, run the oracle and agent against the same physics profile and random seed. The final evaluation should compare the queue-level sequence and the final recovered-yield outcome.
