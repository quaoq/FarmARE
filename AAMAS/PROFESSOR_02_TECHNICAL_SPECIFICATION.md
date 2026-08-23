# Farm D-CORE: Technical and Experimental Specification

This is the second of three documents. It defines the runtime, formal objects,
metrics, environments, experimental design, and scientific limits in more
depth. Read
[`PROFESSOR_01_END_TO_END_OVERVIEW.md`](PROFESSOR_01_END_TO_END_OVERVIEW.md)
first and
[`PROFESSOR_03_EXECUTION_GUIDE.md`](PROFESSOR_03_EXECUTION_GUIDE.md) next.

## 1. Scientific scope

Farm D-CORE extends sequential long-horizon workflow evaluation to distributed
farm agents operating under partial, delayed, and stale information. Its target
is black-box, tool-using agents; it does not assume access to model weights,
policy gradients, or internal reasoning.

The framework separates four relations that are often conflated:

1. **Execution order:** what happened before what in the distributed run.
2. **Normative dependence:** what the reviewed farm workflow required.
3. **Information state:** what a particular actor could know before deciding.
4. **Physical outcome:** what FarmARE simulated and the yield it produced.

Single-trace diagnostics are described as conformance and provenance-based
failure localization. Claims about the causal effect of a fault, guard, or
message representation require a paired controlled intervention.

The frozen scientific versions are:

- `farm_process_spec_v5`
- `dcore_trace_v5`
- `dcore_eval_v5`

Earlier versions remain readable for engineering compatibility but are rejected
by paper-mode aggregation.

## 2. Runtime entities

Let the fixed team be (A=\{a_1,\ldots,a_n\}). The paper's primary team has
(n=2); scalability experiments use (n\in\{2,3,4\}).

### 2.1 Team specification

`AgentTeamSpec` defines:

- Stable team ID and schema version.
- Actor roster and role descriptions.
- Tool grants and observation permissions.
- Communication topology.
- Activation policy and priorities.
- Actors authorized to advance farm time.
- Team-wide and per-agent model-call/token budgets.
- Process and role-refinement digests.
- Independent-review status.

Mutable tools have exclusive ownership. The validator rejects duplicate owners,
unknown actors, invalid topology edges, unauthorized time authority, and
inconsistent budgets before a run begins.

### 2.2 Communication topology

`CommunicationTopologySpec` supports:

- Explicit directed edges.
- Fully connected teams.
- A hierarchical/star organization.
- Shared blackboard.

A broadcast is expanded deterministically into recipient-specific deliveries.
Every delivery retains one root message ID and a stable recipient delivery ID.

### 2.3 Controller interface

All controller modes use the same step-wise contract:

- `scripted`: deterministic semantic oracle and tests.
- `mock_llm`: offline controller integration.
- `llm`: independent native FarmARE `BaseAgent` instances.
- `replay`: reuses recorded intents.

Each activation emits at most one externally visible intent:

```text
OBSERVE | ACT | SEND | WAIT | ABSTAIN | FINISH
```

Each actor retains its own prompt history, tool schemas, prior results,
knowledge store, inbox, vector clock, and telemetry throughout the season.
Actual LLM runs record model snapshot, provider, response ID, prompt digest,
`LLMInputLog` ID, latency, retries, and token use. An LLM sampling seed is
recorded only when the provider supports it; exact LLM determinism is not
claimed.

### 2.4 Role tool gateway

The gateway wraps native `scenario.get_tools()` tools. It:

1. Exposes only role-owned tools to the controller.
2. Revalidates ownership and arguments at execution time.
3. Applies the guard to high-impact writes when configured.
4. Executes the controller's exact tool and arguments through native FarmARE.
5. Links the resulting authoritative `CompletedEvent` to the actor, decision,
   evidence, and causal parents.
6. Uses stable intent IDs to guarantee exactly-once writes.

An invalid ridge or treatment amount is not replaced with an oracle value. If
FarmARE accepts the call, its physical state and yield reflect the proposed
arguments.

## 3. Scheduler, transport, and reproducibility

The runtime is a logical-time discrete-event system rather than a threaded or
network deployment. A scheduler item is ordered by:

```text
(logical_time, priority, actor_id, insertion_sequence)
```

Activation policies are `round_robin`, `seeded_permutation`, and
`event_driven`. The actor roster is fixed during a season.

