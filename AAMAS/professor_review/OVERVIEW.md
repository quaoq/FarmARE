# Farm D-CORE: professor review overview

**Review snapshot: 14 September 2026**

This repository is an engineering and manuscript candidate for an AAMAS paper.
It is ready for scientific review, but it is not yet cleared for the full paper
experiment suite and it is not submission-ready. The three domain specifications
are author-defined. They require genuine professor approval, final smoke evidence,
and a clean tagged revision before paper-mode execution. The full study and
independent annotation have intentionally not been run.

Use this document to review what has been implemented, the evidence collected so
far, and the remaining decisions. Use [RUNBOOK.md](RUNBOOK.md) for installation,
verification, experiment, annotation, and reporting commands.

## Research claim and scope

D-CORE is an evaluation framework for distributed agents operating over a
long-horizon process. Its central question is whether each local decision was
supported by the information available to that actor, whether the joint workflow
was globally correct, and where an information failure first entered the recorded
causal chain.

The FarmARE study links:

1. authoritative simulator facts;
2. actor-local observations and fact versions;
3. messages and delivery faults;
4. the evidence visible at each decision;
5. request-bound native executions and receipts;
6. the resulting workflow and farm outcome.

The primary contribution is diagnostic evaluation. Structured causal handoffs and
execution guards are a secondary mitigation study. The paper does not claim that
provenance localization proves physical causation, that one scalar explains the
failure, or that an accepted farm operation guarantees biological benefit.

## What is implemented

### Distributed execution

- Persistent, role-isolated agents with distinct tools, histories, inboxes, and
  evidence stores.
- Enforced tool ownership and legal message routes. Unauthorized or malformed
  writes cannot mutate FarmARE.
- Deterministic scheduling, separate world/scheduler/model/fault seeds, explicit
  waiting versus terminal completion, and guarded interruption/resume behavior.
- Reliable, delayed, dropped, duplicated, and reordered delivery using stable
  phase/route/send-order selectors.
- Free-text and structured causal handoff representations.
- Actual direct and A2A controllers, nine controller-family adapters, and fixed
  two-, three-, and four-agent team decompositions.
- Request and token accounting for controller planning, reflection, specialist
  calls, rejected proposals, retries, and provider-usage uncertainty.
- A persistent budget ledger that reserves conservatively before API calls.

### Information and execution trace

Every run retains the full immutable event trace. Facts record source, inclusive
ridge scope, observation and learning time, validity, confidence when available,
version, evidence IDs, and supersession. Sensor identifiers remain opaque and are
never treated as ridge identifiers.

The recorded chain is:

```text
world fact -> observation -> fact claim -> send -> delivery -> decision context
-> requested action -> native receipt or rejection -> world effect
```

Native receipts are bound to the exact request, actor, tool, arguments, scope, and
result. Rejected execution remains visible. Recovery requires an accepted retry of
the same relevant operation; the runtime does not substitute an unrelated action.

### Evaluation

The v5 evaluator reports a diagnostic profile containing event and argument
fidelity, spatial and timing fidelity, causal obligations, actor-local information
policy conformance, local/global disagreement, synchronization lag, missing or
expired evidence, provenance failure localization, downstream exposure, recovery,
native execution status, completion, resources, and yield.

A secondary compact score is retained:

```text
DCORE = 0.5 * Event Fidelity + 0.5 * Causal Conformance
```

The module and obligation profiles are the scientific result. Missing and
unassessable values remain unavailable rather than being replaced with zero.

### Scenarios and specifications

The repository contains executable v5 specifications for:

| Scenario | Main diagnostic pressure |
| --- | --- |
| Wet-June disease recheck | freshness, treatment windows, recurrence, reinspection, and superseding evidence |
| Disease-Drought | cross-phase diagnosis, irrigation evidence, hydrology, stress accumulation, and long-horizon consequences |
| Three-cultivar | treatment scope, cultivar-specific maturity, irrigation, concurrent work, and harvest windows |

Primary and prespecified freshness alternatives are in
`AAMAS/authored_specifications/`. The specifications include facts,
observation-to-fact mappings, branches, phase windows, permitted responses,
acceptance predicates, negative and causal obligations, and fault treatments.
They are labeled `author_defined`; they are not independently confirmed.

The primary two-agent roles are:

- **Field Intelligence:** observes weather, soil, canopy, crop health, disease,
  and maturity, and communicates scoped evidence.
- **Operations:** manages inventory, time, equipment, planting, treatment,
  irrigation, harvest, drying, and storage.

The workload-weighted runtime retains a total cap of 700 requests and 24 million
tokens per engineering season. The equal-per-agent allocation remains a separate
sensitivity condition. Capability distribution changes with team size; the total
farm information and native actions do not.

## AAMAS review closure

