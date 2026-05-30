# Farm-ARE Physics Scenario Versions: Action/Tick/Observation Boundary

This folder contains the corrected physics-aware versions of the uploaded baseline scenarios. These are not the earlier “commit after every operation” variants, and they are not the earlier “hide everything inside tools with no contract” variants. They use the intended farm-simulation boundary:

```text
farm action tool  -> direct physical/action effect
clock or tick     -> elapsed-time world evolution
sensor/drone/robot -> noisy, sparse, delayed observation
```

This is the cleanest plug-in path for the current codebase because it matches how the simple FarmWorld scenarios already behave while making the new physics engines explicit at the correct abstraction level.

The scenario files keep the original task scope and oracle flow as close as possible. The main work now moves to the app/tool implementations and the shared world state. The scenarios should be able to run once the existing app methods are upgraded to honor the action/tick/observation contract described here.

## Why this boundary is the right one

A real farm operation has immediate direct effects and delayed indirect effects.

When a tractor plants, something real changes immediately: the ridge now has a seed placement action, seed type, depth, spacing, planting date, and a planting quality. But plants do not emerge at that moment. Emergence is a time-dependent consequence driven by soil temperature, soil moisture, seed type, and accumulated thermal time.

When irrigation runs, something real changes immediately: valves open, water is delivered, the soil receives an input, and inventory/energy/pump state may change. But canopy recovery, NDVI recovery, and yield recovery do not happen immediately. Those are delayed effects.

When pesticide is sprayed, something real changes immediately: treatment is applied to a ridge range and a residual window starts. But pest pressure and biomass response evolve over later ticks.

When harvest runs, recovered yield and trailer inventory can update immediately because the machine physically removes grain in that operation. But if the crop is not at proper moisture, field loss and machine loss must be computed from the yield/recovery model, not assigned as a fixed constant.

Therefore, the boundary should be:

```text
Action tools update direct action effects.
World ticks update time-dependent biological/environmental consequences.
Observation tools expose noisy/sparse products derived from hidden physics state.
```

This is different from two bad extremes:

```text
Bad extreme A:
    tool call does nothing except log action
    scenario must call commit_daily_physics after everything
```

That is artificial and makes scenarios noisy.

```text
Bad extreme B:
    every tool secretly runs arbitrary full-world physics
    scenario gives no clue when world evolution occurs
```

That is hard to debug and hard to evaluate.

The correct implementation is:

```text
plant_seeds(...)
    -> direct planting effect
    -> action history record
    -> no emergence yet

advance_time(days=12) or scheduled ARE ticks
    -> weather evolves
    -> soil evolves
    -> phenology accumulates GDD
    -> emergence may occur
    -> canopy starts later

read sensors / fly survey / inspect with robot
    -> observation model reads hidden state with noise/coverage/latency
```

## Scenario philosophy

The files in this folder are targeted development scaffolds. They are not full-season scenarios. They preserve the original baseline scenario scope:

- field preparation
- planting
- irrigation
- fertilizer
- drone survey
- pesticide
- pesticide outbreak
- harvest

The reason to preserve them is that we want to test the new physics engines without changing everything at once. The scenarios should expose whether the current apps and tools can now support the more realistic world model.

The scenario files contain `_configure_physics_layers()` methods. These methods initialize hidden physics state and compatibility fields. They are not supposed to solve the scenario. They set the initial world condition so the original oracle can run against a physics-aware world.

## Required app-level architecture

### 1. FarmWorldApp should own the shared physics state

`FarmWorldApp` should become the central owner of the physics world. It should hold or route access to:

```python
weather_engine
soil_engine
phenology_engine
canopy_biomass_engine
biotic_pressure_engine
management_effect_engine
yield_recovery_engine
observation_model
```

It should also maintain compatibility mirrors for older tools:

```python
ridge.soil_vwc
ridge.soil_temp_c
ridge.ndvi
ridge.yield_potential
ridge.pest_pressure
ridge.growth_stage
ridge.grain_moisture_pct
```

