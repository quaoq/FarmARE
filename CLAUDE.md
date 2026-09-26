# D-CORE collaborator guide

This is the working context for contributors and coding assistants on the
`aamas_paper` branch. It describes the current system. Historical FarmARE and
FOS code remains in the repository, but it is not the claim of this paper.

## Research objective

D-CORE reconstructs the evidence that was valid and available to each actor at
a consequential decision. It distinguishes failures that can look identical in
an action trace:

- the observation was never acquired;
- the right version existed but was not delivered;
- it was delivered but omitted from the actor's final prompt;
- it was stale or had the wrong scope;
- valid evidence was in the prompt, but the action did not use it; or
- the native operation failed.

The diagnosis determines a bounded legal repair: acquire, deliver, restore,
refresh, reroute, or request reconsideration. A repaired continuation and a
fresh untreated continuation start from the same verified checkpoint, discard
the recorded future, and receive the same remaining budgets. The central
experiment asks whether this localization produces a more appropriate and more
useful repair than action checking or generic reconsideration.

The live-efficiency claim uses a separate matched trigger ablation. Selective
D-CORE and all-eligible D-CORE receive the same legal team-prefix evidence,
repair catalogue and budgets; only invocation frequency differs. LLM
always/periodic verification remains a separately named baseline and is not the
matched comparator for D-CORE selectivity.

The AAMAS paper targets engineering and analysis of multiagent systems. Current
pilots are engineering evidence only. No pilot establishes comparative benefit.

## Read these files first

1. `AAMAS/professor_review/OVERVIEW.md`: implemented behavior, validation, and
   work still requiring professor execution or review.
2. `AAMAS/professor_review/RUNBOOK.md`: exact installation, smoke, checkpoint,
   repair-study, live-policy, review, and reporting commands.
3. `are/simulation/scenarios/scenario_dcore/README.md`: how to add a native ARE
   scenario and bind it to D-CORE without duplicating its mechanics.
4. `AAMAS/handover_validation/course_of_action_20260926.json`: closure evidence
   and professor-owned work for the latest implementation review.

## Terms that must stay distinct

- **Scenario**: a named task definition and its native mechanics, tools,
  starting state, horizon, and outcome contract.
- **Scenario revision**: an explicit, versioned change to mechanics or
  calibration. Never tune an existing revision silently.
- **World seed**: one exogenous realization of a scenario. It is the clustering
  and pairing unit in the paper analysis.
- **Team specification**: actors, tool ownership, responsibilities,
  communication routes, and capability-conservation checks.
- **Authored process specification**: public decision obligations,
  prerequisites, scopes, freshness, windows, transition order, and outcome
  expectations. It observes the native scenario; it does not replace it.
- **Condition**: controller, communication, fault, or verification treatment
  assigned by a campaign manifest.
- **Run**: one condition on one scenario/world/team configuration.
- **Decision**: one structured proposal by an actor.
- **Checkpoint**: the exact predecision state used for replay and branching.
- **Continuation**: an untreated or repaired suffix from a checkpoint.
- **Paper mode**: execution allowed only after required review and hash-bound
  professor approval. Engineering mode does not imply paper release.

## Architecture and ownership

The implementation has four layers. Keep them separate.

1. **Native ARE scenario** owns physical state, time, legal tool execution,
   returned measurements, resource effects, horizon, and agricultural outcome.
2. **Farm adapter** turns actual native receipts into versioned evidence with
   source, holder, recipient, acquisition time, validity, and measured scope.
3. **D-CORE specification and runtime** define actors, public obligations,
   evidence prerequisites, communication, diagnosis, repairs, replay, and live
   verification.
4. **Study and reporting code** assigns treatments, preserves failures and
   missing outcomes, joins frozen labels, computes contrasts, and generates
   tables from saved records.

Key paths:

```
are/simulation/scenarios/                 native ARE scenarios
are/simulation/scenarios/scenario_dcore/  D-CORE scenario catalogue and guide
are/simulation/distributed/
  authored_specs.py                       three authored process specifications
  farm_adapter.py                         native receipt -> evidence mapping
  models.py                               shared records and team/spec models
  operation_resolver.py                   exact authored occurrence resolution
  guard.py, knowledge.py                  evidence validation and scope semantics
  native_season.py                        real distributed execution and repairs
  journal.py                              append-only durable execution evidence
  prefix_replay.py                        checkpoint construction and replay
  repair_study.py                         repair resolution and matched suffixes
  evaluation_adapters/                    diagnostic/comparator implementations
  experiments.py                          campaign expansion and aggregation
  paper_report.py                         label joins and generated reports
AAMAS/authored_specifications/             compiled reviewed specifications
AAMAS/handover_development/                engineering and study manifests
AAMAS/handover_validation/                 tracked validation evidence
AAMAS/professor_review/                    the two professor-facing documents
```

