# Local validation — 2026-09-13

Historical audit: current implementation and gate status are recorded in
[HANDOVER_STATUS.md](HANDOVER_STATUS.md) and
[PDF_CLOSURE_CHECKLIST.md](PDF_CLOSURE_CHECKLIST.md). Dated test and no-run
statements below describe that earlier revision.

Environment: CPython 3.12.12, installed from the existing `uv.lock` using
`uv sync --frozen --python 3.12 --extra dev`. The dependency lock was unchanged.

| Check | Result |
| --- | --- |
| Full distributed regression | 196 passed, zero failures |
| Shared environment and clock regression | 10 passed, zero failures |
| Review-specific regressions | 40 passed, included in the distributed total |
| Paper-foundation validation regressions | 26 passed, included in the distributed total |
| Ruff lint: distributed code/tests and changed shared implementation | Passed |
| Ruff formatting: changed Python files | Passed |
| `git diff --check` | Passed |
| `are-dcore doctor` | `healthy: true`, `paper_ready: false` |
| Professor matrix counts | 480 / 450 / 270 / 45, unchanged |
| Candidate calibration dry run | 20 planned seasons, zero model calls, no season initialized |

The confirmation pass corrected regional and temporal evidence selection,
sensor coverage, false crop-maturity extraction, forecast/current-weather
semantics, policy reconstruction, native retry reporting, observed A2A uptake,
and verification of drought release evidence. Calibration now synchronizes
physics before stress measurement and excludes host overhead from simulation
time through an injected event-driven clock.

The full regression checks retain exact native-yield equivalence, perfect
oracle policy conformance, zero oracle information/global discordance, and the
twenty-case controlled diagnostic suite. Separating forecast evidence exposed
inconsistent guard and policy declarations; those declarations were corrected
without relaxing the reference assertions. A regression explicitly permits a
locally supported forecast-based decision to disagree with actual weather.
The earlier receive-provenance fix also preserves the assertion that every
reliably delivered fact version is accounted for.

The paper-foundation extension adds deterministic episode sampling, separate
public/private annotation packets, source-integrity checks, independent-label
agreement and scoring, a same-evidence flat baseline, and saved-trace ablations
with precommitted sensitivity families. Its 26 dedicated tests cover temporal
and scope limits, immutable source traces, blinding of explicit assignments,
sample shortfalls, duplicate runs, incomplete/invalid annotations, free-text
quote mappings, abstention, world-cluster comparisons, and variant completeness.
The test annotations are synthetic software fixtures, not human study evidence.
See [`PAPER_FOUNDATION.md`](PAPER_FOUNDATION.md) for the prospective study protocol.

Evidence is retained under [`validation/`](validation/): sanitized doctor output,
source/lock SHA-256 hashes, the complete distributed JUnit report and log, and
an additional JUnit report for shared environment/clock tests. Hostname fields
are removed from the saved XML copies; credential-presence fields are omitted
from the doctor copy. Source hashes identify the local implementation under
test, not a release tag or an expert attestation.

These checks cover the distributed research implementation, native simulation
regression fixtures, and the changed shared environment/clock integration.
Repository-wide legacy suites and external provider integration were not run.
No paper matrices, calibration seasons, or real-model smoke calls were executed.
The GitHub workflow includes these offline tests and lint checks but has not
been executed by GitHub in this session.

Paper readiness still requires independent agronomy and team review, validated
scenario sensitivity, a bounded real-model smoke, completed scientific gates,
a clean tagged release, final experiments/analysis, and an anonymous submission
package. See [`CONFIRMATION_AUDIT.md`](CONFIRMATION_AUDIT.md) for every PDF point
and [`REVIEW_RESPONSE_AND_HANDOFF.md`](REVIEW_RESPONSE_AND_HANDOFF.md) for the
calibration handoff and claim limits.