But these compatibility fields should not be the source of truth long term. The source of truth should be the physics state. Compatibility mirrors exist so old tools and validators do not break immediately.

A useful internal pattern is:

```python
class FarmWorldApp:
    physics_state: FarmPhysicsState
    pending_actions: list[FarmActionRecord]
    ridges: list[RidgeState]

    def record_action(self, action: FarmActionRecord) -> None:
        ...

    def sync_compatibility_fields_from_physics(self) -> None:
        ...

    def sync_observation_cache(self) -> None:
        ...
```

### 2. Actions should be recorded

Every operation tool should append a structured action record. This is important for sequence-level evaluation and for debugging.

Recommended fields:

```python
@dataclass
class FarmActionRecord:
    action_id: str
    timestamp: float
    actor_app: str
    action_type: str
    ridge_ids: list[int]
    parameters: dict
    direct_effect_summary: dict
    status: str
```

Examples:

```python
FarmActionRecord(
    action_type="planting",
    ridge_ids=[0,1,2,3],
    parameters={
        "seed_type": "STANDARD",
        "seed_depth_cm": 4.0,
        "spacing_cm": 5.0,
    },
    direct_effect_summary={
        "phenology_stage": "PLANTED_PRE_EMERGENCE",
        "stand_fraction_initial": 0.95,
    },
)
```

```python
FarmActionRecord(
    action_type="irrigation",
    ridge_ids=list(range(22, 33)),
    parameters={
        "duration_hours": 1.5,
        "estimated_water_mm": 9.0,
    },
    direct_effect_summary={
        "soil_water_input_registered": True,
    },
)
```

This gives the queue representation something richer than final state. It also allows the benchmark to evaluate wrong plans that were scheduled or attempted even if later events prevented them.

### 3. Time advancement should run world ticks

Elapsed time needs a single clear pathway. It can be attached to `SystemApp.advance_time(...)`, `wait_for_notification(...)`, or ARE’s time manager. The implementation should ensure that when simulated time advances, the following modules update in order:

```text
weather update
soil update
management residual aging
phenology update
canopy/biomass update
biotic-pressure update
yield/recovery update
observation availability update
compatibility-field sync
```

The exact order matters. A recommended daily tick order is:

```text
1. apply weather for the day
2. apply pending management water/nutrient/treatment inputs
3. update soil moisture and soil temperature
4. update phenology using weather + soil
5. initialize canopy growth if emergence occurred
6. update canopy/biomass using weather + phenology + soil + stress
7. update biotic pressure using weather + crop stage + canopy + treatment residuals
8. update yield/recovery if R8 or harvested
9. generate or update observation caches
10. sync compatibility fields
```

For sub-daily operations, use a smaller interval update where needed. But do not make every scenario manually call all module updates.

### 4. Observation tools should not read hidden truth directly

The drone, sensor, robot, and SPAD tools should read from the observation layer.

Correct pattern:

```text
hidden physics state -> observation model -> product -> agent/tool result
```

Incorrect pattern:

```text
tool directly returns ridge.insect_pressure
tool directly returns ridge.nutrient_index
tool directly returns ridge.disease_pressure
```

This matters because the agent is supposed to operate under partial observability. If tools reveal hidden truth, the benchmark becomes too easy and the “world model” claim is weakened.

## Tool-specific implementation contracts

### TractorApp.level()

Direct effect:

- records a field-prep action
- improves/sets surface preparation state
- may update trafficability or seedbed uniformity
- does not plant anything
- does not change crop state

Expected state updates:

```python
farm_world.field_prepared["level"] = True
farm_world.record_action("level", ridge_ids=all_ridges)
```

Potential physics update:

- seedbed quality improves
- soil surface state becomes more uniform
- if field too wet, action should fail or create compaction/risk tag

### TractorApp.apply_base_fertilizer()

Direct effect:

