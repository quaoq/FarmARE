# First engineering pilot — 2026-09-13

Executed on commit `23d1def`: 40 drought calibration seasons and three Wet-June
scripted seasons. All outputs are engineering observations. No LLM calls or
independent human annotation were performed. Raw native exports, including all
failures, remain under `results/aamas_pilot_20260913/` in the repository workspace.
The analysis manifest binds every raw file by SHA-256; raw exports are retained
locally rather than committed as a 167 MB Git payload.

## Calibration findings

Neither the original nor the candidate passes the predeclared screening rule.
Every world has zero target ridges below the native root-zone stress threshold
at irrigation. Both variants lack final yield in seeds 3, 4, 8, and 9; their
native harvest receipts report rainy conditions. Those missing outcomes are
not final zero yields and are not silently included in a yield-effect average.
The original accepts irrigation in 6/10 worlds; the candidate accepts it in
10/10. Acceptance alone does not establish agronomic value.

| variant | world_pairs | final_yield_pairs | accepted_irrigations | stress_criterion_passes | calibration_passes | mean_omission_shortfall_pct_final_pairs_only |
| --- | --- | --- | --- | --- | --- | --- |
| drought-original | 10 | 6 | 6 | 0 | 0 | -0.013185 |
| drought-candidate | 10 | 6 | 10 | 0 | 0 | -0.022082 |

Negative shortfall means omission produced slightly more marketable yield.
The mean above is descriptive and conditional on the six final-yield pairs,
not an intention-to-treat estimate over ten worlds. The planned screening
required at least 1% omission loss in every pair and at least 50% target-ridge
stress; neither variant qualifies. Native action-presence validation returned
success even in incomplete-harvest worlds; the stricter calibration checks
correctly rejected them. The per-world and native-error CSVs retain all details.

## Scripted pipeline observations

| condition | worlds | event_fidelity | causal_conformance | marketable_yield_kg | yield_shortfall_pct_vs_scripted_reference | physical_blocks_or_deferrals | harvest_and_storage_complete |
| --- | --- | --- | --- | --- | --- | --- | --- |
| local_causal_enforce | 1 | 0.940885 | 0.964470 | 8648.470000 | 2.522576 | 3 | True |
| local_free_text | 1 | 0.974219 | 0.836364 | 8872.280000 | 0.000000 | 0 | True |
| scripted_petri_oracle | 1 | 1.000000 | 1.000000 | 8872.280000 | 0.000000 | 0 | True |

These three cells use one world and change multiple factors. The yield
shortfall is relative to the scripted reference, not an isolated causal effect
of enforcement. The free-text/drop cell has reference-level yield despite
lower causal conformance: this is an example of diagnostics and yield
disagreeing, not independent validation of the diagnosis. All three completed
harvest/storage; both assigned drop conditions recorded an active fault.

## Saved-trace component analysis

| condition | variant | event_fidelity | causal_conformance |
| --- | --- | --- | --- |
| local_causal_enforce | full | 0.940885 | 0.964470 |
| local_causal_enforce | no_expiry | 0.940885 | 0.964470 |
| local_causal_enforce | no_provenance | 0.940885 | 0.964470 |
| local_causal_enforce | serialization_order | 0.940885 | 0.964470 |
| local_free_text | full | 0.974219 | 0.836364 |
| local_free_text | no_expiry | 0.974219 | 0.836364 |
| local_free_text | no_provenance | 0.974219 | 0.836364 |
| local_free_text | serialization_order | 0.974219 | 0.836364 |
| scripted_petri_oracle | full | 1.000000 | 1.000000 |
| scripted_petri_oracle | no_expiry | 1.000000 | 1.000000 |
| scripted_petri_oracle | no_provenance | 1.000000 | 1.000000 |
| scripted_petri_oracle | serialization_order | 1.000000 | 1.000000 |

The component definitions were committed before these runs. They are evaluated
on identical saved inputs and show score dependence only, not accuracy against
human labels. The serialization variant changes only causal edge checks.
No reviewed specification alternatives are available, so this table cannot
support a specification-sensitivity claim.
At the evaluator's six-decimal precision, all four variants give identical
EF/CC values within each condition. These pilot traces therefore provide no
evidence of component necessity; the tiny extra serialization digits are
rounding differences, not an ablation effect.

## Reporting defect found by the pilot

The original v5 guard-effectiveness output counts a non-allow guard verdict as
physical prevention, even when enforcement is off/audit. It reports 22 false
blocks in the free-text/off cell. The pilot table instead counts actual
blocked/deferred action records with `blocked_before_farmare=true` and no native
event receipt. It does not label those interventions beneficial or false.

Further, `global_conforming` includes successful execution, so a prevented
action is not an adequate counterfactual basis for a false-block or safety-benefit
claim. Correct this using independent pre-execution proposal validity before
using those metrics in paper results. The original outputs remain intact;
the stock `scripted-report/table5_guard_mitigation` is quarantined from substantive
interpretation. No claim is made that this defect has been fixed in the evaluator.

## Next decision

Do not promote the drought candidate or launch the full paid matrix on this
evidence. Review the irrigation exposure and the oracle's handling of rainy
harvest windows, record any revised design prospectively, and rerun a separately
versioned calibration. Do not change thresholds to make these observations pass.

The OpenAI key is present, but `DistributedRunnerConfig.validate_modes` and
the native scientific gate require independently reviewed process/team files
and an `offline_complete` gate before the bounded LLM smoke. Those artifacts
were not found. Supplying credentials does not provide the missing reviews;
no review status or gate attestation has been fabricated.

Rebuild the tables with:

```bash
.venv/bin/python AAMAS/analyze_pilot_20260913.py \
  --results results/aamas_pilot_20260913 --output /tmp/rebuilt-aamas-pilot
```
