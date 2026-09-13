# Confirmation audit of D-CORE

This is an author-side engineering and scientific-claims audit, not an AAMAS
peer review or an acceptance prediction. It checks the supplied PDF against
the implementation and the professor guides. No paper experiments, calibration
seasons or paid model calls were run.

## Coverage of every review point

| PDF point | What is covered | Evidence still required |
| --- | --- | --- |
| Explicit teammate capabilities and limits | Grant-derived cards, time authority, legal routing and ownership checks | Whether cards improve real-model coordination is an empirical question. |
| Persistent isolated agents and deterministic scheduling | Separate controllers/stores/inboxes; native gateway; no hidden-world prompt serialization | Provider smoke and model traces. In-process scheduling is not a real-network deployment. |
| Diagnostic decomposition is primary | EF, CC, local/global policy profiles, IGD, provenance, execution, synchronization and recovery outputs | Expert-annotated validation and comparisons against simpler baselines in final results. |
| Earliest recorded break is not the cause of yield loss | Claim restrictions remain explicit; paired worlds and faults are recorded | Yield effects require active paired interventions; descriptive localization alone is insufficient. |
| Incorrect routing and unrelated substitute actions | Permission cards, exact tool execution, refusal/rerouting instructions, receipts | No claim that prompts force correct decisions or that autonomous recovery has already improved. |
| S3-versus-ridge-3 confusion | Opaque sensor IDs, returned coverage instead of installation ridge, scoped messages and runtime guard selection | Spatial-misinterpretation rates from real runs. |
| Old evidence arrives after new evidence | Runtime and v5 saved-trace evaluator prioritize observation time | Fault experiments must confirm active inversions and downstream behavior. |
| Verifiable tool receipts | Native-event/request/result bindings and shareable local receipt facts | Acceptance does not establish subsequent crop benefit. |
| Failed execution followed by retry | Native failure/retry episodes, unresolved failures and changed arguments | Accepted retry is separate from policy/yield recovery. Intention-to-treat classification remains unchanged. |
| A2A configured but never invoked | Matched baselines report observed delegation separately from assignment | Retain nondelegating rows; do not selectively rerun them. Missing evidence remains unknown. |
| Wet-June delay matters mainly when a window is missed | Acceptance/errors, fault timing, provenance and retry diagnostics | Reproduce the window-miss → failure → absent-recovery chain across paired worlds before a yield claim. |
| Disease–Drought irrigation lacks useful benefit | Isolated candidate, paired calibration, complete per-ridge stress checks, verified release report | **Pending:** execute calibration, review it, promote or reject the candidate and freeze the released scenario. No improved-yield claim is supported yet. |
| Three-cultivar irrigation is weak; disease/scope errors are stronger | Regional fidelity, exact actions and wrong-region fixtures | **Pending:** multi-world treatment/scope ablations. Do not call irrigation strongly outcome-sensitive from the review alone. |
| Minimal ontology; graph recall is future work | Public vocabulary/cards and existing scoped fact/evidence/guard objects | No general knowledge graph, ontology reasoner or graph-recall contribution is claimed. |

## Corrections from this confirmation pass

1. Local knowledge selection now constrains region and decision time. An
   unrelated region's newer measurement cannot displace valid regional evidence.
   Policy commitments also honor the frozen maximum evidence age.
2. v5 reconstruction previously prioritized learning time despite the corrected
   runtime. It now prioritizes observation time. Pre-action policy reconstruction
   uses the declared scope; the subsequent action cannot change its earlier
   policy verdict. Action scope is independently evaluated.
3. Generator-valued requirements are materialized once, preserving deadline
   checks after the guard iterates over them.
4. Native retry diagnostics distinguish accepted retries of the same
   actor/tool/region from unresolved failures and unrelated successful actions.
   They require a native receipt and do not change EF/CC weights or the
   intention-to-treat denominator.
