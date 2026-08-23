# Farm D-CORE: End-to-End Overview

This is the first of three documents. It explains, in plain language, what we
built and why. Continue with
[`PROFESSOR_02_TECHNICAL_SPECIFICATION.md`](PROFESSOR_02_TECHNICAL_SPECIFICATION.md)
for the formal details and
[`PROFESSOR_03_EXECUTION_GUIDE.md`](PROFESSOR_03_EXECUTION_GUIDE.md) for the
commands.

## 1. The research goal

Farm D-CORE evaluates distributed, multi-agent farm systems over a complete
growing season.

The central problem is that a distributed agent can make a reasonable decision
from its own limited information while the team still produces an incorrect
global workflow. For example, a field agent may discover that a spray window
has closed, but the operations agent may act using an older message. Looking
only at the final tool sequence, final success, or a single merged ordering can
miss why the failure occurred.

The proposed paper contribution is therefore an evaluation framework that
connects:

1. What was true in the FarmARE world.
2. What each agent had actually observed or received.
3. How information moved between agents.
4. Whether each local decision was justified by that agent's information.
5. Whether the joint workflow was globally correct.
6. How a coordination error affected later operations and final yield.

The primary contribution is evaluation. Structured causal handoffs and guards
are a secondary mitigation experiment.

## 2. What a run looks like

One run is one full FarmARE season, from preparation and planting through
monitoring, treatment, harvest, drying, and storage.

During the season:

1. Each agent has its own history, inbox, tools, and knowledge store.
2. The scheduler activates agents deterministically.
3. An agent may observe, execute an owned tool, send a message, wait, abstain,
   or finish.
4. FarmARE executes the agent's exact tool and arguments. The expert oracle
   does not choose the agent's next action.
5. Messages may be delivered normally or subjected to a controlled fault.
6. Every observation, fact version, message, decision, action, and world effect
   is recorded in a causal sidecar trace.
7. D-CORE evaluates the saved trace against an independently reviewed farm
   workflow specification.
8. FarmARE remains authoritative for physical state, biological yield, and
   marketable yield.

## 3. The agents

The primary paper condition uses two persistent agents:

- **Field Intelligence:** owns weather, soil, canopy, drone, robot, disease,
  and maturity observations. It prepares evidence and handoffs but cannot
  perform high-impact farm writes.
- **Operations:** owns inventory and machinery checks, planting, treatment,
  irrigation, harvest, drying, storage, and farm-time advancement. It cannot
  directly use the Intelligence agent's private diagnostic sensors.

The implementation is parameterized for larger fixed teams. Two bounded
scalability configurations are included:

- Three agents: Scouting → Agronomy → Operations.
- Four agents: Scouting → Agronomy plus Resource Management → Field
  Operations.

Adding agents does not add farm information or capabilities. It redistributes
the same tools and evidence paths. Every decomposition requires its own
reviewed ownership and role-refinement specification.

## 4. Agent isolation

Agents do not receive a serialized global state.

An agent can know a fact only through:

- An observation tool it owns.
- Its own tool result.
- A delivered message.
- A shared blackboard update in the explicit upper-bound condition.

Undelivered facts do not appear in another agent's prompt or history. Tool
ownership is enforced both when prompts are constructed and again when a tool
is executed. Unauthorized or malformed calls cannot mutate FarmARE.

## 5. Deterministic distributed execution

Distribution is simulated in process using logical time. This provides
controlled asynchronous behavior without relying on nondeterministic threads or
real networks.

The runtime supports:

- Round-robin, seeded-permutation, and event-driven activation.
- Explicit, fully connected, hierarchical, and shared-blackboard topologies.
- Reliable delivery, delays, drops, duplicates, and old/new reordering.
- Stable message and fact-version identifiers.
- Exactly-once physical writes and idempotent duplicate delivery.
- Separate world, scheduling, model-sampling, and fault seeds.
- Resume and deterministic sharding for large experiment matrices.

Communication faults never directly change weather or farm dynamics. Paired
conditions must have the same exogenous-world digest.

## 6. Knowledge and causal tracing

Repeated observations create new fact versions; they do not overwrite history.
A fact version records its value, scope, observation time, learning time,
validity, evidence, source, supersession, and vector clock.

The trace links:

```text
world fact → observation → claim → send → delivery → decision context
→ physical action → world effect
```

Vector clocks represent execution order and concurrency. They do not by
themselves prove physical or normative causality.

## 7. Communication and mitigation conditions

The system supports two message forms:

- **Free text:** the baseline. It is useful for natural communication but does
  not provide machine-verifiable fact provenance.
