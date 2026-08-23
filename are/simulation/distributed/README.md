# Farm D-CORE

Farm D-CORE extends FarmARE's long-horizon evaluation to persistent,
role-isolated multi-agent teams. The primary paper benchmark uses two agents;
predeclared three- and four-agent Wet-June decompositions provide a bounded
scalability study. The paper-facing benchmark is farm-only and uses the
existing native L3 planting-to-storage scenarios. It does not use FARM-FOS.

> **Research status:** the native runner is agent-driven and executes the exact
> role-authorized action and arguments selected by each controller. Scripted
> mode is explicitly an oracle ceiling; model modes do not receive oracle
> actions, arguments, or transition IDs. `doctor` still reports
> `paper_ready: false` while the executable specifications await the
> preregistered expert review. Do not launch expensive model matrices until
> those review gates are green.

Model controllers receive a non-procedural public task contract. Native L3
briefing events that contain the expert's ordered solution, exact treatment
amounts, or oracle ridge arguments are reserved for the declared ceiling and
evaluator and are never placed in model prompts.

Wet-June's paper output is the versioned `dcore_eval_v5` profile: categorical
module-normalized event acceptance; semantic end-to-end causal obligations;
independently reconstructed information-policy conformance; information-global
discordance (IGD); fact-version lag; recovery; structural exposure; and exact
provenance failure localization. The scalar score
`0.5 * EF + 0.5 * CC` is a secondary summary.

The definitions and claims boundary are frozen in
[SCIENTIFIC_CONTRACT_V5.md](SCIENTIFIC_CONTRACT_V5.md). Vector clocks encode
execution order, provenance localizes recorded breaks, and only paired
controlled interventions support causal-effect claims.

The professor-facing experimental mapping to the accepted ICML/SIGSPATIAL
setup is frozen in [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md). It keeps
the native L3, controller, oracle, yield, baseline, and cost methodology while
making distributed information/fault conditions the new experimental axis.

## Public scenarios

- `farm_wetjune_recheck`
- `farm_disease_drought`
- `farm_three_cultivar`

Wet-June expands deterministically from a ten-module hierarchical workflow
into the immutable action-level occurrence net. Pure
read-only observation chains use Petri fork/join structure, so their order is
genuinely concurrent; machine, resource, handoff, and action/effect edges stay
direct. The other two seasons remain readable v3 engineering benchmarks until
they pass the same review process.

The checked-in review manifests are intentionally marked `unreviewed`.
Mechanical validation is not a substitute for the paper's two-independent-
experts, adjudication, and third-expert-confirmation protocol. Use
`--require-confirmed` to make incomplete review a hard preflight failure.
Those same manifests are the only accepted source of paper transition weights,
numeric agronomic tolerances, and exogenous safe/reobserve/defer/abstain branch
definitions. Engineering defaults keep smoke tests runnable, but `doctor`
refuses paper readiness until the annotations are frozen and each scenario has
reviewed executable alternatives.

## Wet-June independent review

Create a v5 neutral packet with blank scientific fields:

```bash
.venv312/bin/python -m are.simulation.distributed.cli review export-v5 \
  --output-dir review/wetjune-neutral
```

The neutral packet contains the public module skeleton, draft fact names,
complete native FarmARE tool catalog, blank JSON submission template, DOT, and
PNML. It deliberately contains no proposed policies, transitions, dependency
answers, guards, weights, or tolerances. The two experts complete separate
copies and attest each completed JSON digest. Then use `review validate-v5`, `review compare-v5`,
`review adjudicate-v5`, `review confirm-v5`, and `review freeze-v5` in that
order. Comparison reports categorical, numerical, and
dependency disagreements at leaf-field granularity. Adjudication requires a
rationale for every disagreement plus the adjudicator's expertise and
no-results/no-yield-tuning attestations. Freezing is impossible without an
independent third-expert digest confirmation and the same attestations.
Role-refinement review is recorded separately in the process metadata for each
team decomposition; a larger team cannot reuse the two-agent review digest.

Team ownership/topology and 3/4-agent refinements use the neutral
`team_review.template.json` produced by `review export`. Run the generic
`review validate`, `review compare`, `review adjudicate`, `review confirm`, and
`review freeze --petri-net ...` sequence on two independent team submissions.
Team reviewers, adjudicator, and confirmer must provide UTC timestamps and
attest both that final results were unseen and that yield correlations were not
used for tuning. Confirmed team/refinement objects are rejected unless their
SHA-256 review attestations are present.

`review freeze-v5` writes the immutable confirmed process containing its
expanded occurrence net. Paper mode rejects
the checked-in engineering defaults, incomplete reviews, digest mismatches,
and incomplete real-LLM gates.

