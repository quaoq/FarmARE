# FARM Full-Season V2 L3 Scenarios

This directory contains the current FARM L3 full-season expert-oracle
scenarios. These scenarios are Harbin / Heilongjiang soybean season tasks
built on FARM apps and physics engines.

The older full-season document under `scenario_farm_world_fullseason/` is
left as historical/legacy context. For new L3 full-season work, use this
directory and this document.

## Core Rule

V2 scenarios are expert-oracle scenarios, not hidden full-potential optimizers.
The oracle should be a realistic expert management path:

1. Observe with agent-facing tools.
2. Diagnose from returned values.
3. Act with fixed, agronomically plausible timing, targets, and amounts.
4. Validate the result with daily CSV state and oracle trace returns.

The expert can be very good, but it should not act from hidden constants alone.
Affected ranges may exist in the profile, but the event flow must show how the
agent/expert would discover the affected zone.

## File Pattern

Production scenario files live here:

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/
```

Trace-only runners live under:

```text
scripts/fullseason/run_<scenario_slug>_trace.py
```

Production scenarios should:

- inherit from `Scenario`;
- register a stable `SCENARIO_ID`;
- configure one deterministic physics profile;
- keep hidden scenario facts in `SCENARIO_DESCRIPTION` or constants;
- keep `briefing_text` as the agent-facing prompt only;
- build a complete `self.events` graph;
- avoid `capture_daily_state()` in the production scenario.

Trace runners should:

- reuse the same oracle path;
- add trace capture outside the production scenario;
- write field summary CSV, ridge daily CSV, and oracle trace JSON;
- include enough completed tool returns to audit observation -> decision ->
  action support.

## Briefing Text Standard

`briefing_text` is what the agent receives. It should say:

- the season task;
- allowed checks/tools;
- operation constraints;
- success criteria.

It should not say:

- the hidden affected ridge range;
- the final diagnosis;
- the exact oracle treatment plan;
- the final harvest sequence.

## Observation Chain

Use this chain for targeted diagnosis:

```text
weather / forecast / soil / canopy sensors
-> whole-field or coarse-zone drone survey
-> suspect-zone drone / thermal survey
-> robot ground inspection
-> targeted operation
```

Scope rules:

- Targeted `fly_survey()` must be supported by recent sensor anomaly zones, or
  clearly marked as routine/reference/control.
- Targeted `robot.inspect_*()` must be supported by recent drone coverage, or
  clearly marked as routine/reference/control.
- Targeted spray, irrigation, fertigation, replant, or harvest must be
  supported by preceding tool returns.
- If sensors show no anomaly, do not jump directly to targeted ground checks.
- Drone/robot battery checks must avoid key surveys returning partial coverage.

## How To Add A New V2 Scenario

Use this checklist when adding a new L3 full-season scenario.

### 1. Write The Scenario Semantics First

Before coding, define:

- scenario id and short slug;
- cultivar(s), density, ridge layout, and zones;
- normal season background and the special stress/problem;
- hidden profile facts, such as affected ridges or soil modifiers;
- what the agent can observe through tools;
- what the expert oracle should do, with fixed dates/windows and amounts;
- what the scenario is explicitly not testing.

Example distinction:

- Hidden profile fact: ridges `20-31` are fast-draining.
- Agent-facing path: soil sensors and thermal/canopy observations identify the
  stressed block before targeted irrigation.

### 2. Create The Production Scenario

Create:

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/scenario_<slug>.py
```

Production scenario requirements:

- inherit directly from `Scenario`;
- define `SCENARIO_ID`, `PROFILE_NAME`, seed constants, and zone constants;
- register with `@register_scenario(SCENARIO_ID)`;
- use `SCENARIO_DESCRIPTION` for full hidden scenario description;
- use `briefing_text` only as the agent prompt;
- configure `FarmWorldApp`, `WeatherApp`, `SensorApp`, drones, robot, tractor,
  field ops, and `SystemApp`;
- call `farm_world.configure_physics_profile(profile_name=PROFILE_NAME, ...)`;
- build a complete root briefing plus oracle event graph in `build_events_flow`;
- include hooks such as `_after_daily_advance()` and `_after_named_step()` if
  trace scripts need to instrument waits;
- do not define trace apps or `capture_daily_state()` in the production file.

### 3. Add Or Reuse Physics Profile Support

Put deterministic profile configuration in `are/simulation/physics/profiles.py`
when the scenario needs:

- weather regime or scheduled weather events;
- cultivar parameters;
- initial soil/nutrient/biotic state;
- ridge-level soil modifiers;
- biotic outbreaks;
- harvest dry-down or recovery behavior.

Only change engine code when profile/scenario configuration is not enough and
the same physical inconsistency appears across multiple scenarios.

### 4. Add The Trace Runner

Create:

```text
scripts/fullseason/run_<slug>_trace.py
```

Prefer `scripts/fullseason/harbin_l3_trace_utils.py`:

```python
summary = run_trace(
    scenario_cls=ScenarioClass,
    scenario_id=SCENARIO_ID,
    trace_app_name="ReadableDailyTraceName",
    zones=[("affected_20_31", 20, 31), ("reference_0_11", 0, 11)],
    field_csv=Path("docs/ai/<slug>-field-summary.csv"),
    ridge_csv=Path("docs/ai/<slug>-ridge-states.csv"),
    trace_json=Path("docs/ai/<slug>-oracle-trace.json"),
    diagnostics=optional_extra_checks,
)
```

The trace runner may subclass/wrap the production scenario and inject
`capture_daily_state()`. The production scenario must stay clean.

### 5. Register The Scenario In Review

Add a `ScenarioSpec` to `scripts/fullseason/review_fullseason_l3_scenarios.py` with:

- slug;
- `SCENARIO_ID`;
- field CSV path;
- ridge CSV path;
- oracle trace JSON path;
- required zones.

Add scenario-specific checks only when the generic checks cannot express the
scenario's intended behavior.

### 6. Generate, Read, Then Fix

Run:

```bash
uv run python scripts/fullseason/run_<slug>_trace.py
uv run python scripts/fullseason/review_fullseason_l3_scenarios.py
```

Then manually read:

- ridge CSV around diagnosis, treatment, R5/R6, R8, and harvest;
- oracle trace `return_value` before each key action;
- action support report for targeted scope and harvest readiness.

If the trace is wrong, fix in this order:

1. oracle timing/check/action scope;
2. app return values;
3. profile parameters;
4. engine behavior.

Do not patch generated CSV/JSON.

### 7. Validate Production Oracle

Run:

```bash
uv run python -m are.simulation.main \
  --scenario-id <scenario_id> \
  --oracle \
  --export \
  --output_dir /tmp/<scenario_id>_check \
  --log-level WARNING
```

Then inspect the export for failed events or tool-level `"error"` returns.

## Current Scenarios

| Scenario ID | File | Profile | Crop Layout | Main Point |
|---|---|---|---|---|
| `scenario_full_season_heinong60_high_density_baseline` | `scenario_full_season_heinong60_high_density_baseline.py` | `harbin_baseline_2026_seed_101` | all ridges `HEINONG60`, high density | Normal high-density baseline |
| `scenario_full_season_wet_june_ab_zoned_disease` | `scenario_full_season_wet_june_ab_zoned_disease.py` | `harbin_wet_june_ab_zoned_seed_313` | A `0-31` `HEINONG84`, B `32-63` `HEINONG60` | Wet-June B-zone disease, targeted fungicide |
| `scenario_full_season_heinong84_edge_low_fertility` | `scenario_full_season_heinong84_edge_low_fertility.py` | `harbin_heinong84_edge_low_fertility_seed_414` | all ridges `HEINONG84` | Edge low fertility plus small gap-filling replant |
| `scenario_full_season_fastdraining_dry_patch_irrigation` | `scenario_full_season_fastdraining_dry_patch_irrigation.py` | `harbin_fastdraining_dry_patch_seed_515` | all ridges `HEINONG84` | Local fast-draining R5/R6 water stress |
| `scenario_full_season_heinong84_staggered_planting` | `scenario_full_season_heinong84_staggered_planting.py` | `harbin_heinong84_staggered_planting_seed_616` | `HEINONG84`, three planting-date zones | Split phenology and split harvest windows |
| `scenario_full_season_heinong84_threshold_insect_limited_spray` | `scenario_full_season_heinong84_threshold_insect_limited_spray.py` | `harbin_heinong84_heat_dry_insect_seed_717` | all ridges `HEINONG84` | Threshold insect treatment with limited spray |
| `scenario_full_season_heinong84_low_chemical_wet_disease` | `scenario_full_season_heinong84_low_chemical_wet_disease.py` | `harbin_heinong84_low_chemical_wet_disease_seed_818` | all ridges `HEINONG84` | Low-chemical wet-June disease threshold |
| `scenario_full_season_early_vs_standard_late_rain_harvest` | `scenario_full_season_early_vs_standard_late_rain_harvest.py` | `harbin_early_standard_late_rain_seed_919` | A `0-31` `HEIKE71`, B `32-63` `HEINONG84` | Early vs standard cultivar harvest timing |
| `scenario_full_season_hb_base_hn84_std_normal` | `scenario_full_season_hb_base_hn84_std_normal.py` | `harbin_hb_base_hn84_std_normal_seed_1101` | all ridges `HEINONG84` | Normal Heinong84 standard-density baseline |
| `scenario_full_season_hb_dryr5r6_hn58_std_waterlimit` | `scenario_full_season_hb_dryr5r6_hn58_std_waterlimit.py` | `harbin_hb_dryr5r6_hn58_waterlimit_seed_1202` | all ridges `HEINONG58` | R5/R6 drought with water allocation limits |
| `scenario_full_season_hb_poordrainage_wetjune_disease_trafficability` | `scenario_full_season_hb_poordrainage_wetjune_disease_trafficability.py` | `harbin_hb_poordrainage_wetjune_disease_seed_1303` | all ridges `HEINONG84` | Poor drainage, wet-June disease, spray-window constraints |
| `scenario_full_season_hb_soy_after_soy_wetjune_disease` | `scenario_full_season_hb_soy_after_soy_wetjune_disease.py` | `harbin_hb_soy_after_soy_wetjune_disease_seed_1404` | all ridges `HEINONG84` | Soy-after-soy disease-history risk |

