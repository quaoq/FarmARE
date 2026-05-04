# CLAUDE.md — FarmARE Session Context

This file is loaded into every Claude Code (or Agent SDK) session that
starts in this repo. It tells the assistant what the project is, what's
been built, where things live, and what conventions to follow. Keep it
accurate — if you change something structural, update here.

---

## Mission

This repo is the codebase for an **ICLR 2026 workshop short-paper
submission**. The headline contribution is a new evaluation framework
(**FOS — Farm Operational Score**) that supersedes path-matching for
long-horizon LLM-agent tasks. The work was done on the `farm-physics`
branch and is being prepared for PR back to the upstream.

Quality bar is **publication-grade**: reproducibility, statistical
rigour, and clear documentation matter.

Two human-readable docs sit alongside this one and are the right next
read:
- [`Physics.md`](Physics.md) — plain-English tour of what we built
  (physics engine, scenario tiers, FOS framework).
- [`RUNBOOK.md`](RUNBOOK.md) — how to run experiments, reproduce
  paper §5, and interpret outputs.

---

## What the repo contains (1-paragraph orientation)

FarmARE is a meta-agents-research-environment for evaluating LLM agents
on farm-management tasks. The world is a 64-ridge soybean field; agents
operate via tool calls (planting, irrigation, sensor reads, drone
surveys, etc.). On `farm-physics` we added: (a) a real **physics
engine** under [`are/simulation/physics/`](are/simulation/physics/) with
7 simulation modules + an observation model, enforcing an **action /
tick / observation** boundary; (b) **29 scenarios** across 4 difficulty
tiers, including round-3 mid-season episodes and round-4 full-season
runs from planting to grain storage; (c) the **FOS framework**
([`are/simulation/scenarios/fos/`](are/simulation/scenarios/fos/))
scoring agents on Outcome × Decision × Efficiency, with gate-predicate
matching for Decision; (d) a reproducible 305-cell real-LLM
**validation pipeline** (run locally to confirm everything works) that
shows ~20× divergence between FOS and trace-matching on long-horizon
scenarios. Validation outputs are gitignored; producers re-run the
pipeline via [`RUNBOOK.md`](RUNBOOK.md) §4.

---

## Branch state

The `farm-physics` branch is ahead of `upstream/farm-physics`. The new
work, top of branch first:
- **Docs for PR handoff** — `Physics.md`, `RUNBOOK.md`, this file.
- **ICLR validation pipeline** — `scripts/iclr_validation_runner.py`,
  `scripts/iclr_validation_figures.py`, `scripts/fos_sensitivity_from_csv.py`,
  plus the `FOS_EXPORT_DIR` env-var fix in
  `are/simulation/scenarios/fos/evaluation.py`. (Validation *outputs*
  under `validation_runs/` are gitignored — see [`RUNBOOK.md`](RUNBOOK.md) §9.)
- **Merge of `upstream/main`** — brought in the 10 farm controller families.
- **Rounds 1–4 + FOS framework** — physics engine, scenarios, FOS module.

Run `git log --oneline upstream/farm-physics..HEAD` to see exact commits.

Pre-existing on `farm-physics` before this work: an empty physics
scaffold + 8 baseline scenarios, no FOS, no full-season scenarios.

---

## Key directories (where to look for what)

```
are/
  simulation/
    physics/                      # 7 engines + observation model + profiles
    apps/farm_world/              # FarmWorldApp + sub-apps (tractor, drone, robot, sensor, field-ops)
                                  #   physics_orchestrator.py, farm_physics_state.py, farm_action_record.py
    apps/system.py                # SystemApp.advance_time (linked-time propagation)
    scenarios/
      fos/                        # FOS framework
        metrics.py                #   FOSReport, FOSComponents, OutcomeBreakdown, EfficiencyBreakdown
        gates.py                  #   GateSpec, GateResult
        predicates.py             #   composable predicates (after_observation, targets_ridges_overlap, ...)
        evaluation.py             #   evaluate_fos(), append_fos_evaluation()
        sensitivity.py            #   re_weight_fos(), weight_grid()
      scenario_farm_world/        # legacy round-1+2 scenarios (mirror baseline)
      scenario_farm_world_physics/        # round-1+2 baseline, physics-aware (8 scenarios)
      scenario_farm_worldpp_physics/      # round-3 episodes (8 scenarios)
      scenario_farm_world_fullseason/     # round-4 full-season (5 active + 5 scaffolded)
      oracle_matching.py          # restored from upstream/main; required for oracle workflow checks

scripts/
  iclr_validation_runner.py       # per-cell driver (parallel, cost-capped)
  iclr_validation_figures.py      # generates figs A/B/C + summary.csv + validation_report.md
  fos_sensitivity_from_csv.py     # fast sensitivity from results.csv (no JSON needed)
  fos_sensitivity_analysis.py     # full sensitivity from per-cell fos_*.json files
  wire_round3_scenarios.py        # one-shot patcher (already applied)
  wire_round4_scenarios.py        # one-shot patcher (already applied)
  run_agent_suite.py              # legacy suite runner (mock/real, A2A on/off)
  check_readiness.sh              # confirms 10 families + agents register

tests/                            # 67 unit tests (physics engines, orchestrator, FOS, round-3 tools)

validation_runs/                  # GITIGNORED — produced by scripts/iclr_validation_runner.py
                                  # (each run nests under validation_runs/iclr_sweep_<ts>/)

configs/agent_suite/              # smoke.yaml, full_compare.yaml — for the legacy suite runner
```