The completed scientific-gate manifest must be stored outside the Git worktree
(or in an already ignored external artifact directory). It contains the exact
commit hash it authorizes, so tracking that same mutable manifest in the commit
would create a self-reference and make the clean-worktree check impossible.
The confirmed process/team specifications may be tracked normally; only the
stage/release gate attestation is external.

Validate the resulting scientific object directly (rather than recompiling an
engineering oracle) with:

```bash
.venv312/bin/python -m are.simulation.distributed.cli validate-spec \
  --process-spec review/frozen/farm_process_spec_v5.frozen.json \
  --team-spec-path review/frozen/wetjune_2agent.confirmed.json \
  --scenario-id farm_wetjune_recheck --require-confirmed --with-mutants \
  --output-dir results/spec-validation
```

The built-in 3/4-agent refinements are deterministic engineering fixtures, not
scientific oracles. A reviewed refinement is supplied with
`--role-refinement-path`; its scenario, target team, status, and digest are
checked before paper execution. Extra communication transitions divide the
existing module budget and cannot increase its total weight.

## Parameterized teams

`AgentTeamSpec` declares the fixed roster, exclusive tool and observation
grants, time authority, topology, activation policy, and call/token budgets.
All actor stores, inboxes, vector-clock components, histories, controllers,
telemetry, and completion state are created from that roster. Directed,
fully-connected, hierarchical/star, and shared-blackboard topologies are
supported; a broadcast expands into stable per-recipient deliveries.

The two-agent default is the full experimental condition. The three-agent
scout→agronomist→operator and four-agent scout→agronomist→operator plus
resource-manager→operator teams are intentionally limited to reliable and one
mixed-fault schedule. Use the bounded manifest:

```bash
.venv312/bin/python -m are.simulation.distributed.cli matrix \
  are/simulation/distributed/configs/farm_dcore_scalability.yaml \
  --output-dir results/scalability --dry-run
```

It resolves the matched-total reliable/mixed cells plus a clearly labeled
matched-per-agent reliable sensitivity block, and refuses an accidental full
Cartesian expansion unless explicitly enabled. Aggregate rows
include team and refinement digests, per-agent local scores and telemetry,
message/path overhead, coordination density, and matched compute budgets.

## Quick start

Use the repository's Python environment so `click`, `inputimeout`, and the
FarmARE dependencies are present:

```bash
.venv312/bin/python -m are.simulation.distributed.cli doctor --output-dir results
.venv312/bin/python -m are.simulation.distributed.cli validate-spec
.venv312/bin/python -m are.simulation.distributed.cli run \
  --scenario-id farm_wetjune_recheck \
  --controller-mode mock_llm \
  --output-dir results/smoke
```

Add `--with-mutants --output-dir results/validation` to export controlled
v5 provenance checks and the six-class earliest-link confusion matrix. These
mutants use exact provenance IDs and never inject runtime violation labels.
`doctor` reports runtime health separately from `paper_ready`; the latter
remains false until the three expert reviews are complete.

For release readiness, repeat `--process-spec`, `--team-spec`,
`--role-refinement`, and `--gate-manifest` for the selected frozen artifacts.
`doctor` verifies their digests, the clean tagged commit, `uv.lock`, the
analysis protocol, PM4Py pin, and team/process compatibility.

After installing this branch as a package, the shorter `are-dcore` entry point
registered in `pyproject.toml` provides the same commands.

`scripted` is the deterministic Petri oracle. `mock_llm` drives deterministic
oracle proposals through the same native ReAct parser, agent builder, capture
tools, histories, and telemetry used by real models. `llm` creates one
independent native FarmARE agent per role through the existing agent/config
builders and advances it with exactly one `BaseAgent.step()` per activation.
For the OpenAI provider, D-CORE requests JSON-constrained tool intents and then
applies FarmARE's tool-specific parser and role gateway validation; it does not
depend on unconstrained free-form ReAct formatting.
Existing research-family planning,
memory, skill, verification, and telemetry hooks therefore remain active.
Every FarmARE tool visible inside the agent is a non-mutating intent proxy; the
role gateway remains the sole executor. Model, provider, endpoint, family, and
history are configurable per actor:

```bash
.venv312/bin/python -m are.simulation.distributed.cli run \
  --scenario-id farm_wetjune_recheck \
  --controller-mode llm \
  --model field_intelligence=MODEL \
  --model operations=MODEL \
  --agent-family field_intelligence=farm_adaptive_verifier \
  --agent-family operations=farm_planner_executor \
  --history-window field_intelligence=8 \
  --history-window operations=8 \
  --output-dir results/llm-run
```

After the expert/tolerance gates are frozen, a deliberately bounded API smoke
uses at most 12 model calls (six per role). It validates connectivity and the
step-wise contract; it is expected to end before season completion and is not
an experimental result. Its scientific-gate manifest must have
`status: offline_complete`: confirmed process/team digests, a clean pinned
commit, frozen lock/protocol digests, and every offline check must already be
present. A release tag and `bounded_real_llm_smoke: true` are required only
after this smoke succeeds, when the final gate becomes `complete`:

