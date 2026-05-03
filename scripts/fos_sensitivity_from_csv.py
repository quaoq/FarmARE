"""
Sensitivity analysis from validation-runner results.csv (no structured JSON
needed). Re-weights O/D/E from each cell's rationale-derived components and
emits a per-(family, scenario, weight-cell) CSV plus rank-stability summary.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input-csv", type=Path, required=True)
    p.add_argument("--output-csv", type=Path, required=True)
    args = p.parse_args()

    weight_grid = []
    for w_o in (0.4, 0.5, 0.6):
        for w_d in (0.2, 0.3, 0.4):
            w_e = 1.0 - w_o - w_d
            if w_e < 0:
                continue
            weight_grid.append({"outcome": w_o, "decision": w_d, "efficiency": w_e})

    rows_in = list(csv.DictReader(args.input_csv.open()))
    print(f"loaded {len(rows_in)} cells from {args.input_csv}")
    rows_out = []
    for r in rows_in:
        try:
            o = float(r.get("outcome") or "")
            d = float(r.get("decision") or "")
            e = float(r.get("efficiency") or "")
        except ValueError:
            continue
        for w in weight_grid:
            fos = w["outcome"] * o + w["decision"] * d + w["efficiency"] * e
            rows_out.append({
                "family": r.get("family"),
                "scenario": r.get("scenario"),
                "repeat": r.get("repeat"),
                "weight_outcome": f"{w['outcome']:.3f}",
                "weight_decision": f"{w['decision']:.3f}",
                "weight_efficiency": f"{w['efficiency']:.3f}",
                "outcome": f"{o:.4f}",
                "decision": f"{d:.4f}",
                "efficiency": f"{e:.4f}",
                "fos": f"{max(0.0, min(1.0, fos)):.4f}",
            })
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    if rows_out:
        with args.output_csv.open("w", newline="") as h:
            w = csv.DictWriter(h, fieldnames=list(rows_out[0].keys()))
            w.writeheader()
            for r in rows_out:
                w.writerow(r)
    print(f"wrote {len(rows_out)} rows to {args.output_csv}")

    # Rank-stability per scenario across the weight grid.
    print("\nrank stability (top-3 family overlap across weight grid):")
    by_sc_w: dict[tuple[str, tuple[float, float, float]], list[tuple[str, float]]] = defaultdict(list)
    for r in rows_out:
        weights = (
            float(r["weight_outcome"]),
            float(r["weight_decision"]),
            float(r["weight_efficiency"]),
        )
        by_sc_w[(r["scenario"], weights)].append((r["family"], float(r["fos"])))

    by_sc_top3: dict[str, list[set[str]]] = defaultdict(list)
    for (sc, weights), pairs in by_sc_w.items():
        pairs.sort(key=lambda x: x[1], reverse=True)
        # Aggregate per-family within (sc, weights) — multiple repeats: use mean.
        per_fam_means: dict[str, list[float]] = defaultdict(list)
        for fam, val in pairs:
            per_fam_means[fam].append(val)
        ranked = sorted(per_fam_means.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
        by_sc_top3[sc].append({fam for fam, _ in ranked[:3]})

    for sc, top3_sets in by_sc_top3.items():
        if not top3_sets:
            continue
        common = set.intersection(*top3_sets)
        union = set.union(*top3_sets)
        stab = len(common) / len(union) if union else 0.0
        print(f"  {sc:60s}  stability={stab:.2f}  ({len(common)}/{len(union)} families always in top-3)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
