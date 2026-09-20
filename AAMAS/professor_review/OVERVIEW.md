# D-CORE professor review overview

## Current status

D-CORE reconstructs the evidence available to an actor at a consequential
decision, identifies the failed prerequisite, selects at most two legal
information repairs, and measures the repair through a matched native
continuation. This revision implements that engineering path without changing
the manuscript or making an empirical superiority claim.

The final engineering pass on 2026-09-20 corrected several additional defects
found by live execution: farm-time controls are no longer misclassified as
management writes; prompt budgeting retains current scoped evidence and the
newest request before historical failures; causal handoffs can bind exact
prompt-visible evidence versions; and planting/harvest guards use the exact
native batch scope. Three-cultivar planting now rejects the wrong cultivar
without mutating the world, and sensor documentation no longer implies that an
opaque sensor ID identifies a ridge. Durable checkpoints retain the bounded
history actually consumed by the controller, while the append-only journal
continues to retain the replay record.

Paid engineering execution has stopped. The preserved worlds 110--114 were
development-only failure-discovery cohorts and are not paper evidence. World
113 reached legal treatment and completed one cultivar-zone harvest, unload and
storage cycle before exposing a stale broad-scope harvest guard. World 114
verified exact-version handoff use and continued native progression, but was
manually stopped when the agents entered a costly refresh/retry loop around
regional disease evidence. These runs must not be cherry-picked or promoted to
results; they document why the professor's controlled miniature and full study
remain necessary.

The replacement OpenAI credential passes a read-only authentication probe. All
six real source assignments then completed with available outcomes and no
infrastructure failures. The cohort did not pass the progression gate: none of
the six seasons completed harvest, storage and safe postharvest handling. The
2,604 provider calls, 48,705,297 prompt tokens, 288,000 completion tokens and
$27.227768 accounted cost are preserved under
`results/aamas_handover/miniature_sources_v2_retry1`.

This failed cohort exposed an implementation defect rather than an API failure.
An accepted unload/dry/store chain remained in completion memory after a later
harvest put new grain in the combine. The runtime now invalidates downstream
postharvest receipts whenever the grain state changes. The same run also exposed
impractical durable-journal growth; proposal checkpoints now compress prompt
history deterministically and journal consumers stream or selectively hydrate
the required checkpoint. It also exposed synthetic evidence blocks on
argument-free unload operations that legitimately map to several authored
occurrences. Those operations remain unresolved for diagnosis but now proceed to
native physical validation when none of the candidate occurrences has an
information prerequisite. Replay equivalence and focused recovery tests pass.
The failed cohort remains pre-fix engineering evidence and is not reused. A
post-fix cohort on prospectively declared unused worlds 72--73 is defined in
`AAMAS/handover_development/progression_v20_worlds72_73.yaml`.

The exact closure matrix is
`AAMAS/handover_validation/bulletproof_revision_closure_v3.json`. The validation
record is `AAMAS/handover_validation/offline_validation_20260919.json`.

The manifest-bound scripted reference cohort is also complete. All six assigned
worlds produced available outcomes; five completed harvest, storage and safe
postharvest handling. Drought world 71 retained its partial 5,056.73 kg recovered
harvest outcome and failed full completion rather than being replaced. Every
scenario has at least one complete reference world. The tracked summary is
`AAMAS/handover_validation/scripted_reference_validation_20260919.json`; raw
records and regenerated engineering tables remain under the ignored `results/`
artifact tree.

## What is implemented

- One shared resolver binds a proposal to an exact authored operation using
  actor, action, arguments, inclusive scope, phase, time window, prior
  occurrences and transition order. It returns `unique`, `ambiguous` or
  `unmatched`; unresolved proposals cannot trigger a repair.
- New v2 packets and witnesses are prefix-only. They retain the target proposal,
  actual prompt evidence, exact prerequisite, version, holder, recipient,
  delivery, scope, freshness and deadline while excluding execution, receipts,
  future events, outcomes and terminal metrics. Historical v1/v2 artifacts remain
  readable.
- Native observation estimates are side-effect free and use the same duration
  calculations as drone, robot, sensor and transport execution. Repairs are
  rechecked at execution time against equipment, battery, route delay, exact
  scope, response lead and the earliest authored deadline. Unknown feasibility
  remains unresolved; late applied repairs remain visible and ineffective.
- The native scenario horizon is now part of the compiled and authored
  scientific contract. Policy, operation-resolution and reference-retry
  deadlines use the earliest applicable authored deadline and native horizon,
  so a rejected operation cannot create an unbounded one-day retry loop.
- Routing requires exact-version ownership. Prompt restoration requires that the
  recipient already holds that version and is successful only when the evidence
  identifier appears in the next final prompt.
- Continuation manifests bind physical state, actor context, knowledge, prompt
  history, memory, request counters, pending envelopes, clocks, scheduler state,
  random state, scientific configuration and original request/token caps. Fresh
  untreated and repaired arms verify the same prefix, discard recorded futures,
  and receive the same remaining suffix allowance.
