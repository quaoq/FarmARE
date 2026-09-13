# Engineering follow-up plan, 2026-09-13

Written before executing the follow-up simulations or model calls. This is a
local prospective engineering record, not an externally registered study.
The original pilot remains unchanged in `../pilot_20260913/`.

## Measurement correction

Distinguish a guard recommendation from physical prevention. Prevention requires
enforcement mode and matching pre-FarmARE blocked/deferred action records with no
native event ID. Determine proposal validity separately from execution status:
check the global policy, arguments, scope and time against the draft process.
Missing world evidence or unmatched acceptance constraints are unassessable.
This does not certify complete counterfactual workflow or agronomic safety.
Report assessed and unassessable blocks separately. Re-evaluate all three saved
scripted traces, retaining their original outputs and comparing EF/CC unchanged.

## Drought diagnosis

Use the original drought forcing and irrigation dose. Test only a bounded rain
retry policy: on the native rain rejection, wait one simulated day, advance the
native physics and retry; allow at most seven extra days per season. Preserve
every attempted harvest and native failure. Do not retry other errors.

Run both irrigation arms on development worlds 0–9, then held-out worlds 10–14
with identical settings and no intervening tuning (30 seasons total). Retain the
existing minimum 1% yield shortfall and 50% target-ridge stress criteria. This
modified workflow cannot validate the unchanged released scenario. Improved
harvest completion alone is not evidence of irrigation sensitivity. If stress
remains absent, keep drought outside the paper's released experiment cohort
until independent agronomic review and a separately frozen calibration succeed.

## Real-LLM integration pilot

Use `gpt-5.4-mini-2026-03-17`, OpenAI JSON mode, `farm_baseline_react`,
temperature 0, history window 8, the existing two-agent Wet-June team and draft
v5 process. The engineering marker is compulsory and paper aggregation and
natural-study sampling reject these traces.

First run a connectivity/schema check with 12 team calls (6 per actor), 100,000
recorded team tokens (50,000 per actor), and 1,024 output tokens per call. Stop
and preserve failures if authentication, API compatibility or schema errors
prevent integration; document any repair before a new attempt.

After successful integration, run eight conditions: worlds 0 and 1 crossed with
local causal/audit/no fault, local causal/audit/outage, local causal/enforce/outage,
and local free-text/off/outage. Hold scheduler, model and fault seeds at 0 and
repeat index at 0. An outage drops **all handoff messages** via the existing
`prefix:handoff:` target, equally across outage conditions. This is not a single
dropped-warning treatment. If no messages are sent, retain the inactive fault.

Each run permits 128 team calls (64 per actor), 1,000,000 recorded team tokens
(500,000 per actor), and 1,024 output tokens per call. Token accounting stops
before the next call and may overshoot by the final response for each actor;
it is not a provider-enforced dollar cap. Run sequentially and retain all eight
assignments, including failures and budget truncations. Incomplete harvests do
not produce final-yield zeroes. This small budget primarily tests integration;
do not assume it can complete a season or supports inferential comparisons.

Official model pricing checked on this date: $0.75/M input tokens, $0.075/M
cached input tokens and $4.50/M output tokens. Report recorded usage and an
uncached price estimate separately from provider billing. Source:
[OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

## Review and reporting

Export neutral independent process-review packets and document the external
review tasks. Do not fill in reviewer identities, verdicts or confirmations.
Produce per-run CSV/LaTeX tables, source hashes and an execution amendment log.
Report model-call usage, completion, native errors, communication activity,
fault activation, diagnostic coverage and corrected guard metrics. No human
diagnostic accuracy is available until independent annotation is completed.
