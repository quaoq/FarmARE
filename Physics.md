# Physics.md — What We Built (Plain-English Tour)

This document is for the professor (and anyone else picking up the repo). It
explains, in simple words, what was added on the `farm-physics` branch:
the physics engine, the long-horizon scenario tiers, and the new evaluation
framework (FOS) that supersedes path-matching for long-horizon tasks.

If you want to *run* things, see [`RUNBOOK.md`](RUNBOOK.md).
If you want LLM-assistant context, see [`CLAUDE.md`](CLAUDE.md).

---

## 1. The big picture in one paragraph

The original FarmARE was a toy farm world: tools logged actions, but the
world didn't really evolve. Plant seeds → nothing actually grew unless the
scenario hard-coded an outcome. We replaced that with a **physics engine**
(7 modules + an observation model) that simulates a real soybean field over
weeks or a whole season — soil, weather, phenology, biomass, biotic
pressure, management effects, yield. We added **long-horizon scenarios**
that exercise this engine — single mid-season *episodes* (round 3) and
**full-season** runs from planting to storage (round 4). And because
trajectory-matching ("did the agent's tool sequence match the oracle's?")
breaks down on horizons of 100+ steps, we built a new **Farm Operational
Score (FOS)** that judges agents on three orthogonal axes: did the right
*outcome* happen, did they make the right *decisions* at the right
moments, and were they *efficient* about it.

That last point — FOS — is the core methodological contribution of the paper.

---

## 2. The physics engine

Located in [`are/simulation/physics/`](are/simulation/physics/). Seven
engines plus an observation model. Each one is a deterministic simulator
keyed on `(physics_profile, seed)` so a given scenario always evolves the
same way.

| Engine | What it models |
|---|---|
| [`weather_engine.py`](are/simulation/physics/weather_engine.py) | Daily temperature, rainfall, ET₀, solar radiation, wind. Driven by `PhysicsProfile` climate (e.g. `harbin_baseline_2026_seed_101`). |
| [`soil_engine.py`](are/simulation/physics/soil_engine.py) | Per-ridge soil temperature, volumetric water content (VWC), nutrient state. Updates from rainfall, irrigation, ET. |
| [`phenology_engine.py`](are/simulation/physics/phenology_engine.py) | Growing-degree-day accumulation; emergence (VE) → vegetative stages → flowering (R1) → pod fill (R5) → maturity (R8). Depends on seed type and soil temp. |
| [`canopy_biomass_engine.py`](are/simulation/physics/canopy_biomass_engine.py) | Above-ground biomass, NDVI, leaf area index. Driven by phenology + water + nutrient stress. |
| [`biotic_pressure_engine.py`](are/simulation/physics/biotic_pressure_engine.py) | Pest counts (aphids, beetles), disease pressure (rust, blight). Outbreaks are scheduled via `PhysicsProfile.biotic_outbreaks`. |
| [`management_effect_engine.py`](are/simulation/physics/management_effect_engine.py) | Translates **direct action effects** — planting, irrigation, fertilization, pesticide application, fungicide, residue incorporation — into per-ridge state changes. |
| [`yield_recovery_engine.py`](are/simulation/physics/yield_recovery_engine.py) | At harvest: turns biomass + grain moisture + canopy state into recovered yield kg per ridge. Field loss + machine loss accounted separately. |
| [`observation_model.py`](are/simulation/physics/observation_model.py) | Wraps hidden physics state with **noise, sparsity, and latency** for sensor reads, drone surveys, and robot inspections. The agent never sees ground truth — only this. |

All eight files have a paired `.md` markdown spec next to them documenting
the equations, parameters, and assumptions.

### The action / tick / observation boundary

The single most important design choice. Every farm tool falls into one of
three categories:

```
plant_seeds(...)               <- ACTION: direct physical effect, recorded immediately
advance_time(days=12)          <- TICK: weather/soil/phenology/biomass/pressure evolve
read_sensors() / fly_survey()  <- OBSERVATION: noisy, sparse, latency-bound view of hidden state
```

