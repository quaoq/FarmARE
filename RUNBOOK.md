# RUNBOOK.md — How to Run Everything for the ICLR Paper

This is the operator's manual. It covers: setup, smoke checks, the full
validation sweep, figure generation, sensitivity analysis, and how to
interpret outputs.

For the *concepts* behind the code (physics engine, scenarios, FOS), read
[`Physics.md`](Physics.md) first.

For the legacy agent-suite runner (smoke / full_compare suite packs,
A2A modes), see [`PROFESSOR_RUNBOOK.md`](PROFESSOR_RUNBOOK.md). This
runbook focuses on the **physics + FOS validation pipeline** that produced
the paper's §5 numbers.

---

## 1. Setup (one-time, ~3 min)

```bash
# from the repo root
git checkout farm-physics

# Python 3.12 venv. Two options — pick one:
uv sync                                      # (A) uv-managed, recommended
python3.12 -m venv .venv312 && .venv312/bin/pip install -r requirements.txt -r requirements-dev.txt   # (B) plain venv
```

The validation scripts default to `.venv312/bin/python` for subprocess
isolation. If you used `uv sync`, point them at the uv venv via
`--python-bin` or symlink `.venv312` to wherever uv put it.

### Environment file

Copy `example.env` → `.env` and set:

```bash
OPENAI_API_KEY=sk-...                 # any OpenAI-compatible key
OPENAI_BASE_URL=https://...           # optional; for EU/proxy endpoints
```

The runner maps these to `LLAMA_API_KEY` / `LLAMA_API_BASE` automatically
(the `-mp llama-api` provider goes through litellm; `-mp openai` does
**not** route through this key path).

### Verify install

```bash
.venv312/bin/pytest tests/                              # 67 unit tests
.venv312/bin/python -m are.simulation.main -s scenario_drone_survey_physics_action_tick -a farm_baseline_react -o
```

The first command runs the physics + FOS test suite (zero LLM cost).
The second runs one scenario in **oracle mode** end-to-end (zero LLM cost,
~10s); good for confirming the action/tick/observation pipeline works.

---

## 2. The validation pipeline at a glance

Three scripts. Run in order:

```
scripts/iclr_validation_runner.py     -> per-cell driver; outputs results.csv
scripts/iclr_validation_figures.py    -> reads results.csv; outputs figures + report
scripts/fos_sensitivity_from_csv.py   -> reads summary.csv; outputs sensitivity table
```

A "cell" = one `(family, scenario, repeat)` triple = one subprocess
running `are.simulation.main` with one LLM agent on one scenario.

### What the runner does per cell

1. Spawns a Python subprocess running `are.simulation.main`.
2. Injects `.env` keys + `FOS_EXPORT_DIR=<cell_dir>` so each cell's
   structured FOS JSON is written to its own dir (no race).
3. Caps wall-clock at `--cell-timeout-s` (default 300s).
4. Parses the cell's `output.jsonl` rationale to extract:
   - `workflow_combined` (legacy path-matching score)
   - `outcome` / `decision` / `efficiency` / `fos` (the new metric)
   - safety violations, gates matched, gates total
5. Estimates LLM cost from tool-call count (~$0.02/cell on `gpt-4o-mini`).
6. Aborts the whole sweep at 80% of `--cost-cap-dollars` as a safety
   guard.

Cells run in parallel via a thread pool (`--max-concurrent 6` is safe).

---

## 3. Quick smoke (~$0.05, ~2 min)

Confirm everything is wired before spending real money:

```bash
.venv312/bin/python scripts/iclr_validation_runner.py \
  --phase smoke \
  --output-root validation_runs/smoke_$(date -u +%Y%m%dT%H%M%SZ) \
  --families farm_baseline_react \
  --scenarios scenario_drone_survey_physics_action_tick,scenario_full_season_balanced \
  --repeats 1 \
  --model gpt-4o-mini \
  --cost-cap-dollars 1.0 \
  --max-concurrent 2
```

