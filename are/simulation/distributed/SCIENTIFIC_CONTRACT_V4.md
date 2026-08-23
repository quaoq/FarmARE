# Wet-June Farm D-CORE v4 scientific contract

This document states what the implementation measures. It is not a claim that
the draft agronomic values are expert-approved. Paper runs require a confirmed
specification digest.

## Four distinct objects

1. **World truth** is a timestamped, authoritative FarmARE fact version. It is
   evaluator-visible and never automatically enters an agent prompt.
2. **Local information** is the exact append-only knowledge snapshot available
   to one actor before its decision. It contains only owned observations,
   delivered claims, and shared-blackboard records in that declared condition.
3. **Information policy** maps the three-valued requirements in that snapshot,
   agronomic deadline state, and channel state to predeclared permissible
   responses. Its commitment event happens-before the decision and cannot read
   the proposed action or a later outcome.
4. **Occurrence-net correctness** evaluates the executed workflow against the
   world-committed action-level Petri net. World branches are selected only from
   authoritative fact-version IDs recorded before the relevant action.

Guard verdicts do not define correctness. Audit and enforcement can prevent a
physical write, but an unsafe proposal still counts as an information-policy
violation. Guard effectiveness and agent correctness are reported separately.

## Frozen metric

Each of the ten modules has an expert budget. Expansion divides the budget
among applicable scored instances, so adding daily waits or changing harvest
batch granularity cannot increase a module's influence. Benign loops and
optional safe actions do not enter the required-event denominator.

For applicable module `m`, event fidelity separately accounts for required
progress, evidence, arguments, scope, timing, execution, omissions, and harmful
extras. Causal conformance scores only direct token, message, provenance,
resource, freshness, scope, and conflict constraints charged to their target
module. Incomparable transitions are not scored as ordered.

The primary result is the module profile. The secondary scalar is:

`DCORE = 0.5 * EF + 0.5 * CC`

Sensitivity at event weights 0.25, 0.5, and 0.75 is exported without selecting
a weight from model outcomes. Legacy CORE PC, PC-KTC/KTC, BFCL, Petri fitness,
partial-order agreement, final success, and oracle-relative yield shortfall are
reported as baselines.

## Non-circularity and provenance checks

- Matching uses actor, native tool, arguments, scope, time, and completion; it
  does not consume runtime transition IDs or violation flags.
- A world commitment records the branch/specification digests and the exact
  authoritative fact versions used. A policy commitment records its
  specification digest and exact pre-decision knowledge digest.
- Every newer overlapping fact version explicitly lists the versions it
  supersedes. Delivered claims retain their origin-version link.
- Saved traces are validated for causal parents, vector clocks, message
  send/receive dominance, snapshot visibility, future evidence, supersession,
  and commitment/action order before evaluation.

## Review and eligibility

The neutral packet contains no proposed weights, thresholds, tolerances,
guards, or policy answers. Two distinct reviewers submit complete digest-
attested specifications. Leaf-level disagreements require explicit
adjudication. A distinct third expert confirms the resolved digest. Every
reviewer attests that final model results and yield correlations were not seen.

Only the resulting immutable net may set `paper_eligible=true`. Real-LLM runs
additionally require a matching scientific-gate manifest proving the offline
semantic, prompt-leakage, replay, blocked-write, and exactly-once tests passed.

## Team-size refinement contract

Agent count is an experimental treatment, never an oracle generator. Each
decomposition has a versioned `AgentTeamSpec`; every non-primary decomposition
also has an independently reviewed `RoleRefinementSpec`. The refinement may
redistribute ownership and add send/receive synchronization, but it cannot add
farm observations, tools, world information, agronomic tolerances, or module
weight. Every fact required by an action must retain a topology-valid,
provenance-preserving observation-to-action path.

Local scores are reported for every actor with applicable obligations. Actors
with none are `NA`; LGG uses only the reported `n_local_scored` actors.
Multi-hop attribution walks the declared path and assigns the earliest broken
observation, send, delivery, uptake, reasoning, or composition link. Team-size
comparisons report calls, tokens, messages, latency, path length, and
coordination density under a matched total-team budget; matched-per-agent
budgets are a separately labeled increased-compute analysis.