- records base fertilizer action
- updates management-effect nutrient index
- reduces initial nutrient stress
- consumes fertilizer inventory

Expected state updates:

```python
management_effect_engine.apply_base_fertilizer(...)
farm_world.inventory.fertilizer_kg -= amount
farm_world.record_action("base_fertilizer", ...)
```

It should not immediately raise NDVI because crop has not emerged yet.

### TractorApp.form_ridges()

Direct effect:

- records ridge-formation action
- sets ridge geometry/status
- makes field compatible with ridge-indexed operations

Expected state updates:

```python
farm_world.ridge_geometry_ready = True
farm_world.record_action("form_ridges", ...)
```

Pitfall: If ridge geometry is only a boolean, later operations cannot reason about ridge width, pass coverage, or alignment. Keep at least:

```python
ridge_width_m = 1.1
num_ridges = 64
rows_per_ridge = 2
field_length_m = 268
field_width_m = 71
```

### TractorApp.plant_seeds(start_ridge, end_ridge, depth_cm, spacing_cm)

Direct effect:

- records planting action
- assigns seed type
- assigns depth and spacing
- computes planting quality
- initializes phenology as planted/pre-emergence
- initializes management stand fraction or pending stand establishment
- consumes seed inventory/hopper
- updates ridge operation state

Expected state updates:

```python
for ridge in ridges:
    ridge.planted = True
    ridge.seed_type = current_seed_type
    ridge.planting_depth_cm = depth_cm
    ridge.spacing_cm = spacing_cm
    ridge.phenology_stage = "PLANTED_PRE_EMERGENCE"
    ridge.days_since_planted = 0
```

Physics-engine state:

```python
management_effect_engine.apply_planting(...)
phenology_engine.plant_ridges(...)
```

It should not:

- set VE immediately
- set NDVI to a canopy value immediately
- set yield potential immediately
- assume emergence succeeded

The first emergence check should occur only after elapsed time and thermal-time accumulation.

### FieldOpsApp.irrigate(...)

Direct effect:

- records irrigation action
- computes water delivered in mm or liters
- applies water input to soil model
- updates irrigation system state
- possibly consumes water/energy

Recommended conversion:

```text
duration_hours + flow_rate -> water_mm
```

Expected state updates:

```python
management_effect_engine.apply_irrigation(...)
soil_engine.apply_irrigation_input(...)
farm_world.record_action("irrigation", ...)
```

It may update top/root VWC immediately if the irrigation duration is represented as having elapsed in the operation event. If the tool only schedules irrigation, then water should enter during the subsequent tick or notification event.

It should not:

- directly restore NDVI
- directly restore yield potential
- erase water-stress history

### Fertilizer / fertigation tools

The codebase may have multiple names for these. The physics contract is the same.

Direct effect:

- records nutrient application
- consumes fertilizer inventory
- updates nutrient index
- updates nutrient stress multiplier
- optionally applies water if fertigation

Expected state updates:

```python
management_effect_engine.apply_base_fertilizer(...)
# or
management_effect_engine.apply_fertigation(...)
```

It should not:

- immediately set NDVI to normal
- immediately reset yield potential
- hide nutrient stress from future evaluation

Canopy recovery should occur through future growth ticks.

### TractorApp.spray_pesticide(...)

Direct effect:

- records insecticide/pesticide treatment action
- checks weather/application constraints
- updates biotic-pressure treatment residual
- reduces current insect pressure according to efficacy
- consumes chemical inventory
- updates tank level

Expected state updates:

```python
biotic_pressure_engine.apply_treatment(TreatmentType.INSECTICIDE, ...)
management_effect_engine.apply_treatment(...)
farm_world.record_action("insecticide", ...)
```

Weather constraints:

- rain should block or reduce efficacy
- high wind should block or reduce efficacy
- wet field may block tractor access

It should not:

- directly restore NDVI
- directly erase crop damage
- treat disease unless the treatment type is fungicide

### Fungicide tools

