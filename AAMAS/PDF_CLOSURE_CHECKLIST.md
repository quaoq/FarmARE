# PDF review closure checklist

This is an author-side implementation audit of `AAMAS_DCORE_REVIEW.pdf`, not an
independent validation or a conference acceptance judgment. “Implemented” does
not mean a live scientific gate passed. Final-revision regression evidence is
kept separately from earlier tests and failed development attempts.

Test paths below are relative to `are/simulation/tests/distributed/`.
Implementation paths are relative to `are/simulation/distributed/` unless
otherwise stated. Saved local runs are below `results/aamas_handover/`; compact
summaries are in `handover_validation/`. Old pilot/follow-up artifacts are intact.

| Recommendation | Implementation | Test or validation task | Evidence artifact | Remaining empirical limitation |
| --- | --- | --- | --- | --- |
| Teammate capabilities and ownership boundaries | `ontology.py`, `teams.py`, role gateway; grant-derived cards include legal routes and time authority | `test_review_hardening.py::test_capabilities_match_actual_grants_and_legal_routes`; duplicate/authorization gateway cases | Final regression XML; cards and team digests in traces | No measured improvement in autonomous coordination. |
| Refusal, legal rerouting, recovery without unrelated substitution | Common coordination contract, exact tool/argument capture, blocked-write nonmutation, native retry diagnostics | `test_confirmation_audit.py::test_native_retries_require_same_actor_region_and_native_receipt`; gateway/guard suite | Native rejection and receipt records in retained development pilots | Agents still fail progression; prompt instructions do not prove recovery. |
| Opaque sensor IDs; inclusive ranges and regional evidence | `farm_adapter.py` uses returned contiguous coverage; scoped fact/message frontiers | `test_confirmation_audit.py::test_sensor_evidence_uses_actual_contiguous_coverage`; `test_handoff_preserves_multiple_regions` in review-hardening tests | Regression XML; scoped facts in immutable traces | Live wrong-region rates remain unmeasured; exploratory Three-cultivar treatment/scope pairs are recorded below. |
| Observation-time freshness, expiry, supersession and reordered delivery | `knowledge.py`/guard/evaluator reconstruction prioritize observation time; forecast separated from current weather | Observation/future-evidence/closed-deadline tests in `test_confirmation_audit.py`; receive-order test in review hardening | Controlled fixtures and regression XML | Matched live late-delivery/recovery evidence is pending. |
| Request-bound native receipts; failure and accepted retry | Gateway binds intent, actor, exact arguments, native event and result digest; `recovery.py` checks actor/tool/scope | Gateway duplicate request test; `test_native_retries_track_accepted_repairs_and_unresolved_failures` | Pilot native error lists and native trace receipts | A receipt proves accepted execution, not crop benefit; uncertain interrupted writes are not replayed. |
| Actual A2A delegation versus condition assignment | `matched_baselines.py` reports observed delegation independently of assigned mode | `test_confirmation_audit.py::test_a2a_assignment_is_not_evidence_that_delegation_occurred` | Offline fixtures | New bounded live A2A and controller compatibility checks remain pending. |
| Diagnostic decomposition versus one scalar | `evaluator_v5.py`, `diagnostic_validation.py`, `validation_study.py`; EF/CC/IGD/local-global/provenance/coverage profiles | Frozen controlled cases in `test_farm_dcore_v5.py`; same-evidence and annotation tests | Saved controlled fixtures; pending manuscript tables | Independent 60-episode annotation and study estimates are pending. |
| Provenance localization versus physical causation | Trace-based earliest recorded break, explicit unknown cases; separate paired physical interventions | Positive/negative scope/expiry/provenance cases in v5 tests | Manuscript formal definitions and limitations | Localization alone cannot support a yield-causation claim. |
| Wet-June timing/window/recovery effects | Native action acceptance, execution errors and retry episodes preserved; separate forecast/current weather | `test_legacy_forecast_plan_keeps_execution_weather_independent`; `test_native_interventions.py`; fixed-workflow omission/delay/recovery arms | `native_interventions_v1/wetjune/intervention_report.json`; `native_interventions_window_v2/intervention_report.json`; compact `native_interventions.csv` | Three-day delay caused no rejection. A declared two-day development delay caused rain rejection; a one-day wait allowed accepted recovery and completed harvest, at 3.9878% lower yield than reference. No-recovery outcome is incomplete. Matched live and confirmation coverage remain pending. |
| Disease–Drought sensitivity | Synchronized, time-resolved hydrology instrumentation; separately named soil/workflow development variants; unchanged release screening | Calibration integrity tests; development paired seasons | `hydrology_original`, `hydrology_candidate_v1`, `drought_rootzone_v2_dev0`, `drought_rootzone_v3_dev0`, `drought_maturity_v2_dev0`, `drought_moisture_v3_dev0` | **Blocked:** no candidate has passed complete outcomes and >=1% omission loss. Worlds 20–24 remain unused. |
| Three-cultivar treatment and scope effects | Native region/tool fidelity; corrected phase classification so scenario slug does not turn preparation into harvest | `test_handover_integrity.py::test_three_cultivar_slug_does_not_turn_preparation_into_harvest`; `test_native_interventions.py`; completed fungicide/irrigation/swapped-scope pairs | `native_interventions_v1/three_cultivar/intervention_report.json`; compact `native_interventions.csv` | World-0 replication is exploratory; the swap changes 22 versus 21 ridges as well as scope. Full specification and live smoke remain pending. |
| Minimal ontology with source, scope, time, validity and version | Grant cards plus existing observations, facts, receipts and provenance; no new knowledge-graph subsystem | Capability/scope/freshness/receipt cases above | `ontology.py` and saved trace schemas | No ontology-reasoning or graph-recall contribution is claimed. |
| Real-agent prompt usability and honest accounting | Current snapshot deduplication, scoped-fact priority, omitted IDs, recent failures, bounded corrective retries, provider-level metering and SQLite cost reservations | `test_persistent_pilot_budget.py`, `test_handover_integrity.py`; **task:** final no-fault progression pilot | Retained `development_v*` folders and spending ledger | The first three pilots stopped before high-impact decisions; inspect the current CSV for subsequent attempts. Later repairs do not retroactively validate them. |
| Equivalent fault assignment and inactive ITT rows | Frozen phase/route/send-order selectors independent of message names/representation; inactive assignments retained | Selector integration cases in `test_handover_integrity.py`; inactive aggregation case in v5 tests | Deterministic fault fixtures and regression XML | Live activation, nonactivation, recovery and failure coverage remain pending. |
| Reproducibility and honest human review | Source/config-bound run identity; refuse duplicate and uncertain interrupted runs; central author/professor versus independent-review rule | Resume/mismatch and attestation tampering cases in handover-integrity tests | Source hashes, regression XML, `REVIEW_ROUTE.md` | Complete authored frozen specifications, genuine sign-off and a verified review/release package are still required. |