The transport supports:

- Reliable delivery.
- Delay within evidence validity.
- First delivery after evidence validity.
- First delivery after the agronomic deadline.
- Drop by stable message/fact identifier.
- Duplicate delivery.
- Explicit old/new receive-order inversion.
- A frozen mixed seasonal fault schedule.

Duplicate receives are idempotent. A faulted run records whether its intended
fault actually manifested; paper mode rejects inactive fault treatments.

Four seeds remain separate:

- `world_seed`: weather, crop, and exogenous FarmARE trajectory.
- `scheduler_seed`: enabled-agent activation ordering.
- `model_seed`: provider sampling when supported.
- `fault_seed`: stochastic fault selection only.

Paired conditions must have identical exogenous-world digests. Communication
faults cannot directly modify weather or crop physics.

## 4. Execution partial order

For actor (a_i), each local event increments vector-clock component (i).
A send carries the sender clock. A receive performs a componentwise maximum
with the carried clock and then increments the receiver component. World
effects have an explicit world component and recorded action/effect parents.

The observed happens-before relation (\prec_{HB}) is generated by:

1. Per-actor program order.
2. Message send → receive.
3. Explicit tool/action → world-effect provenance.
4. Declared runtime causal parents.

Independent events remain incomparable. The graph validator rejects duplicate
IDs, missing parents, clock regressions, inconsistent send/receive clocks, and
cycles.

Vector clocks encode this execution relation only. Normative farm prerequisites
come from the reviewed process specification, not from clocks.

## 5. Facts and information state

For actor (i), (K_i(t)) is the immutable set of fact versions available
immediately before a decision at time (t).

A fact version records:

- Fact key, value, units, and entity/ridge scope.
- Epistemic status: `observed`, `inferred`, `claimed`, or `unknown`.
- Source actor and authoritative/observation origin.
- Source `CompletedEvent` and evidence IDs.
- World observation time and local learning time.
- Validity deadline and supersession policy.
- Confidence when supplied.
- Causal parents and vector clock.

Repeated observations create new versions. A new version supersedes an older
one only over overlapping scope according to the reviewed fact definition.

Authoritative FarmARE truth is available to the evaluator. It is not available
to an agent or guard unless that actor legitimately observed or received it.

Requirements use three-valued logic:

```text
TRUE | FALSE | UNKNOWN
```

`UNKNOWN` is never silently counted as successful execution.

## 6. Handoffs and guards

### 6.1 Free-text envelope

A free-text message records sender, recipient, text, send time, clock, and
message ID. Its semantics are `unverifiable_handoff` unless a controlled
mutation or frozen human annotation maps its content to fact-version IDs.

### 6.2 Causal handoff

A causal handoff contains scoped fact claims with values, epistemic status,
confidence, observation time, validity, evidence IDs, origin versions, causal
parents, unresolved requirements, and sender clock. It does not serialize the
sender's entire private state.

### 6.3 Guard semantics

The guard independently checks authorization, evidence provenance, scope,
freshness, supersession, contradiction, task/world prerequisites, and deadline
state. Its result is:

- `ALLOW`: execute exactly once.
- `DEFER`: reactivate at a delivery, observation opportunity, or deadline.
- `BLOCK`: do not send the write to FarmARE; expose the reason to the actor.

Modes isolate the experimental effect:

- `off`: no guard computation changes execution.
- `audit`: record the reconstructed verdict but execute anyway.
- `enforce`: apply allow/defer/block.

A blocked unsafe proposal remains a reasoning or policy violation even if the
guard prevents physical harm. Guard effectiveness and agent reasoning are
reported separately.

## 7. Process formalism

The authoritative object is a hierarchical, data-aware farm process compiled
deterministically into a labeled 1-safe occurrence net. This is intentionally
narrower than claiming support for arbitrary timed/data Petri nets.

### 7.1 Hierarchical objects

- `FarmProcessSpecV5`: digest-bound reviewed process.
- `PetriModuleSpec`: semantic phase with a fixed weight budget.
- Transition templates and deterministic expansion rules.
- Fact definitions and data guards.
- World branches chosen from authoritative fact versions.
- Information policies over the actor's pre-decision state.
- Choice groups for predeclared safe alternatives.
- Semantic causal-obligation groups.