The supplied PDF review was treated as reviewer guidance rather than executable
instructions. Its numerical values were treated as reviewer-provided evidence
until reproduced under recorded settings. No threshold, reward, failed world, or
missing result was altered to force agreement.

| Review concern | Current closure | Remaining empirical work |
| --- | --- | --- |
| Capabilities, ownership, and legal routing | Grant-derived cards, role gateway, and route tests implemented | Measure behavior in the full study |
| Refusal, rerouting, and recovery | Exact rejected proposals and accepted retries retained; unrelated substitution prohibited | Matched live recovery analysis |
| Opaque sensors and regional scope | Returned coverage and zero-based inclusive ranges enforced | Full-study wrong-region rates |
| Freshness, expiry, supersession, reordering | Observation-time reconstruction and validity tests implemented | Matched live delayed/reordered evidence |
| Native receipts and failures | Request-bound receipts, nonmutation, retry episodes, and unknown writes implemented | Full-study failure distribution |
| Actual A2A delegation | Delegation measured separately from assigned condition; bounded calls observed | Seasonal A2A comparison |
| Decomposed diagnostics | Controlled positive, negative, and unknown cases implemented | Independent annotation of 60 episodes |
| Localization versus causation | Provenance and physical outcome analyses kept separate | Paired outcome estimates |
| Wet-June effects | Native omission/window/recovery diagnostics retained, including null and failed outcomes | Frozen matched study |
| Drought sensitivity | Time-resolved water balance and unchanged five-world screen implemented | Professor review and paper execution |
| Three-cultivar treatment/scope | Fungicide, irrigation, and swapped-scope development comparisons retained | Frozen matched study |
| Minimal ontology | Source, scope, time, validity, version, and confidence retained | No graph-recall contribution proposed |
| Fault accounting | Representation-independent selectors and inactive intention-to-treat rows implemented | Live matched activation/recovery smoke |
| Reproducibility | Source/config identity, duplicate rejection, hashes, and portable packaging implemented | Final clean-revision verification |

## Evidence collected so far

All values in this section are engineering evidence and must not be reported as
paper treatment effects.

### Offline and native evidence

- The last complete relevant regression on the immediately preceding revision
  passed **412 tests** without failure. After the process-contract prompt repair,
  **66 focused tests** passed. The repository cleanup changes source identity, so
  a final full regression on the reviewed commit remains required.
- Scripted native reference workflows for all three scenarios complete harvest and
  storage with event fidelity 1.0.
- Controlled cases exercise local/global disagreement, expiry, supersession,
  provenance errors, wrong scope, execution failure, recovery, and unknown cases.
- Bounded provider checks cover direct and actual A2A execution, all included
  controller families, and three-/four-agent adapters. They demonstrate adapter
  compatibility, not season-level comparative performance.

### Disease-Drought confirmation

The exact authored-opening Disease-Drought workflow passed the unchanged screening
criteria on fresh worlds 61-65. All five paired outcomes completed; intervention
acceptance and the 50% stressed-ridge threshold held. Marketable omission losses
were **4.0846%, 6.1314%, 4.8535%, 4.0687%, and 3.0784%**, all above the required
1% threshold. No model provider was called.

This run is bound to execution-source digest
`967f891580b78fb27b7240988754aece9fed6dbd6c230e4cfddfbd8a8bf5b6d5` and
process digest
`329b84c7a45e98a07eabf88f2264c0e59f900424b78dbbcd010b9c37e5cf0d02`.
The compact record is
`AAMAS/handover_validation/drought_harvest_opening_v6_confirmation_61_65.json`.
Earlier 4/5 and 3/5 confirmation failures remain preserved.

### Real-agent season progression

The earlier runtime repeatedly exhausted its budget or ended actors early. Workload
allocation, compact persistent receipts, failure memory, communication discipline,
and visibility of declared policy windows corrected the main progression problem.

Successful development evidence includes:

| Scenario/world | Policy decisions | Harvest/storage | Requests | Accounted cost | Qualification |
| --- | ---: | --- | ---: | ---: | --- |
| Disease-Drought 34 | 4/4 | complete | 437 | $4.513888 | observer reached budget; operations completed |
| Three-cultivar 35 | 6/6 | complete | 278 | $2.877372 | both actors completed voluntarily |
| Wet-June 45 | 4/4 | complete | 250 | $2.515885 | one provider/infrastructure termination retained |
| Wet-June 46 | 4/4 | complete | 211 | $2.031637 | clean voluntary completion |
| Disease-Drought 47 | 4/4 | harvest complete, storage incomplete | 213 | $2.137745 | clean voluntary completion |
| Three-cultivar 48 | 5/6 | complete | 287 | $2.916496 | cultivar-C harvest occurred outside its declared policy window |