## Numerical claims in the supplied review

These values are reviewer-provided evidence. Exact seeds/settings were not
supplied in the review and numerical agreement must not be forced.

| Scenario | Reviewer comparison | Reviewer marketable yield / change |
| --- | --- | --- |
| Wet-June | Reference; first fungicide omitted; R5 fungicide omitted | 8872.28 kg; 8647.97 kg (-2.53%); 8758.89 kg (-1.28%) |
| Disease–Drought | Reference; fungicide omitted; irrigation omitted | 8037.56 kg; 7629 kg (-5.08%); 8038.99 kg (+0.02%) |
| Three-cultivar | Reference; B fungicide omitted; C irrigation omitted; swapped scope | 7317.74 kg; 6914.61 kg (-5.51%); 7297.65 kg (-0.27%); 6876.06 kg (-6.04%) |

Earlier saved pilots and current drought development results have different
workflows/settings. They are not substituted for documented replication of
these comparisons. Partial harvest amounts are not final yield or valid
omission-loss evidence. Weak, null and negative effects remain reportable.

## Documented native development comparisons

New fixed-workflow checks use native world 0 and explicit manifests in
`handover_development/interventions/`. Nine seasons completed harvest/storage;
seven paired contrasts share verified exogenous-world digests. Regenerate the
compact table with `python AAMAS/analyze_handover_development.py`.

| Scenario / intervention | New final yield (kg) | Reference (kg) | Omission or treatment loss | Discrepancy / qualification |
| --- | ---: | ---: | ---: | --- |
| Wet-June / omit midseason fungicide | 8648.47 | 8872.28 | 2.5226% | 0.50 kg above reviewer value; exact reviewer settings unavailable. |
| Wet-June / omit R5 fungicide | 8759.41 | 8872.28 | 1.2722% | 0.52 kg above reviewer value. |
| Wet-June / delay midseason group three days | 8518.47 | 8872.28 | 3.9878% | Later workflow also shifts; no native spray rejection occurred. |
| Wet-June / same delay with bounded recovery | 8518.47 | 8872.28 | 3.9878% | Zero recovery waits: no demonstrated recovery mechanism. |
| Three-cultivar / omit B fungicide | 6914.61 | 7317.74 | 5.5089% | Agrees with supplied rounded yield. |
| Three-cultivar / omit C irrigation | 7297.65 | 7317.74 | 0.2745% | Weak effect; agrees with supplied rounded yield. |
| Three-cultivar / swap treatment regions | 6876.03 | 7317.74 | 6.0361% | 0.03 kg below reviewer value; scope and treatment area both change. |

These are development diagnostics, not independent confirmation or paper study
observations. Disease–Drought fungicide replication remains a separate empirical
task; drought calibration failures do not establish its claimed irrigation benefit.

## Final reporting and portability repairs

The final audit also found that report-table projection discarded metric
availability counts and that primary paired contrasts imputed missing scores as
zero. Reporting now retains denominators; primary paired outcomes remain
unavailable when either score is missing, with missing-pair counts. A separately
named missing-as-zero sensitivity is not presented as an observed score.
Single-world intervals are unavailable; paired p-values use the protocol's
explicit cluster sign-flip null and finite-sampling correction. Controlled
regressions cover these behaviors. Run identity ignores unshipped Python files
inside local JavaScript `node_modules`, while retaining executable source and
lock changes. These are engineering corrections, not new paper results.

## Authored-contract closure evidence

`test_authored_specs.py` validates all three author-defined contracts and tests
all eight fault treatments across causal/free-text representations, including
sends later than the start of a phase. Validity comparison uses real selected
evidence expiry; missing expiry is explicitly unassessable. The mixed treatment
requires three actual sends and never manufactures them.

`test_snapshot_provenance.py` distinguishes identical repeated snapshots from
actual same-region changes, verifies request-bound observation origins for
opaque sensor reads, and rejects using a whole-field aggregate to prove an exact
regional guard. Hidden root facts remain evaluator-only. The 13-artifact
inventory includes three prespecified stricter freshness alternatives.

The combined drought candidate failed 0/5 development pairs. The separately
declared 25 mm pulse candidate passed 4/5; world 0 stayed at 21.711% grain moisture
after the unchanged 21-day cap and both harvest outcomes are incomplete. This
closes the development accounting task, not the drought scientific gate. No
confirmation worlds have been consumed. Complete saved-source reports and all
failed predecessors remain in the raw-artifact archive.