---

## The action / tick / observation boundary (critical)

Every farm tool falls into one of three buckets. Don't blur the lines.

| Bucket | Tool examples | What happens |
|---|---|---|
| **Action** | `plant_seeds`, `apply_fertigation`, `spray_pesticide`, `irrigate`, `harvest` | Direct effect recorded immediately + appended to `FarmActionRecord` queue. Future biological consequences are deferred. |
| **Tick** | `advance_time`, `commit_daily_physics` | Drives all 7 physics engines forward by N logical days. Idempotent per day. |
| **Observation** | `read_sensors`, `fly_survey`, `inspect_crop_health`, `inspect_pests`, `inspect_emergence` | Samples hidden state via `ObservationModel` with noise / sparsity / latency. |

This boundary is what makes long-horizon evaluation meaningful. A
scenario where the world ticks forward in response to time (not in
response to tool calls) is a scenario where the agent must actually plan
and observe.

---

## The FOS evaluation framework (what makes the paper)

```
FOS = 0.5 · Outcome  +  0.3 · Decision  +  0.2 · Efficiency
```

- **Outcome** — final-state quality (yield_ratio, crop loss, safety violations).
- **Decision** — gate-predicate matching: did the agent take the right
  action *after the right observation*, regardless of order. Built from
  composable predicates in [`fos/predicates.py`](are/simulation/scenarios/fos/predicates.py).
- **Efficiency** — tool inflation (capped at 3×) + redundant-read fraction.

Each scenario calls `append_fos_evaluation(env, gates=...)` in its
`validation_fn`. Output goes to:
- the run's `output.jsonl` `metadata.rationale` (always reliable)
- `<output_dir>/fos/fos_<scenario>.json` (structured report; per-cell
  isolation via `FOS_EXPORT_DIR` env var)

Re-weighting is **post-hoc** — given any per-cell O/D/E tuple you can
compute FOS under different `(w_O, w_D, w_E)` without re-running. See
[`fos/sensitivity.py`](are/simulation/scenarios/fos/sensitivity.py).

---

## The 10 controller families

Registered after the `upstream/main` merge:

`farm_baseline_react`, `farm_planner_executor`, `farm_reflective_memory`,
`farm_skill_rag`, `farm_multi_specialist`, `farm_adaptive_verifier`,
`farm_rewoo_modular`, `farm_tree_search`, `farm_critic_refiner`,
`farm_graph_memory`.

Architecture notes per family: [`AGENT_FAMILIES_AND_PAPERS.md`](AGENT_FAMILIES_AND_PAPERS.md).

---

## LLM provider routing (gotcha)

The codebase has two OpenAI-shaped providers:
- `-mp openai` — routes through `huggingface_hub`; **does not** see
  `OPENAI_API_KEY`. Don't use this for our endpoint.
- `-mp llama-api` — routes through `litellm`. **Use this.** Reads
  `LLAMA_API_KEY` and `LLAMA_API_BASE`. The validation runner maps these
  from `OPENAI_API_KEY` / `OPENAI_BASE_URL` automatically.

---

## Running anything (TL;DR — full instructions in RUNBOOK.md)

```bash
# tests
.venv312/bin/pytest tests/

# one oracle run (zero LLM cost)
.venv312/bin/python -m are.simulation.main -s <scenario> -a <family> -o

# one real-LLM run
.venv312/bin/python -m are.simulation.main -s <scenario> -a <family> \
  -mp llama-api -m gpt-4o-mini -e -w 5 --output_dir outputs/single

# the validation sweep
.venv312/bin/python scripts/iclr_validation_runner.py \
  --phase paper_matrix --output-root <dir> \
  --families <comma-list> --scenarios <comma-list> --repeats 3 \
  --cost-cap-dollars 80.0 --max-concurrent 6
```

