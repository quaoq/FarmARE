# Development record

These manifests are bounded engineering attempts, excluded from paper evidence.
Run only an explicitly selected manifest with `are-dcore matrix MANIFEST
--output-dir NEW_ATTEMPT_DIRECTORY`. Their persistent ledger is
`results/aamas_handover/spending.sqlite`, capped at $40 development, $60
confirmation/compatibility and $100 total. Unknown request usage retains its
reservation. Do not create a new ledger to reset spending.

Attempts v1–v4 use development world 0. Each records launch-source hashes,
identity, full traces, original outcomes and provider accounting. A later code
change does not validate an earlier attempt. All four failed harvest/storage;
v4 reached policy commitments after the prompt-history repair. Confirmation
worlds 20–24 and 30–31 remain unused. See `../HANDOVER_STATUS.md` for blockers.

The two intervention manifests contain explicit fixed native workflow contrasts
for Wet-June and Three-cultivar on world 0. `DROUGHT_REVISION_V2.md` prospectively
records the separately named drought soil and bounded-harvest variants. Native
rejections and incomplete paired outcomes remain in the raw data.

Regenerate compact evidence with:

```sh
python AAMAS/analyze_handover_development.py
```

The CSVs are development summaries. Missing final yields remain unavailable.
The four professor study manifests still contain deliberately unresolved
scientific-artifact paths; they are not executable paper releases yet.