Expect: 2 cells succeeding (rc=0), wall ~30–90s each, FOS reported on at
least one cell. If both fail → check `.env`, the LLM endpoint, and
`OPENAI_API_KEY`.

---

## 4. Reproduce the paper's §5 numbers (~$5, ~50 min)

This is the headline 240-cell sweep that produces Figure A and the
divergence table. Validation outputs are not committed to the repo;
the command below produces them fresh under `validation_runs/`
(which is gitignored). Numbers should reproduce within LLM
nondeterminism the values reported in [`Physics.md`](Physics.md) §6.

```bash
SWEEP=validation_runs/iclr_sweep_$(date -u +%Y%m%dT%H%M%SZ)

# Phase 5: 10 families × 8 scenarios × 3 repeats = 240 cells
.venv312/bin/python scripts/iclr_validation_runner.py \
  --phase paper_matrix \
  --output-root $SWEEP/phase5_paper_matrix \
  --families farm_baseline_react,farm_planner_executor,farm_reflective_memory,farm_skill_rag,farm_multi_specialist,farm_adaptive_verifier,farm_rewoo_modular,farm_tree_search,farm_critic_refiner,farm_graph_memory \
  --scenarios scenario_drone_survey_physics_action_tick,scenario_irrigation_physics_action_tick,scenario_physics_emergence_replant_decision,scenario_physics_threshold_pest_monitoring,scenario_full_season_balanced,scenario_full_season_dry_pod_fill,scenario_full_season_adversarial_weather,scenario_full_season_late_harvest_rain_risk \
  --repeats 3 \
  --model gpt-4o-mini \
  --cost-cap-dollars 80.0 \
  --max-concurrent 6 \
  --cell-timeout-s 300

# Generate figures + validation_report.md
.venv312/bin/python scripts/iclr_validation_figures.py \
  --sweep-dir $SWEEP

# Sensitivity table for the appendix
.venv312/bin/python scripts/fos_sensitivity_from_csv.py \
  --input-csv $SWEEP/figures/summary.csv \
  --output-csv $SWEEP/figures/fos_sensitivity.csv
```

Expected outputs in `$SWEEP/`:
```
phase5_paper_matrix/
  results.csv                                          # per-cell rows
  farm_baseline_react__scenario_full_season_balanced__r1/
    output.jsonl                                       # the cell's full trace
    fos/fos_<scenario>.json                            # structured FOS report
  ... (240 cell dirs total)
figures/
  fig_A_workflow_vs_fos.pdf                            # the (E)-thesis scatter
  fig_B_per_family_heatmap.pdf                         # 10×8 mean-FOS grid
  fig_C_decomposition_adversarial.pdf                  # O/D/E bars per family
  summary.csv                                          # all cells + tier column
  fos_sensitivity.csv                                  # 9-cell weight grid × cells
  validation_report.md                                 # narrative + headline numbers
```

The validation_report.md is what you'd cite in the paper. Open it and
look for "Headline numbers (Phase 5, paper §5)".

---

## 5. Full coverage sweep (~$20, ~3h)

If you want to run **all 29 scenarios** instead of the 8 representative
ones, replace the `--scenarios` argument with the full list. (Phase 5
sampled 8; the other 21 are wired but never exercised end-to-end.) See
[`Physics.md`](Physics.md) §3 for the complete scenario list per tier.

For a cheap "do they all crash?" smoke first (~$0.50, ~10 min):

```bash
.venv312/bin/python scripts/iclr_validation_runner.py \
  --phase coverage_smoke \
  --output-root $SWEEP/coverage_smoke \
  --families farm_baseline_react \
  --scenarios <comma-separated list of all 29> \
  --repeats 1 \
  --cost-cap-dollars 5.0 \
  --max-concurrent 6
```

Then scale up to the full matrix if all 29 succeed.

---

## 6. Single scenario / single family (debugging)

