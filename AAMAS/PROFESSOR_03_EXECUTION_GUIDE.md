# Farm D-CORE: Professor Execution Guide

This is the third of three documents. It describes how to review, validate,
package, run, resume, reevaluate, aggregate, and report the Farm D-CORE
experiments.

Read
[`PROFESSOR_01_END_TO_END_OVERVIEW.md`](PROFESSOR_01_END_TO_END_OVERVIEW.md)
and
[`PROFESSOR_02_TECHNICAL_SPECIFICATION.md`](PROFESSOR_02_TECHNICAL_SPECIFICATION.md)
before launching any experiment.

## 1. Important readiness distinction

There are three different states:

- `healthy: true`: dependencies and software mechanisms work.
- `paper_ready: false`: at least one scientific review, smoke, digest, or
  release gate is missing.
- `paper_ready: true`: the exact reviewed specifications and release evidence
  supplied to `doctor` satisfy every paper gate.

The repository currently reaches the first state, not the third. It is safe to
run tests, review export, scripted runs, mock runs, bounded engineering
preflight, and dry runs. Do not launch the paid paper matrices until `doctor`
reports `paper_ready: true` using the final artifacts.

The software intentionally refuses unresolved `REPLACE_WITH_*` paths and
invalid paper rows before a model call.

## 2. Repository location and reading order

Run commands from the FarmARE root:

```bash
cd /path/to/FarmARE
```

The relevant implementation is under:

```text
are/simulation/distributed/
are/simulation/scenarios/scenario_dcore/
are/simulation/tests/distributed/
```

The authoritative supporting documents are:

```text
PROFESSOR_01_END_TO_END_OVERVIEW.md
PROFESSOR_02_TECHNICAL_SPECIFICATION.md
PROFESSOR_03_EXECUTION_GUIDE.md
are/simulation/distributed/SCIENTIFIC_CONTRACT_V5.md
are/simulation/distributed/EXPERIMENT_PROTOCOL.md
```

## 3. Install and verify the environment

Use the repository lock file:

```bash
uv sync --extra dev
```

Do not update dependencies between experimental blocks. PM4Py is pinned to
`2.7.23.4` for the sequential conformance baseline.

Run the non-destructive doctor and distributed tests:

```bash
uv run are-dcore doctor --output-dir results/doctor-engineering
uv run pytest -q are/simulation/tests/distributed
uv run ruff check are/simulation/distributed are/simulation/tests/distributed
```

Expected before expert review:

```text
healthy: true
paper_ready: false
```

The last verified distributed run contained 127 passing tests. The release
commit must rerun the suite and store the new test output and digest.

## 4. Run the no-key engineering checks

### 4.1 Resolve the planned counts

These commands do not call a model:

```bash
uv run are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/dry-run/pass1 --dry-run

uv run are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass2.yaml \
  --output-dir results/dry-run/pass2 --dry-run

uv run are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_controller_robustness.yaml \
  --output-dir results/dry-run/controller --dry-run

uv run are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_scalability.yaml \
  --output-dir results/dry-run/scalability --dry-run
```

The exact counts must be:

```text
primary_pass_1:       480
primary_pass_2:       450
controller_robustness: 270
scalability:           45
```

Dry-run output also shows maximum call/token caps and an assumption-bound
maximum API cost. This is not an expected-spend forecast.

### 4.2 Execute the scripted full-season smoke

This is a three-run, no-model test of execution, faulting, aggregation, and
artifacts:

```bash
uv run are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_smoke.yaml \
  --output-dir results/scripted-smoke

uv run are-dcore aggregate results/scripted-smoke
uv run are-dcore report results/scripted-smoke \
  --output-dir results/scripted-smoke-report
```

Confirm that all three run directories contain `COMPLETED.json`, the aggregate
succeeds, and the report contains six tables and five figures.

### 4.3 Bounded engineering preflight

Before reviewed paths exist, only the explicitly bounded engineering form is
valid:

```bash
uv run are-dcore preflight \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/preflight-engineering \
  --limit-worlds 1
```

This deliberately uses built-in draft contracts and cannot satisfy scientific
review. Running without `--limit-worlds` must refuse unresolved review paths.

## 5. Complete the expert process reviews

Repeat this section independently for:

```text
farm_wetjune_recheck
farm_disease_drought
farm_three_cultivar
```

### 5.1 Export a neutral v5 packet

Example for Wet-June:

```bash
uv run are-dcore review export-v5 \
  --scenario-id farm_wetjune_recheck \
  --output-dir review/wetjune/process-neutral
```

The packet contains a neutral JSON submission template, module and fact CSVs,
the FarmARE tool list, DOT/PNML diagrams, instructions, and a packet digest. It
contains no proposed correct thresholds, weights, guards, or tolerances.

Give separate copies to two qualified domain experts. They must work
independently and must not inspect another submission, final model results, or
yield correlations.

### 5.2 Validate each independent submission

```bash
uv run are-dcore review validate-v5 review/wetjune/expert-a.json
uv run are-dcore review validate-v5 review/wetjune/expert-b.json
```

Do not compare submissions until both pass separately.

### 5.3 Compare and adjudicate every disagreement

```bash
uv run are-dcore review compare-v5 \
  review/wetjune/expert-a.json \
  review/wetjune/expert-b.json \
  --output review/wetjune/comparison.json
```

Create `resolutions.json` by resolving every path listed as unresolved in the
comparison report. Then run:

```bash
uv run are-dcore review adjudicate-v5 \
  review/wetjune/expert-a.json \
  review/wetjune/expert-b.json \
  review/wetjune/resolutions.json \
  --adjudicator-id EXPERT_C_OR_PANEL_ID \
  --adjudicator-expertise-role "agronomy and farm operations" \
  --attest-no-final-results \
  --attest-no-yield-tuning \
  --output review/wetjune/adjudication.json
```

Original submissions remain immutable.

### 5.4 Obtain independent third-expert confirmation

The confirmation JSON must contain the third expert's ID, expertise role, UTC
timestamp, resolved process digest, and the two no-results/no-tuning
attestations. The third expert must differ from both reviewers and the
adjudicator.

```bash
uv run are-dcore review confirm-v5 \
  review/wetjune/adjudication.json \
  review/wetjune/third-expert-confirmation.json \
  --output review/wetjune/confirmed.json
```

### 5.5 Freeze the digest-bound process

```bash
uv run are-dcore review freeze-v5 \
  review/wetjune/adjudication.json \
  review/wetjune/confirmed.json \
  --output review/frozen/wetjune.process.v5.json
```

This also creates a scientific-gate template next to the frozen process. Repeat
the workflow for the two transfer scenarios.

Paper freezing rejects missing branches, incomplete fact definitions,
non-exhaustive policies, missing fault treatments, engineering defaults, and
unresolved acceptance predicates.

## 6. Review the team decompositions

The process review and team/role review are separate. Export the neutral
Wet-June team packet:

```bash
uv run are-dcore review export \
  --output-dir review/wetjune/team-neutral
```

Use `team_refinement_review.template.json` for two independent team reviews.
The reviewers assess tool ownership, observation ownership, time authority,
topology, preserved aggregate capability, communication paths, module budgets,
and intended concurrency for the 2-, 3-, and 4-agent decompositions.

The workflow uses the corresponding commands:

```bash
uv run are-dcore review validate REVIEWER_A_TEAM.json
uv run are-dcore review validate REVIEWER_B_TEAM.json

uv run are-dcore review compare REVIEWER_A_TEAM.json REVIEWER_B_TEAM.json \
  --output TEAM_COMPARISON.json

uv run are-dcore review adjudicate \
  REVIEWER_A_TEAM.json REVIEWER_B_TEAM.json RESOLVED_TEAM.json \
  --adjudicator ADJUDICATOR_ATTESTATION.json \
  --output TEAM_ADJUDICATION.json

uv run are-dcore review confirm \
  TEAM_ADJUDICATION.json THIRD_EXPERT_TEAM_CONFIRMATION.json \
  --output TEAM_CONFIRMED.json
```

First export and validate the confirmed Wet-June occurrence net:

```bash
uv run are-dcore validate-spec \
  --process-spec review/frozen/wetjune.process.v5.json \
  --output-dir review/frozen/validated
```

Then freeze the team bundle:

```bash
uv run are-dcore review freeze TEAM_CONFIRMED.json \
  --petri-net review/frozen/validated/farm_wetjune_recheck/petri_net.json \
  --output-dir review/frozen/teams
```

The output contains three confirmed team specifications and separate 3-/4-agent
role refinements. Derive the immutable 3- and 4-agent v5 processes from the
confirmed two-agent process and the reviewed refinements:

```bash
uv run are-dcore review refine-team-v5 \
  review/frozen/wetjune.process.v5.json \
  review/frozen/teams/wetjune_3agent.team.frozen.json \
  review/frozen/teams/wetjune_3agent.refinement.frozen.json \
  --output review/frozen/wetjune-3agent.process.v5.json

uv run are-dcore review refine-team-v5 \
  review/frozen/wetjune.process.v5.json \
  review/frozen/teams/wetjune_4agent.team.frozen.json \
  review/frozen/teams/wetjune_4agent.refinement.frozen.json \
  --output review/frozen/wetjune-4agent.process.v5.json
```

This deterministic transformation preserves agronomic definitions and module
budgets, expands reviewed communication paths, remaps ownership and policies,
and generates a new digest from the confirmed base-process, team, and
role-refinement digests. It is not an automatic scientific oracle. Larger-team
paper rows are rejected without these derived files and their matching gates.

## 7. Run offline scientific validation

### 7.1 Validate all frozen processes

```bash
uv run are-dcore validate-spec \
  --process-spec review/frozen/wetjune.process.v5.json \
  --process-spec review/frozen/disease-drought.process.v5.json \
  --process-spec review/frozen/three-cultivar.process.v5.json \
  --team-id wetjune_2agent \
  --team-spec-path review/frozen/teams/wetjune_2agent.team.frozen.json \
  --require-confirmed \
  --output-dir results/spec-validation
```

Validate the two derived processes separately because each has a different
actor roster:

```bash
uv run are-dcore validate-spec \
  --process-spec review/frozen/wetjune-3agent.process.v5.json \
  --team-id wetjune_3agent \
  --team-spec-path review/frozen/teams/wetjune_3agent.team.frozen.json \
  --role-refinement-path review/frozen/teams/wetjune_3agent.refinement.frozen.json \
  --require-confirmed \
  --output-dir results/spec-validation-3agent

uv run are-dcore validate-spec \
  --process-spec review/frozen/wetjune-4agent.process.v5.json \
  --team-id wetjune_4agent \
  --team-spec-path review/frozen/teams/wetjune_4agent.team.frozen.json \
  --role-refinement-path review/frozen/teams/wetjune_4agent.refinement.frozen.json \
  --require-confirmed \
  --output-dir results/spec-validation-4agent
```

### 7.2 Run the 20-case controlled suite

The preregistered 20-case suite is run on the development scenario:

```bash
uv run are-dcore validate-spec \
  --process-spec review/frozen/wetjune.process.v5.json \
  --team-id wetjune_2agent \
  --team-spec-path review/frozen/teams/wetjune_2agent.team.frozen.json \
  --require-confirmed \
  --with-mutants \
  --output-dir results/metric-validation
```

Require `paper_validation_passed: true`. A pending harmful-write obligation is
acceptable only in engineering mode, never in the paper package.

### 7.3 Create resolved offline matrix copies

The repository manifests contain deliberate `REPLACE_WITH_*` placeholders.
Create working copies outside the source config directory and replace every
process, team, refinement, and gate path with the reviewed file that has the
matching digest.

For offline mock validation only:

- Set `paper_mode: false`.
- Set `controller_mode: mock_llm`.
- Do not alter conditions, worlds, repeats, faults, budgets, or profiles.

Dry-run each resolved copy and confirm its count is unchanged. Execute the
mock copies, test resume, reevaluate saved traces, aggregate, and render the
report. Record the output digest before setting `mock_matrix_complete: true`.

### 7.4 Run full no-model paired preflight

Using a resolved primary-pass-1 working manifest:

```bash
uv run are-dcore preflight \
  review/resolved-manifests/farm_dcore_primary_pass1.yaml \
  --output-dir results/preflight-scientific
```

Do not use `--limit-worlds`. This checks every selected scenario/world for
planting-to-storage completion, native-oracle yield equivalence, exogenous
digest equality, stable fault targets, and review/release paths.

Complete the gate template only from saved evidence. Required offline fields
are:

```text
offline_semantic_gates
oracle_yield_equivalence
prompt_leakage_check
saved_trace_replay
blocked_write_nonmutation
allowed_write_exactly_once
mock_matrix_complete
```

The gate must also bind the confirmed process/team digests, code commit, test
report digest, environment-lock digest, analysis-protocol digest, and UTC
completion time. Do not mark a field true merely because the corresponding
test exists; preserve the actual result artifact.