The disease scenario variants assume a separate fungicide concept. If the current code only has `spray_pesticide`, decide whether to:

1. add `load_fungicide` and `apply_fungicide`, or
2. generalize `spray_pesticide` with `chemical_type`.

The physics engine should distinguish:

```text
insecticide -> insect pressure
fungicide -> disease pressure
herbicide -> weed pressure
```

Do not collapse all three into one generic “pesticide” effect if the scenario is meant to test diagnosis.

### RobotApp.inspect_pests(...)

Observation effect:

- reads observation model
- returns pest detection with uncertainty
- may include confidence
- may include ridge-level result

It should not expose hidden `insect_pressure` directly.

Recommended result shape:

```python
{
    "ridge_ids": [18],
    "detections": {
        18: {
            "pest_present": True,
            "confidence": 0.83,
            "method": "ground_rgb_inspection"
        }
    }
}
```

### RobotApp.inspect_crop_health(...)

Observation effect:

- combines local visual symptoms, SPAD-like reading, pest/disease detection, or canopy condition
- returns observation, not hidden truth

This is important for the fertilizer scenario because the agent should distinguish nutrient stress from drought/pest/disease using observation evidence.

### DroneApp.fly_survey(...)

Observation effect:

- uses observation model to generate NDVI or thermal product
- respects battery/coverage/weather limits
- returns ridge-indexed observation product
- may return partial survey if battery insufficient

It should not:

- read hidden pest pressure and label the cause directly
- deterministically reveal all anomalies without noise
- ignore cloud/rain/wind constraints

Recommended output fields:

```python
{
    "surveyed_ridges": [...],
    "product_type": "NDVI_MAP",
    "observations": {
        ridge_id: {"ndvi": 0.61}
    },
    "anomaly_tags": [...],
    "battery_remaining_pct": ...
}
```

### SensorApp.read_soil_sensors()

Observation effect:

- reads fixed sensor layout
- returns sparse measurements
- optionally interpolates or reports nearby ridges
- includes measurement noise

Important: fixed sensors are not installed on every ridge. If the current baseline returns all-ridge soil truth, that should be treated as a temporary compatibility mode. The observation model should eventually reflect the six installed soil sensors.

### SensorApp.read_canopy_sensors()

Observation effect:

- reads canopy index sensors
- returns sparse canopy-index values
- may not capture every local anomaly unless sensors are near the affected ridge

For development, it is acceptable to keep compatibility outputs, but the long-term model should separate fixed sensor readings from UAV maps.

### TractorApp.harvest(...)

Direct effect:

- records harvest pass
- computes recovered yield for the ridge range
- updates ridge harvested state
- updates machine/trailer state
- applies machine loss and moisture adjustment
- may leave grain in tractor/combine tank until unload

Expected state updates:

```python
yield_recovery_engine.apply_harvest_pass(...)
tractor.grain_tank_kg += recovered_kg
ridge.harvested = True
farm_world.record_action("harvest", ...)
```

It should not:

- add a fixed yield independent of grain moisture
- ignore machine quality
- ignore field loss
- ignore repeated wet/dry or delayed harvest risks

### TractorApp.unload_grain()

Direct effect:

- transfers grain from harvester/combine tank to trailer/storage
- records logistics event
- should not create yield by itself

Expected state updates:

```python
container_trailer.grain_kg += tractor.grain_tank_kg
tractor.grain_tank_kg = 0
farm_world.record_action("unload_grain", ...)
```

## Scenario-specific notes

### `scenario_field_prep_physics_action_tick.py`

This scenario should work almost identically to the baseline. The physics changes are in the direct action effects:

- `level()` records seedbed/surface preparation
- `apply_base_fertilizer()` updates nutrient baseline
- `form_ridges()` updates ridge geometry and prepared-field state

No time tick is necessary unless the implementation models field drying or post-operation settling. Do not add artificial waiting unless required by the tool implementation.

Main implementation risk:

