# Farm D-CORE experimental protocol

## Prospective diagnostic validation extension

Before freezing the final study, use the bounded plan in
[`AAMAS/PAPER_FOUNDATION.md`](../../../AAMAS/PAPER_FOUNDATION.md): independent
annotation of 60 natural decision episodes (maximum two per run), a simple
checker given the same evidence, saved-trace component ablations, and every
expert-prespecified sensitivity variant. Freeze the sampling budget/seed,
manifest, and process families before final model outcomes. The
`are-dcore validation` commands prepare and analyze saved evidence; they do not
run agents or change the primary metric. Report uncertainty, annotation
agreement, coverage, paired world-cluster intervals, and weakening outcomes.
The existing model/scenario matrix counts remain unchanged. Software fixtures
are explicitly separated from natural-agent evidence.

## Confirmation revision before final experiments

The runtime and v5 evaluator select evidence by observation time, with declared
scope and decision-time eligibility. Pre-action policies use their frozen scope;
the later proposed action cannot retroactively alter the commitment. Action
scope fidelity remains separately scored. Reevaluate previous saved traces with
this corrected implementation and freeze a fresh release before model results.

Sensor observations use returned contiguous ridge coverage, not probe labels
or installation ridges. Unknown coverage cannot establish a regional guard.
Forecast suitability uses `weather:forecast_spray_window_open`; it is distinct
from the observed current `weather:spray_window_open` fact. The legacy oracle's
forecast-based knowledge guard is distinct from its execution-time world
weather guard. Its policy templates and controlled policy fixtures now declare
forecast evidence explicitly. A guard's optional `world_fact_key` declares the
physical comparator used by v5 global evaluation; it defaults to the local fact
key. The forecast guard compares its issued prediction with actual weather.
An issued forecast is a historical record with bounded validity, not an
authoritative observation of future weather. These declarations require expert
review and a new process digest before paper use. Calibration records
root-zone stress after synchronizing physics to the intervention time in both
control and omission arms.
Both calibration arms use the injected `event_queue_only` clock. Saved plans
bind this timing policy, and release validation rejects reports without it.
Host overhead is excluded from calibration simulation time; wall-clock runtime
remains a separate execution measurement.

`native_execution_retries` reports failed writes and accepted retries for the
same actor/tool/region. It does not imply agronomic or causal recovery and does
not remove failures from intention-to-treat analysis. Matched A2A rows separate
assignment from `delegation_observed`; nondelegating rows remain in the assigned
condition, with uptake reported separately.

Disease–Drought complete gates include `scenario_sensitivity_report_path` and
its SHA-256 digest. Runtime, doctor and handoff verify the file and recompute
acceptance; runtime also checks the selected world against the report.
Candidate-only reports cannot authorize the released default scenario.

## Continuity with the ICML and SIGSPATIAL studies

The prior farm papers compare full execution traces with human-oracle farm
trajectories and report downstream yield. Their main experimental axes are
task horizon, controller family, model/backbone, context, direct versus A2A
routing, and deployment cost. Farm D-CORE keeps the validated native L3
physics, tools, full planting-to-storage horizon, controller builders, oracle
replay, yield accounting, CORE/KTC/BFCL baselines, and token/runtime reporting.

The new independent variable is distributed information and coordination:
shared versus role-local state, free-text versus causal handoffs, communication
faults, and causal enforcement. D-CORE adds partial-order, information-relative,
and provenance failure-localization diagnostics. It does not use FARM-FOS.

## Frozen experimental blocks

Run separate blocks rather than one uncontrolled Cartesian product.

1. **Primary evaluation block:** the two-agent team and frozen ReAct/OpenAI
   profile. Pass 1 has 480 runs and pass 2 has 450; use
   `configs/farm_dcore_primary_pass1.yaml` and
   `configs/farm_dcore_primary_pass2.yaml`. Wet-June receives the complete
   fault matrix; held-out seasons receive reliable and mixed-fault cells only.
2. **Controller robustness:** the nine controller-family counterparts used in
   the prior papers, one fixed backbone, and reliable shared/free-text/causal
   conditions (shared and local causal-audit), totaling 270 runs. Use
   `configs/farm_dcore_controller_robustness.yaml`.
3. **Team-size scalability:** the predeclared 2/3/4-agent refinements under
   reliable and one mixed seasonal fault, with matched total-team compute as
   the primary analysis. Use `configs/farm_dcore_scalability.yaml`.
4. **Prior-model continuity (optional):** Qwen and DeepSeek under shared and
   local causal-audit reliable transport only (60 runs). Start from
   `configs/farm_dcore_prior_model_continuity.template.yaml`.

The scenario prompt is held fixed within every paired block. The prior
scenario-specific Expert-Instruct workflows are not automatically injected:
they contain oracle actions and arguments that would violate D-CORE's
non-leakage contract. Any context ablation must be a separately reviewed,
digest-frozen prompt treatment and cannot expose Petri transitions, branch
commitments, expected calls, or hidden authoritative truth.

## Pairing and observational unit

- One complete season is one observational unit.
- Pair conditions on scenario, world seed, team, controller/model profile, and
  repeat.
- Keep world, scheduler, model, and fault seeds separate.
- Named deterministic faults do not create independent observations merely by
  changing `fault_seed`.
