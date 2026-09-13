# Paper foundation and independent diagnostic validation

This is a prospective study plan, not a results section. It adds a bounded
validation layer to the existing experiment protocol. No new model matrix,
scenario, controller, or primary metric is introduced. The implementation lives
in `diagnostic_validation.py`, `validation_study.py`, and `validation_cli.py`;
the command entry point is `are-dcore validation`.

## The claim to develop

**D-CORE evaluates whether distributed actions conform to a reviewed workflow,
distinguishes local information from global validity, and localizes failures
that are identifiable from recorded information paths.** The empirical question
is whether these diagnostics agree with independent reviewers and explain
failures beyond simpler checks on the same evidence.

“Available information” means recorded decision-time evidence, not an agent's
internal beliefs. Receipt does not prove cognitive uptake. Happens-before does
not establish physical causation. A physical benefit requires the existing
paired intervention study, including unsuccessful interventions and false blocks.

The main narrative can group the existing questions into diagnostic validity,
component value/robustness, and mitigation outcomes. Preserve the registered
experimental blocks and their counts; narrative grouping does not authorize
dropping conditions after observing results.

## One motivating example

Consider two hypothetical executions with the same proposed spray operation at
time 4 and the same native action outcome. At time 0, the operator has a forecast
permitting spraying through time 5. Actual weather closes the window at time 1;
the sensing agent observes the closure at time 2. Hold all other relevant inputs
fixed. Times illustrate ordering, not calibrated agronomic thresholds.

| Evidence at the decision | Execution A | Execution B |
| --- | --- | --- |
| Closure warning | Delivered before time 4 | Not delivered before time 4 |
| Operator's recorded information | Current closure warning | Still-valid earlier forecast |
| Same proposed spray operation | Contradicts available warning | Can be locally supported under the forecast policy |
| Actual weather | Window closed | Window closed |
| Diagnostic question | Why did the action contradict available evidence? | Where did the information path break? |

This requires a reviewed policy specifying how a current warning overrides a
forecast. A missing warning alone cannot distinguish omission from transit loss:
the send/receive evidence must establish that distinction. Identical action logs
and final yield cannot resolve it. The flat evidence baseline can also distinguish
the two local information states; the example motivates the representation and
does not prove that D-CORE outperforms that baseline. No yields or effect sizes
are assumed.

## Three bounded formal observations

1. **Information loss under projection.** Let `P(T)` retain only the native action
   sequence and outcome. If `P(T_A) = P(T_B)` but their reviewed information-path
   labels differ, any deterministic diagnostic `f(P(T))` returns the same answer
   for both and cannot correctly recover both labels. A randomized diagnostic
   given only the projection has the same output distribution for both. This
   is a direct indistinguishability argument, not a new general theorem.
2. **Nonanticipation of the local policy.** Fix the specification, pre-decision
   snapshot, eligibility times, scope, deadline, and channel state. The fact
   verdict vector is fixed, and the exhaustive, disjoint policy table selects
   one rule. A later proposal or future observation cannot change that local
   permitted-response set. Global action conformance can still differ because
   it separately checks the actual proposed/executed operation.
3. **Conditional serialization invariance.** Fix event-to-transition matching,
   applicability, predicates, world times, weights, and the causal graph. A
   topological reserialization of independent events preserves event fidelity
   and the causal-conformance edge/guard tests: neither their truth values nor
   their denominators change. This does not assert invariance when ambiguous
   matching changes, when simulation actions are reordered, or when the causal
   graph changes. Existing metamorphic regressions cover the bounded property.

These belong in a short method passage or appendix. Do not expand them into an
unrelated theory contribution.

## Positioning against existing work