- The append-only journal records provider intent and response, parsed and
  rejected proposals, native-write intent and receipt, transport, repair and
  termination. Cumulative prompt histories are stored in a deterministic,
  integrity-checked compressed form and hydrated only when needed. Uncertain
  provider requests and native writes are non-replayable.
- The primary comparison paths are distinct implementations: action-trace
  checker with generic reconsideration, generic reconsideration alone, frozen
  fixed protocol, independent same-information checker, and D-CORE. Unsupported
  historical FAIRY, enriched-CORE, MARBLE, DCFA and DoVer names return typed
  `unavailable` results rather than misleading comparisons.
- Saved-packet ablations remove temporal validity, actor/delivery information,
  prompt inclusion, scope or targeted selection one component at a time.
  Diagnosis-only reuses the fresh untreated continuation. A native suffix is
  needed only when an ablation changes the selected intervention.
- Audit-only, existing guard, metered always-verify, metered periodic-verify and
  witness-triggered D-CORE policies cover treatment, irrigation, harvest, unload,
  drying, storage and incomplete finish. Finish deferral is reported separately
  from information repair.
- Manifest-first reports retain assigned failures, abstentions, infeasible
  repairs, partial harvest, missing outcomes, costs and adverse effects. They
  report mechanism confusion matrices, F1, exact prerequisite/actor/scope/version
  accuracy, legal repair and deadline accuracy, matched harvest changes, and
  separate always-verify noninferiority and audit-only improvement contrasts.
- The 24 agricultural packets now use deterministic stratified set cover. Every
  scenario covers establishment, treatment or irrigation, harvest, genuine
  occurrence-net postharvest order, and its required scenario-specific cases.
  Duplicate content or coverage shortfall fails generation.
- The future full-study template freezes 60 labelled checkpoints and 900 possible
  suffix assignments while keeping execution disabled pending professor release.

## Validation evidence

- The preceding implementation checkpoint completed the distributed suite with
  **453 passed**. After the final live-discovered fixes, the focused resolver,
  replay, budget, prompt, exact-version, scope and review suites pass **83 tests**.
  A broad rerun passed its first **122 tests** before it was manually stopped to
  end the engineering session; the professor should run the complete command in
  the runbook from a clean checkout.
- Native farm checks: **5 passed, 3 documented expected failures**.
- Ruff lint, Ruff formatting and Python compilation pass for the changed scope.
- New standalone resolver, metrics, study-template and agricultural-review
  modules pass scoped Pyright with zero errors. Repository-wide Pyright already
  had 1,121 errors at the preceding commit, so it is not presented as a clean
  repository-wide gate.
- The post-fix manifests resolve without placeholders: six source seasons, six
  matching scripted references and 30 live-policy seasons.
- Agricultural packet generation produces 24 unique packets with complete
  required coverage.
- The six scripted references terminate within the frozen logical-step limit;
  all six outcomes remain available, five are fully successful, and each
  scenario has at least one complete harvest/storage/postharvest world. Their
  six rows regenerate the aggregate plus four main tables and five figures.
- The replacement credential returned HTTP 200. The six pre-fix real source
  seasons retained all assigned rows and available outcomes with zero
  infrastructure failures and zero unknown-usage requests. They used 2,604
  accounted model calls at $27.227768 total and failed the postharvest progression
  gate honestly; no checkpoint labels, suffixes or policy seasons were opened.
- Focused tests verify that a new accepted harvest invalidates prior postharvest
  completion receipts and that compressed journals preserve exact checkpoint
  history and replay semantics. A real-spec test also verifies that an ambiguous
  prerequisite-free unload does not fabricate missing evidence or block the
  native safety operation.

## What remains

1. Complete final regression validation, then run the six post-fix no-fault
   source seasons from `progression_v20_worlds72_73.yaml` in a fresh output root.
   Both worlds must reach each scenario's high-impact decisions, and at least one
   world per scenario must complete harvest, storage and postharvest handling.
2. Before opening D-CORE output, freeze one author-labelled repairable failure
   and one valid decision per scenario. Cover native acquisition, exact-version
   routing and prompt restoration, using a declared controlled fault only if a
   natural case is absent.
3. Generate the v2 repair-study manifest and execute all 90 assigned suffixes.
   Preserve every failure, abstention, infeasible repair, null effect and adverse
   outcome.
4. Execute the 30 post-fix live-policy assignments from
   `miniature_live_policy_v3_worlds72_73.yaml` and regenerate diagnosis, repair,
   policy, completion, missingness and cost tables from the saved records.
5. Obtain two independent agricultural reviews and professor approval bound to
   the exact specification, team, protocol, repair catalogue, comparator,
   analysis, manifest and review hashes.

The professor's full paper study, independent annotation, adjudication and
empirical conclusions remain separate. The miniature validates the pipeline; it
does not establish that D-CORE wins.