```text
field prep currently may only update completed_prep_ops
```

That is not enough. Later planting scenarios need prepared geometry and soil/management state.

### `scenario_planting_physics_action_tick.py`

This scenario should preserve the same planting sequence and hopper reloads.

The planting tool must initialize physics state. The scenario should not need `commit_daily_physics()` after planting if `plant_seeds()` correctly applies direct planting effects.

Expected after planting:

```text
planted=True
phenology_stage=PLANTED_PRE_EMERGENCE
seed_type=STANDARD
seed_depth_cm=4.0
spacing_cm=5.0
planting_quality computed
stand_fraction initialized/pending
```

The next time tick, not the planting action, should accumulate GDD and potentially trigger emergence.

### `scenario_irrigation_physics_action_tick.py`

This scenario currently irrigates ridges 22-32 and then waits for a notification before verifying soil moisture.

That is already a good action/tick pattern:

```text
irrigate -> wait/notification -> read sensors
```

The missing implementation is that the irrigation event or the wait/notification must advance the soil model before the sensor read.

Recommended behavior:

- `irrigate()` records water delivery and opens/closes system state
- the operation duration or notification wait advances soil response
- sensor read observes updated VWC with measurement noise

Main pitfall:

```text
sensor read may still use manually initialized cached values
```

Make sure sensor values refresh from soil-engine state after irrigation.

### `scenario_fertilizer_physics_action_tick.py`

The baseline applies targeted fertilizer after low NDVI is detected.

The physics-aware behavior should be:

```text
low NDVI observed
fertilizer applied
nutrient_index increases
nutrient_stress improves
NDVI does not immediately jump
future growth ticks show recovery
```

If the scenario ends immediately after fertilizer, validation should focus on whether the correct action was taken and the management state was updated, not whether canopy recovered instantly.

Main pitfall:

```text
existing code may assume immediate yield_potential recovery
```

Avoid that. Recovery belongs to canopy/biomass evolution.

### `scenario_drone_survey_physics_action_tick.py`

The baseline is already useful because it includes partial survey, charging, and ground inspection.

The physics-aware behavior should be:

```text
hidden canopy/biotic state exists
drone survey observes NDVI product with noise
anomaly is detected as an observation
robot inspects selected ridge(s)
robot returns detection/confidence
```

Important note:

The baseline comment says the anomaly spans ridges 15-22, but the constants define a single anomaly ridge 18. The physics version preserves the executable constants. If the intended anomaly is 15-22, update the constants before final benchmarking.

Main pitfall:

```text
drone survey may currently reveal pest_pressure directly
```

It should not. UAV NDVI should indicate anomaly, not cause. Robot/ground inspection should help identify cause.

### `scenario_pesticide_physics_action_tick.py`

The baseline intent is targeted pesticide treatment: tractor boom handles the main block, and manual/backpack treatment handles ridge 25.

The physics-aware behavior should be:

```text
insect pressure exists as hidden biotic state
weather and wind are checked
treatment is applied
biotic pressure is reduced according to efficacy
residual insecticide window begins
future growth ticks reflect reduced biotic stress
```

Main pitfall:

If the code only records that a ridge was sprayed but does not update biotic-pressure state, the scenario will not test the physics engine.

Second pitfall:

Manual treatment must also create a treatment action. Do not update only tractor-boom spray and forget the backpack/hand-spray path.

### `scenario_pesticide_outbreak_physics_action_tick.py`

This scenario represents an outbreak that is already above threshold at start. Unlike the threshold-monitoring L3 scenario, this one does not need to wait for trend confirmation.

The physics-aware behavior should be:

```text
large hidden insect pressure on ridges 15-39
agent confirms with available observations
tractor sprays in multiple passes
biotic pressure drops
residual treatment active
```

Main pitfall:

Do not erase crop damage. Treatment reduces future pressure. It does not rewind past biomass/yield loss.

### `scenario_harvest_physics_action_tick.py`

