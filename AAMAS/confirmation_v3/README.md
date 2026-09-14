# Native confirmation v3 — failed; not a release

The exact water-balance candidate and freshness alternative were committed in
`80e3b4d` before native runs on previously unused worlds 40–44. The source and
protocol hashes stayed unchanged. All ten seasons completed, but only three
pairs met the unchanged marketable-yield criterion. Both failed pairs remain
included. This supersedes no historical result and enables no paper experiment.

Raw results: `results/aamas_handover/drought_water_balance_v5_confirmation40to44/`.
Compact report: `AAMAS/handover_validation/drought_water_balance_v5_confirmation.json`.
Regenerate diagnosis from the repository root:

```sh
python AAMAS/analyze_drought_confirmation.py \
  results/aamas_handover/drought_water_balance_v5_confirmation40to44 \
  --output-dir AAMAS/handover_validation/water_balance_v5_confirmation
```

Worlds 34–35 are only reserved for future live smoke. They have not been run.
Worlds 40–44 are consumed. Do not rerun unchanged settings on another cohort
to search for success. Subsequent executable changes invalidate this source
binding for release purposes but do not erase this failed confirmation.
