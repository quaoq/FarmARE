# D-CORE professor review overview

## Review status

This branch is the offline-tested review candidate for the revised AAMAS EMAS paper.
The paper claim is now:

> D-CORE reconstructs decision-time evidence, diagnoses consequential distributed
> information failures, selects bounded legal repairs, and tests those repairs
> through matched native continuations and selective live verification.

The repository is suitable for scientific review. It is not yet released for the
professor's full experiment suite.
Paper mode remains disabled until digest-bound professor approval, two independent
agricultural reviews, the final bounded smoke, and all engineering gates pass.
No pilot is presented as paper evidence.

## What is implemented

### Runtime and outcome correctness

- `unload_grain` moves grain from the combine to the harvest trailer and reports
  combine, trailer, and warehouse mass.
- Drying reports whether it occurred or was skipped because moisture was already
  safe; either result states that storage is still required.
- Unload, dry, and store remain in operations memory and seasonal duties.
- `wait` is nonterminal and `finish` is terminal. An incomplete finish is recorded
  as `premature_abandonment`; a live policy may defer it only as a recorded
  intervention.
- `farm_outcome_v2` distinguishes recovered harvest, all three grain locations,
  harvest completion, mass conservation, storage completion, measured-moisture
  compliance, missingness, measurement time, and scenario horizon.
- Ordinary controller termination adds no management action. Native physics alone
  advances to the scenario horizon; premature biological measurements remain
  provisional.
- Native simulation time uses explicit simulated actions plus a deterministic
  one-millisecond tick before each native tool invocation. Logging, controller
  bookkeeping, and `wait` do not advance farm time; consecutive zero-delay native
  effects still execute in a stable order across fresh replay.

### Durable evidence and recovery

Each new native run writes a flushed, append-only `progress.dcore.jsonl`. It
records context snapshots, every exposed provider request and response, rejected
proposals, parsed proposals, native-write intent before execution, request-bound
receipt after execution, deliveries, budget changes, interventions, termination,
and the final outcome. Secret-like fields are redacted.

A crash between write intent and receipt becomes `uncertain_native_write`.
Interrupted attempts receive `RECOVERY_STATUS.json` and are never restarted in
place. Consolidated traces remain the analysis artifact but are reconstructable
from a durable journal prefix.

### Diagnostic contracts and witnesses

Public schemas are implemented for:

- `diagnostic_packet_v1`
- `diagnostic_witness_v1`
- `repair_candidate_v1`
- `continuation_manifest_v1`

Every authored high-impact obligation has explicit prerequisites binding its
guard, fact key, actor, inclusive scope, validity rule, and transition-order
dependencies. Multiple failed prerequisites generate separate witnesses with a
shared root-support group when appropriate.

Supported witness mechanisms are missing observation, failed delivery, expired
evidence, incorrect scope, received evidence omitted from context, failure to use
available evidence, native execution failure, transition-order failure, and
unresolved evidence. These labels localize recorded support; they do not claim
physical causation.

### Fair baselines and comparators

Distributed, direct, and A2A conditions receive the same serialized public task
contract and save its digest. Private role context and tools remain separate.
Assessable traces now report native CORE path correctness, merged PC-KTC, and
average actor-local PC-KTC.

The typed adapter registry includes CORE, FAIRY temporal evaluation,
information-enriched CORE, MARBLE-style agricultural milestones, Who&When
All-at-Once, AgentRx, reviewed-constraint AgentRx, a clean DCFA-style
reimplementation, an independent full-information checker, generic
reconsideration, fixed protocol repair, DoVer adaptation, and D-CORE. Adapters
use one diagnostic packet and capability-typed outputs. The checked-in bridge
projects that packet into the pinned Who&When and AgentRx inputs, executes them
in isolated environments, and returns typed diagnoses with source, prompt,
model, request, and available token metadata. An uninstalled or unsupported
method remains explicitly unavailable. MARBLE-style milestones and the DoVer
asynchronous continuation-boundary adaptation are local, documented methods;
they are not presented as executions of external code.

Exact upstream revisions and license decisions are in
`AAMAS/comparators/source_lock.json`. No DCFA repository code is copied because
the reviewed repository exposes no license.

### Replay, repair, and live policies

The replay command builds a digest-bound decision checkpoint and runs an
unchanged native reconstruction from a fresh environment. Immediately before the
selected decision it verifies the semantic prefix, physical app state, actor
contexts, knowledge, pending deliveries, and vector clocks. By default it feeds
every journaled model response, including rejected format attempts, through the
normal FarmARE ReAct parser, history, memory, and tool gateway. The offline
integration test requires equal semantic traces and outcomes with zero provider
calls and no inserted management action. Proposal-level replay remains available
as an engineering diagnostic.

The repair catalogue permits one or two primitives: acquire a missing native
observation, redeliver acquired evidence, refresh expired evidence, route evidence
to the correct actor, restore received evidence omitted from context, or request
reconsideration with valid evidence. Hidden, future, fabricated, wrong-scope, and
late evidence is rejected. Unknown duration remains unresolved. The executor
verifies the checkpoint before intervention, records native observation receipts
and routed evidence, discards every cached future response, and switches to fresh
suffix calls under the checkpoint's remaining budget. The frozen order is earliest
failed obligation, fewer primitives, then lower native cost; cost-only and
unrestricted selectors remain comparison conditions.