5. Drought release checks read the saved report, verify SHA-256, recompute
   acceptance, require complete finite per-ridge stress evidence, reject
   candidate-only reports, and check the actual selected world. Doctor and
   handoff verify the report too; handoff copies it and resolves its path.
   Expert judgment remains necessary after mechanical checks.
6. A2A baseline rows record observed delegation from exported logs, without
   excluding inconvenient outcomes.
7. Sensor evidence uses the returned coverage. A single S3 probe no longer
   becomes whole-field evidence; missing or disjoint coverage stays unknown.
   A soil probe's installation ridge no longer creates a crop-maturity fact.
8. Forecast spray suitability has its own fact key and cannot overwrite
   evidence about the current spray window. The legacy oracle's three-day
   forecast plan uses this knowledge key; its world guard still checks the
   actual weather at execution. The predeclared policy templates and their
   controlled fixtures use the same distinction. Native-yield equivalence and
   v4/v5 oracle policy conformance remain required.
   `world_fact_key` explicitly declares the actual-weather comparator used for
   global evaluation. Issued forecasts are historical prediction records with
   bounded validity; they are not authoritative observations of future weather.
   A test verifies that forecast-supported action can still be globally false.
9. Paired calibration synchronizes lazy physics to the intervention time
   before recording root-zone moisture in both arms. This prevents stale
   pre-intervention stress measurements.
10. Calibration injects an event-driven clock through the native runner.
    Wall-clock overhead cannot change simulation timestamps in the paired arms.
    The clock mode is bound into the saved plan and required by release checks;
    ordinary native runs retain their existing clock. A short event-queue test
    verifies timing, pause/resume and reset without running a calibration season.

Reevaluate old traces under the corrected evaluator and freeze a new release
before final experiments. Do not mix these outputs with previous evaluator
results. `VALIDATION_REPORT.md` records the tested source hashes and suite result.

## Conference fit and submission preparation

Assumption: the target is the upcoming AAMAS 2027 main track. Its criteria
include soundness, reproducibility, originality and relevance. My assessment:
present D-CORE as evaluation and diagnosis of distributed agents. GAAI is a
plausible area if generative-agent evaluation is central; EMAS fits a general
MAS-analysis contribution. This is a fit assessment, not novelty certification.
[Official main-track call](https://warwick.ac.uk/fac/sci/dcs/aamas2027/calls/call-for-main-track/)

For AAMAS 2027, OpenReview author registration is due **17 September 2026**,
abstracts **1 October**, and papers **8 October**, at end of day Anywhere on
Earth. Confirm the intended edition before relying on these dates.
[Official dates](https://warwick.ac.uk/fac/sci/dcs/aamas2027/calls/call-for-main-track/)

The main paper has eight pages plus references and uses the required LaTeX
format. Supplementary material is one anonymous ZIP of at most 25 MB. Essential
definitions and evidence belong in the paper: reviewers need not consult the
supplement. The working repository is not certified as an anonymous submission
bundle. Host metadata is removed from the saved local JUnit copy; retain
third-party licenses when preparing the final package.
[Submission instructions](https://warwick.ac.uk/fac/sci/dcs/aamas2027/guidelines-and-policies/instructions/)

The policy permits AI-assisted code development and requires details for
AI-assisted methodology or experimental design. This work included a candidate
weather schedule and engineering screening thresholds: document that assistance
if adopted, using the actual tool/model/version and relevant prompts from the
session export. Do not invent an exact model snapshot. Authors must review and
take responsibility for the methodology.
[AI-assistance policy](https://warwick.ac.uk/fac/sci/dcs/aamas2027/guidelines-and-policies/instructions/)

## Confirmation boundary

Every PDF item has an implementation or an explicit outstanding evidence
requirement above. Offline checks establish behavior on tested cases. They
cannot establish empirical advantages, agronomic calibration, sufficient
novelty, or acceptance. Remaining work is reviewed calibration and experiments,
domain attestations, release freeze, final analysis and the submission package.
