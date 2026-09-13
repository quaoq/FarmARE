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
| Actual A2A delegation versus condition assignment | `matched_baselines.py` reports observed delegation independently of assigned mode | `test_confirmation_audit.py::test_a2a_assignment_is_not_evidence_that_delegation_occurred` | `baseline_compatibility_v5/results.jsonl`; `test_live_compatibility_repairs.py`; `test_baseline_request_contract.py` | Actual study-provider checks completed: direct made 12 requests with no delegation; A2A made nine requests with two completed delegations. Structured-answer crashes and generation mismatches are repaired; rejected proposals remain recorded. No seasonal benefit is established. |
| Diagnostic decomposition versus one scalar | `evaluator_v5.py`, `diagnostic_validation.py`, `validation_study.py`; EF/CC/IGD/local-global/provenance/coverage profiles | Frozen controlled cases in `test_farm_dcore_v5.py`; same-evidence and annotation tests | Saved controlled fixtures; pending manuscript tables | Independent 60-episode annotation and study estimates are pending. |
| Provenance localization versus physical causation | Trace-based earliest recorded break, explicit unknown cases; separate paired physical interventions | Positive/negative scope/expiry/provenance cases in v5 tests | Manuscript formal definitions and limitations | Localization alone cannot support a yield-causation claim. |
| Wet-June timing/window/recovery effects | Native action acceptance, execution errors and retry episodes preserved; separate forecast/current weather | `test_legacy_forecast_plan_keeps_execution_weather_independent`; `test_native_interventions.py`; fixed-workflow omission/delay/recovery arms | `native_interventions_v1/wetjune/intervention_report.json`; `native_interventions_window_v2/intervention_report.json`; compact `native_interventions.csv` | Three-day delay caused no rejection. A declared two-day development delay caused rain rejection; a one-day wait allowed accepted recovery and completed harvest, at 3.9878% lower yield than reference. No-recovery outcome is incomplete. Matched live and confirmation coverage remain pending. |
| Disease–Drought sensitivity | Synchronized, time-resolved hydrology instrumentation; separately named soil/workflow development variants; unchanged release screening | Calibration integrity tests; development paired seasons | `hydrology_original`, `hydrology_candidate_v1`, `drought_rootzone_v2_dev0`, `drought_rootzone_v3_dev0`, `drought_maturity_v2_dev0`, `drought_moisture_v3_dev0` | Calendar-reference pulse candidate passes 10/10 development worlds with complete pairs, 100% target stress and >=1% omission loss. Fresh confirmation passed 4/5: world 20 failed with 0.30285% omission loss despite complete pairs and accepted irrigation. See `drought_confirmation_v1/calibration_report.json`; release is blocked. |
| Three-cultivar treatment and scope effects | Native region/tool fidelity; corrected phase classification so scenario slug does not turn preparation into harvest | `test_handover_integrity.py::test_three_cultivar_slug_does_not_turn_preparation_into_harvest`; `test_native_interventions.py`; completed fungicide/irrigation/swapped-scope pairs | `native_interventions_v1/three_cultivar/intervention_report.json`; compact `native_interventions.csv` | World-0 replication is exploratory; the swap changes 22 versus 21 ridges as well as scope. Authored specification exists; final live smoke and approval remain pending. |
| Minimal ontology with source, scope, time, validity, confidence and version | Grant cards plus observations, facts, receipts and provenance; confidence is optional and bounded to [0,1], with unknown retained; no new knowledge-graph subsystem | Capability/scope/freshness/receipt cases above | `ontology.py` and saved trace schemas | No ontology-reasoning or graph-recall contribution is claimed. |
| Real-agent prompt usability and honest accounting | Current snapshot deduplication, scoped-fact priority, omitted IDs, recent failures, bounded corrective retries, provider-level metering and SQLite cost reservations | `test_persistent_pilot_budget.py`, `test_handover_integrity.py`; **task:** final no-fault progression pilot | Retained `development_v*` folders and spending ledger | The first three pilots stopped before high-impact decisions; inspect the current CSV for subsequent attempts. Later repairs do not retroactively validate them. |
| Equivalent fault assignment and inactive ITT rows | Frozen phase/route/send-order selectors independent of message names/representation; inactive assignments retained | Selector integration cases in `test_handover_integrity.py`; inactive aggregation case in v5 tests | Deterministic fault fixtures and regression XML | Live activation, nonactivation, recovery and failure coverage remain pending. |
| Reproducibility and honest human review | Source/config-bound run identity; refuse duplicate and uncertain interrupted runs; central author/professor versus independent-review rule | Resume/mismatch and attestation tampering cases in handover-integrity tests | Source hashes, regression XML, `REVIEW_ROUTE.md` | Authored specifications are frozen; failed scientific/progression gates, genuine sign-off and verified packaging remain outstanding. |

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
observations. Disease–Drought original-scenario replication is now documented below;
drought calibration failures do not establish irrigation benefit for the candidate.

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
closes the development accounting task, not the drought scientific gate. That historical checkpoint preceded confirmation. The subsequent frozen
20–24 cohort failed the all-pairs gate; see the current table above. Complete saved-source reports and all
failed predecessors remain in the raw-artifact archive.

