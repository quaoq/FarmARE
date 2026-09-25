# D-CORE review and execution runbook

Run commands from the repository root with Python 3.12. Keep credentials only in
the ignored `.env` or process environment. Never copy them into a manifest,
journal or handoff archive.

## 1. Install and verify offline

```bash
uv sync --frozen --python 3.12 --extra dev

MPLCONFIGDIR=/tmp/farmare-mpl \
  uv run --frozen pytest -q are/simulation/tests/distributed

MPLCONFIGDIR=/tmp/farmare-mpl \
  uv run --frozen pytest -q \
  are/simulation/tests/test_farm_world_physics_app_gaps.py \
  are/simulation/tests/test_farm_worldpp_physics_observation_outcomes.py

uv run --frozen ruff check \
  are/simulation/distributed are/simulation/tests/distributed
uv run --frozen ruff format --check \
  are/simulation/distributed are/simulation/tests/distributed
```

The tracked validation counts describe the exact commits on which they were
run; do not infer a current pass from an older record. Run the complete commands
above on the review checkout before opening any experiment. Read
`AAMAS/handover_validation/student_review_20260925.json` before execution.

## 2. Inspect the frozen engineering assignments

```bash
uv run --frozen are-dcore matrix \
  AAMAS/handover_development/progression_v21_worlds74_75.yaml \
  --output-dir results/dry-run/miniature-sources --dry-run

uv run --frozen are-dcore matrix \
  AAMAS/handover_development/miniature_live_policy_v4_worlds74_75.yaml \
  --output-dir results/dry-run/miniature-live --dry-run

uv run --frozen are-dcore matrix \
  AAMAS/handover_development/miniature_scripted_references_v4_worlds74_75.yaml \
  --output-dir results/dry-run/miniature-references --dry-run
```

The expected counts are 6, 30 and 6, with no unresolved placeholders. Request,
token, retry, model, temperature and seed limits remain frozen scientific
controls. There is no monetary stop; every request and cost is still recorded.
No further engineering live run should be launched by default; the professor
should deliberately open the next prospectively declared cohort.

The 2026-09-25 correction used no paid provider calls. Start any future live
session with the read-only authentication check below, then one bounded source
assignment. Inspect its journal, usage record and native progression before
opening the remaining five assignments.

Worlds 70--73 and 110--114 are preserved development/failure-discovery cohorts.
Do not resume them, select new checkpoints from them, or use them as paper
evidence. Use only the prospectively declared worlds 74--75 cohort after the
complete offline gate passes. If that cohort fails after a scientific or runtime
change, preserve it and declare another unused cohort instead of rerunning it as
new evidence.

## 3. Check provider authentication before a live campaign

The replacement credential returned HTTP 200 on 2026-09-19 and the six-run
provider cohort completed. Recheck authentication before any new campaign with a
read-only model lookup that does not print the key:

```bash
set -a
source .env
set +a
curl -sS -o /tmp/dcore-openai-auth.json -w '%{http_code}\n' \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  https://api.openai.com/v1/models/gpt-5.4-mini-2026-03-17
```

Do not resume either `miniature_sources_v2` (uncertain requests from the invalid
credential) or `miniature_sources_v2_retry1` (complete pre-fix evidence). Use the
prospectively declared post-fix manifest and a fresh root.

## 4. Run and inspect the six source seasons

Run the frozen six-assignment matrix into one empty directory:

```bash
MPLCONFIGDIR=/tmp/farmare-mpl \
uv run --frozen are-dcore matrix \
  AAMAS/handover_development/progression_v21_worlds74_75.yaml \
  --output-dir results/aamas_handover/miniature_sources_v4_scope_contract_fix \
  --no-resume
```

Confirm the six `results.jsonl` rows have unique `run_key` values. Confirm both
worlds reach each scenario's declared high-impact
decisions and at least one world per scenario has `harvest_complete`,
`storage_complete` and `postharvest_compliant` true. Preserve failed rows.

The pre-fix worlds 70--71 are preserved at
`results/aamas_handover/miniature_sources_v2_retry1`: all six outcomes were
available with no infrastructure failures, but none passed the complete
postharvest gate. The failure exposed stale postharvest completion memory, which
is fixed and regression-tested. Do not select checkpoint labels from that
cohort.

Run the matching six scripted references from
`AAMAS/handover_development/miniature_scripted_references_v4_worlds74_75.yaml`;
these make no provider calls and remain a separate ceiling, not a replacement
for live source seasons:

```bash
uv run --frozen are-dcore matrix \
  AAMAS/handover_development/miniature_scripted_references_v4_worlds74_75.yaml \
  --output-dir results/aamas_handover/miniature_references_v4_scope_contract_fix \
  --no-resume
```

