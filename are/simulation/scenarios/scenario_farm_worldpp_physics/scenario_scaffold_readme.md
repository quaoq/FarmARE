# Farm-ARE Physics-Aware Scenario Scaffolds

This folder contains scenario scaffolds intended to extend the current FarmWorld ARE scenarios into closed-loop, physics-aware scenarios. The existing scenarios already cover important operational tasks such as field preparation, planting, drone survey, irrigation, fertilization, pesticide response, and harvest. These scaffolds preserve that structure but make the oracle logic depend on the evolving physics engines: weather, soil, phenology, canopy/biomass, biotic pressure, management effects, observation, and recovered yield.

The files are not meant to be treated as final drop-in scenarios. They intentionally reference a small number of assumed tools, such as `commit_daily_physics`, `advance_time`, `inspect_emergence`, `apply_fertigation`, `apply_fungicide`, `dry_grain`, and `incorporate_residue`. Those tools should be implemented or mapped onto existing apps as the physics engine is integrated. The purpose here is to define realistic scenario logic first, then update the apps to support the required actions and state transitions.

## Scenario list

### 1. `scenario_physics_planting_window_reschedule.py`

This scenario tests planting-window reasoning. The field is prepared, but the initial soil state is wet and cold. The oracle checks weather, forecast, and soil sensors, then waits through daily physics updates until seed-zone temperature and VWC enter the planting range. It then plants all 64 ridges in 4-ridge passes and commits the planting effects. This scenario expands the existing planting scenario by making the decision to wait part of the oracle rather than assuming the field is already ready.

Physics emphasis:

- weather playback
- soil temperature and VWC
- trafficability / planting readiness
- management effect from planting
- phenology initialization

### 2. `scenario_physics_emergence_replant_decision.py`

This scenario tests delayed feedback from planting. The field was planted earlier, but a cold/wet period caused poor emergence in ridges 12-19. The oracle uses soil sensors, canopy readings, UAV survey, and ground inspection to verify low stand establishment. Because the date is still inside the acceptable replant window, it replants only the failed block, not the full field.

Physics emphasis:

- phenology emergence
- planting quality / stand fraction
- observation model under partial coverage
- replant vs accept-loss decision

### 3. `scenario_physics_differential_diagnosis_fertigation.py`

This scenario tests differential diagnosis of a low-NDVI anomaly. A ridge block has depressed NDVI, but soil moisture is normal and thermal imagery does not show water stress. Ground inspection/SPAD-like evidence indicates nutrient limitation rather than pest or drought. The oracle applies ridge-level fertigation rather than irrigation or pesticide.

Physics emphasis:

- observation model
- canopy/biomass state
- nutrient stress
- management-effect model
- avoiding wrong action type

### 4. `scenario_physics_pod_fill_drought_irrigation.py`

This scenario tests irrigation during the sensitive seed-fill period. A dry spell affects ridges 20-43 during R5/R6. The oracle uses soil sensors, forecast, and thermal survey to confirm water stress and irrigates before yield potential is penalized further. A follow-up check verifies delayed soil response.

Physics emphasis:

- soil bucket model
- water-stress multiplier
- phenology-stage sensitivity
- delayed irrigation response
- yield-potential preservation

### 5. `scenario_physics_disease_after_rain_fungicide.py`

This scenario tests disease response after a wet period. Repeated rain and high topsoil moisture increase disease pressure in a ridge block. The oracle uses multispectral and thermal observations plus ground inspection to confirm disease risk, waits until the weather is sprayable, then applies fungicide. This scenario separates disease logic from generic pesticide response.

Physics emphasis:

- disease pressure from rainfall and wet soil
- observation model
- spray window constraints
- fungicide residual effect
- avoiding spraying during rain/high wind

### 6. `scenario_physics_threshold_pest_monitoring.py`

This scenario tests pest monitoring with a thresholded response. Initial evidence is weak, so the oracle does not spray immediately. After one daily physics update, insect pressure increases under favorable weather, and ground inspection crosses a threshold-like condition. The oracle then treats only the affected block.

Physics emphasis:

- latent biotic pressure
- trend over time
- threshold-like pest decision
- ground verification
- treatment timing

### 7. `scenario_physics_harvest_moisture_timing.py`

This scenario tests harvest timing. The crop is at R8, but grain moisture is initially too high for ideal harvest. The oracle waits one dry-down day, rechecks maturity/moisture, then harvests before forecast rain and shattering risk increase. This extends the existing harvest scenario by making grain moisture and dry-down part of the control problem.

Physics emphasis:

- R8 vs harvest readiness
- grain moisture dry-down
- weather risk
- recovered-yield loss from timing
- harvest logistics

### 8. `scenario_physics_postharvest_drying_storage.py`

This scenario tests post-harvest handling. Harvest is complete, but grain moisture is too high for safe storage. The oracle dries grain to a safe storage moisture, stores it, and handles residue through incorporation rather than open burning. This extends the lifecycle beyond field harvest.

Physics emphasis:

- recovered-yield state
- grain moisture and drying
- storage readiness
- residue status
- next-cycle state reset

## Design notes

Each scenario follows the same pattern:

1. Initialize a realistic farm state.
2. Expose a high-level task to the agent.
3. Require the oracle to observe current state rather than read hidden truth directly.
4. Use physics-aware waiting or commitment steps where delayed effects matter.
5. End with a report and a state commitment.

The scenarios are intended as L2 episodes: bounded control problems with feedback. They are not L1 single-event reactions and not full L3 season-long cycles. They can later be composed into L3 scenarios such as planting-to-emergence, growth-monitoring-to-treatment, or maturity-to-storage.
