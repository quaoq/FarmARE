# D-CORE review and execution runbook

Run commands from the repository root with Python 3.12. Keep credentials in an
ignored `.env` or the process environment.

## 1. Install and inspect

```bash
uv sync --frozen --python 3.12 --extra dev
git status --short
```

Read:

```text
AAMAS/professor_review/OVERVIEW.md
AAMAS/manuscript/main.pdf
AAMAS/authored_specifications/manifest.json
AAMAS/handover_development/repair_catalogue_v2.json
AAMAS/handover_development/analysis_contract_v2.json
AAMAS/comparators/source_lock.json
are/simulation/distributed/EXPERIMENT_PROTOCOL.md
```

## 2. Offline verification

```bash
uv run --frozen ruff check \
  are/simulation/distributed are/simulation/tests/distributed \
  AAMAS/tools AAMAS/comparators AAMAS/manuscript/build_tables.py

uv run --frozen pytest -q are/simulation/tests/distributed

uv run --frozen python AAMAS/tools/build_authored_specs.py
uv run --frozen python AAMAS/tools/validate_authored_workflows.py \
  --output-dir results/authored-workflows-review

MPLCONFIGDIR=/tmp/farmare-mpl \
  uv run --frozen python AAMAS/manuscript/build_tables.py
```

Specification regeneration must produce no Git diff. Run the farm-world tests
identified by:

```bash
rg --files are/simulation/tests | rg 'farm_world|farm'
```

## 3. Inspect exact whole-season assignments

Dry-run each manifest and confirm 480, 450, 300 and 15 assignments:

```bash
for manifest in \
  farm_dcore_primary_pass1.yaml \
  farm_dcore_primary_pass2.yaml \
  farm_dcore_live_verification.yaml \
  farm_dcore_reserve.yaml; do
  uv run --frozen are-dcore matrix \
    are/simulation/distributed/configs/$manifest \
    --output-dir results/dry-run/$manifest --dry-run
done
```

The reserve must refuse execution. Paper manifests remain disabled before release.

## 4. Diagnose and verify a checkpoint

```bash
uv run --frozen are-dcore diagnose RUN_DIR \
  --decision-id DECISION_ID \
  --method full_information_checker \
  --method fixed_protocol_rules \
  --method generic_reconsideration \
  --method dcore \
  --method dcore_bounded_repair \
  --output analysis/diagnosis.json

uv run --frozen are-dcore replay RUN_DIR \
  --decision-id DECISION_ID \
  --remaining-call-budget CALLS \
  --remaining-token-budget TOKENS \
  --replay-level response \
  --execute-output-dir results/replay/DECISION_ID \
  --output analysis/checkpoint.json
```

`execution.verified` and `checkpoint_verified` must both be true. Runs with an
uncertain provider request or native write are ineligible.

## 5. Build and run the miniature repair manifest

Freeze one independently labeled repairable failure and one valid decision per
scenario before running D-CORE. Each checkpoint in the manifest must contain a
`continuation_manifest_v2` and an `independent_label` with
`frozen_before_dcore: true`.
The prospective design and label template are
`AAMAS/handover_development/miniature_study_plan_v2.json` and
`AAMAS/handover_development/miniature_checkpoint_labels_v2.template.json`.
After source runs and labels are frozen, save the generated executable manifest
as `results/miniature_repair_study_v2.json`.

```bash
uv run --frozen are-dcore repair-checkpoints \
  results/miniature-sources/results.jsonl \
  results/miniature_checkpoint_labels_v2.json \
  --output results/miniature_repair_study_v2.json
```

Validate the 90 assignments without provider calls:

```bash
uv run --frozen are-dcore repair-study \
  results/miniature_repair_study_v2.json \
  --output analysis/miniature-plan.json
```

After a new numeric budget is approved, execute to a new empty directory:

```bash
uv run --frozen are-dcore repair-study \
  results/miniature_repair_study_v2.json \
  --execute-output-dir results/miniature-repair-v2 \
  --output analysis/miniature-execution.json
```