## 8. Run the bounded OpenAI smoke

This is the only real-model call allowed before the final release gate. It is a
connectivity/controller integration smoke, not a paper observation. It is
capped at 12 total model calls and is rejected by paper aggregation.

Load the key without printing it:

```bash
set -a
source .env
set +a
```

Run Wet-June using its `offline_complete` gate:

```bash
uv run are-dcore run \
  --scenario-id farm_wetjune_recheck \
  --controller-mode llm \
  --model field_intelligence=gpt-5.4-mini-2026-03-17 \
  --model operations=gpt-5.4-mini-2026-03-17 \
  --provider field_intelligence=openai-json \
  --provider operations=openai-json \
  --temperature field_intelligence=0 \
  --temperature operations=0 \
  --team-id wetjune_2agent \
  --team-spec-path review/frozen/teams/wetjune_2agent.team.frozen.json \
  --petri-spec-path review/frozen/wetjune.process.v5.json \
  --scientific-gate-manifest review/gates/wetjune.offline.json \
  --scientific-contract v5 \
  --enforcement-mode audit \
  --max-model-calls 12 \
  --max-logical-steps 6 \
  --paper-mode \
  --bounded-llm-smoke \
  --output-dir results/openai-smoke
```

Inspect the trace, prompt-leakage audit, response IDs, token telemetry, retry
history, and structured completion. Never copy the key into a manifest, trace,
report, or handoff directory.

After a successful smoke, set `bounded_real_llm_smoke: true` in the external
gate evidence. The smoke row itself must never enter paper aggregation.

## 9. Create the release-bound handoff package

### 9.1 Freeze code and environment

Rerun tests, ensure the worktree is clean, commit the reviewed release state,
and create a release tag:

```bash
git status --short
git rev-parse HEAD
git tag --points-at HEAD
```

Create a tag only after the final code and lock file are fixed. The complete
gate files may be stored outside the repository; each must reference the clean
tagged code commit and exact release tag. This avoids changing the commit after
recording it.

Update each external gate to `status: complete`, including the release tag and
all required digests. Wet-June additionally requires the bounded-smoke field.

### 9.2 Confirm final readiness

Supply every frozen artifact explicitly:

```bash
uv run are-dcore doctor \
  --output-dir results/doctor-final \
  --process-spec review/frozen/wetjune.process.v5.json \
  --process-spec review/frozen/disease-drought.process.v5.json \
  --process-spec review/frozen/three-cultivar.process.v5.json \
  --process-spec review/frozen/wetjune-3agent.process.v5.json \
  --process-spec review/frozen/wetjune-4agent.process.v5.json \
  --team-spec review/frozen/teams/wetjune_2agent.team.frozen.json \
  --team-spec review/frozen/teams/wetjune_3agent.team.frozen.json \
  --team-spec review/frozen/teams/wetjune_4agent.team.frozen.json \
  --role-refinement review/frozen/teams/wetjune_3agent.refinement.frozen.json \
  --role-refinement review/frozen/teams/wetjune_4agent.refinement.frozen.json \
  --gate-manifest review/gates/wetjune.complete.json \
  --gate-manifest review/gates/disease-drought.complete.json \
  --gate-manifest review/gates/three-cultivar.complete.json \
  --gate-manifest review/gates/wetjune-3agent.complete.json \
  --gate-manifest review/gates/wetjune-4agent.complete.json
```

Proceed only when the output says:

```text
healthy: true
paper_ready: true
```

### 9.3 Build the immutable professor directory

Use all four required manifests, all five process specifications, three teams,
two refinements, and five gates:

```bash
uv run are-dcore handoff build \
  --manifest are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --manifest are/simulation/distributed/configs/farm_dcore_primary_pass2.yaml \
  --manifest are/simulation/distributed/configs/farm_dcore_controller_robustness.yaml \
  --manifest are/simulation/distributed/configs/farm_dcore_scalability.yaml \
  --process-spec review/frozen/wetjune.process.v5.json \
  --process-spec review/frozen/disease-drought.process.v5.json \
  --process-spec review/frozen/three-cultivar.process.v5.json \
  --process-spec review/frozen/wetjune-3agent.process.v5.json \
  --process-spec review/frozen/wetjune-4agent.process.v5.json \
  --team-spec review/frozen/teams/wetjune_2agent.team.frozen.json \
  --team-spec review/frozen/teams/wetjune_3agent.team.frozen.json \
  --team-spec review/frozen/teams/wetjune_4agent.team.frozen.json \
  --role-refinement review/frozen/teams/wetjune_3agent.refinement.frozen.json \
  --role-refinement review/frozen/teams/wetjune_4agent.refinement.frozen.json \
  --gate-manifest review/gates/wetjune.complete.json \
  --gate-manifest review/gates/disease-drought.complete.json \
  --gate-manifest review/gates/three-cultivar.complete.json \
  --gate-manifest review/gates/wetjune-3agent.complete.json \
  --gate-manifest review/gates/wetjune-4agent.complete.json \
  --output-dir professor_handoff
```