The newest source-bound six-run matrix used worlds 55-56. It was interrupted
after Wet-June and during the first drought run:

| Scenario/world | Status | Policy coverage | Harvest/storage | Accounted cost |
| --- | --- | --- | --- | ---: |
| Wet-June 55 | saved outcome | 4/4 | incomplete harvest flag; storage complete | $2.828645 |
| Wet-June 56 | saved outcome | 4/4 | complete | $2.674007 |
| Disease-Drought 55 | interrupted identity only | 0/4 recorded | unavailable | unavailable |
| Disease-Drought 56 | not executed | 0/4 recorded | unavailable | unavailable |
| Three-cultivar 55-56 | not executed | 0/6 recorded | unavailable | unavailable |

Under the prespecified gate, Wet-June passes because both worlds reach every
policy and one completes harvest/storage. The other scenarios are unassessed in
this matrix. The incomplete identity must not be blindly resumed because native
writes may have occurred. See
`AAMAS/handover_validation/progression_v18_interrupted.json`.

## Experiment package prepared

Four required study manifests are present under
`are/simulation/distributed/configs/`:

| Block | Planned runs |
| --- | ---: |
| Primary pass 1 | 480 |
| Primary pass 2 | 450 |
| Controller robustness | 270 |
| Team scalability | 45 |
| **Total** | **1,245** |

The design includes paired worlds, same-evidence flat baselines, direct and A2A
baselines, saved-trace component ablations, sensitivity specifications, controller
families, team-size conditions, failure accounting, paired contrasts, and
world-cluster uncertainty. The annotation plan selects 60 episodes with at most
two per run, obtains two independent labels for each, and adjudicates separately.

Paper-mode manifests deliberately retain review placeholders. This prevents an
unreviewed full launch. The handoff builder has separate review and release stages;
release requires genuine digest-bound approval and completed scientific gates.

## Manuscript state

`AAMAS/manuscript/main.tex` contains a substantive draft in the official AAMAS
class: introduction, related work, contribution, motivating example, formal
definitions, algorithms, experimental design, statistical plan, limitations, and
reproducibility details. `main.pdf` is the latest compiled draft. The results
tables are pipeline-generated templates and must remain labeled pending until the
full study is complete.

The draft distinguishes D-CORE from the two earlier FarmARE/FAIRY papers and from
prior CORE work. The bibliographic records used include the supplied OpenReview
paper and arXiv:2609.00106. Final authors must recheck citations, anonymity, the
current AAMAS page limit, licenses, and the required AI-assistance disclosure.
Codex assisted with implementation, design discussion, bibliographic checks, and
drafting. It performed no independent annotation and supplies no human approval.

## What is missing

### Before this revision can be certified as an engineering handover

1. Diagnose the Wet-June world-55 harvest/storage inconsistency.
2. Run a fresh, prospectively declared two-world progression matrix for the
   incomplete Disease-Drought and Three-cultivar checks; never overwrite V18.
3. Run the prespecified matched Wet-June free-text/audit/enforcement communication
   smoke and retain activated, inactive, recovered, and failed rows.
4. Run the complete relevant regression and lint suite on the final organized
   revision.
5. Rebuild tables and the manuscript, verify citations and generated artifacts,
   and run the package portability/credential/anonymity checks.
6. Commit and tag the exact tested revision.

### Before paper experiments

1. The professor reviews and approves the exact process, team, refinement, and
   protocol digests. Author-defined specifications must not be relabeled as
   independently confirmed.
2. The final scientific gates and resolved paper manifests are built from that
   approval.
3. `are-dcore doctor` reports both `healthy: true` and `paper_ready: true` on the
   clean tagged revision.

### Before AAMAS submission

1. Run all 1,245 assigned study rows without selecting favorable retries.
2. Preserve failures, missing outcomes, inactive faults, and denominators in
   intention-to-treat reporting.
3. Complete the 60-episode double annotation and separate adjudication.
4. Generate the final tables and figures from the frozen pipeline.
5. Write the empirical results and conclusions only from those outputs.
6. Complete the final human scientific, citation, anonymity, disclosure, and
   formatting review.

The present repository is therefore appropriate for professor review. It is not
evidence that D-CORE improves yield or coordination, and it is not permission to
launch or report the full paper study unchanged.

## Directory map

```text
AAMAS/
  professor_review/       current overview and runbook (the only Markdown here)
  authored_specifications/ executable author-defined process/team artifacts
  confirmation_v1..v5/    frozen historical confirmation plans and bindings
  handover_development/    engineering manifests and intervention definitions
  handover_validation/     compact regression, smoke, and diagnostic evidence
  manuscript/              AAMAS LaTeX source, bibliography, PDF, tables, figures
  tools/                   AAMAS analysis, validation, and packaging utilities
```