The boundary is enforced by [`farm_world_app.py`](are/simulation/apps/farm_world/farm_world_app.py):
actions go through `record_action()` and call into the relevant engine for
their direct effect; the clock advances physics via
[`physics_orchestrator.py`](are/simulation/apps/farm_world/physics_orchestrator.py)
(`advance_physics_time` is idempotent per logical day); observation tools
sample through `ObservationModel`.

This boundary is what lets us write scenarios where the agent doesn't see
the world's state — they have to plan, observe, and adapt.

### Physics profiles

[`profiles.py`](are/simulation/physics/profiles.py) defines 10
`PhysicsProfile` dataclasses, each pinning a deterministic climate +
biotic-outbreak schedule. Examples:

- `harbin_baseline_2026_seed_101` — normal Harbin soybean season
- `harbin_cold_spring_seed_202` — early cold snap, delayed planting
- `harbin_dry_pod_fill_seed_303` — drought during pod fill
- `harbin_adversarial_weather_seed_911` — wet planting + drought + storms
- ...

Profiles are how scenarios get reproducible weather/pest pressure. Same
profile + same seed → same world.

---

## 3. The scenario tiers (29 scenarios)

Four tiers, increasing in difficulty:

### Round 1+2 (8 baseline + 8 mirror) — short horizons, physics-aware tools
Folder: [`scenario_farm_world_physics/`](are/simulation/scenarios/scenario_farm_world_physics/)
Plus mirror variants under the legacy [`scenario_farm_world/`](are/simulation/scenarios/scenario_farm_world/).

Targeted single-task scaffolds: planting, irrigation, fertilizer, drone
survey, pesticide, pesticide-outbreak, harvest, field-prep. Each is ~10–30
tool calls; the physics engine evolves the world a few days at most. These
are the **path-matching control group** — workflow_combined and FOS should
agree here.

### Round 3 (8 episodes) — mid-season decision points
Folder: [`scenario_farm_worldpp_physics/`](are/simulation/scenarios/scenario_farm_worldpp_physics/)

Single-decision episodes that require the agent to read the world before
acting:
- `physics_emergence_replant_decision` — did the seedlings emerge? if not, replant?
- `physics_threshold_pest_monitoring` — is pest count above the action threshold?
- `physics_planting_window_reschedule` — is the soil ready, or wait?
- `physics_disease_after_rain_fungicide` — wet conditions → preventive fungicide?
- `physics_pod_fill_drought_irrigation` — irrigate or hope for rain?
- `physics_harvest_moisture_timing` — moisture in range yet?
- `physics_postharvest_drying_storage` — dry then store
- `physics_differential_diagnosis_fertigation` — which deficiency, which fix

Each is ~20–60 tool calls with a 1–4 week physics horizon.

### Round 4 (5 full-season) — planting through storage
Folder: [`scenario_farm_world_fullseason/`](are/simulation/scenarios/scenario_farm_world_fullseason/)

Full soybean season runs (priority subset of the 10 designed):
- `scenario_full_season_baseline_balanced_season`
- `scenario_full_season_cold_spring_delayed_planting`
- `scenario_full_season_dry_pod_fill_yield_protection`
- `scenario_full_season_full_adversarial_weather_season`
- `scenario_full_season_late_harvest_rain_risk`

(Five more are scaffolded in the folder but not in Phase-5 of the validation
sweep: `aphid_threshold_trend`, `mixed_stress_wrong_action_trap`,
`nutrient_vs_drought_differential`, `resource_limited_operations`,
`wet_june_disease_pressure`.)

These are 100–200+ tool calls over a simulated 4–5 month season. This
is where path-matching collapses.

---

## 4. The Farm Operational Score (FOS)

Module: [`are/simulation/scenarios/fos/`](are/simulation/scenarios/fos/).

### Why we needed it

On round-1+2 scenarios, `workflow_combined` (the Levenshtein-style trace
match against an oracle workflow) tracks what we'd intuitively call "good
performance." On full-season scenarios it doesn't:

| Tier | median workflow_combined | median FOS |
|---|---:|---:|
| r1+2 baseline | 0.76 | 0.89 (agree) |
| r3 episode | 0.04 | 0.76 (**20× divergence**) |
| r4 fullseason | 0.04 | 0.74 (**21× divergence**) |