- **Causal handoff:** carries scoped fact claims, evidence identifiers,
  observation times, validity, unresolved requirements, and causal parents.

The causal guard has three modes:

- `off`: no guard affects execution.
- `audit`: compute and record the verdict, but execute the proposed action.
- `enforce`: allow, defer, or block according to the reviewed requirements.

The guard never invents evidence or substitutes the oracle action. After a
defer or block, the agent must reobserve, communicate, replan, or abstain.

## 8. The executable reference workflow

The reference is not a single serialized list of oracle actions. It is a
hierarchical, data-aware farm workflow compiled deterministically into a
labeled, 1-safe occurrence net.

The workflow represents:

- Required and optional farm transitions.
- Genuine concurrency between independent operations.
- Evidence, freshness, scope, resource, and timing requirements.
- Safe alternatives such as execute, reobserve, handoff, defer, or abstain.
- World branches selected only by authoritative FarmARE facts.

For Wet-June the hierarchy covers ten modules: preparation, planting,
establishment, R1 monitoring, initial disease diagnosis, initial treatment,
R5 recheck/treatment, harvest readiness, harvest/unloading, and
drying/storage.

The code can export JSON, DOT, PNML, CSV worksheets, and neutral expert-review
packets.

## 9. What D-CORE reports

D-CORE reports a profile rather than relying on one number:

- Event presence and full action fidelity.
- Argument, ridge scope, timing, and execution acceptance.
- Conformance to semantic causal obligations.
- Local information-policy conformance for every actor.
- Information–global discordance: locally justified but globally incorrect
  decisions.
- Synchronization lag and never-received fact versions.
- First critical divergence, downstream exposure, and recovery.
- Provenance-based localization of the earliest recorded broken link.
- Harmful, unnecessary, benign, and unresolved extra actions.
- FarmARE completion, safety, resource use, and yield shortfall.

A secondary scalar is retained for compact reporting:

```text
DCORE = 0.5 × Event Fidelity + 0.5 × Causal Conformance
```

The module and obligation profiles are the primary scientific result. The
scalar is not tuned against yield.

## 10. Farm scenarios

The public benchmark contains three full L3 seasons:

1. **Wet-June disease recheck:** freshness, recurrence, reinspection, and
   superseding evidence.
2. **Disease then drought:** changing diagnoses, cross-phase recovery, and
   long-horizon error consequences.
3. **Three-cultivar sequence:** spatial scope, irrigation, maturity,
   concurrent ridge work, and harvest ordering.

Wet-June is the development scenario. The other two are transfer scenarios;
the metric formulas must be frozen before inspecting their model results.

## 11. Experiments prepared for the professor

The required suite contains:

- 20 no-LLM controlled validation fixtures.
- 480 primary pass-1 seasons, including 30 scripted oracles.
- 450 primary pass-2 seasons.
- 270 controller-robustness seasons.
- 45 team-scalability seasons.

This is 1,215 LLM seasons plus 30 scripted oracle seasons. An optional 60-run
Qwen/DeepSeek continuity block is provided separately.

The primary experiment includes fresh, paired FarmARE direct-tool and
synchronous-A2A baselines. Historical paper traces are not treated as paired
evidence.

## 12. Outputs

Every run records the resolved configuration, FarmARE trace, D-CORE trace,
process and team digests, occurrence net, evaluation profile, failure
localization, farm outcome, per-ridge yield, telemetry, and a tidy row.

The analysis pipeline produces:

- Six preregistered CSV/LaTeX tables.
- Five preregistered PNG/PDF figures.
- Clustered bootstrap and paired-comparison inputs.
- A digest-bound analysis manifest listing source rows and failures.

Saved traces can be reevaluated without another model call.

## 13. Current readiness

The software mechanism is operational:

- The full distributed regression completed with 127 passing tests.
- A three-condition full-season CLI smoke completed.
- Matrix counts, sharding, aggregation, and report rendering are validated.
- `are-dcore doctor` currently reports `healthy: true`.

The repository intentionally reports `paper_ready: false`. Final experiments
must not begin until the professor's team supplies:

1. Two independent domain specifications for each paper scenario.
2. Complete adjudication and third-expert confirmation.
3. Frozen agronomic thresholds, tolerances, branches, and negative-action
   definitions.
4. Confirmed team and larger-team role-refinement specifications.
5. Completed scientific gate manifests and the bounded real-model smoke.
6. A clean, tagged release commit.

This is a scientific safeguard, not a missing implementation. The code is
ready to send for review and preflight; it will refuse final paper execution
until those requirements are satisfied.
