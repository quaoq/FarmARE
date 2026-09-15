# D-CORE professor review and execution runbook

Run commands from the repository root. Commands in sections 1-7 are offline unless
explicitly marked **provider**.

## 1. Install

Use Python 3.12 and the committed lock without updating it:

```bash
uv sync --frozen --python 3.12 --extra dev
```

Keep credentials in an ignored `.env` or the process environment. Never put an
API key, authorization header, or provider credential in a manifest or archive.

## 2. Read the review package

Start with:

```text
AAMAS/professor_review/OVERVIEW.md
AAMAS/manuscript/main.pdf
AAMAS/authored_specifications/manifest.json
AAMAS/comparators/source_lock.json
AAMAS/agricultural_review_packets/packets.json
are/simulation/distributed/EXPERIMENT_PROTOCOL.md
are/simulation/distributed/SCIENTIFIC_CONTRACT_V5.md
```

Historical pilots are engineering evidence only. The corrected V18 summary is
`AAMAS/handover_validation/progression_v18_completed_after_review.json`.

## 3. Verify the code offline

```bash
uv run --frozen ruff check \
  are/simulation/distributed \
  are/simulation/tests/distributed \
  AAMAS/tools AAMAS/manuscript/build_tables.py

uv run --frozen pytest -q are/simulation/tests/distributed
```

Run the farm-world regression directory identified by `rg --files
are/simulation/tests | rg 'farm_world|farm'` after the distributed suite. Failures
must be fixed or listed in the status artifact; do not weaken scientific gates.

Run the doctor:

```bash
uv run --frozen are-dcore doctor --output-dir results/doctor-review
```

Before approval, `paper_ready: false` is expected. `healthy: false` is an
engineering blocker.

## 4. Rebuild and validate authored specifications

```bash
uv run --frozen python AAMAS/tools/build_authored_specs.py
uv run --frozen python AAMAS/tools/validate_authored_workflows.py \
  --output-dir results/authored-workflows-review
```

The first command must produce no Git diff. Reference workflows must complete
harvest, unload, safe drying or an explicit safe-moisture skip, and storage with
mass conservation.

## 5. Verify exact study assignments

```bash
uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/dry-run/pass1 --dry-run
uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass2.yaml \
  --output-dir results/dry-run/pass2 --dry-run
uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_live_verification.yaml \
  --output-dir results/dry-run/live --dry-run
uv run --frozen are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_reserve.yaml \
  --output-dir results/dry-run/reserve --dry-run
```

Expected counts are 480, 450, 300, and 15. The reserve must refuse execution. All
paper manifests remain `paper_mode: false` until release.

## 6. Diagnose, replay, and select repairs

Build one common diagnostic packet and run selected methods:

```bash
uv run --frozen are-dcore diagnose RUN_DIR \
  --method core \
  --method dcore \
  --method dcfa_style_reimplementation \
  --output analysis/diagnosis.json
```

Create a checkpoint immediately before a decision and execute an unchanged replay:

```bash
uv run --frozen are-dcore replay RUN_DIR \
  --decision-id DECISION_ID \
  --remaining-call-budget CALLS \
  --remaining-token-budget TOKENS \
  --replay-level response \
  --execute-output-dir results/replay/DECISION_ID \
  --output analysis/checkpoint.json
```

`execution.verified` must be true before a repair study uses that checkpoint.
Interrupted runs with uncertain native writes are ineligible.

Select bounded candidates from a witness bundle:

```bash
uv run --frozen are-dcore repair-study WITNESS_MANIFEST.json \
  --strategy frozen_priority --output analysis/repair-selection.json

# After unchanged checkpoint verification, execute the single selected repair:
uv run --frozen are-dcore repair-study EXECUTION_MANIFEST.json \
  --strategy frozen_priority \
  --execute-output-dir results/repair/DECISION_ID \
  --output analysis/repair-execution.json
```

Also run `cost_only` and `unrestricted` as prespecified comparisons. A candidate
has at most two primitives and must identify its required evidence, native cost,
timing slack, and feasibility. `EXECUTION_MANIFEST.json` additionally supplies
`source_run_dir`, the verified `continuation_manifest`, and release-approved
`execution_overrides`. The executor rejects non-feasible candidates, mismatched
checkpoints, illegal tools/routes, expired evidence, and nonempty output paths.

## 7. Configure comparator environments

Create isolated checkouts at the exact revisions in
`AAMAS/comparators/source_lock.json`. Copy
`AAMAS/comparators/adapter_config.template.json` to an ignored local file, replace
absolute command paths, set `DCORE_WHO_WHEN_ROOT` and `DCORE_AGENTRX_ROOT` to
those checkouts, configure their documented Azure provider variables, and set:

```bash
export DCORE_COMPARATOR_ADAPTERS=/absolute/path/to/local-adapters.json
```