Expansion makes repeated ridge batches and checkpoints visible while preserving
each module's total weight. Adding communication hops in a role refinement does
not increase a module's budget.

For 3-/4-agent paper conditions, a deterministic reviewed-refinement compiler
combines the frozen two-agent process with the confirmed team and
`RoleRefinementSpec`. It remaps transition ownership and policies, expands
communication dependencies, creates categorical acceptance records for new
explicit handoff transitions, and emits a distinct immutable process digest.
It cannot invent agronomic guards, thresholds, or capabilities; those remain
inherited from the reviewed base process.

### 7.2 World branches

A world branch is selected from timestamped authoritative FarmARE facts at its
declared commitment phase. The evaluator recomputes the unique branch. It
rejects missing, ambiguous, future-dependent, or runtime-only branch choices.

Alternative agent behavior cannot retroactively select an easier branch.

### 7.3 Information policies

For each high-impact decision, the policy is a finite table:

```text
fact-verdict vector × deadline state × channel state
    → permitted responses, required responses
```

Specification patterns may use `ANY`; reconstructed facts are always
`TRUE/FALSE/UNKNOWN`. Validation enumerates the finite state space and requires
the rules to be exhaustive and disjoint.

A `PolicyCommitmentRecord` is written before inspecting the proposed action.
Runtime commitments are audit records. The evaluator independently rebuilds
the fact vector, freshness, deadline, channel state, and selected rule.

## 8. D-CORE v5 metrics

The metric profile is primary; the scalar summary is secondary.

### 8.1 Event alignment and fidelity

Observed native FarmARE events are aligned to applicable transition instances
using deterministic maximum-weight matching. Embedded runtime transition IDs
and violation labels are ignored.

Owner and tool are hard compatibility gates. Experts freeze categorical
acceptance predicates for:

- Required presence.
- Arguments and numeric intervals/tolerances.
- Ridge or entity scope.
- Timing window.
- Successful execution.

A required transition is fully conforming only if every critical predicate
passes. There is no unreviewed soft numeric-decay function.

For applicable module (m), let (W_m) be its fixed expert budget and
(EF_m\in[0,1]) its transition-level fidelity after explicit negative
obligations. Overall event fidelity is:

\[
EF=\frac{\sum_m W_m EF_m}{\sum_m W_m}.
\]

Presence, conditional argument acceptance, scope acceptance, timing acceptance,
fully conforming rate, and negative-action classifications are also reported
separately.

Unmatched actions are classified from frozen predicates as `prohibited`,
`unnecessary`, `benign`, or `unresolved`. A high-impact unmatched call is not
automatically labeled harmful.

### 8.2 Causal conformance

A semantic causal obligation expresses one end-to-end requirement, such as:

```text
current disease evidence reaches the treatment decision
```

It may contain observation, forwarding, delivery, uptake, freshness, scope,
resource, and happens-before checks. The obligation passes only if one
predeclared realization satisfies all its constituent edges and guards.

This grouping prevents a three-hop communication refinement from receiving a
different denominator than a one-hop realization. Direct-edge diagnostics
remain visible. Missing target actions are charged by event fidelity. Modules
with no applicable causal obligation report `NA` and are excluded from the CC
denominator.

For modules with applicable obligations:

\[
CC=\frac{\sum_m W_m CC_m}{\sum_m W_m}.
\]

Incomparable concurrent events are not compared or penalized.

### 8.3 Secondary scalar

\[
DCORE=0.5EF+0.5CC.
\]

Sensitivity is exported for EF weights 0.25, 0.5, and 0.75. Weights and
acceptance tolerances are frozen before final model results and are never
chosen using yield correlation.

### 8.4 Information–global discordance

For each applicable high-impact decision (d):

- (L(d)=1) if the response conforms to the policy derived from the actor's
  actual (K_i(t)).
- (G(d)=1) if the response/action conforms to authoritative truth and the
  occurrence net.

The evaluator reports the weighted 2×2 table and:

\[
IGD=\frac{\sum_d w_d\mathbf{1}[L(d)=1\land G(d)=0]}
{\sum_d w_d}.
\]

IGD directly measures locally justified but globally incorrect behavior.
Per-agent policy conformance is reported separately. Actors with no applicable
decisions are `NA`.