[MultiAgentBench](https://aclanthology.org/2025.acl-long.421/) evaluates collaboration
and competition using task completion and milestone indicators.
[MAST](https://arxiv.org/abs/2503.13657) develops a multi-agent failure taxonomy
from annotated execution traces. Neither milestone evaluation nor human failure
annotation is new here. Partial orders and conformance checking are established
foundations, cited in the [scientific contract](../are/simulation/distributed/SCIENTIFIC_CONTRACT_V5.md).

The proposed contribution is their operational combination with reviewed local
information policies, versioned evidence, explicit communication paths, and
paired delayed farm outcomes. Novelty and added value must be demonstrated;
avoid “first” or “missing intersection” claims without a fuller related-work check.

## Claims, evidence, and results that would weaken the case

| Proposed claim | Required evidence | Result that requires a weaker claim |
| --- | --- | --- |
| Diagnostics are valid on natural agent behavior | Two blinded annotations, pre-adjudication agreement, distinct adjudication, coverage and per-label errors | Low agreement, frequent unidentifiable cases, or poor agreement with adjudicated labels |
| The structured diagnostic adds value | Same-evidence flat checker and existing legacy baselines, paired comparisons on identical episodes | Flat checker ties/wins; present narrower localization benefits only if independently supported |
| Selected components matter | Full versus no-expiry, no-provenance, and serialization-order analyses on saved traces | No meaningful differences, or removing a component improves correctness |
| Conclusions survive reasonable specification choices | Every expert-prespecified alternative; report changes and ranking/sign reversals | Conclusions depend on one deadline, tolerance, or obligation definition |
| Enforcement improves outcomes | Existing paired audit/enforcement contrasts, intention-to-treat accounting, false blocks, cost and recovery | No benefit, excessive false blocks/cost, or adverse yield effects |

Diagnostic correctness is the primary argument. Yield is separate downstream
evidence: a real coordination error may have no yield cost, and a successful
recovery does not erase the original error. Report null and negative results.

## Independent annotation protocol

Before inspecting final model outcomes, freeze the experiment manifest digest,
reviewed base process digests, all sensitivity alternatives, sampling seed, and
sample budget. The default is **60 decision episodes, at most two per run**.
This is a bounded annotation budget, not a power calculation or a promise of
precision for rare labels. Revise it prospectively if independent annotation
capacity warrants, with a new documented plan version.

Archive this rubric and the prediction/scoring code with the frozen release.
The JSON plan binds the inputs and sampling choices; its timestamp alone does
not establish when the research decisions were made. Keep dated review and
plan-amendment records alongside the release rather than relying on a locally
generated hash as proof of preregistration.

The sampler inventories the supplied results tree and selects policy-covered
proposed-action decisions from real LLM runs. It balances scenario, communication
condition, and native execution status (accepted/error/not executed), with seeded
selection within strata. It never selects on D-CORE labels or aggregate yield.
Retain sample shortfalls and excluded-controller inventory; do not substitute
more interesting episodes. Reconcile the supplied tree with the full manifest,
including failed/incomplete runs: a directory hash cannot prove that the
operator supplied the complete cohort. This balanced sample does not estimate
population failure prevalence.

The public packet contains pseudonymous actors and evidence IDs, relevant
pre-decision facts, preceding observation/message/action evidence, proposed
action, reviewed prerequisites and action acceptance constraints. Global facts
are marked separately from facts available to the deciding actor. Explicit
model identities, treatment assignments, evaluator/guard verdicts, private
source paths, and final aggregate outcomes are withheld. Native results and
message text remain evidence: they may reveal treatment format or identity
indirectly, and a native harvest receipt may contain a local yield. Describe
this as blinding to explicit assignments and evaluator outputs, not guaranteed
anonymity or complete outcome blinding.

Two reviewers annotate independently. A third reviewer adjudicates after their
original submissions are locked; report agreement before adjudication. The
domain experts who define the process answer a different question from the
reviewers who label executions. Document reviewer expertise, conflicts,
training examples, and independence. Distinct IDs and timestamps are integrity
checks, not proof of independent people or timely preregistration.

Each assessment concerns the **proposed-action episode**, including a prevented
attempt, not a claim that a blocked action physically executed. Use `failure`,
`no_failure`, or `insufficient_evidence`, plus all supported failure labels,
prerequisite readiness, evidence references, and rationale. Evidence that a
different operation or an earlier episode failed does not label the target.
An absent action receipt alone does not identify the reason for nonexecution.

| Label | Evidence needed for the operational judgment |
| --- | --- |
| `observation_gap` | A required observation is missing from the recorded decision evidence |
| `handoff_omission` | An observed, required handoff was not sent in the relevant recorded prefix |
| `transit_gap` | A relevant send exists, but timely delivery to the deciding actor is absent |
| `stale_information` | The relied-on recorded evidence is expired or superseded under the reviewed rule |
| `unsupported_claim` | A claim contradicts or lacks the required recorded support; unlinked free text alone is insufficient |
| `uptake_error` | A relevant delivered item is absent from the recorded decision snapshot; this is not a claim about cognition |
| `composition_error` | Recorded constituent evidence does not support the claimed combined scope/value |
| `reasoning_error` | The proposed/action arguments contradict available prerequisites or reviewed acceptance constraints |
| `execution_error` | A native receipt explicitly records an execution error |
| `scope_error` | The proposed/executed scope violates the reviewed scope constraint |
| `unlocalized_failure` | A violation is supported, but the available packet cannot identify its source |

If missing workflow context prevents a judgment, retain `insufficient_evidence`;
the packet is a decision-focused evidence workbook, not a complete expert replay
of the entire occurrence net. Record this coverage limit. Absence claims require
an explicit rationale about the inspected prefix. Labels are operational,
potentially overlapping observations, never direct readings of internal thought.

For received free-text messages, reviewers may map an exact quote to an observed
source fact using `message_id`, `quote`, and `source_fact_id`. The tool checks
delivery before the decision, exact text, and source availability to the sender.
It cannot certify that the quote semantically entails the fact; that is the
independent reviewer judgment. Preserve disagreements and unsupported mappings.

## Fair comparisons and bounded ablations

The flat baseline independently checks the latest scoped snapshot evidence,
age/validity, prerequisite predicates, and native execution status. It receives
the same trace and reviewed process as D-CORE. It does not use Petri alignment,
causal-path reconstruction, or D-CORE verdicts, and does not produce fine-grained
failure localization. Its simpler local-readiness decision rule is explicit;
it is not claimed to implement the full normative workflow. Keep the existing
CORE/KTC/BFCL comparisons alongside it.

Report determinate coverage, accuracy conditional on making a diagnosis,
unconditional correct fraction counting abstentions as unresolved, and the
confusion matrix. For methods that localize, include per-label precision/recall
and their counts. Human-uncertain episodes remain visible and are excluded from
determinate accuracy. An unverifiable native free-text handoff is uncertainty,
not automatically a coordination failure. Human quote mappings support an
independent mapping-coverage audit; they never silently enter native scores.

The paired accuracy difference and its interval use the same episodes for both
methods. Bootstrap scenario/world clusters, retaining conditions and repeated
decisions within a cluster; the default is 2,000 resamples. Report cluster counts.
No interval is produced with fewer than two clusters; even two clusters are too
few for a persuasive inferential claim. Do not interpret a selected-sample
interval as population prevalence or evidence of a physical causal effect.

| Saved-trace variant | Exactly what changes |
| --- | --- |
| `full` | Original v5 evaluator |
| `no_expiry` | Remove maximum-age and validity limits; preserve observation order and supersession |
| `no_provenance` | Remove support, required actor-route, and current-root checks; retain predicates and temporal edges |
| `serialization_order` | Replace only causal-conformance happens-before edge tests with recorded list order; preserve matching and guard tests |

The variants report event fidelity and causal conformance. They measure score
dependence on components; changes alone do not prove improved diagnostic
accuracy. Reduced variants do not certify full fine-grained localization.
Inputs are copied, and all outputs are analysis-only. None authorizes a native
paper run or redefines the primary metric.

For specification sensitivity, independent experts must select defensible
alternative deadlines/tolerances/obligations before final model results, with
written rationale and reviewed files. Do not invent numerical ranges or tune
them for favorable rankings. The plan binds complete variant families per
process; evaluation refuses an omitted or extra family member. Report all
variants and their process digests, including unchanged scores and reversals.

## Operator handoff

These commands are templates for **after** independent process review and,
where required, after the real experiment traces exist. They do not launch
seasons or call models. Paths below stand for the actual frozen inputs. Repeat
`--process` and `--alternative` for all planned process families. Omit alternatives
only if the prospectively documented study has none; that would leave the
specification-robustness claim unsupported.

```bash
are-dcore validation plan --manifest frozen-matrix.yaml \
  --process frozen-process.json --alternative reviewed-alternative.json \
  --episodes 60 --max-per-run 2 --seed 2027 --output study-plan.json

are-dcore validation sample --plan study-plan.json --manifest frozen-matrix.yaml \
  --results complete-results --output annotation-packet

are-dcore validation predict --packet-dir annotation-packet \
  --output annotation-packet/private/predictions.json

are-dcore validation compare --packet annotation-packet/public/episodes.json \
  --left completed-a.json --right completed-b.json --output agreement.json

are-dcore validation score --packet-dir annotation-packet \
  --left completed-a.json --right completed-b.json --adjudicated adjudicated.json \
  --predictions annotation-packet/private/predictions.json --output accuracy.json

are-dcore validation evaluate --plan study-plan.json --process frozen-process.json \
  --trace complete-results/run/trace.dcore_trace_v5.json \
  --alternative reviewed-alternative.json --output saved-trace-variants.json
```

Give each annotator `public/episodes.json`, the rubric above, and their own blank
annotation template. Keep private keys and predictions with the study operator.
Do not share another annotator's completed file before independent submissions
are locked. Adjudicate every episode, including agreements, without access to
method predictions. Outputs refuse overwrite; archive immutable versions with
the release and document amendments. `--fixture-only` is exclusively for software
tests and cannot supply natural-agent paper evidence.

Implementation tests establish tooling behavior. The remaining scientific work
is independent domain review, scenario calibration, real-model smoke, release
freeze, planned experiments, independent annotation, and analysis. Acceptance
depends on the resulting evidence and the paper; a codebase cannot guarantee it.