The component-table audit additionally exposed two v5 denominator defects:
decision-time guards were being evaluated against entry-branch context during
unfolding, and selected branch transitions were omitted from required-event
counts. The corrected evaluator retains those actions and evaluates their
prerequisites at decision/execution time; conditional requiredness now affects
both matching priority and event denominators. A closed-entry-weather omission
fixture must report a nonempty disease denominator and zero fidelity. Earlier
metrics remain preserved, including their omitted denominators. Their complete
native harvests do not validate those earlier diagnostic scores.

The author contract also now keeps the policy's fixed management-region evidence
scope on its batch actions. Exact native application scope is checked separately;
it must not redefine the policy after the action is chosen. This patch-treatment
assumption is explicit and tested. The primary and freshness-alternative digests
were regenerated before any confirmation; prior exact specifications remain
with the earlier native attempts.

### Release-wiring follow-up

`test_scenario_confirmation.py` covers exact candidate-dose acceptance, native
variant mismatch rejection, separate confirmation/study cohorts, unchanged
thresholds, failed-pair retention, and scientific gating of direct/A2A baselines.
Preflight target availability now uses treatment modes and actual frozen route
selectors, including the historical `delay` alias. Actual A2A delegation is now documented in the corrected bounded v3 baseline;
matched live fault/recovery evidence remains pending. See
`handover_development/RELEASE_WIRING_V1.md`.

## Live compatibility audit on 13 September

`test_live_compatibility_repairs.py` closes defects found by real API calls:
optional refined handoffs lacked acceptance predicates; direct/A2A exporters used
the incompatible lite schema; public task overrides were installed after the
briefing event had captured the detailed oracle text; flattened native exports
hid delegation from the group-only summary. Corrected v3 baseline exports contain
the same public task, and A2A has three completed expert calls. Historical v1/v2
baselines remain invalid for matched comparison, with original artifacts intact.

The same test file checks that a recovered failure is marked only by an accepted
retry of the same action and arguments. Original errors and times are retained.
Three-/four-agent v2 and nine controller-family bounded checks completed without
recorded controller/infrastructure failures. Three v6 progression attempts still
failed harvest/storage; see `handover_validation/live_development_v6.json`.
The old v5b evaluator failure lost its full trace; only launch/failure and ledger
evidence survive. The new failure-capture path was exercised by both original
refined-team failures and saves process, native/distributed traces and outcome.

## First held-out confirmation outcome

`handover_validation/confirmation_v1_failures.json` preserves all six pre-provider
live failures and the 4/5 drought result. The resolved-team runtime had retained
an 8M limit after configuration validation admitted the existing 24M study limit.
Team resolution now uses the validated allocation; regression tests exercise
actual two-, three- and four-agent resolution and reject overallocated actors.

`analyze_drought_confirmation.py` regenerates the paired diagnostic CSV and JSON
from saved native telemetry. World 20 accepted irrigation at R5 with 100% stressed
target ridges and a positive immediate root-moisture response. Both arms completed
harvest/storage on matched exogenous worlds; marketable yield was 6821.87 versus
6801.21 kg. The 0.30285% loss fails the unchanged 1% criterion. This is not an
implementation success or a missing-outcome exception. No physics, rewards or
thresholds were changed to make confirmation pass.

## Original Disease–Drought review comparison reproduced

`python AAMAS/replicate_drought_review.py --output-dir NEW_DIRECTORY` runs the
original native scenario on development world 0, using the deterministic queue
clock and no candidate weather, soil change, or added harvest recovery. It
preserves all successors and replaces only the assigned treatment batches with
recorded no-ops. The manifest and script hash are saved before execution.

All three seasons completed harvest/storage on the same exogenous world:

| Arm | Biological yield (kg) | Marketable yield (kg) | Marketable omission loss |
| --- | ---: | ---: | ---: |
| Original reference | 8881.177789 | 8037.56 | — |
| Midseason fungicide omitted (three native batches) | 8464.785355 | 7629.00 | 5.0831% |
| R5 irrigation omitted | 8882.188341 | 8038.99 | -0.01779% |

These reproduce the review's rounded original-scenario values under the saved
settings. The small irrigation reversal remains visible. They are development
interventions, not evidence that the revised drought candidate passed calibration
or that D-CORE improves yield. See `handover_validation/drought_review_replication.json`
and the separate immutable native traces under `drought_review_replication_v1/`.

The review also reports Wet-June delay losses of approximately 0.065% for
12–24 hours, 0.195% for three days, 0.279% for five days, and 2.52% after a
window-crossing rejection without recovery. Those precise delay figures remain
reviewer-provided: our documented delay shifts downstream operations as well,
and the no-recovery native reference attempt terminates without a complete
yield. It is not the same isolated-action timing intervention. The discrepancy
and incomplete outcome must remain visible; omission/recovery checks do not
establish numerical replication of that unpublished timing protocol.

The failed drought confirmation's immediate action snapshots additionally show
that every target ridge remains below its 0.18 root-VWC stress threshold after
the accepted pulse. World 20's mean rises from 0.15277 to 0.16140 m3/m3. This
checks units and native response without asserting a complete subdaily water
balance or attributing the entire final yield contrast to one mechanism.