This scenario should use harvest as a direct recovered-yield operation.

The baseline already checks:

- R8 maturity
- grain moisture
- soil/trafficability
- weather/forecast
- drone uniformity
- tractor fuel
- harvester attachment
- 4-ridge harvest passes
- unload cycles

That is a good structure.

The missing implementation is in `harvest()` and `unload_grain()`:

```text
harvest() computes recovered yield per ridge/pass
unload_grain() transfers grain from machine to trailer/storage
inventory reports transferred grain
```

The yield engine should handle:

- biological yield potential
- grain moisture
- field loss
- machine loss
- recovered yield at market moisture
- harvested state

Main pitfall:

If yield is added as a fixed kg amount, the recovered-yield model is bypassed.

## Required engine changes checklist

### A. Central physics state

Add a central state container or equivalent fields for:

```python
soil_state_by_ridge
phenology_state_by_ridge
canopy_biomass_state_by_ridge
biotic_pressure_state_by_ridge
management_effect_state_by_ridge
yield_recovery_state_by_ridge
observation_products
pending_action_history
```

### B. Compatibility synchronization

Old code likely expects direct ridge attributes. Keep them synchronized:

```python
ridge.soil_vwc <- soil.root_vwc or top_vwc depending on context
ridge.soil_temp_c <- soil.top_temp_c
ridge.growth_stage <- phenology.stage
ridge.ndvi <- canopy.ndvi_proxy or observation cache
ridge.yield_potential <- canopy/yield potential
ridge.pest_pressure <- biotic.insect_pressure compatibility only
ridge.grain_moisture_pct <- yield.grain_moisture_frac * 100
```

But document which ones are hidden truth and which ones are observable.

### C. Action records

Every operation tool should call:

```python
farm_world.record_action(...)
```

This enables:

- physics state update
- trace debugging
- oracle/agent sequence comparison
- future retrieval of similar action traces

### D. Time tick

Add one clear function for elapsed time:

```python
farm_world.advance_physics_time(delta_hours)
```

or call it from:

```python
SystemApp.advance_time(...)
SystemApp.wait_for_notification(...)
time_manager.add_offset(...)
```

The important part is that elapsed time should not just move a timestamp. It should run the physics modules.

### E. Observation refresh

After physics updates, refresh observation caches. But do not expose hidden truth unless the tool is explicitly a ground-truth debugging tool.

### F. Validation update

The validator should understand that in physics-aware scenarios:

- immediate direct action correctness matters
- delayed effect correctness may appear later
- final state may differ depending on elapsed time
- action history is part of correctness

For example, in irrigation, the correct oracle is not simply:

```text
called irrigate
```

It is:

```text
called irrigate on correct ridges
water was applied
soil stress decreased after elapsed time
no unnecessary irrigation elsewhere
```

## Minimal implementation order for the team

Implement in this order:

1. Add central physics state to `FarmWorldApp`.
2. Add `record_action`.
3. Upgrade `plant_seeds` to initialize management + phenology.
4. Upgrade `irrigate` and wait/notification to update soil.
5. Upgrade sensor reads to refresh from soil/canopy state.
6. Upgrade drone survey to use observation model.
7. Upgrade pesticide tools to update biotic pressure and residual windows.
8. Upgrade fertilizer tools to update nutrient state.
9. Upgrade harvest/unload to use yield recovery.
10. Add elapsed-time tick from `SystemApp` or time manager.

This order lets you test the scenarios one by one instead of integrating the full season at once.

## Why these scenario files are the most plug-in version

These files are closest to the original baselines while still reflecting the new physics boundary. They do not require adding explicit `commit_daily_physics()` after every action. They also do not pretend the physics engines are unrelated background code. They state the exact contract each existing app/tool must satisfy.

Use these for targeted dev. Use the explicit-commit version only as a diagnostic if you want scenarios to fail on a single missing hook. Use the long-horizon full-season scenarios only after these baseline physics variants work.
