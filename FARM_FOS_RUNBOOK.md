# FARM-FOS Runbook — Cross-Agent Validation of the Spatiotemporal Path Metric

**Audience:** the Harbin team running experiments on the Qwen pipeline.
**Goal:** measure how well our new metric, **FARM-FOS**, correlates with real
farm yield versus three standard path metrics (BFCL, CORE, vanilla KTC), and
produce the paper's scatter figure + correlation table.

**TL;DR for the busy reader:** run several agent *families* on the same
full-season scenarios with Qwen (the model that completes seasons), then run
three scripts (`build_oracle_baselines.py` → `rebatch_fos_from_traces.py` →
`farm_fos_analysis.py`). The third script prints the correlation table and
writes the scatter PDF. Everything except the Qwen runs is zero-LLM-cost.

---

## 0. Why we need *your* runs (the one thing we could not do)

FARM-FOS scores an agent on **where, when, and how-much-it-matters** each farm
action was, relative to the expert oracle — the timing/targeting information
that BFCL/CORE/KTC throw away. To show it tracks yield *better* than those
baselines, we need runs where **agents of different quality complete the full
season** (so recovered yield actually varies by decision quality).

We hit a hard wall on our side: with OpenAI models (gpt-4o, gpt-4o-mini) the
agents **plant the field and then stop** — they never call `advance_time` to
drive the 120-day season to harvest, so recovered yield is always 0 and there
is nothing to correlate. **Your phase5 runs used Qwen (`qwen3.6-flash`), which
*does* run the full `advance_time → harvest` loop** (we verified: 12
`advance_time` + 23 `harvest` calls, 142 season-days). So only the Qwen
pipeline can produce the completing-season, multi-agent traces this test needs.

Also: the data you already shared (`phase5_paper_matrix`) is **one agent**
(`farm_baseline_react`) across 90 scenarios. That answers "does the metric
track this one agent across fields?" but **not** the reviewer's real question:
"does it rank *different agents* by their yield on the *same* field?" That
needs **multiple families on the same scenarios** — the run described below.

---

## 1. What we implemented (files in this PR)

| File | What it is |
|---|---|
| `are/simulation/scenarios/fos/calibration.py` | **Frozen agronomic priors** — per-action yield weight + timing tolerance (e.g. R5 = water-critical, R3–R6 = disease window). Set a priori from crop-stage agronomy; **never fitted to yield** (a unit test enforces this — the anti-circularity firewall). |
| `are/simulation/scenarios/fos/spatiotemporal.py` | **FARM-FOS** (`compute_farm_fos`) + the **BFCL** baseline; reuses the repo's existing CORE/KTC. The whole metric is one equation over the same (agent, oracle) workflows the baselines use. |
| `are/simulation/tests/test_spatiotemporal_metric.py` | 13 unit tests (perfect-replay → 1.0, monotonic timing decay, spatial penalty, omission weighting, asymmetric harvest kernel, frozen-calibration guard, BFCL-is-timing-blind). |
| `scripts/rebatch_fos_from_traces.py` | Replays saved traces (zero LLM cost) and emits `farm_fos(%)`, `bfcl_success(%)`, `core_path_correctness(%)`, `ktc(%)` + yield columns per run. |
| `scripts/farm_fos_analysis.py` | **The analysis entry point.** Reads the rebatch CSV → correlation table (CSV + LaTeX) + scatter PDF + bar chart. Pooled **and** within-scenario-across-agents. |
| `scripts/farm_fos_demo.py` | Local behavior demo (no traces needed): shows FARM-FOS degrades smoothly as timing drifts while baselines stay flat. |
| `scripts/build_oracle_baselines.py` | Builds the oracle yield baselines used for the `yield_preserved_ratio` outcome (already in repo; needed before rebatch for the best yield axis). |
| `docs/farm_fos/farm_fos_explainer.md` | Plain-English explanation of the metric (read this first). |
| `docs/farm_fos/farm_fos_math.tex` | The math, standalone-compilable. |