### 8.5 Synchronization and long-horizon diagnostics

For fact version (v), first available in the world at (t_w(v)) and learned
by actor (i) at (t_i(v)):

\[
SSL(v,i)=t_i(v)-t_w(v).
\]

The report includes median, p95, maximum, deadline-normalized lag, freshness
slack, and never-received versions. It also records first critical divergence,
CORE-style prefix criticality, structural downstream reach, remaining-horizon
exposure, recovery latency, reobservation, and successful replanning.

Structural reach is not described as causal propagation. Outcome causality is
estimated only with paired interventions.

### 8.6 Provenance failure localization

Exact identifiers are traversed through:

```text
authoritative fact → observation → claim → send → delivery → forwarding
→ decision snapshot → action
```

The earliest recorded broken link receives a primary localization:

- `observation_gap`
- `handoff_omission`
- `transit_gap`
- `uptake_error`
- `reasoning_error`
- `unsupported_claim`
- `stale_information`
- `composition_error`

This is provenance-based failure localization, not Halpern–Pearl actual-cause
identification.

## 9. Farm outcomes and baselines

FarmARE remains authoritative for:

- Task validation and phase completion.
- Biological and marketable yield.
- Per-ridge yield.
- Resource use and wasted inputs.
- Harmful or unnecessary physical writes under reviewed definitions.
- Safety violations.
- Harvest, drying, and storage completion.

Paired oracle-relative yield shortfall is:

\[
YieldShortfall=\frac{Y_{oracle}-Y_{run}}{Y_{oracle}}.
\]

Negative values are retained if a run exceeds the oracle.

Baselines include final success, yield shortfall, native BFCL/tool success,
CORE path correctness, merged PC-KTC/KTC, average local KTC where available,
partial-order pair agreement, and pinned PM4Py token replay/alignment on a
sequential projection. Missing historical local-knowledge information is `NA`,
not reconstructed after the fact.

## 10. Farm environments

### 10.1 Wet-June disease recheck

Development environment for repeated observation, evidence freshness,
supersession, spray-window and trafficability changes, recurrence, treatment,
and recovery.

Its ten process modules are:

1. Field preparation.
2. Planting readiness and execution.
3. Establishment monitoring.
4. R1 monitoring.
5. Initial midseason disease diagnosis.
6. Initial treatment and response monitoring.
7. R5 recurrence/recheck and treatment.
8. Harvest readiness.
9. Harvest and unloading.
10. Drying and storage.

### 10.2 Disease–drought recovery

Transfer environment for a disease intervention followed by R5/R6 drought,
persistent local knowledge, changing diagnoses, resource allocation, recovery,
and cross-phase consequences.

### 10.3 Three-cultivar disease–water–harvest

Transfer environment with three spatial zones, differing cultivars, disease,
irrigation, maturity, and harvest timing. It tests scope, valid concurrency,
resource synchronization, and multi-stage harvest ordering.

All three use native FarmARE L3 tools, event physics, validation, and yield
accounting from planting through storage.

## 11. Controlled validation suite

Twenty no-model fixtures cover:

- Perfect replay.
- Valid concurrent permutation.
- Communication-hop refinement.
- Duplicate idempotency.
- Benign read.
- Correct defer/recovery.
- Within-agent reorder.
- Missing prerequisite.
- Observation gap.
- Handoff omission.
- Dropped and expired delivery.
- Stale old/new inversion.
- Unsupported claim.
- Uptake error.
- Wrong ridge and wrong amount.
- Missing beneficial action.
- Harmful write.
- Unnecessary abstention.

Each fixture freezes its expected EF, CC, IGD, invariance, and localization
behavior. Runtime verdicts and reference transition IDs do not determine the
evaluation.

The engineering suite currently passes. The harmful-write paper property stays
explicitly pending until experts freeze the negative obligation; paper
validation cannot silently accept the engineering default.

## 12. Experimental design

### 12.1 Primary block

- Two agents.
- Frozen ReAct controller family and one pinned backbone.
- Ten world seeds.
- Two independent controller repeats.
- Team-wide cap of 700 model calls and 24 million total tokens per season.
- Budget exhaustion, controller crash, and timeout are intention-to-treat
  failures.