The agent on a full-season run might irrigate on day 87 instead of day 89,
or split a 64-ridge harvest into 6 passes instead of 4. Trace-distance
hammers them both. But the field still ends up healthy and harvested.
Path-matching saturates near zero — every agent looks equally bad — and
loses all discriminating power.

### What FOS measures

```
FOS = 0.5 · Outcome  +  0.3 · Decision  +  0.2 · Efficiency
```

Each component is in [0, 1]. The composite is also in [0, 1].

**Outcome (O)** — *did the field do well?*
[`evaluation.py::_compute_outcome`](are/simulation/scenarios/fos/evaluation.py).
Computed from the physics state at scenario end:
- `yield_ratio` — recovered yield / scenario potential (the headline number)
- `crop_loss_count` — ridges marked lost (penalty)
- `safety_violations` — e.g. spraying without re-entry interval respected
On mid-season scenarios with no harvest event, `yield_ratio` defaults to
1.0 so a working field doesn't get penalised for not harvesting yet.

**Decision (D)** — *did the agent make the right call at the right moment?*
[`gates.py`](are/simulation/scenarios/fos/gates.py) +
[`predicates.py`](are/simulation/scenarios/fos/predicates.py).
Each scenario defines a list of `GateSpec`s — semantic checkpoints like
"after seeing high pest count, agent applies pesticide to the right ridge
range" — built from composable predicates (`after_observation`,
`targets_ridges_overlap`, `arg_equals`, `succeeded`, `and_`, `or_`,
`not_`). A gate is *matched* if any of the agent's actions satisfy its
predicates. Decision = matched / total.

This is the key insight: gates are **flexible** about ordering and timing
but **strict** about causal/semantic correctness. Two agents that took
different paths to the same right-action-after-right-observation both
score full marks.

**Efficiency (E)** — *was the agent wasteful?*
[`evaluation.py::_compute_efficiency`](are/simulation/scenarios/fos/evaluation.py).
Penalises tool inflation (agent calls / oracle calls, capped at 3×) and
redundant reads (calling the same observation tool back-to-back with no
intervening action).

### How FOS is wired into a scenario

Each physics scenario calls `append_fos_evaluation(env, ...)` at the end
of its `validation_fn`, alongside the existing `append_workflow_evaluation`.
Both metrics are computed and recorded — workflow_combined for legacy
comparison, FOS as the primary signal. Output goes to:
- the run's `output.jsonl` rationale (in-memory values, always reliable)
- `<output_dir>/fos/fos_<scenario_id>.json` (full structured breakdown)

### Sensitivity analysis

[`sensitivity.py`](are/simulation/scenarios/fos/sensitivity.py) +
[`scripts/fos_sensitivity_from_csv.py`](scripts/fos_sensitivity_from_csv.py).
Re-weighting is post-hoc — given any per-cell O/D/E tuple you can compute
FOS under a different `(w_O, w_D, w_E)` without re-running. The appendix
table sweeps `w_O ∈ {0.4, 0.5, 0.6}` and `w_D ∈ {0.2, 0.3, 0.4}` and
reports rank stability. Headline: the family ranking is robust on easier
cells; the adversarial full-season scenario is the only one where weights
substantially change top-3 family identity (a defensible reviewer answer).

---

## 5. The 10 controller families

10 architecturally distinct LLM agent families are registered (came in via
the `upstream/main` merge):

`farm_baseline_react`, `farm_planner_executor`, `farm_reflective_memory`,
`farm_skill_rag`, `farm_multi_specialist`, `farm_adaptive_verifier`,
`farm_rewoo_modular`, `farm_tree_search`, `farm_critic_refiner`,
`farm_graph_memory`.

Paper mapping is in [`AGENT_FAMILIES_AND_PAPERS.md`](AGENT_FAMILIES_AND_PAPERS.md).
All 10 were validated to run on the physics scenarios in our local sweep
(10 families × 8 scenarios × 3 repeats = 240 cells, 235 succeeded).

---

## 6. What the local validation sweep showed

The pipeline was exercised end-to-end as a local sanity check before
shipping. Validation outputs are **not** committed — they're produced
fresh by re-running [`RUNBOOK.md`](RUNBOOK.md) §4. The numbers below
are what we observed on `gpt-4o-mini` via the EU OpenAI endpoint.