Five prefix-only live policies are represented in runtime and manifests:
audit-only, existing guard, always verify, periodic verification, and D-CORE
witness-triggered verification. Treatment, irrigation, harvest, postharvest, and
incomplete finish proposals are high impact. Verification and correction consume
the same team ledger as ordinary model calls.

### Scientific package

The three authored specifications cover Wet-June, Disease-Drought, and
Three-cultivar. Each includes mappings, phases, branches, permitted responses,
action acceptance, negative and causal obligations, fault treatments, and
assumption provenance. The 12-hour freshness alternatives were frozen before
confirmation. Twenty-four agricultural review packets are ready: eight per
scenario, with two independent blank submissions.

The revised whole-season allocation is exact and contains no path placeholders:

| Block | Assignments | Status |
| --- | ---: | --- |
| Primary pass 1 | 480 | disabled pending release |
| Primary pass 2 | 450 | disabled pending release |
| Live verification | 300 | disabled pending release |
| Reserve | 15 | explicitly `execution_allowed: false` |
| **Total** | **1,245** | |

The independent annotation plan selects 120 decisions, 40 per scenario and at
most two per run. The repair study selects 60 checkpoints, 20 per scenario, with
five conditions and three suffix repetitions (up to 900 continuations). The
professor's primary worlds are 100-109; focused analyses use 100-104.

The resource estimate is provisional. Four completed V18 engineering seasons
averaged 318.5 provider requests, 5.94 million tokens, 17.3 minutes, and $3.3045
each. Straight multiplication suggests about $4,064.54 for the 1,230 assigned
non-reserve seasons, before repair suffixes. The estimate must be refreshed after
the smoke budget is resolved; its run-level basis and limitations are saved in
`AAMAS/handover_validation/resource_estimates_20260915.json`.

## Preserved engineering evidence

Historical pilots and failed confirmations remain unchanged. A new versioned V18
summary corrects the completed Disease-Drought world-55 raw run: harvest finished,
4,450.43 kg remained in the trailer, no grain reached the warehouse, and both
actors finished voluntarily. The earlier interrupted summary remains and is
marked superseded rather than overwritten.

The compact correction is
`AAMAS/handover_validation/progression_v18_completed_after_review.json`.
Raw V13, V16, and V18 inventories and hashes are recorded in
`AAMAS/handover_validation/raw_progression_inventory_v1.json`. The V18 raw archive
is outside Git under `results/aamas_handover/exports/`.

Disease-Drought scripted confirmation on worlds 61-65 remains engineering
calibration evidence: all five pairs satisfied the unchanged screening criteria.
It is not evidence about autonomous agent performance or D-CORE repair efficacy.

## Manuscript state

`AAMAS/manuscript/main.tex` uses the official AAMAS class and is restructured
around diagnostic witnesses, legal repair, matched continuation, and selective
verification. It includes the relationship to the two earlier FarmARE/FAIRY
papers and capability-appropriate comparisons to Who&When, AgentRx, MARBLE, DCFA,
and DoVer. Four main result tables are generated by the analysis pipeline:
evaluation disagreement, diagnosis, matched repair, and live verification.
Every empirical cell and the conclusion remain pending.

## Remaining before engineering handover

1. Install/configure the pinned external Who&When and AgentRx environments and run
   one bounded packet through both bridges. The projections, common isolated
   adapter contract, local comparators, clean DCFA-style method, DoVer adaptation,
   and unsupported-output handling are tested; paid upstream inference remains
   unverified under the exhausted smoke allocation.
2. Resolve the final provider smoke budget. The live ledger is $135.814958, already
   $30.348556 above the recorded $105.466402 baseline and therefore $5.348556 past
   this revision's $25 incremental cap. One request remains reserved and one has
   unknown usage. No V19 smoke was launched after this was discovered.
3. After those two checks pass, build the immutable review handoff. Release tagging
   and paper execution remain gated on professor approval and the scientific gates.

The current offline record is
`AAMAS/handover_validation/review_status_20260915.json`: 423 distributed tests
pass, native farm physics reports five passes and three declared expected failures,
lint is clean, specifications regenerate byte-for-byte, all three scripted native
workflows complete storage, the four manifests resolve at their frozen counts, and
the seven-page manuscript compiles with no undefined citation key. Credential and
machine-path scans are clean. Exact review artifact hashes are included in that
record.

## Remaining for the professor

The professor reviews the exact specifications, repair catalogue, comparator
methods, protocol, manifests, and saved smoke evidence. Two agricultural reviewers
complete the 24 packets. After genuine digest-bound approval, the release package
enables paper mode. The professor then runs the full seasons and repair suffixes,
freezes 120 annotation decisions before D-CORE predictions, obtains two independent
labels and adjudication, regenerates the analyses, and writes results and
conclusions from those outputs.

Engineering completion does not establish AAMAS acceptance. The evidence needed
for submission is the professor's complete, failure-preserving study and the
independent reviews—not the historical pilots.