Wet-June crosses free text, causal audit, and causal enforcement with eight
fault schedules. The transfer scenarios use reliable and frozen mixed-fault
conditions only. Shared blackboard, fresh native direct-tool, and fresh native
synchronous-A2A conditions are included.

Pass 1 has 450 LLM seasons plus 30 deterministic oracles. Pass 2 has 450 LLM
seasons.

### 12.2 Controller robustness

Nine controller-family counterparts are evaluated across three seasons, five
worlds, and two reliable information conditions. The backbone is held fixed so
controller design is not confounded with model choice: (9×3×5×2=270) runs.

### 12.3 Scalability

Wet-June uses 2-, 3-, and 4-agent reviewed decompositions, five worlds, and
three conditions:

- Reliable with matched total-team compute.
- Frozen mixed fault with matched total-team compute.
- Reliable matched-per-agent compute, explicitly labeled as increased total
  compute.

This gives (3×5×3=45) runs. Results include calls, tokens, messages, latency,
path length, coordination density, and workload dispersion so additional
inference is not mistaken for better decomposition.

### 12.4 Optional model continuity

Qwen and DeepSeek profiles may be evaluated under two reliable information
conditions across three seasons and five worlds: 60 optional runs. These are
not required for the primary paper claim.

## 13. Statistical protocol

One season is one observational unit. Conditions are paired by scenario,
world seed, controller/model profile, and repeat.

Primary reporting includes mean, median, IQM, and 95% world-cluster bootstrap
intervals. Binary completion and safety outcomes are also clustered by world.
Holm correction is applied within preregistered contrast families.

Primary analyses are intention-to-treat. Infrastructure failures are separated
and receive a sensitivity analysis. Metric-versus-yield calibration uses
held-out or leave-one-world-cluster-out predictions and is described as
predictive validity, not a causal relationship.

The frozen contrasts are:

- Free text versus causal audit under reliable delivery.
- Causal enforcement versus causal audit under the same fault.
- Local causal audit versus shared blackboard.
- Each active fault versus reliable transport within representation.
- Team-size comparisons only in the bounded scalability block.

## 14. Paper outputs

The report generator produces six tables:

1. Controlled metric validation.
2. Local/global decision table and IGD.
3. Fault and downstream outcome effects.
4. Earliest-link localization accuracy.
5. Guard mitigation effects.
6. Team-size scalability.

It also produces five figures:

1. Module/prefix D-CORE across the season.
2. Paired yield-shortfall distributions.
3. Fact-version synchronization lag and freshness.
4. Localization confusion matrix.
5. Leave-one-world-cluster-out metric/yield calibration.

Each output is accompanied by an analysis manifest containing source-row
digests, metric/specification versions, failures, exclusions, plotting
parameters, and artifact hashes.

## 15. Scientific review and release boundary

Agronomy experts freeze facts, thresholds, safe alternatives, timing windows,
action tolerances, and negative-action definitions. MAS authors freeze metric
construction, normalization, statistical estimands, and the conventional
scalar weighting.

For each scenario:

1. Two experts independently complete neutral specifications.
2. The tool validates each submission separately.
3. A comparison report lists every categorical and numerical disagreement.
4. Every disagreement is adjudicated.
5. An independent third expert confirms the resolved digest.
6. Reviewers attest that final model results and yield-correlation tuning were
   not inspected.

Paper execution additionally requires reviewed team/refinement files,
completed scientific gate manifests, a bounded real-LLM smoke, a clean commit,
and a release tag. `doctor`, paper aggregation, and `handoff build` enforce
these requirements.

## 16. Claims we do not make

- Vector clocks do not prove physical or normative causality.
- Petri-net notation is not itself the novelty.
- A provenance path is not an actual-cause proof.
- Guard enforcement does not improve the underlying model's reasoning.
- Correlation with yield does not show that D-CORE causes yield.
- More agents are not assumed to be better.
- In-process deterministic distribution is not a real-network deployment.
- Engineering defaults are not paper-grade agronomic judgments.

The detailed source contract is available in
[`are/simulation/distributed/SCIENTIFIC_CONTRACT_V5.md`](../are/simulation/distributed/SCIENTIFIC_CONTRACT_V5.md),
and the preregistered design is in
[`are/simulation/distributed/EXPERIMENT_PROTOCOL.md`](../are/simulation/distributed/EXPERIMENT_PROTOCOL.md).