## Multiagent decomposition

The default two-actor team is intentionally asymmetric:

- `field_intelligence` owns sensing and interpretation tools and is responsible
  for acquiring, refreshing, and communicating scoped evidence.
- `operations` owns crop-management and postharvest tools and is responsible for
  executing legal actions, completing harvest, and accounting for grain through
  storage.

This is a benchmark decomposition, not a universal claim that every domain has
one observer and one executor. New teams must satisfy the following standard:

1. Every required native capability has at least one legal owner.
2. Each consequential obligation has one responsible actor at decision time.
3. Private tools and private evidence remain actor-specific.
4. If an actor needs evidence owned elsewhere, a legal communication route and
   sufficient response time must exist.
5. Refining an actor into several specialists conserves the aggregate tools and
   responsibilities of the original team; refinement must not add oracle
   knowledge or capabilities.
6. The public task briefing is serialized once and is identical across the
   distributed, direct, and A2A comparison conditions.

Three- and four-actor team files demonstrate refinements of the Wet-June team.
They are compatibility checks, not the main paper population.

## Scientific invariants

- The same scope rule (`exact` or `covers`) follows a prerequisite through the
  evaluator, diagnosis, guard, repair resolver, and native executor.
- Evidence coverage comes from the returned measurement, never merely from the
  requested arguments. A robot capped at eight ridges does not create evidence
  for a wider requested range.
- An observation repair is applied only if the native receipt produces the
  required fact with valid scope. A dependent route is blocked when acquisition
  fails.
- Routing requires that the sender holds the exact evidence version. Context
  restoration requires that the recipient already holds it and succeeds only
  when its identifier appears in the next final model prompt.
- A repair contains at most two primitives and must be legal and physically
  timely. Unknown duration remains unresolved in paper mode.
- A target operation must resolve uniquely. Ambiguous or unmatched proposals
  remain unresolved and cannot trigger a repair.
- Packets contain the target proposal and the context that produced it, but no
  later guard result, execution receipt, outcome, or future event.
- Checkpoint state and semantic-prefix hashes describe the same predecision
  boundary. Uncertain provider calls or native writes are not replayed.
- `wait` is nonterminal. `finish` is terminal only when declared duties are
  satisfied; a deferred finish is recorded as an intervention.
- Assignment denominators come from manifests before outcome filtering.
  Infrastructure failures, partial harvest, abstentions, infeasible repairs,
  null effects, and adverse results remain in reports.

## Working rules

- Use Python 3.12 and `uv run --frozen`.
- Do not put credentials, provider headers, or machine-specific paths in
  manifests, journals, documentation, or archives.
- Do not edit generated authored JSON directly. Change `authored_specs.py`, run
  `AAMAS/tools/build_authored_specs.py`, inspect the diff, and obtain renewed
  approval because specification hashes changed.
- Preserve old results and failed attempts. New scientific changes require a
  new scenario revision, manifest, and unused cohort.
- Do not promote engineering worlds or scripted references into paper evidence.
- Do not hand-edit witnesses, repair ownership, feasibility, or missing report
  fields to make a study run.
- Do not change thresholds or rewards to obtain a favorable result.
- Generated scenario scaffolds and LLM-assisted mappings require human review;
  the native mechanics and process specification are separate review surfaces.

## Fast offline check

```bash
uv sync --frozen --python 3.12 --extra dev

UV_CACHE_DIR=/tmp/farmare-uv-cache MPLCONFIGDIR=/tmp/farmare-mpl \
  uv run --frozen pytest -q are/simulation/tests/distributed

UV_CACHE_DIR=/tmp/farmare-uv-cache uv run --frozen ruff check \
  are/simulation/distributed are/simulation/tests/distributed

UV_CACHE_DIR=/tmp/farmare-uv-cache uv run --frozen ruff format --check \
  are/simulation/distributed are/simulation/tests/distributed
```

Follow the professor runbook for experiment commands. Do not begin the 90
continuations until the six labels are frozen before D-CORE predictions are
opened. Do not begin the full paper campaign until the professor approves the
exact specification, team, protocol, repair catalogue, comparator, analysis,
manifest, and agricultural-review hashes.
