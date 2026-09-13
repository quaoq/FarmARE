# Frozen professor-handover confirmation design

These are engineering/calibration checks, excluded from the professor's paper
observations. The author-defined specifications await genuine professor review.
`FREEZE.json` binds the exact specifications, sensitivity alternatives, protocol,
execution source, generation settings and untouched cohorts.

- Calibration: all five worlds 20–24, selected drought pulse and authored harvest
  calendar, unchanged 50% stress / 1% omission-loss criteria in every pair.
- No-fault live progression: all three scenarios on both worlds 30–31; 700 model
  calls, 24M team tokens, 4096 maximum output tokens, exactly as already declared
  for the main study. Both roles receive 350 calls and 12M tokens.
- Matched communication smoke: Wet-June, both worlds, causal audit, causal
  enforcement and free-text audit, with the frozen drop selector and 200 calls
  per run. Launch only after all no-fault progression gates pass. This bounded
  comparison checks recording and diagnostic coverage, not favorable effects.

Every request uses the shared ledger and the $60 confirmation allocation.
Reservations assume no cache hit; settlement uses only valid provider-reported
cached usage. Unknown usage remains reserved. Do not erase an inactive fault,
failed/partial outcome, or unassessable diagnosis. A source/scientific revision
after confirmation requires a newly declared unused confirmation cohort.

From the repository root, after final offline/compatibility checks pass:

```sh
are-dcore calibrate-scenario --candidate --scenario-revision drought_pulse_v4 \
  --world-seed 20 --world-seed 21 --world-seed 22 --world-seed 23 --world-seed 24 \
  --harvest-policy rain_maturity_moisture \
  --confirmation-manifest AAMAS/confirmation_v1/drought_binding.json \
  --reference-process AAMAS/authored_specifications/farm_disease_drought.process.json \
  --output-dir results/aamas_handover/drought_confirmation_v1
are-dcore matrix AAMAS/confirmation_v1/live_no_fault.yaml \
  --output-dir results/aamas_handover/live_no_fault_confirmation_v1 --dry-run
```

For six independent workers, use shard-count 6 and shard-index 0 through 5, each
with a separate output directory. All workers share the one frozen manifest
and spending ledger. Preserve incomplete attempts; never blindly replay their
native writes. This directory is a prospective design, not a passed-gate certificate.
