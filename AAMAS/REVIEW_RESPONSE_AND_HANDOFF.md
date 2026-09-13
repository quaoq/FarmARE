# D-CORE review response and implementation handoff

The subsequent [`CONFIRMATION_AUDIT.md`](CONFIRMATION_AUDIT.md) maps every PDF
point to implementation and remaining evidence, and records additional scope,
freshness, policy, retry and release-verification fixes.

Prepared 2026-09-12. This records engineering changes prompted by
`AAMAS_DCORE_REVIEW.pdf`; its reported ablations are reviewer-provided evidence,
not experiments rerun in this change. No paid model calls, paper matrices, or
calibration seasons were launched. Offline regression fixtures exercise native
simulations as software tests.

## Changes and their scientific purpose

| Review issue / audit finding | Implementation | Scientific boundary |
| --- | --- | --- |
| Teammate capabilities are implicit | Fixed capability cards derived from the resolved team grants; legal next hops and time authority; identical coordination instructions across native LLM conditions | Cards contain permissions, no hidden world state or oracle actions. They are saved in trace configuration with digests. |
| Routing refusals can lead to unrelated operations | Prompt contract requires reporting the refusal and owner, routing through a legal next hop, inspecting failed execution and replanning | The runtime does not replace an agent's proposed action or force a successful recovery. |
| Sensor labels and ridge ranges are confused | Explicit zero-based inclusive scope convention, opaque sensor IDs, and multiple scope-specific versions per handoff | No global sensor state is exposed through capability cards. |
| Late old evidence can replace newer evidence | Both prompt adapters and local selection rank observation time before learning time; exact-scope frontiers retain separate regions | Delivery clocks describe execution order, not observational freshness. Overlapping unequal scopes remain available for downstream checks. |
| Multiple claims with the same fact key share the wrong origin | Receive provenance binds each claim by its exact message claim index | Synchronization metrics can account for each delivered version. |
| Intent ID cache can bypass authorization | Ownership and normalized request identity are checked before cached results are returned; changed requests are rejected; result/argument snapshots are isolated | Exactly-once behavior is scoped to one in-process gateway. It does not claim crash-safe distributed transactions. |
| Execution outcomes need receipts | Digest-bound request/result/native-event receipts in action traces and local results; write receipts are scoped local facts that can be sent through causal handoffs | A digest is not a signature. Acceptance records execution, not eventual agronomic benefit. A past receipt does not establish current evidence freshness. |
| Drought irrigation benefit is unvalidated | Isolated weather candidate, paired irrigation omission runner, root-zone stress instrumentation, explicit acceptance failures | Candidate is not promoted into paper matrices. No improved yield is claimed. |
| World pairing misses soil changes | Exogenous manifests also bind soil parameters, hydraulic modifiers and initial soil states | Old world digests change; regenerate paired artifacts together. |
| Transfer handoff requires an unnecessary extra smoke | Handoff follows scenario-specific requirements already enforced by the scientific gate model | The Wet-June smoke requirement remains. |

The ontology deliberately stays small: agents, tool permissions, regions,
observations, actions and facts, with a documented relation vocabulary. It is
not a general knowledge graph, inference engine, or graph-recall contribution.

## Delegated drought calibration

Install the frozen development environment from the repository root:

```bash
uv sync --frozen --python 3.12 --extra dev
uv run --frozen pytest are/simulation/tests/distributed -q --junitxml=validation/dcore-tests.xml
```

Inspect the proposed workload without starting a season:

```bash
uv run --frozen are-dcore calibrate-scenario \
  --candidate --dry-run --output-dir results/drought-candidate
```

The following commands are for the later experiment operator. Each runs 20
no-model seasons: control and omission for world seeds 0–9. Each requires a new
output directory and retains the plan and native exports. Keep both reports,
including failures and negative effects.

```bash
uv run --frozen are-dcore calibrate-scenario --output-dir results/drought-original
uv run --frozen are-dcore calibrate-scenario --candidate --output-dir results/drought-candidate
```

The opt-in candidate moves the dry spell to July 10–August 16, leaving the
shared profile registry, native default scenario, yield functions and action
rewards unchanged. It is an engineering hypothesis about timing, not a
validated agronomic calibration. Candidate initialization copies the profile;
it cannot mutate another run's forcing.

The intervention omits exactly the R5 irrigation action and preserves the
remaining oracle workflow and dependency graph. Both arms record the target
event, time, ridge scope, per-ridge root moisture and stress thresholds. The
calibration runner uses an event-driven clock (`clock_mode: event_queue_only`)
so host overhead does not alter paired simulation times. It synchronizes lazy
physics before measuring stress in either arm. The
report rejects missing/duplicate worlds, inactive or repeated interventions,
different pre-intervention states or exogenous digests, incomplete seasons,
invalid yields, insufficient stress, and insufficient loss. Omission may fail
the native action-presence validator; that is retained separately from physical
harvest/storage completion. Control must pass native validation.

Default engineering acceptance requires at least five worlds, at least half
the target ridges below their native root-zone stress threshold at irrigation,
and at least 1% whole-field marketable-yield shortfall in **every** omission
pair. These are transparent screening choices, configurable before execution;
they are not expert-approved agronomic thresholds or statistical significance
tests. Freeze the plan before comparing results. The report always carries
`paper_eligible: false`, including when engineering acceptance passes.

The full calibration CLI has been unit-tested for target selection, omission
nonmutation, graph preservation, candidate isolation, evidence checks and
dry-run behavior. Its complete seasonal workload remains for the operator to
execute and inspect.

After validation, domain experts must review the evidence, choose or reject
the candidate, and freeze a new scenario/process revision and protocol before
held-out model runs. Disease–Drought complete release gates now require
`scenario_sensitivity_passed: true` and a SHA-256
`scenario_sensitivity_report_digest`, plus `scenario_sensitivity_report_path`
(relative to the gate JSON, or absolute). Populate them only from reviewed saved
evidence for the exact released scenario. Runtime, doctor and handoff load the
file, verify its byte digest and recompute acceptance checks. Runtime also
checks coverage of the selected world and its current exogenous digest. Handoff
copies the report into its package and rewrites the path. A candidate-only
success does not validate the unchanged default and is explicitly rejected.

## Reproducibility and remaining work

These changes alter prompts, scoped handoffs, receipts and exogenous digests.
Existing model runs and old manifests must not be silently pooled with new
runs. Freeze a fresh code commit, process/team artifacts, protocol and release
tag together. Keep the diagnostic profile primary; no localization is evidence
of actual yield causation without an appropriate paired intervention.

The independent agronomy review, final scenario calibration, bounded real-model
smoke, complete scientific gates and tagged release remain required. This
change does not manufacture expert attestations or certify conference
acceptance. The three professor guides remain the execution reference, with
this document supplying the review-related additions.

The dedicated `D-CORE offline regression` workflow runs the locked distributed
test suite and uploads its JUnit report without provider credentials. Local
verification details are recorded in `VALIDATION_REPORT.md`.