The older worlds 70--71 reference validation remains historical evidence under
`AAMAS/handover_validation/scripted_reference_validation_20260919.json`. It does
not replace the matched worlds 74--75 references required here.

## 5. Freeze six checkpoint labels before D-CORE prediction

Use `AAMAS/handover_development/miniature_checkpoint_labels_v2.template.json`.
Freeze exactly one `repairable_information_failure` and one `valid_decision` per
scenario. Set `frozen_before_dcore: true` and
`dcore_outputs_opened_before_freeze: false`. Record the source run, decision,
mechanism, exact evidence version and rationale. Across the three repairable
cases, cover native acquisition, exact-version delivery/routing and prompt
restoration. Declare any controlled communication fault prospectively.

```bash
uv run --frozen are-dcore repair-checkpoints \
  results/aamas_handover/miniature_sources_v4_scope_contract_fix/results.jsonl \
  results/aamas_handover/miniature_checkpoint_labels_v2.json \
  --output results/aamas_handover/miniature_repair_study_v2.json
```

This command rejects uncertain writes, absent decisions, opened D-CORE output,
unbalanced labels and exhausted suffix budgets. It freezes the rounded-up
successful-call p95 response lead, with a one-second minimum.

## 6. Diagnose, replay and execute the matched continuations

Inspect a checkpoint over one shared prefix packet:

```bash
uv run --frozen are-dcore diagnose RUN_DIR \
  --decision-id DECISION_ID \
  --method action_trace_checker \
  --method generic_reconsideration \
  --method fixed_protocol_rules \
  --method full_information_checker \
  --method dcore_full \
  --method dcore_no_temporal_validity \
  --method dcore_no_actor_delivery \
  --method dcore_no_prompt_inclusion \
  --method dcore_no_scope \
  --method dcore_no_targeted_selection \
  --method dcore_diagnosis_only \
  --output analysis/diagnosis.json
```

Plan all 90 assignments without provider calls, then execute to an empty root:

```bash
uv run --frozen are-dcore repair-study \
  results/aamas_handover/miniature_repair_study_v2.json \
  --output analysis/miniature-repair-plan.json

uv run --frozen are-dcore repair-study \
  results/aamas_handover/miniature_repair_study_v2.json \
  --execute-output-dir results/aamas_handover/miniature-repair-v2 \
  --output analysis/miniature-repair-results.json
```

The five conditions are fresh untreated, generic reconsideration, fixed protocol,
independent-checker repair and D-CORE repair, each with three repetitions. The
append-only study ledger declares all assignments before execution and supports
compatible resume. It never replays an uncertain provider or native write.

## 7. Run the 30 live-policy seasons

```bash
uv run --frozen are-dcore matrix \
  AAMAS/handover_development/miniature_live_policy_v4_worlds74_75.yaml \
  --output-dir results/aamas_handover/miniature-live-v4-scope-contract-fix \
  --no-resume
```

Audit-only, existing guard, always-verify, periodic-verify and D-CORE must retain
the same assigned scenario/world/transport denominator. Always and periodic must
contain metered verifier calls. D-CORE must contain prefix witness, selection and
native application records. Keep attempted, applied, rejected, infeasible,
ineffective and successful interventions, and count finish deferrals separately.

## 8. Agricultural review and reporting

The tracked packet set is `AAMAS/agricultural_review_packets/packets.json`. Two
distinct agricultural reviewers complete `reviewer_a.json` and `reviewer_b.json`
independently, then validate them:

```bash
uv run --frozen are-dcore agricultural-review-validate \
  AAMAS/agricultural_review_packets/packets.json \
  AAMAS/agricultural_review_packets/reviewer_a.json \
  AAMAS/agricultural_review_packets/reviewer_b.json \
  --output AAMAS/agricultural_review_packets/validation.json
```

Regenerate engineering reports from the declared manifests. Preserve partial
harvest, missing outcomes, infrastructure failures, costs, abstentions and adverse
results. Compare selective verification with always-verify for noninferiority and
with audit-only for improvement. The miniature validates plumbing and discovers
failures; it is not paper evidence of superiority.

## 9. Professor release

Professor approval binds the exact process, team, protocol, repair catalogue,
comparator lock, analysis contract, experiment manifests and completed
agricultural-review digest. Only then may the professor run the full 480, 450 and
300 assignments, leave the 15 reserve rows closed unless opened prospectively,
freeze and independently label 120 paper decisions, adjudicate, and write
empirical conclusions from the resulting records.
