# Pending study tables

Rebuild from the repository root with:

```sh
python AAMAS/manuscript/build_tables.py
```

`study_status` resolves the four actual study manifests (1,245 assignments).
`pending_results/` runs the ordinary six-table/five-figure reporting pipeline
with an empty JSONL source. Its analysis manifest must report zero source rows;
empty panels and zero denominators are not empirical estimates. No pilot or
reviewer values populate the paper result cells. The empty source is intentional.

For the final paper, use the same reporting function on approved study outputs
and independent annotation results. Generated CSVs retain metric availability,
missing counts and world-cluster denominators. Select readable panels for the
main paper; do not hide denominators when composing publication tables.
