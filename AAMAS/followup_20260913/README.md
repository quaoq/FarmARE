# Engineering follow-up: results and research decisions

The bounded follow-up is complete. It fixes measurement and API integration,
executes real-agent pilots, and identifies what must improve before the paper
study. **It provides no evidence yet for diagnostic accuracy or enforcement
benefit.** All observations remain excluded from paper-mode aggregation and
the final natural-behavior annotation cohort.

## What ran

- Thirty no-model drought seasons: paired irrigation control/omission in ten
  development worlds and five held-out worlds, using the prospectively recorded
  bounded rain-harvest retry policy.
- A successful eight-call OpenAI integration check after three preserved failed
  attempts (network, regional endpoint, then an incompatible token parameter).
- Eight matched Wet-June engineering runs at commit `1ae54ee`: two worlds crossed
  with four conditions. All eight assignments are retained. The exogenous-world
  digest agrees across all four conditions within each world.
- Re-analysis of the three original scripted traces with corrected guard
  accounting. Original exports remain unchanged; EF and CC are unchanged.

The model matrix used **157 recorded calls, 3,896,820 prompt tokens and 12,417
completion tokens**. Including the successful connectivity check, recorded usage
is 165 calls and 4,050,780 tokens. The estimate at
[OpenAI's uncached list prices](https://developers.openai.com/api/docs/models/gpt-5.4-mini) is about
**$3.09**, including connectivity, not a provider invoice. Cached-input discounts,
unreported failed-request usage and billing adjustments are not reconstructed.
The pinned model, endpoint, caps and price source are in [PLAN.md](PLAN.md) and
the manifests; [AMENDMENTS.md](AMENDMENTS.md) preserves the integration repairs.

## Findings that determine the next study

| Measure | Result | Interpretation |
| --- | --- | --- |
| Final drought yield pairs after retry | 10/10 development; 5/5 held-out | Bounded rain handling resolves the observed incomplete harvests |
| Drought calibration passes | 0/10 development; 0/5 held-out | Harvest completion does not establish irrigation sensitivity |
| Target root-zone stress criterion | 0/15 worlds | Keep drought outside the released paper cohort pending review/calibration |
| LLM runs with completed harvest | 0/8 | Final yield is unavailable, not zero |
| LLM required-event coverage | 2.20–8.07% | The current small-budget agents remain early in the workflow |
| Active communication outages | 6/6 assigned outages | The fault mechanism activates on real handoffs |
| Information-policy diagnostic decisions | 0 across all eight runs | These traces cannot test the paper's central diagnostic claim |
| Runs reaching an actor token cap | 6/8 | Team-only budget flags hid six actor-level cap hits |
| Physical blocks in LLM runs | 3, all outside policy coverage | All three remain unassessable for benefit/false-block classification |

Mean omission shortfall is −0.00894% in development and −0.01243% held-out;
negative values mean slightly greater yield under omission. All pairs now have
final yield, but there is still no meaningful positive irrigation effect under
the original dose and forcing. The thresholds were not changed. This exploratory
workflow cannot certify the unchanged released drought scenario.

Ten native action-error receipts occur in the model matrix: preparation-order,
equipment, fertilizer-stock and soil-readiness errors. One further controller
error requests a tool outside the operations role. Five runs set the existing
`controller_failure` flag, which also includes native action errors; this is
not a count of five API or infrastructure crashes. Other agents finish early
or reach their allocations. Token-cap attainment is reconstructed from recorded
actor usage, not inferred from aggregate yield or a claim about agent intent.
The largest individual token overshoot is 44,947; calls stop before the next
request, as declared prospectively.

For the saved scripted traces, the free-text/off condition's old **22 false
blocks become zero physical blocks**. The enforcement condition has **three
assessed false blocks**, under the explicitly bounded pre-execution policy,
argument, scope and timing checks. Three R5 proposals in each scripted trace
remain unassessable because draft runtime phase hints conflict. These are
engineering-specification diagnostics, not independently confirmed agronomic
safety judgments or an isolated yield-effect estimate.

## What the paper needs next

1. **Establish useful long-horizon agent coverage.** Inspect prompt growth,
   tool-schema errors, premature role completion and budget allocation before
   buying a larger matrix. Start with a prospectively defined no-fault pilot
   that can reach the target disease/recheck decisions. A higher call cap alone
   does not address the actor token cap or premature completion. Preserve role
   isolation and avoid exposing the oracle solution to improve scores.
2. **Obtain independent process, team and simulator review.** The neutral
   [review packets](../review_packets/README.md) are prepared. Reviewers must
   define missing scientific fields and freeze defensible alternatives. The
   drought effect remains an unresolved domain/modeling question; changing its
   threshold or selecting favorable worlds would not resolve it.
3. **Then freeze and run the paper study.** Use the existing paired conditions
   and [independent diagnostic validation plan](../PAPER_FOUNDATION.md), retain
   failures, compare against the same-evidence flat baseline and report component
   and specification sensitivity. Do not spend annotation effort estimating
   diagnostic accuracy from a cohort with zero eligible policy decisions.

The central paper case should remain independently validated diagnostics of
distributed information failures. Yield is separate downstream evidence. This
pilot does not justify adding models, scenarios, methods or stronger claims.

## Artifacts and checks

- [Compact per-run table](llm_summary.csv) and [LaTeX](llm_summary.tex).
- [Full model accounting](llm_runs.csv), including raw and corrected budget
  flags, failure state, coverage and unknown final yields.
- [Connectivity attempts](connectivity.csv) and [native errors](native_errors.csv).
- [Calibration summary](calibration_summary.csv), [pairs](calibration_pairs.csv)
  and [corrected scripted guards](corrected_guard_metrics.csv).
- [Source manifest](analysis_manifest.json) and [test record](validation_summary.json).

The broad regression suite passed 229 tests. Subsequent focused suites cover
the adapter, team allocation, calibration, phase uncertainty and final reporting
changes; **256 distinct test cases passed across these overlapping runs**.
This is not a claim that the whole repository test suite was run on the final
revision. Ruff and whitespace checks pass. LaTeX fragments were generated but
not compiled because a LaTeX installation is unavailable.

Raw follow-up exports occupy about 207 MB under
`results/aamas_followup_20260913/`; they are retained locally and hash-indexed,
not committed. Preserve that directory and the original pilot directory when
moving the study to another machine. Git alone does not contain the raw data.
No reviewer identity, annotation or release approval has been fabricated.

Rebuild all tables without model calls from the repository root:

```bash
.venv/bin/python AAMAS/analyze_followup_20260913.py
```

Re-run manifests only into a fresh output directory to preserve this pilot.
Load `.env` with Python's `dotenv.load_dotenv`, then invoke `are-dcore matrix`
with `llm_connectivity.yaml` or `llm_pilot.yaml`. Both manifests explicitly mark
engineering mode. Model seed 0 is recorded but not applied by the provider;
temperature 0 does not make independent API calls deterministically replayable.