Conceptual one-liner (full derivation in `docs/farm_fos/farm_fos_math.tex`):

```
FARM-FOS = clip_[0,1]( ( Σ_i w_i · o_i · k_σ(Δ_i)  −  λ Σ_j h_j ) / Σ_i w_i )
  w_i = frozen agronomic weight of oracle action i
  o_i = spatial overlap of agent vs oracle target ridges
  k_σ(Δ_i) = triangular timing kernel, Δ_i in season-days  (harvest: asymmetric)
```

---

## 2. The experiment to run (the only step that needs Qwen + GPU/$)

**Run several agent families on the same full-season scenarios, with Qwen.**

Requirements that make or break the result:
- **≥ 5 agent families** spanning quality. Suggested 6:
  `farm_baseline_react, farm_planner_executor, farm_multi_specialist,
  farm_tree_search, farm_reflective_memory, farm_rewoo_modular`
  (add the rest of the 10 if budget allows — more agents = stronger result).
- **The same scenario set across all families** (so within-scenario comparison
  is possible). Start with ~12–20 of the 90 (diverse stress types), or all 90.
  The full list is `scripts/fullseason/formal_90_scenario_ids.txt`.
- **Qwen model** (the one that completed phase5), so agents reach harvest.
- **≥ 1 repeat** (2–3 better for variance estimates).

Use the existing runner with your Qwen provider/endpoint (mirror your phase5
config). Example shape — adjust provider/endpoint/model to your Qwen setup:

```bash
python scripts/iclr_validation_runner.py \
  --phase xagent_qwen \
  --output-root validation_runs/xagent_qwen \
  --families farm_baseline_react,farm_planner_executor,farm_multi_specialist,farm_tree_search,farm_reflective_memory,farm_rewoo_modular \
  --scenarios "$(paste -sd, scripts/fullseason/formal_90_scenario_ids.txt)" \
  --repeats 1 \
  --provider qwen --model qwen3.6-flash --endpoint <YOUR_QWEN_ENDPOINT> \
  --agent-max-iterations 400 --cell-timeout-s 1800 \
  --max-concurrent 8 --cost-cap-dollars <YOUR_CAP>
```

> **Critical check:** after a few cells finish, confirm agents actually
> completed the season. A completed run has `advance_time` and `harvest` tool
> calls and `recovered yield > 0`. If you see runs that only `plant_seeds`
> and stop (0 `advance_time`, 0 `harvest`), the agents are quitting early —
> the correlation cannot be computed from those. (This is exactly the failure
> we hit with OpenAI; Qwen should not have it.)

`--output-root` will contain one subdirectory per `(family, scenario, repeat)`
cell, each with a `scenario_*.json` trace. That directory is the input to
step 3.

---

## 3. Build oracle baselines (zero LLM cost, ~deterministic)

Needed so the rebatch reports `yield_preserved_ratio = agent_biological /
oracle_biological` (the cleanest yield axis). Run once:

```bash
python scripts/build_oracle_baselines.py \
  --scenarios round4 \
  --output-dir oracle_baselines
# (round4 = the full-season scenarios; or pass the explicit comma list)
```

---

## 4. Rebatch traces → metrics CSV (zero LLM cost)

Replays the saved traces and computes all four metrics + yield per cell:

```bash
python scripts/rebatch_fos_from_traces.py \
  --root validation_runs/xagent_qwen \
  --out-root validation_runs/xagent_qwen_reeval \
  --extrapolate \
  --oracle-baselines oracle_baselines \
  --workers 4
```

Output: `validation_runs/xagent_qwen_reeval/summary_v2.csv`, one row per cell
with `agent_family`, `scenario`, `farm_fos(%)`, `bfcl_success(%)`,
`core_path_correctness(%)`, `ktc(%)`, `yield_preserved_ratio(%)`,
`yield_ratio(%)`, etc.

---

