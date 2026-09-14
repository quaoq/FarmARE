# Farm D-CORE: professor review and execution runbook

This runbook covers the current engineering candidate and the later paper
workflow. Start with [OVERVIEW.md](OVERVIEW.md). Commands assume the repository
root as the working directory.

The current branch is suitable for review. It is not yet cleared for the paid
1,245-run paper suite. Commands labeled **offline** make no model calls. Commands
labeled **provider** require credentials and can incur cost.

## 1. Install

Required environment:

- Python 3.12; the development target used Python 3.12.12.
- Dependencies from the committed `uv.lock`.
- PM4Py 2.7.23.4, resolved by the lock file.
- OpenAI credentials only for provider-backed checks and experiments.

Install without updating the lock:

```bash
uv sync --frozen --python 3.12 --extra dev
```

Keep credentials in the process environment or an ignored local `.env`. Never
copy `.env`, API keys, tokens, or provider headers into a manifest or archive.

## 2. Inspect the review snapshot

The most useful files are:

```text
AAMAS/professor_review/OVERVIEW.md
AAMAS/manuscript/main.pdf
AAMAS/manuscript/main.tex
AAMAS/authored_specifications/manifest.json
AAMAS/handover_validation/drought_harvest_opening_v6_confirmation_61_65.json
AAMAS/handover_validation/progression_v18_interrupted.json
are/simulation/distributed/EXPERIMENT_PROTOCOL.md
are/simulation/distributed/SCIENTIFIC_CONTRACT_V5.md
```

## 3. Verify the code offline

Run the doctor, relevant tests, and lint:

```bash
uv run --frozen are-dcore doctor --output-dir results/doctor-review
uv run --frozen pytest -q are/simulation/tests/distributed
uv run --frozen ruff check \
  are/simulation/distributed \
  are/simulation/tests/distributed \
  AAMAS/tools
```

At the review stage, the expected doctor distinction is:

```text
healthy: true
paper_ready: false
```

`paper_ready: false` is correct before professor approval and complete release
gates. Any test or `healthy` failure is an engineering defect and must be resolved
before the full study.

Validate the authored specifications and native workflows:

```bash
uv run --frozen python AAMAS/tools/build_authored_specs.py
uv run --frozen python AAMAS/tools/validate_authored_workflows.py \
  --output-dir results/authored-workflows-review
```

The build must be deterministic: inspect changes after running it. All three
native reference workflows must complete harvest/storage with event fidelity 1.0.

## 4. Verify study assignments without model calls

Dry-run each required block:

```bash
uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/dry-run/pass1 --dry-run

uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass2.yaml \
  --output-dir results/dry-run/pass2 --dry-run

uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_controller_robustness.yaml \
  --output-dir results/dry-run/controller --dry-run

uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_scalability.yaml \
  --output-dir results/dry-run/scalability --dry-run
```

Expected assignment counts:

```text
primary pass 1:         480
primary pass 2:         450
controller robustness: 270
scalability:             45
total:                 1,245
```

The source manifests intentionally contain review placeholders. A dry run may
report those unresolved paths while still exposing the planned matrix. Do not
replace them with unreviewed files merely to enable paper mode.

## 5. Run the no-model integration smoke

This three-run scripted smoke exercises native execution, fault handling,
aggregation, and reporting without a provider:

```bash
uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_smoke.yaml \
  --output-dir results/scripted-smoke

uv run --frozen are-dcore aggregate results/scripted-smoke
uv run --frozen are-dcore report results/scripted-smoke \
  --output-dir results/scripted-smoke-report
```

Expect every run to have `COMPLETED.json`. The report should contain six CSV
tables, six LaTeX tables, five PNG figures, five PDF figures, and an analysis
manifest. Check the generated manifest rather than accepting file count alone.

## 6. Inspect and reproduce current engineering evidence

Regenerate the interrupted V18 summary without calling a model:

```bash
uv run --frozen python AAMAS/tools/summarize_live_smoke.py \
  AAMAS/handover_development/progression_v18_final_all.yaml \
  results/aamas_handover/progression_v18_final_all \
  --output /tmp/progression_v18_interrupted.json
```

Compare it with
`AAMAS/handover_validation/progression_v18_interrupted.json`. Wet-June should
show two policy-complete worlds and one complete harvest/storage outcome. The
Disease-Drought world-55 directory contains only an interrupted run identity.
Do not resume or delete it: native writes may have occurred.

The saved Disease-Drought worlds 61-65 confirmation is tied to the pre-cleanup
execution-source digest recorded in its plan. It should be inspected as immutable
engineering evidence. Do not edit its plan or rerun it under the same confirmation
label after a source change. A new confirmation requires a versioned plan and a
prospectively declared unused cohort.

Historical analysis utilities are under `AAMAS/tools/`. Each utility writes to a
new output location unless its explicit purpose is to rebuild a checked-in compact
summary. Never overwrite raw `results/` evidence.

## 7. Run a new bounded provider smoke

This section incurs provider cost. Use it only after the offline suite passes and
after declaring new unused worlds, an output directory, a budget pool, and an
unchanged manifest. Load credentials without printing them:

```bash
set -a
source .env
set +a
```

Dry-run the manifest first:

```bash
uv run --frozen are-dcore matrix \
  AAMAS/handover_development/progression_v18_final_all.yaml \
  --output-dir results/aamas_handover/NEW_PROSPECTIVE_SMOKE \
  --dry-run
```

V18 worlds 55-56 have already been consumed, so create and review a new versioned
manifest with unused worlds before actual execution. Keep the two completed
Wet-June rows and the interrupted drought identity unchanged. A valid progression
gate requires both declared worlds to reach every high-impact policy and at least
one to complete harvest/storage for each scenario.

