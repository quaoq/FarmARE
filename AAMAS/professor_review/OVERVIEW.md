# D-CORE professor review overview

## Purpose and current status

D-CORE reconstructs the evidence available at a consequential multiagent decision,
diagnoses a failed prerequisite, selects at most two legal information repairs, and
tests the repair through a matched native continuation. The target is the AAMAS
Engineering and Analysis of Multiagent Systems area.

This branch is an engineering review candidate. The manuscript has no comparative
results. Paper execution remains disabled until the focused offline gates, bounded
miniature study, agricultural review, and digest-bound professor approval pass.
Historical pilots remain engineering evidence and are not paper observations.
The itemized response to the latest review is
`AAMAS/handover_validation/focused_revision_closure_v2.json`.

## Implemented foundation

- `farm_outcome_v2` reports recovered harvest and grain in the combine, trailer,
  and warehouse; harvest, storage, mass-conservation and measured-moisture status;
  fixed-horizon measurement; and explicit missingness. Partial grain recovered at
  the horizon remains quantitative even when storage is incomplete.
- `wait` is nonterminal. `finish` is terminal, and incomplete duties are recorded
  as `premature_abandonment`. A policy may defer finish only as a recorded
  intervention.
- The append-only journal flushes provider intent before dispatch, response and
  usage after return, parsed and rejected proposals, native-write intent and
  receipt, messages, checkpoints, budget use, interventions and termination.
  Unpaired provider and native intents become `uncertain_provider_request` and
  `uncertain_native_write` and are not automatically replayed.
- New studies emit `diagnostic_packet_v2`, `diagnostic_witness_v2`,
  `repair_candidate_v2`, and `continuation_manifest_v2`; historical v1 records
  remain readable. Prefix packets include the target proposal and actual input
  context while excluding its execution, future events, outcomes and full-run
  metrics.
- A v2 checkpoint binds physical state, actor contexts, knowledge, pending
  envelopes, clocks, controller state, prompt/history, memory, counters,
  scheduler state, random state, scientific configuration and remaining suffix
  budget. Graph-aware canonicalization accepts consistent generated-ID renaming
  but retains owners, recipients, versions and causal edges.
- Response replay passes recorded outputs through the normal parser, history,
  memory and tool gateway. A fresh untreated continuation and repaired arms both
  verify the prefix, discard recorded futures and resume from the same remaining
  budget and exogenous continuation.
- Repair ownership comes from the recorded team and native tool inventory.
  Routing requires the sender to hold the exact version; context restoration
  requires that the recipient already hold it. Native sensing, including drone
  survey battery effects, is allowed. Hidden, future, fabricated, expired,
  wrong-scope and unauthorized management actions are rejected. The frozen
  catalogue is `AAMAS/handover_development/repair_catalogue_v2.json`.
- The implemented core comparison set is: independent full-information checker,
  frozen fixed-protocol rules, generic reconsideration without evidence insertion,
  and D-CORE bounded repair. Native CORE and a scope-, multiplicity- and
  completion-aware agricultural milestone adaptation are retained. Misleading
  historical FAIRY, enriched-CORE, MARBLE, DCFA and DoVer adapter names now return
  `unavailable`; DCFA and DoVer remain related work.
- Who&When and AgentRx bridges receive the same documented evidence view: fact
  values and scopes, messages, receipts, deliveries and actual prompt inclusion.
  They preserve abstention, no-error, invalid-step and inconclusive output. They
  enter the paper only after isolated normalized-input and raw-output fixtures
  pass.
- Always-verify and periodic-verify make real metered verifier calls over the
  actor-local legal prefix. The existing programmatic guard remains separately
  named. D-CORE constructs a prefix-local witness and can execute the selected
  native repair. Treatment, irrigation, harvest, storage, postharvest and
  incomplete finish are high impact.
- Reporting joins records by analysis block, assignment, run, decision,
  checkpoint, method, condition and repetition. It generates diagnosis, repair,
  live-policy, completion, missingness and provider-cost tables. Selective-policy
  noninferiority is against always-verify; improvement over audit-only is separate.