## 5. Analyze → the scatter + correlation table (zero LLM cost)

```bash
python scripts/farm_fos_analysis.py \
  --csv validation_runs/xagent_qwen_reeval/summary_v2.csv \
  --out-dir figures_farm_fos
```

This prints, and writes to `figures_farm_fos/`:
- `fig_scatter.pdf` — **the headline figure**: 4 panels, x = real yield,
  y = each metric, with the regression line and Spearman ρ in each title.
- `fig_bars.pdf` — |Spearman| per metric, FARM-FOS highlighted.
- `farm_fos_correlation.csv` / `.tex` — the correlation table for the paper.
- `farm_fos_summary.txt` — full readout (pooled + within-scenario + Steiger).

Useful flags:
- `--yield-col yield_preserved_ratio(%)` to force the oracle-relative yield
  (auto-detected by default).
- `--min-yield 50` to exclude catastrophic no-harvest runs and isolate the
  "among agents that completed the season" comparison.

---

## 6. How to read the results (and what success looks like)

Two regimes are reported:

1. **POOLED** — all cells together. Useful, but mixes scenario difficulty with
   agent skill.
2. **WITHIN-SCENARIO across agents** — the confound-free test. For each
   scenario it ranks the agents by each metric and by yield, then averages the
   rank-correlation across scenarios. **This is the number that answers "does
   the metric rank agents by their real farm outcome on the same field?"** —
   it only populates when you have ≥2 agents per scenario (hence the
   multi-family run).

**What we expect / what "good" looks like:**
- FARM-FOS Spearman in roughly the **0.6–0.8** band, and **above** all three
  baselines — especially in the within-scenario regime.
- **Not ≈ 1.0.** A near-perfect correlation would be a red flag for
  circularity, not a success — weather stochasticity and partial observability
  cap the honest ceiling around 0.7–0.85.
- Baselines (BFCL/CORE/KTC) should be markedly weaker, ideally near-flat in the
  within-scenario regime — that is the paper's core claim (standard path
  metrics miss physical timing).

**Honest caveat we already know:** on the single-agent phase5 data the
correlation was modest (Spearman ≈ 0.31) — because one competent agent's yield
barely varies. The multi-agent run is expected to lift this substantially by
introducing real, decision-driven yield variance. If it does not, that is
itself an informative result and we will report it honestly rather than tune
the metric to inflate it.

---

## 7. Sanity checks you can run with no Qwen (verify the code is sound)

```bash
# 13 unit tests for the metric
pytest are/simulation/tests/test_spatiotemporal_metric.py -q

# behavior demo on a real scenario's oracle workflow (no LLM):
# shows FARM-FOS falling as timing drifts while baselines stay pinned at 1.0
python scripts/farm_fos_demo.py --scenario scenario_full_season_hb_base_hn84_std_normal
```

---

## 8. Anti-circularity guarantee (for reviewers)

The metric's weights and timing windows are **frozen agronomic priors** set in
`calibration.py` *before any correlation is computed*, derived from published
crop-stage agronomy and the engine's documented sensitivity *structure* — never
from the measured yields we then correlate against. The unit test
`test_calibration_is_yield_independent` fails if the calibration module ever
reads a yield/correlation/outcome quantity in code. All four metrics are
computed from the *same* (agent, oracle) trace pair; FARM-FOS simply *uses* the
timing/spatial/importance information the baselines discard.

---

## 9. Roadmap (v2, not in this PR)

If v1 lands below the target band even with multi-agent data, the principled
(still non-circular) next step is **stress-aware weighting**: scale each
action's weight by the leverage it has *given the scenario's observable stress
state* (e.g. irrigation weighted up in a drought scenario, down in a wet one),
read from the engine's documented stress→yield coefficients. Plus a
**GDD/growth-stage time axis** instead of calendar-days for the timing kernel.
Both are model-structure-derived, not yield-fitted. We will add these only if
the data shows they are needed, and we will freeze them a priori as well.