## Scenario Families

### Baselines

- `scenario_full_season_heinong60_high_density_baseline`
- `scenario_full_season_hb_base_hn84_std_normal`

These should not hide disease, drought, or late-rain traps. They are comparison
points for cultivar, density, yield, and normal management flow.

### Disease And Biotic Pressure

- `scenario_full_season_wet_june_ab_zoned_disease`
- `scenario_full_season_heinong84_low_chemical_wet_disease`
- `scenario_full_season_hb_poordrainage_wetjune_disease_trafficability`
- `scenario_full_season_hb_soy_after_soy_wetjune_disease`
- `scenario_full_season_heinong84_threshold_insect_limited_spray`

These must show pressure in CSV and tool returns. Treatment should be delayed
until threshold/diagnosis support exists, then targeted to the supported range.

### Water And Soil Heterogeneity

- `scenario_full_season_fastdraining_dry_patch_irrigation`
- `scenario_full_season_hb_dryr5r6_hn58_std_waterlimit`

These should separate root-zone crop stress from topsoil trafficability. The
agent should not irrigate the whole field when only a block is stressed.

### Phenology And Harvest Windows

- `scenario_full_season_heinong84_staggered_planting`
- `scenario_full_season_early_vs_standard_late_rain_harvest`

These test non-uniform maturity. Harvest must follow per-zone stage, grain
moisture, weather, and trafficability checks, with unload/dry/store tied to
each harvested batch when appropriate.

### Local Establishment Or Fertility

- `scenario_full_season_heinong84_edge_low_fertility`

This tests early diagnosis of weak edge ridges. Gap-filling replant should
improve `stand_fraction` without resetting established phenology.

## Daily Trace Expectations

The ridge daily CSV should include, at minimum:

- `date`, `ridge_id`, `zone`;
- stage and days after planting;
- top/root VWC and water stress;
- nutrient index/stress;
- weed, insect, and disease pressure;
- LAI, NDVI proxy, canopy temperature proxy;
- grain moisture;
- biological and recovered yield.

Manual review should check:

- stages move forward without regression;
- R8 is maturity, not automatic harvest day;
- post-R8 biological yield does not jump unexpectedly;
- recovered yield does not increase after harvest;
- VWC responds to rainfall, ET, irrigation, and drainage;
- disease/insect pressure changes with outbreak and treatment;
- affected and reference zones differ for the intended reason;
- harvest operations only cover ridges supported by preceding checks.

## Useful Scripts

Core scripts for v2 scenario work:

| Script | Use |
|---|---|
| `scripts/fullseason/harbin_l3_trace_utils.py` | Shared trace app, CSV/JSON writers, yield summary, and generic diagnostics for v2 L3 scenarios |
| `scripts/fullseason/review_fullseason_l3_scenarios.py` | Unified audit for all v2 L3 trace outputs and action-support chains |
| `scripts/fullseason/run_heinong60_high_density_baseline_trace.py` | Trace runner for high-density Heinong60 baseline |
| `scripts/fullseason/run_wet_june_ab_zoned_disease_trace.py` | Trace runner for wet-June A/B zoned disease |
| `scripts/fullseason/run_heinong84_edge_low_fertility_trace.py` | Trace runner for edge low-fertility scenario |
| `scripts/fullseason/run_fastdraining_dry_patch_irrigation_trace.py` | Trace runner for fast-draining dry patch scenario |
| `scripts/fullseason/run_heinong84_staggered_planting_trace.py` | Trace runner for staggered planting scenario |
| `scripts/fullseason/run_threshold_insect_limited_spray_trace.py` | Trace runner for threshold insect scenario |
| `scripts/fullseason/run_low_chemical_wet_disease_trace.py` | Trace runner for low-chemical wet disease scenario |
| `scripts/fullseason/run_early_vs_standard_late_rain_harvest_trace.py` | Trace runner for early-vs-standard late-rain harvest scenario |
| `scripts/fullseason/run_hb_base_hn84_std_normal_trace.py` | Trace runner for HB normal Heinong84 baseline |
| `scripts/fullseason/run_hb_dryr5r6_hn58_std_waterlimit_trace.py` | Trace runner for HB dry R5/R6 water-limit scenario |
| `scripts/fullseason/run_hb_poordrainage_wetjune_disease_trafficability_trace.py` | Trace runner for HB poor-drainage wet-June disease scenario |
| `scripts/fullseason/run_hb_soy_after_soy_wetjune_disease_trace.py` | Trace runner for HB soy-after-soy wet-June disease scenario |

Related but not v2-specific:

| Script | Use |
|---|---|
| `scripts/build_oracle_baselines.py` | Build oracle-baseline JSONs for registered scenarios |
| `scripts/rebatch_fos_from_traces.py` | Re-evaluate FOS traces in batch |
| `scripts/trace_tangyan5_event_path_daily_states.py` | Tangyan event-path daily-state diagnostics |
| `scripts/export_balanced_v2_daily_trace_csv.py` | Older balanced-v2 daily CSV export |
| `scripts/_dump_farm_world_fullseason_oracle_log.py` | Debug helper for full-season oracle event logs |
| `scripts/_dump_farm_world_physics_oracle_log.py` | Debug helper for FARM physics oracle event logs |

Usually do not include these in a v2 scenario commit unless the commit is
specifically about reporting or debugging:

- `scripts/_rerun_failed_once.py`
- `scripts/finalize_summaries.py`
- `scripts/generate_paper_tables_sheet.py`

## Validation Commands

Run the relevant trace first:

```bash
uv run python scripts/fullseason/run_<scenario_slug>_trace.py
```

Then run the unified review:

```bash
uv run python scripts/fullseason/review_fullseason_l3_scenarios.py
```

Accepted current result:

```text
status_counts: {"pass": 12}
```

Run production oracle export:

```bash
uv run python -m are.simulation.main \
  --scenario-id <scenario_id> \
  --oracle \
  --export \
  --output_dir /tmp/<scenario_id>_check \
  --log-level WARNING
```

Acceptance:

- trace `failed_event_count=0`;
- trace `error_return_count=0`;
- review report `status="pass"`;
- `supported_key_actions == key_action_count`;
- production export succeeds;
- exported completed events contain no tool-level `"error"` return.

## Generated Artifacts

Generated trace artifacts live in `docs/ai/`:

```text
docs/ai/<slug>-field-summary.csv
docs/ai/<slug>-ridge-states.csv
docs/ai/<slug>-oracle-trace.json
docs/ai/<scenario>-review-report.json
docs/ai/fullseason-l3-review-summary.md
```

These are diagnostic outputs, not source files to hand-edit. If generated state
is wrong, fix scenario flow, app return semantics, physics profile, or engine,
then regenerate.

## Related Files

- Shared v2 helpers:
  `are/simulation/scenarios/scenario_farm_world_fullseason_v2/harbin_l3_scenario_helpers.py`
- Trace utilities:
  `scripts/fullseason/harbin_l3_trace_utils.py`
- Review script:
  `scripts/fullseason/review_fullseason_l3_scenarios.py`
- Workflow checklist:
  `docs/ai/fullseason-l3-scenario-workflow.md`