- Agricultural packets are unique obligation/prerequisite cases. The builder
  fails rather than padding a scenario with duplicate content.
- Release approval binds the exact specifications, team/refinement, protocol,
  repair catalogue, comparator lock, analysis contract, experiment manifests and
  completed agricultural-review validation. Any changed digest invalidates it.

## Study design

The three scenarios remain Wet-June, Disease-Drought and Three-cultivar. The
frozen whole-season plan retains 480 and 450 primary runs, 300 live-verification
runs and 15 disabled reserve assignments. Full execution and independent
annotation remain the professor's responsibility.

Before expansion, the prospective miniature uses worlds 70 and 71. It requires
one independently labeled repairable information failure and one valid decision
per scenario, frozen before D-CORE prediction. Six checkpoints cross five
conditions and three suffix repetitions (90 assigned continuations), followed by
five live policies on six scenario-world assignments (30 assigned seasons). It is
pipeline validation, not a paper claim.

No paid miniature or live-policy run may start until a conservative request,
token, retry and monetary estimate is reviewed and the professor supplies a new
explicit numeric allocation. The earlier smoke allocation is exhausted.

## Evidence completed in this revision

- The complete distributed regression suite passes: 439/439.
- Focused v2 correction and whitepaper tests pass: 33/33. They cover hand-labelled
  evidence cases, multiple prerequisites, graph-preserving identifier renaming,
  semantic replay mismatches, provider and native interruption boundaries,
  deadline feasibility, exact-horizon behavior and metered verification.
- Native farm physics checks pass: 11 passed and 3 documented expected failures.
- Unchanged native replay reproduces the semantic trace and agricultural outcome.
- Response-level checkpoint verification and fresh repaired suffix execution pass.
- A two-primitive native observation plus team-transport repair is exercised.
- All three scripted authored workflows complete harvest and storage with native
  outcome agreement. Their paper-eligibility flag correctly remains false.
- Lint and Python compilation pass. The 38 table artifacts regenerate
  byte-for-byte deterministically.
- The AAMAS manuscript compiles with Tectonic, resolves its citations and is seven
  pages. Result cells and empirical conclusions remain pending.

## Remaining before experiment release

The code and documentation are ready for professor engineering review. Expansion
to paper experiments remains intentionally blocked by the following scientific
and spending gates:

1. Build the six source seasons for worlds 70--71, then freeze independent
   checkpoint labels without reading D-CORE output, and generate the executable
   `dcore_repair_study_manifest_v2`.
2. Review `AAMAS/handover_validation/miniature_cost_preestimate_v2.json` and
   provide a new explicit numeric allocation. The conservative combined ceiling is
   $728.04; the recommended staged allocation is $30.34 for the six source seasons,
   followed by a tighter estimate from their frozen checkpoints. Then execute the
   90 matched suffixes and 30 live-policy seasons, preserving every failed, null
   and adverse assignment.
3. Run isolated Who&When and AgentRx normalized-input/raw-output fixtures only if
   these methods will be included. They cannot delay the core miniature.
4. Obtain two real, independent agricultural reviews of the 24 unique packets and
   resolve any `revise` or `unknown` result prospectively.
5. Bind professor approval to the final specification, team, protocol, repair
   catalogue, comparator, analysis, manifest and agricultural-review hashes.

No implementation gate is being waived. The miniature cannot start because the
plan explicitly requires a new numeric budget after offline completion.

## Professor work after engineering handover

The professor reviews the exact specification, repair catalogue, comparator
selection, analysis contract and miniature checkpoints. Two agricultural reviewers
complete the packets. After the miniature passes and genuine digest-bound approval
is recorded, the professor runs the full seasons, freezes and independently labels
120 decisions, adjudicates disagreements, regenerates all tables, and writes the
results and conclusions from those records.

The historical corrected drought run remains preserved: harvest completed,
4,450.43 kg stayed in the trailer, nothing reached storage, and both actors
finished voluntarily. It is a motivating engineering failure, not comparative
paper evidence.