| Phase | Cells | Succeeded | Cost |
|---|---:|---:|---:|
| 2 — smoke | 4 | 4/4 | $0.06 |
| 3 — family coverage | 40 | 40/40 | $0.63 |
| 4 — tier breadth | 21 | 21/21 | $0.33 |
| 5 — paper matrix | 240 | 235/240 | $3.71 |
| **Total** | **305** | **300/305 (98%)** | **$4.74** |

Phase 5's 240 cells = 10 families × 8 representative scenarios × 3 repeats.

**Headline (paper §5):**

| Tier | n | med FOS | σ(FOS) | med wf | divergence |
|---|---:|---:|---:|---:|---:|
| r1+2 baseline | 90 | 0.89 | 0.080 | 0.76 | 1.16× (agree) |
| r3 episode | 60 | 0.76 | 0.056 | 0.04 | **20×** |
| r4 fullseason | 90 | 0.74 | 0.043 | 0.04 | **21×** |

Variance is low (σ ≤ 0.08 across all tiers, 3 repeats). Sensitivity
analysis: top-3 family ranking 100% stable on easier scenarios across the
9-cell weight grid; only the adversarial full-season scenario shows
meaningful weight sensitivity. Figures A/B/C, the summary CSV, and the
full report are produced by `scripts/iclr_validation_figures.py`
against the runner's output dir — see [`RUNBOOK.md`](RUNBOOK.md) §4.

---

## 7. Coverage gap (full transparency)

Phase 5 sampled **8 of 29** scenarios (2 per tier). The (E)-thesis
divergence is statistically supported — 240 cells, low variance — but
generalization to the other 21 scenarios is an inference, not a
measurement. They all load and pass oracle validation; they have not all
been exercised end-to-end with a real LLM.

Two ways to close the gap:
1. Run the full 29 × 10 × 3 = 870 cells (~$20, ~3h on max-concurrent=6).
2. Run a 29 × 1 × 1 = 29-cell smoke first to catch crashes for ~$0.50.

The runner supports both via `--scenarios` and `--families` lists. See
[`RUNBOOK.md`](RUNBOOK.md).

---

## 8. Code-orientation cheat sheet

**If you want to read the physics:** start at
[`are/simulation/physics/__init__.py`](are/simulation/physics/__init__.py)
and follow each engine. Each `.py` has a sibling `.md` spec.

**If you want to read the FOS framework:** start at
[`are/simulation/scenarios/fos/evaluation.py`](are/simulation/scenarios/fos/evaluation.py)
and look at any round-3 scenario for an end-to-end usage example.

**If you want to add a scenario:** copy any file under
`scenario_farm_worldpp_physics/`, change the physics profile, and
redefine `_gates()`. The wiring is uniform across the tier.

**If you want to add an agent family:** the 10 are registered via the
`upstream/main` infrastructure (see `AGENT_FAMILIES_AND_PAPERS.md`).

**If you want to verify everything still works:**
```
.venv312/bin/pytest tests/  # 67 tests
.venv312/bin/python -m are.simulation.main -s scenario_drone_survey_physics_action_tick -a farm_baseline_react -o
```
The first command exercises physics + FOS. The second runs an oracle
(no LLM cost) end-to-end through the full action/tick/observation
pipeline.

---

## 9. What's *not* done

- The 5 round-4 scenarios that exist as files but weren't sampled in
  our local Phase-5 (`aphid_threshold_trend`, `mixed_stress_wrong_action_trap`,
  `nutrient_vs_drought_differential`, `resource_limited_operations`,
  `wet_june_disease_pressure`) — they are wired with FOS but not
  empirically exercised yet.
- The runner uses `FOS_EXPORT_DIR=<cell_dir>` to write each cell's
  structured `fos_<scenario>.json` into its own directory, so concurrent
  cells don't race. (An earlier shared-dir collision in our local Phase 5
  was mitigated by reading FOS values from the per-cell rationale; the
  current code path is race-free.)
- A "round 5" multi-season / climate-change tier is mentioned in earlier
  plans but not built; not needed for the ICLR submission.