The builder resolves placeholders into package-relative paths, rechecks exact
run counts, copies specifications/gates/protocols/`uv.lock`, and writes:

```text
professor_handoff/HANDOFF_MANIFEST.json
professor_handoff/COMMANDS.json
professor_handoff/manifests/
professor_handoff/specifications/
professor_handoff/gates/
professor_handoff/protocol/
```

Run the commands from `COMMANDS.json` in their listed order.

## 10. Launch the experiment blocks

All commands below refer to the resolved manifests in `professor_handoff`.

### 10.1 Use sharding safely

Use a separate output directory for each shard so concurrent writers cannot
overwrite block-level tidy files. For example, with eight shards:

```bash
uv run are-dcore matrix \
  professor_handoff/manifests/farm_dcore_primary_pass1.yaml \
  --output-dir paper_results/pass1/shard-0 \
  --shard-count 8 --shard-index 0
```

Launch shard indices 0 through 7 with otherwise identical commands. Shards are
stable, disjoint, and exhaustive by immutable run key.

### 10.2 Primary pass 1

First dry-run the resolved manifest and inspect count and cost:

```bash
uv run are-dcore matrix \
  professor_handoff/manifests/farm_dcore_primary_pass1.yaml \
  --output-dir paper_results/pass1-dry --dry-run
```

Then execute all shards. After completion, run a non-paper integrity aggregate.
Pass 1 alone intentionally lacks repeat 1 and therefore cannot pass strict
paper aggregation:

```bash
uv run are-dcore aggregate paper_results/pass1 \
  --output-dir analysis/pass1
```

Inspect completion/failure artifacts, inactive faults, exogenous digest pairs,
budgets, and infrastructure failures. Do not change cells before pass 2.

### 10.3 Primary pass 2

Run the second manifest using the same shard policy:

```bash
uv run are-dcore matrix \
  professor_handoff/manifests/farm_dcore_primary_pass2.yaml \
  --output-dir paper_results/pass2/shard-0 \
  --shard-count 8 --shard-index 0
```

Pass 2 contains controller repeat 1 and does not duplicate deterministic
oracles.

### 10.4 Controller robustness

```bash
uv run are-dcore matrix \
  professor_handoff/manifests/farm_dcore_controller_robustness.yaml \
  --output-dir paper_results/controller/shard-0 \
  --shard-count 8 --shard-index 0
```

This block holds the backbone and reliable transport fixed while varying nine
controller families.

### 10.5 Scalability

```bash
uv run are-dcore matrix \
  professor_handoff/manifests/farm_dcore_scalability.yaml \
  --output-dir paper_results/scalability/shard-0 \
  --shard-count 4 --shard-index 0
```

Keep matched-total-team and matched-per-agent compute rows separate in analysis.

### 10.6 Optional prior-model continuity

This 60-run template is optional and is not included by the required handoff
count. Copy it, fill the exact Qwen/DeepSeek model and provider identifiers,
bind the reviewed paths, dry-run it, and place its results under a distinct
`paper_results/optional-model-continuity/` directory.

## 11. Resume and failure handling

Matrix execution resumes by default. Rerun the exact same shard command after
an interruption. Completed run keys are skipped.

Each successful run has `COMPLETED.json`. A failed run has a structured failure
artifact and remains an intention-to-treat failure unless it is classified as
an infrastructure failure under the frozen protocol.

Do not:

- Delete failed rows from primary results.
- Rerun only unfavorable model outcomes.
- Change a world, model, or fault seed under the same run key.
- Treat an inactive fault as a valid fault observation.
- Pool different backbones or controller profiles in one condition row.

## 12. Reevaluate saved traces

Reevaluation does not call a model:

```bash
uv run are-dcore evaluate \
  paper_results/pass1/shard-0/SCENARIO/RUN/trace.dcore.json \
  --output results/reevaluated-metrics.json
```

For paper traces, saved-trace reevaluation must reproduce the frozen metrics
byte-for-byte. If it does not, stop and investigate version/digest mismatch.

## 13. Merge, validate, aggregate, and report

The aggregator recursively reads `results.jsonl` files below a common root. It
rejects duplicate run keys in paper mode.

After all required blocks finish:

```bash
uv run are-dcore aggregate paper_results \
  --output-dir analysis/merged \
  --paper-mode
```

Strict paper aggregation rejects:

- Unresolved placeholders.
- Engineering or bounded-smoke rows.
- Pre-v5 metric or trace versions.
- Missing process digests.
- Failed scientific audit gates.
- Inactive fault treatments.
- Duplicate run keys.
- Missing primary repeat 0 or repeat 1 cells.

Run the digest-bound controlled validation command from
`professor_handoff/COMMANDS.json`. It writes `metric_validation.json` beneath
`analysis/merged`, ensuring Table 1 uses the 20 controlled fixtures rather than
a fallback baseline.

Then render the frozen outputs:

```bash
uv run are-dcore report analysis/merged \
  --output-dir paper_outputs
```

Confirm the inventory:

```text
6 CSV tables
6 LaTeX tables
5 PNG figures
5 PDF figures
analysis_manifest.json
```

The manifest binds source-row digests, controlled-fixture digest, failures,
metric/specification versions, plotting settings, and artifact hashes.

## 14. Expected run artifacts

Each D-CORE run directory should contain, where applicable:

```text
resolved configuration / run manifest
native FarmARE trace
dcore_trace_v5 causal trace
process and team/refinement digests
expanded occurrence net
metrics and policy-conformance profile
provenance failure localization
farm outcome and per-ridge yield
controller/token/message telemetry
experiment_row.json
COMPLETED.json or structured failure report
resume checkpoint
```

Block roots contain:

```text
results.jsonl
results.csv
phase_metrics.csv
module_metrics.csv
ridge_yields.csv
agent_telemetry.csv
aggregate.json (after aggregation)
```

## 15. Troubleshooting

### `healthy: true`, `paper_ready: false`

Read the false entries under `paper_readiness_gates`. Typical causes are an
unconfirmed process/team, an incomplete gate, a dirty worktree, a tag mismatch,
or a changed `uv.lock`/analysis-protocol digest.

### Matrix contains unresolved placeholders

Do not bypass the error. Use resolved working copies for offline validation or
the final manifests emitted by `handoff build`.

### Fault did not manifest

The row is invalid for the intended treatment. Check the stable target ID and
the reviewed scenario branch; do not relabel it as a successful fault run.

### Exogenous digests differ across paired conditions

Stop the paired block. This indicates world and communication treatments are
confounded.

### Budget exhausted

Keep the row as an intention-to-treat controller failure. Do not silently raise
the cap for only that cell.

### Provider or credential failure

Record it as infrastructure failure, fix the environment, and rerun the same
immutable run key. Never write the API key into logs or manifests.

### Repository-wide legacy failures

The previous audit found unrelated tests that depend on unavailable external
fixtures and legacy scenario-discovery assumptions. Classify these explicitly
in the release report. Do not weaken D-CORE checks to make unrelated tests
green.

## 16. Final launch checklist

Before the first paid paper season, verify all of the following:

- All three two-agent process specifications are confirmed and frozen.
- Wet-June 3-/4-agent process refinements are confirmed and frozen.
- All three team specifications and both role refinements are confirmed.
- The 20-case paper validation passes with no pending property.
- Full no-model preflight passes for every selected world.
- Scripted oracle yield equivalence passes.
- Prompt leakage and exactly-once/nonmutation checks pass.
- Mock matrices, resume, reevaluation, aggregation, and reporting pass.
- The bounded OpenAI smoke passes without credential leakage.
- Code, `uv.lock`, process files, gate files, and protocol digests agree.
- The code commit is clean and release-tagged.
- `doctor` reports `healthy: true` and `paper_ready: true`.
- Every final matrix dry-run matches 480, 450, 270, and 45.
- The estimated maximum calls, tokens, and cost have been reviewed.
- The professor handoff builder succeeds.

If any item is false, stop before the paid matrix. This preserves the validity
of the held-out transfer results and the defensibility of the AAMAS paper.