```bash
# Oracle mode (no LLM cost) — confirms physics + FOS wiring
.venv312/bin/python -m are.simulation.main \
  -s scenario_full_season_balanced \
  -a farm_baseline_react \
  -o

# Real LLM
.venv312/bin/python -m are.simulation.main \
  -s scenario_full_season_balanced \
  -a farm_baseline_react \
  -mp llama-api -m gpt-4o-mini \
  -e -w 5 \
  --output_dir outputs/single_run \
  --log-level INFO
```

The cell directory will contain `output.jsonl` (the full trace) and
`fos/fos_<scenario>.json` (structured FOS report).

---

## 7. Output schemas (so you know what you're looking at)

### `results.csv` (per-cell)
| Column | Meaning |
|---|---|
| `family` | controller family name |
| `scenario` | scenario id |
| `repeat` | 1, 2, or 3 |
| `wall_s` | subprocess wall-clock |
| `return_code` | 0 = success |
| `workflow_combined` | legacy path-matching score ∈ [0, 1] |
| `fos` | composite FOS ∈ [0, 1] |
| `outcome` / `decision` / `efficiency` | per-component scores ∈ [0, 1] |
| `safety_violations` | int |
| `crop_loss` | int |
| `gates_matched` / `gates_total` | decision-component raw counts |
| `estimated_cost_dollars` | conservative LLM cost estimate |
| `cell_dir` | path to the per-cell output dir |

### `summary.csv` (figures pipeline)
Same as `results.csv` plus a `tier` column derived from the scenario name
(`r1+2_baseline`, `r1+2_mirror`, `r3_episode`, `r4_fullseason`).

### `fos/fos_<scenario>.json` (per-cell structured report)
The full `FOSReport` dataclass serialised — components, outcome breakdown,
gate-by-gate decision results, efficiency breakdown, weights used. Useful
for paper figures that need detailed per-gate or per-component data.

### `validation_report.md`
Narrative summary: per-phase pass rates, cost, headline numbers per tier,
sensitivity-analysis result, figure list. **This is the artifact you cite
in the paper.**

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Cell returns rc=0 but `fos=None` in CSV | The agent ran but didn't reach the validation step (timed out or hit `agent_max_iterations`). Increase `--cell-timeout-s` or check `output.jsonl`. |
| All cells fail with "auth error" | `.env` not picked up. Confirm `OPENAI_API_KEY` is set and `OPENAI_BASE_URL` matches the endpoint. |
| Cells fail with "model not found" | The `-mp openai` provider routes through `huggingface_hub`. Use `-mp llama-api` (the runner already does this). |
| Sweep aborts at 80% cost | Cost cap hit. Either raise `--cost-cap-dollars` or check why per-cell cost is higher than expected (long agent loops). |
| `fos_<scenario>.json` files race / overwrite | The runner injects `FOS_EXPORT_DIR=<cell_dir>` per subprocess, so concurrent cells write to their own directories. If you bypass the runner and call `are.simulation.main` directly without setting it, you may see this. The in-memory FOS values in each cell's `output.jsonl` rationale are always correct. |
| Wall-clock varies wildly per cell (10s–160s) | Expected. Full-season scenarios have long agent loops. Don't lower `--cell-timeout-s` below 300. |

---

## 9. What's not committed (and why)

`validation_runs/`, `outputs/`, `fos_exports/`, and `workflow_exports/`
are **gitignored** — they're produced by running the pipeline locally.
We exercised the pipeline ourselves to confirm it all works end-to-end;
the published numbers in [`Physics.md`](Physics.md) §6 reflect that
local run. To get your own copy of the paper §5 figures and report,
follow §4 above.

---

## 10. Cost & wall-clock budgeting cheatsheet

| What | Cells | Cost (gpt-4o-mini) | Wall (max-concurrent=6) |
|---|---:|---:|---:|
| Smoke (2 cells) | 2 | $0.05 | 2 min |
| Phase-5 reproduction | 240 | ~$5 | ~50 min |
| Full 29×10×3 sweep | 870 | ~$20 | ~3 h |
| Coverage smoke (29×1×1) | 29 | ~$0.50 | ~10 min |

The runner aborts at 80% of `--cost-cap-dollars` as a hard safety. Always
set this when running anything bigger than smoke.