- Cluster confidence intervals by scenario/world seed; report paired
  differences against the matching scripted oracle.
- Treat controller crashes and timeouts as failures in primary tables while
  reporting infrastructure failure separately.

## Required conditions and outputs

Every primary profile contains the scripted Petri oracle, freshly rerun matched
single-agent direct-tool and synchronous-A2A baselines, shared blackboard,
role-local free text, causal audit, and causal enforcement. Historical
SIGSPATIAL traces are optional descriptive appendix material rather than paired
evidence. Missing local knowledge for direct/A2A remains `NA`.

Each run writes the native FarmARE trace, D-CORE trace, exogenous-world digest,
team/refinement digests, frozen Petri and occurrence nets, metric/attribution
reports, final and per-ridge yield, resource use, telemetry, and a tidy row.
Matrix/aggregate additionally write:

- `results.csv` and `results.jsonl`
- `phase_metrics.csv`
- `module_metrics.csv`
- `ridge_yields.csv`
- `agent_telemetry.csv`
- `aggregate.json`

Calibration is exported for D-CORE, EF, CC, BFCL, merged PC-KTC, CORE path
correctness, partial-order agreement, and PM4Py token replay against both
biological and marketable yield shortfall. It uses held-out or
leave-one-world-cluster-out predictions and is interpreted as predictive
validity, never as evidence that a metric causes yield.

## Scientific gates

Expensive runs begin only when `doctor` reports the selected scenario/team
ready. This requires two independent expert submissions, adjudication,
third-expert confirmation, frozen weights/tolerances/branches/guards, reviewed
role refinements, offline semantic and leakage tests, saved-trace replay, and
the bounded real-LLM smoke. Wet-June is the development target. After
`dcore_eval_v5` freezes, disease-drought and three-cultivar are held-out
transfer scenarios; no metric formula may change after inspecting their model
results. No scenario may enter paper tables until its
`farm_process_spec_v5` review is confirmed.

Always inspect `matrix --dry-run` before execution. It reports unresolved
placeholders, engineering-versus-paper run counts, the total model-call cap,
and explicit `execution_preflight_ready` and `paper_experiment_ready` flags.
Non-dry execution refuses any `REPLACE_WITH_*` placeholder before making a
model call.

## Frozen table and figure recipes

These recipes are specified before final model runs. Do not add, remove, or
select panels after inspecting final outcomes.

1. **Evaluation validity table (RQ1):** one row per controlled mutant; columns
   for final success, yield shortfall, BFCL, CORE path correctness, merged KTC,
   partial-order agreement, PM4Py token fitness, EF, CC, IGD, and expected versus
   localized earliest link. Report concurrency/refinement invariance separately
   from defect sensitivity.
2. **Local/global table (RQ2):** weighted `L0/G0`, `L0/G1`, `L1/G0`, and `L1/G1`
   mass, IGD, and per-role policy conformance. Zero-obligation roles are `NA`.
3. **Fault/outcome table (RQ3):** paired condition-minus-reliable differences
   for D-CORE profile components, completion, safety, biological yield shortfall,
   marketable yield shortfall, wasted inputs, and recovery. Cluster intervals by
   scenario/world seed.
4. **Localization table (RQ4):** planted-fault confusion matrix plus per-label
   precision and recall. Free-text rows remain `unverifiable_handoff` unless the
   frozen annotation provides a fact mapping.
5. **Mitigation table (RQ5):** causal enforce minus causal audit under each
   matched fault; report unsafe proposals, prevented writes, false blocks,
   unnecessary abstention, eventual recovery, safety benefit, and yield cost.
6. **Scalability table:** 2/3/4 agents under reliable and mixed faults. Primary
   rows match total team calls/tokens; the increased-compute per-agent block is
   labeled sensitivity. Include messages, path length, coordination density,
   token/call dispersion, and latency.

Figures are: module/prefix D-CORE across the season; paired yield-shortfall
distributions; fact-version lag/freshness slack; the localization confusion
matrix; and leave-one-world-cluster-out metric-versus-yield calibration.
Calibration plots label correlation and prediction as non-causal. Any all-run
plot includes controller failures under intention-to-treat and shows
infrastructure failures in a separate panel or annotation.
# Implementation revision: review hardening, 2026-09-12

All native distributed LLM conditions receive the same fixed coordination
contract and capability cards, derived only from their declared team. Cards
describe permissions and legal routes, not current observations. Handoffs
retain multiple spatial scopes, and prompt frontiers prioritize observation
time over arrival time. Native write receipts are shareable local historical
facts. These changes require a fresh frozen release; old and new controller
results must not be pooled under one condition identifier.

Disease–Drought irrigation outcome claims require independent multi-world
scenario-sensitivity validation before final model runs. The opt-in drought
candidate and `calibrate-scenario` command are engineering calibration tools;
their outputs are never paper observations. Screening thresholds and worlds
must be fixed before inspecting candidate results. Candidate success requires
expert review and a new frozen scenario revision before promotion. Complete
Disease–Drought gates bind a reviewed sensitivity report digest. See
`AAMAS/REVIEW_RESPONSE_AND_HANDOFF.md` for operator commands and limitations.