Each bridge reads one `dcore_comparator_request_v1` from stdin and writes one
`comparator_result_v1` to stdout. Revision mismatch, packet-digest mismatch,
unsupported capability, nonzero exit, and timeout are explicit results. DCFA uses
only the clean local reimplementation; do not copy unlicensed repository code.
The MARBLE-style milestone projection and the DoVer asynchronous-boundary
adaptation run locally and require no external checkout. The current budget cap
also applies to comparator provider calls.

## 8. Complete agricultural review

Two distinct agricultural reviewers independently edit the supplied copies:

```text
AAMAS/agricultural_review_packets/reviewer_a.json
AAMAS/agricultural_review_packets/reviewer_b.json
```

Allowed determinations are `approve`, `revise`, and `unknown`; every packet needs
a rationale. Validate the locked submissions:

```bash
uv run --frozen are-dcore agricultural-review-validate \
  AAMAS/agricultural_review_packets/packets.json \
  AAMAS/agricultural_review_packets/reviewer_a.json \
  AAMAS/agricultural_review_packets/reviewer_b.json \
  --output analysis/agricultural-review-validation.json
```

Any `revise` or `unknown` blocks confirmation until resolved prospectively.

## 9. Run the bounded final smoke (**provider**)

The declared no-fault manifest is
`AAMAS/handover_development/progression_v19_worlds70_71.yaml`. Its offline dry-run
resolves exactly six runs with no placeholders. **Do not launch it in the current
review package:** `AAMAS/handover_validation/current_spending_20260915.json` shows
$135.814958 effective spend, which is $5.348556 beyond this phase's incremental
cap. One request is still reserved and one has unknown usage. A professor-approved
new allocation and resolution of those ledger entries are required first. Do not
print the environment when credentials are eventually loaded.

```bash
set -a
source .env
set +a
uv run --frozen are-dcore matrix \
  AAMAS/handover_development/progression_v19_worlds70_71.yaml \
  --output-dir results/aamas_handover/progression_v19_worlds70_71
```

Both worlds must reach each scenario's declared high-impact decisions; at least
one world per scenario must complete harvest, storage, and safe postharvest
handling. Only then run the matched Wet-June communication smoke. Correct fault
activation/nonactivation, recovery, failure, journal, and accounting are the gate;
a favorable effect is not.

Preserve every attempt. Resume only completed compatible rows. Never rerun an
uncertain native write in place.

## 10. Professor approval and release

Approval uses the `author_defined_professor_approved` route and binds exact hashes
for all process/team/refinement specifications, the repair catalogue and selection
order, comparator source lock, experiment protocol, analysis plan, manifests, and
agricultural review validation. Human identity, role, timestamp, statement, and
subject digests are required. Approval does not make author-written specifications
independently authored.

Build `--stage review` while approval is pending and `--stage release` only after
all gates pass:

```bash
uv run --frozen are-dcore handoff build --help
```

The release worktree must be clean and tagged. Verify `HASHES.json`, portability,
credential exclusion, and `COMMANDS.json` from a fresh checkout.

## 11. Execute the professor study

Use separate output directories and shards for:

```text
primary pass 1: 480 seasons
primary pass 2: 450 seasons
live verification: 300 seasons
reserve: 15 disabled assignments
```

Worlds 100-109 are primary; focused analyses use 100-104. Keep the main ReAct
backbone fixed. Retain all assigned failures, missing outcomes, and inactive faults.
Do not replace an unfavorable row or open reserve after inspecting results.

The repair study selects 20 checkpoints per scenario, crosses five conditions,
and runs three suffix repetitions. Continuations and decisions remain clustered
within world. The primary repair outcome is recovered-harvest change; report 0.5%,
1%, and 2% reference-harvest thresholds.

## 12. Annotation and reporting

Freeze 120 decisions before D-CORE predictions: 40 per scenario and at most two
per run. Obtain two independent labels, retain `unknown`, then adjudicate
separately. Report pre-adjudication agreement and multi-label confusion matrices.

Aggregate and rebuild:

```bash
uv run --frozen are-dcore aggregate paper_results \
  --output-dir analysis/merged --paper-mode
uv run --frozen are-dcore report analysis/merged --output-dir paper_outputs
uv run --frozen python AAMAS/manuscript/build_tables.py
```

The main paper contains four pipeline-generated tables: evaluation disagreement,
diagnosis, matched repairs, and live verification. Report scenario-first paired
contrasts and world-cluster bootstrap intervals. Live verification includes the
one-sided 95% bound against the -1% normalized-harvest noninferiority margin and
the full two-sided interval.

Compile the manuscript with the included AAMAS class. Check the eight-page
main-text limit, citations, anonymity, licenses, supplement size, and the current
AI-assistance policy. Write results and conclusions only from frozen outputs.