The command orchestrates fresh untreated, generic reconsideration, fixed protocol,
independent-checker repair and D-CORE repair. It derives owners, tools, route delay,
cost and feasibility from the saved run, team, native inventory and frozen repair
catalogue. Do not hand-edit ownership or feasibility maps.

## 6. External diagnostic adapters

Who&When and AgentRx are optional until their fixtures pass. Create isolated
checkouts at the revisions in `AAMAS/comparators/source_lock.json`, configure an
ignored copy of `adapter_config.template.json`, and set
`DCORE_COMPARATOR_ADAPTERS`. Verify normalized input and retained raw output on
bounded packets. Preserve abstention, no-error, invalid steps and inconclusive
outputs. Do not include a failing adapter in the study. DCFA and DoVer are related
work only.

## 7. Agricultural review

Regenerate unique cases into a new directory and have two distinct agricultural
reviewers complete the submissions independently:

```bash
uv run --frozen are-dcore agricultural-review-packets \
  --process AAMAS/authored_specifications/farm_wetjune_recheck.process.json \
  --process AAMAS/authored_specifications/farm_disease_drought.process.json \
  --process AAMAS/authored_specifications/farm_three_cultivar.process.json \
  --per-scenario 8 --output-dir results/agricultural-review-v2

uv run --frozen are-dcore agricultural-review-validate \
  results/agricultural-review-v2/packets.json \
  results/agricultural-review-v2/reviewer_a.json \
  results/agricultural-review-v2/reviewer_b.json \
  --output AAMAS/agricultural_review_packets/validation.json
```

Any `revise` or `unknown` finding must be resolved prospectively. Never pad a
shortfall with duplicate content.

## 8. Budget gate and live miniature

No paid call is currently authorized. Review
`AAMAS/handover_validation/miniature_cost_preestimate_v2.json`. Its deliberately
loose combined ceiling is $728.04 because it prices every suffix at the largest
observed full-season usage. The recommended route is a $30.34 stage-one allocation
for the six source seasons, followed by a tighter checkpoint-based estimate before
the 90 suffixes and 30 live-policy seasons. The professor must supply a new explicit
numeric allocation. Preserve every attempt and reservation; unknown usage remains
charged as reserved.

The live miniature uses worlds 70--71, all three scenarios, one frozen transport
and five policies. Always and periodic policies must show real verifier requests.
D-CORE repairs must show witness, selection and native application records. The
miniature validates plumbing and exposes failures; it does not establish a paper
effect.

## 9. Reporting and manuscript

```bash
uv run --frozen are-dcore aggregate paper_results \
  --output-dir analysis/merged --paper-mode
uv run --frozen are-dcore report paper_results --output-dir paper_outputs
MPLCONFIGDIR=/tmp/farmare-mpl \
  uv run --frozen python AAMAS/manuscript/build_tables.py
```

The report directly generates diagnosis, repair, live-policy, completion,
missingness and provider-cost tables. Partial horizon harvest is retained.
Selective verification is compared with always-verify for noninferiority and
with audit-only for improvement.

Compile `AAMAS/manuscript/main.tex` with the included AAMAS class. Check the
eight-page main-text limit, citations, anonymity, licenses, supplement size and
AI-assistance statement. Results and conclusions stay pending until the study is
complete.

## 10. Professor approval and full study

Build a review handoff while approval is pending. A release handoff additionally
requires a clean tagged commit, completed agricultural validation, all engineering
gates, and a professor attestation whose subject digests exactly match the process,
team/refinement, protocol, repair catalogue, comparator lock, analysis contract,
experiment manifests and review validation.

After release, run the 480, 450 and 300 assignments; leave the 15 reserve rows
disabled unless opened prospectively. Freeze 120 annotation decisions before
D-CORE predictions, obtain two independent labels, adjudicate separately, and
retain every failed, missing, inactive, null and adverse result.