```bash
.venv312/bin/python -m are.simulation.distributed.cli run \
  --scenario-id farm_wetjune_recheck \
  --controller-mode llm \
  --model field_intelligence=gpt-5.4-mini-2026-03-17 \
  --model operations=gpt-5.4-mini-2026-03-17 \
  --provider field_intelligence=openai \
  --provider operations=openai \
  --temperature field_intelligence=0 \
  --temperature operations=0 \
  --max-model-calls 12 --max-logical-steps 6 \
  --enforcement-mode audit --paper-mode --bounded-llm-smoke \
  --petri-spec-path review/frozen/farm_process_spec_v5.frozen.json \
  --scientific-contract v5 \
  --scientific-gate-manifest review/frozen/scientific_gates.json \
  --output-dir results/openai-smoke
```

Rows marked `bounded_llm_smoke` are rejected by paper aggregation even if the
connectivity check happens to complete a useful action.

Controllers receive only their immutable local knowledge, delivered messages,
role-owned tool names, time, and prior local result. A selected action that is
unknown, malformed, or not role-owned is rejected before it can mutate the
environment. In enforcement mode, high-impact actions additionally require the
frozen Petri safety policy; in off/audit mode, scientifically wrong but valid
tool calls execute unchanged so their physical and yield consequences can be
measured. Custom adapters implementing `AgentController` can be injected
without changing trace, guard, or evaluator contracts.

## Professor matrices

Resolve the complete run count without model calls:

```bash
.venv312/bin/python -m are.simulation.distributed.cli matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/pass1 --dry-run
.venv312/bin/python -m are.simulation.distributed.cli matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass2.yaml \
  --output-dir results/pass2 --dry-run
```

After the review, smoke, and release gates resolve the specification paths,
run pass 1 first. Inspect its integrity before launching pass 2:

```bash
.venv312/bin/python -m are.simulation.distributed.cli matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/pass1
```

Each matrix resumes completed runs by default and writes a structured failure
artifact instead of discarding the rest of the matrix. Legacy single-agent and
synchronous-A2A conditions are freshly executed as `farmare_direct` and
`farmare_a2a` using the paired world, public task, controller profile, repeat,
and aggregate budget. Their unavailable distributed local-knowledge fields
remain `NA`.

For the bounded nine-family robustness block, use
`configs/farm_dcore_controller_robustness.yaml`. Explicit controller profiles
let the professor add backbone comparisons without silently multiplying the
full fault matrix. Dry-run output reports counts per profile and the maximum
planned model-call budget.

Re-evaluation and aggregation do not call a model:

```bash
.venv312/bin/python -m are.simulation.distributed.cli evaluate \
  results/smoke/trace.dcore.json
.venv312/bin/python -m are.simulation.distributed.cli aggregate results/paper
```

Use `aggregate --paper-mode` for final tables. It rejects non-v5 D-CORE rows,
non-paper runs, scientific-audit failures, and communication faults that did
not actually manifest. Imported native FarmARE legacy baselines remain allowed
only with D-CORE knowledge fields explicitly `NA`.

## Artifacts

Every completed run contains:

- resolved `dcore_run_v5` configuration, versions, commit, and four seeds;
- ordinary FarmARE `are_simulation_v1` trace;
- season-wide `dcore_trace_v5` sidecar with world and policy commitments;
- frozen date-indexed exogenous weather/outbreak manifest and digest;
- Petri JSON, applicable occurrence net, DOT, and PNML;
- the exact expert annotation/review manifest and its digest;
- frozen metrics, provenance localization, outcome, per-ridge yield, and tidy run row;
- `COMPLETED.json`, or `FAILURE.json` on a failed matrix unit.

World, scheduler, model, and fault seeds are separate. Communication faults do
not inject weather or crop changes. For a fixed world seed, every condition
records the same exogenous-world digest. Yield differences therefore arise
only through FarmARE action and physics pathways.

The current fault suite consists of named, deterministic schedules targeting
stable message/fact identifiers. Consequently, `fault_seed` is recorded but is
not an experimental replicate; manifests must provide exactly one fault seed.
The resolver rejects multiple fault seeds to prevent pseudo-replication until a
seeded stochastic fault sampler is implemented.

## Verification

```bash
.venv312/bin/pytest -q are/simulation/tests/distributed
```

The suite checks clocks, transport, guards, deterministic replay, all three
full seasons, perfect oracle scores, role isolation, explicit Petri handoffs,
fault localization, guard blocking without oracle recovery, exact adversarial
argument execution, concurrency invariance, CLI replay, manifest resolution,
and statistical export.