---

## Headline empirical result (paper §5)

From a local 305-cell run on `gpt-4o-mini` (validation outputs are not
committed; reproduce via [`RUNBOOK.md`](RUNBOOK.md) §4):

| Tier | n | med FOS | σ(FOS) | med wf | divergence (FOS / wf) |
|---|---:|---:|---:|---:|---:|
| r1+2 baseline | 90 | 0.887 | 0.080 | 0.764 | 1.16× (agree) |
| r3 episode | 60 | 0.760 | 0.056 | 0.038 | **20×** |
| r4 fullseason | 90 | 0.742 | 0.043 | 0.035 | **21×** |

Total cells: 305. Total cost: $4.74. Phase-5 success rate: 235/240
(97.9%). Variance is low; sensitivity analysis stable on easier cells,
weight-sensitive only on the adversarial full-season scenario (a
defensible reviewer answer).

---

## Conventions and standards

- **Backwards compat**: legacy paths are gated behind `physics_active`
  flags. The 10-family suite runner (`run_agent_suite.py`), the A2A
  modes, and the smoke/full_compare configs all still work unchanged.
- **Determinism**: every physics scenario pins a `(profile, seed)` so
  re-runs reproduce the same world. Agent stochasticity is the only
  source of variance.
- **Tests**: 67 unit tests. New physics or FOS code should add tests.
- **No half-finished implementations**: if a scenario is wired, it
  passes oracle validation. (See "Coverage gap" below for what's wired
  but not empirically validated.)
- **Cost discipline**: the validation runner aborts at 80% of
  `--cost-cap-dollars`. Always set this for non-smoke runs.

---

## Coverage gap (be honest about this)

Our local Phase-5 sweep used **8 of 29 scenarios** (2 per tier). The
other 21 are wired with FOS, pass oracle validation, and have unit
tests, but have not been exercised end-to-end with a real LLM. The
(E)-thesis result is statistically supported on the 240-cell matrix;
its generalisation to the other 21 scenarios is an inference, not a
measurement.

If a future session is asked to "run everything", the right cheap step
is the 29-cell coverage smoke (~$0.50, ~10 min) before the 870-cell full
matrix (~$20, ~3h). See [`RUNBOOK.md`](RUNBOOK.md) §5.

---

## FOS JSON write isolation

The runner injects `FOS_EXPORT_DIR=<cell_dir>` per subprocess so each
cell's structured `fos_<scenario>.json` lands in its own directory; this
is **race-free** for parallel runs. The headline FOS numbers reported
by the runner come from each cell's `output.jsonl` `metadata.rationale`
(in-memory values, computed before any file write), so they're robust
to file-layout issues.

---

## What *not* to do

- Don't reintroduce the "tools secretly run full-world physics" anti-
  pattern (Bad Extreme B in
  [`physics_action_tick_integration_guide.md`](are/simulation/scenarios/scenario_farm_world_physics/physics_action_tick_integration_guide.md)).
- Don't bypass `record_action()` when adding a new farm tool — the
  action history is what FOS Decision gates match against.
- Don't commit `validation_runs/`, `outputs/`, `fos_exports/`, or
  `workflow_exports/` — they're gitignored on purpose.
- Don't add the `-mp openai` provider path to validation scripts (see
  "LLM provider routing").

---

## Quick "what's where" lookup

| Question | File |
|---|---|
| How does the physics engine work? | [`are/simulation/physics/`](are/simulation/physics/) (each `.py` has a `.md` spec) |
| How does FOS compute Outcome/Decision/Efficiency? | [`are/simulation/scenarios/fos/evaluation.py`](are/simulation/scenarios/fos/evaluation.py) |
| What gates does scenario X check? | The scenario file's `_gates()` function |
| How do I run a single cell? | [`RUNBOOK.md`](RUNBOOK.md) §6 |
| How do I reproduce paper §5? | [`RUNBOOK.md`](RUNBOOK.md) §4 |
| What's the headline number? | This file's "Headline empirical result" table |
| What's the agent family roster? | [`AGENT_FAMILIES_AND_PAPERS.md`](AGENT_FAMILIES_AND_PAPERS.md) |
| Concept-level walkthrough for a new contributor? | [`Physics.md`](Physics.md) |

---

## When in doubt

Run the oracle pipeline first:
```bash
.venv312/bin/python -m are.simulation.main -s scenario_drone_survey_physics_action_tick -a farm_baseline_react -o
```
Zero cost, ~10 seconds, exercises physics + FOS end-to-end. If this
fails, something fundamental is broken; if it passes, the validation
runner will work.