After no-fault progression passes, run the prespecified matched Wet-June
free-text/audit/enforcement conditions. Require correct fault activation,
nonactivation, failure, and recovery records. A favorable treatment effect is not
a smoke-test requirement.

For every provider run, monitor:

- request, token, reservation, and settled-cost totals;
- invalid proposals and corrective retries;
- native execution failures and accepted receipts;
- controller, provider, voluntary, and budget termination separately;
- policy-window coverage and final harvest/storage state;
- credential exclusion from traces and archives.

## 8. Professor specification approval

The selected route is author-defined specifications followed by actual professor
approval. Approval is separate from independent episode annotation. The professor
reviews the exact files in `AAMAS/authored_specifications/`, the team and refinement
artifacts, the experiment protocol, the sensitivity alternatives, and the saved
calibration/smoke evidence.

The attestation must contain actual human values and exact digests:

```json
{
  "route": "author_defined_professor_approved",
  "approved": true,
  "reviewer_name": "HUMAN NAME",
  "reviewer_role": "professor",
  "signed_at": "ISO-8601 TIMESTAMP WITH TIMEZONE",
  "statement": "I reviewed and approve the bound scientific artifacts.",
  "subject_digests": {
    "process": "EXACT PROCESS DIGEST",
    "team": "EXACT TEAM DIGEST",
    "refinement": "INCLUDE WHEN APPLICABLE",
    "protocol": "EXACT PROTOCOL DIGEST"
  }
}
```

Do not use a blank template as approval. A changed process, team, refinement, or
protocol requires another review. Keep the label `author_defined`; professor
approval does not turn it into independently authored confirmation.

The alternative historical route remains supported by `are-dcore review`: two
independent submissions, comparison, adjudication, and a distinct third-expert
confirmation. It is optional for specification authorship under the selected
route.

## 9. Build review and release packages

The handoff command takes all four manifests, five process specifications, three
team specifications, two refinements, and their gate manifests. Inspect the exact
CLI before use:

```bash
uv run --frozen are-dcore handoff build --help
```

Use `--stage review` for frozen author-defined contents and engineering evidence
while professor approval is pending. Use `--stage release` only after genuine
approval and all scientific gates pass. The generated directory contains resolved
portable manifests, specifications, gates, protocols, `uv.lock`, hashes, and
`COMMANDS.json`.

Before a release build:

```bash
git status --short
git rev-parse HEAD
git tag --points-at HEAD
```

The worktree must be clean and the gate files must bind the exact commit, tag,
lock, protocol, test report, and scientific artifact digests. Run the generated
`COMMANDS.json` steps in order. Proceed only when the final doctor reports:

```text
healthy: true
paper_ready: true
```

## 10. Run the full study after release

Use separate directories per block and shard. Example for pass 1 with eight
shards:

```bash
uv run --frozen are-dcore matrix \
  professor_handoff/manifests/farm_dcore_primary_pass1.yaml \
  --output-dir paper_results/pass1/shard-0 \
  --shard-count 8 --shard-index 0
```

Run shard indices 0-7. Repeat with the resolved primary-pass-2 and
controller-robustness manifests. The scalability block can use four shards.
Always dry-run each resolved manifest and verify its count and maximum resource
estimate before launch.

Reissuing an identical command resumes compatible completed runs. A source or
configuration mismatch must be rejected. Preserve structured failures and
interrupted attempts. Never replay uncertain native writes blindly, change seeds
under the same run key, raise a cap for one unfavorable cell, or omit an inactive
fault from intention-to-treat analysis.

## 11. Annotation

After the study traces are frozen:

1. select 60 episodes with at most two from any run;
2. obtain two independent annotations per episode;
3. keep annotators blind to each other's labels and final treatment comparisons;
4. adjudicate disagreements separately;
5. preserve `unknown` when the trace cannot support a judgment.

Use `uv run --frozen are-dcore validation --help` for the installed command
surface. Specification approval cannot replace this annotation study.

## 12. Aggregate, report, and rebuild the paper

After every required block and annotation is complete:

```bash
uv run --frozen are-dcore aggregate paper_results \
  --output-dir analysis/merged --paper-mode

uv run --frozen are-dcore report analysis/merged \
  --output-dir paper_outputs
```

Paper-mode aggregation must reject unresolved placeholders, engineering rows,
old metric schemas, failed scientific gates, duplicate run keys, inactive fault
treatments presented as active, and missing primary repeats. Reports must include
coverage and denominators, assigned failures, missing outcomes, paired contrasts,
and world-cluster uncertainty.

Rebuild the manuscript tables:

```bash
uv run --frozen python AAMAS/manuscript/build_tables.py
```

Compile `AAMAS/manuscript/main.tex` with the included official class and
bibliography style. Verify that citations resolve, essential definitions and
evidence stay in the main paper, anonymity holds, the current AAMAS page limit is
met, licenses are retained, the supplement is below 25 MB, and AI assistance is
disclosed according to the current conference policy.

## 13. Expected artifacts and failure handling

A completed run normally contains its resolved configuration, run manifest,
FarmARE trace, D-CORE v5 trace, process/team digests, occurrence net, metrics,
provenance localization, farm outcome, per-ridge yield, telemetry, and
`COMPLETED.json`. Failures must have a structured failure artifact and remain in
their assigned analysis row.

If a fault does not activate, record it as inactive rather than successful. If
paired exogenous-world digests differ, stop the paired block. If a budget ends,
retain budget termination separately from invalid proposals and provider errors.
If provider usage is unknown, retain the conservative reservation. If saved-trace
reevaluation changes frozen metrics, stop and investigate the source/specification
identity before reporting results.
