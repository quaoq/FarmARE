"""
FARM-FOS analysis: metric-vs-yield correlation + figures (the paper's headline).

Canonical entry point for the professor's team. Consumes the rebatch summary
CSV (one row per (agent, scenario) cell, produced by
``scripts/rebatch_fos_from_traces.py``) which already carries:

    farm_fos(%)               our spatiotemporal metric
    bfcl_success(%)           BFCL-style success-rate baseline
    core_path_correctness(%)  CORE / path-correctness (Levenshtein) baseline
    ktc(%)                    vanilla Kendall temporal-correctness baseline
    agent_family, scenario    grouping keys
    + a yield column (auto-detected, see --yield-col)

Produces, under --out-dir:
    farm_fos_correlation.csv     per-metric Pearson/Spearman (pooled)
    farm_fos_correlation.tex     LaTeX table for the paper
    fig_scatter.pdf              scatter panels: metric (y) vs yield (x), OLS + rho
    fig_bars.pdf                 |Spearman| bar chart, FARM-FOS highlighted
    farm_fos_summary.txt         human-readable readout (pooled + within-scenario)

Two correlation regimes are reported:
  POOLED              — across all cells.
  WITHIN-SCENARIO     — across agents on a *fixed* scenario, averaged
                        (Fisher-z) over scenarios. This is the confound-free
                        test: does the metric rank AGENTS by their realized
                        yield on the SAME field? Needs >=2 agents per scenario.

No third-party stats deps (Pearson/Spearman/Steiger computed in-module).
matplotlib is optional (figures skipped with a note if unavailable).
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

# Metric display name -> candidate column names (first match wins).
METRICS = [
    ("BFCL success", ["bfcl_success(%)", "bfcl_success", "bfcl"]),
    ("CORE (path-corr.)", ["core_path_correctness(%)", "core_path_correctness", "path_correctness(%)"]),
    ("vanilla KTC", ["ktc(%)", "ktc", "ktc_score(%)"]),
    ("FARM-FOS v2 (total)", ["farm_fos_v2_total(%)", "farm_fos_v2_total"]),
    ("FARM-FOS (ours)", ["farm_fos(%)", "farm_fos"]),
]
# Yield column candidates, best first. yield_preserved_ratio (vs oracle) is the
# preferred outcome; yield_ratio (vs potential) is the fallback.
YIELD_CANDIDATES = [
    "yield_preserved_ratio(%)", "yield_preserved_ratio",
    "yield_ratio(%)", "yield_ratio",
    "normalized_yield_score(%)",
]


def _f(r, k):
    try:
        return float(r.get(k, ""))
    except (TypeError, ValueError):
        return None


def _first_present(rows, cands):
    for c in cands:
        if any((r.get(c, "") not in ("", None)) for r in rows):
            return c
    return None


def _pearson(x, y):
    p = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(p) < 3:
        return float("nan"), len(p)
    xs = [a for a, _ in p]
    ys = [b for _, b in p]
    mx, my = mean(xs), mean(ys)
    num = sum((a - mx) * (b - my) for a, b in p)
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return (num / den if den else float("nan")), len(p)


def _rank(v):
    idx = sorted(range(len(v)), key=lambda k: v[k])
    r = [0.0] * len(v)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
            j += 1
        a = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[idx[k]] = a
        i = j + 1
    return r


def _spearman(x, y):
    p = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(p) < 3:
        return float("nan")
    return _pearson(_rank([a for a, _ in p]), _rank([b for _, b in p]))[0]


def _fisher_mean(rs):
    rs = [r for r in rs if r is not None and not math.isnan(r) and abs(r) < 0.999]
    if not rs:
        return float("nan"), 0
    z = [0.5 * math.log((1 + r) / (1 - r)) for r in rs]
    return math.tanh(mean(z)), len(rs)


def _norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _steiger(r_jk, r_jh, r_kh, n):
    """Steiger t for r_jk (FARM-FOS vs yield) > r_jh (baseline vs yield), sharing
    yield. r_kh = corr between the two metrics. Returns (t, two-sided p)."""
    if n < 10 or any(v is None or math.isnan(v) for v in (r_jk, r_jh, r_kh)):
        return float("nan"), float("nan")
    rjk, rjh = abs(r_jk), abs(r_jh)
    rbar = (rjk + rjh) / 2.0
    det = (1 - rjk**2 - rjh**2 - r_kh**2) + 2 * rjk * rjh * r_kh
    if det <= 0 or (1 - rbar**2) <= 0:
        return float("nan"), float("nan")
    num = (rjk - rjh) * math.sqrt((n - 1) * (1 + r_kh))
    den = math.sqrt(2 * ((n - 1) / (n - 3)) * det + (rbar**2) * ((1 - r_kh) ** 3) / (1 - rbar**2))
    if den == 0:
        return float("nan"), float("nan")
    t = num / den
    return t, 2 * (1 - _norm_cdf(abs(t)))


def _report_steiger(log, pooled, metric_name):
    if metric_name not in pooled:
        return
    log("")
    log(f"Steiger test ({metric_name} Pearson > baseline Pearson, shared=yield):")
    target_pr, _, target_n, target_m = pooled[metric_name]
    for disp in ["BFCL success", "CORE (path-corr.)", "vanilla KTC"]:
        if disp not in pooled:
            continue
        base_pr, _, _, base_m = pooled[disp]
        r_kh, _ = _pearson(target_m, base_m)
        t, p = _steiger(target_pr, base_pr, r_kh, target_n)
        log(f"  vs {disp:22s} t={t:+.2f}  p={p:.4g}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True, help="rebatch summary CSV")
    ap.add_argument("--yield-col", default=None,
                    help="yield column name; auto-detected if omitted")
    ap.add_argument("--out-dir", default="figures_farm_fos")
    ap.add_argument("--min-yield", type=float, default=None,
                    help="optional: drop cells with yield below this "
                         "(e.g. to exclude catastrophic no-harvest runs)")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csv)))
    if not rows:
        raise SystemExit("empty CSV")

    ycol = args.yield_col or _first_present(rows, YIELD_CANDIDATES)
    if ycol is None:
        raise SystemExit(f"No yield column found. Tried: {YIELD_CANDIDATES}")
    mcols = {}
    for disp, cands in METRICS:
        c = _first_present(rows, cands)
        if c:
            mcols[disp] = c

    if args.min_yield is not None:
        rows = [r for r in rows if (_f(r, ycol) or -1) >= args.min_yield]

    y = [_f(r, ycol) for r in rows]
    fams = sorted({r.get("agent_family", "") for r in rows})
    scens = sorted({r.get("scenario", "") for r in rows})
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    log(f"FARM-FOS analysis")
    log(f"  csv={args.csv}")
    log(f"  cells={len(rows)}  agents={len(fams)}  scenarios={len(scens)}")
    log(f"  yield_col={ycol}  (n with yield={sum(1 for v in y if v is not None)})")
    if args.min_yield is not None:
        log(f"  filtered to yield >= {args.min_yield}")
    log("")

    # Pooled
    log("=== POOLED (all cells) ===")
    log(f"{'metric':22s} {'Pearson':>9} {'Spearman':>9} {'R^2':>7}")
    pooled = {}
    for disp, _cands in METRICS:
        c = mcols.get(disp)
        if not c:
            log(f"{disp:22s}   (column absent)")
            continue
        m = [_f(r, c) for r in rows]
        pr, npts = _pearson(m, y)
        sr = _spearman(m, y)
        pooled[disp] = (pr, sr, npts, m)
        log(f"{disp:22s} {pr:+9.3f} {sr:+9.3f} {pr*pr:7.3f}")

    # Steiger: compare each FARM metric against the shared-yield baselines.
    _report_steiger(log, pooled, "FARM-FOS (ours)")
    _report_steiger(log, pooled, "FARM-FOS v2 (total)")

    # Within-scenario across agents
    by_sc = defaultdict(list)
    for r in rows:
        by_sc[r.get("scenario", "")].append(r)
    multi = {sc: cs for sc, cs in by_sc.items() if len(cs) >= 2}
    log("")
    log(f"=== WITHIN-SCENARIO across agents (mean Spearman, Fisher-z; "
        f"{len(multi)} scenarios with >=2 agents) ===")
    if not multi:
        log("  (need >=2 agents per scenario — run multiple families to populate)")
    else:
        log(f"{'metric':22s} {'mean rho':>9} {'#scen':>6}")
        for disp, _cands in METRICS:
            c = mcols.get(disp)
            if not c:
                continue
            per = []
            for sc, cs in multi.items():
                if len(cs) < 3:
                    continue
                per.append(_spearman([_f(x, c) for x in cs], [_f(x, ycol) for x in cs]))
            mr, nsc = _fisher_mean(per)
            log(f"{disp:22s} {mr:+9.3f} {nsc:>6}")

    # write tables
    with (out / "farm_fos_correlation.csv").open("w", newline="") as h:
        w = csv.writer(h)
        w.writerow(["metric", "pearson", "spearman", "r2", "n"])
        for disp, _c in METRICS:
            if disp in pooled:
                pr, sr, npts, _ = pooled[disp]
                w.writerow([disp, f"{pr:.4f}", f"{sr:.4f}", f"{pr*pr:.4f}", npts])
    with (out / "farm_fos_correlation.tex").open("w") as h:
        h.write("\\begin{tabular}{lccc}\n\\toprule\n")
        h.write("Metric & Pearson $r$ & Spearman $\\rho$ & $R^2$ \\\\\n\\midrule\n")
        for disp, _c in METRICS:
            if disp in pooled:
                pr, sr, _n, _ = pooled[disp]
                b = "\\textbf" if "FARM" in disp else ""
                h.write(f"{b}{{{disp.replace('&','\\&')}}} & {pr:+.3f} & {sr:+.3f} & {pr*pr:.3f} \\\\\n")
        h.write("\\bottomrule\n\\end{tabular}\n")
    (out / "farm_fos_summary.txt").write_text("\n".join(lines) + "\n")

    _figures(rows, y, ycol, pooled, mcols, fams, scens, out, log)
    log(f"\nwrote: farm_fos_correlation.csv, farm_fos_correlation.tex, "
        f"farm_fos_summary.txt, fig_scatter.pdf, fig_bars.pdf  -> {out}/")
    (out / "farm_fos_summary.txt").write_text("\n".join(lines) + "\n")


def _figures(rows, y, ycol, pooled, mcols, fams, scens, out, log):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        log(f"(figures skipped: matplotlib unavailable: {exc})")
        return
    present = [d for d, _ in METRICS if d in pooled]
    if not present:
        return
    fig, axes = plt.subplots(1, len(present), figsize=(3.5 * len(present), 3.4), squeeze=False)
    for ax, disp in zip(axes[0], present):
        m = [_f(r, mcols[disp]) for r in rows]
        pts = [(a, b) for a, b in zip(m, y) if a is not None and b is not None]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(xs, ys, s=18, alpha=0.6,
                   color="#2c7fb8" if "FARM" in disp else "#888888")
        if len(pts) >= 2 and len(set(xs)) > 1:
            mx, my = mean(xs), mean(ys)
            b1 = sum((a - mx) * (c - my) for a, c in pts) / sum((a - mx) ** 2 for a in xs)
            b0 = my - b1 * mx
            lo, hi = min(xs), max(xs)
            ax.plot([lo, hi], [b0 + b1 * lo, b0 + b1 * hi], "r-", lw=1.4)
        sr = _spearman(m, y)
        ax.set_title(f"{disp}\nSpearman={sr:+.2f}", fontsize=10)
        ax.set_xlabel(disp + " value")
        ax.set_ylabel(ycol)
    fig.suptitle(f"Path metric vs real yield ({len(fams)} agents x {len(scens)} scenarios)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "fig_scatter.pdf")
    plt.close(fig)

    fig2, ax2 = plt.subplots(figsize=(4.6, 3.0))
    labels = [d for d in present]
    vals = [abs(_spearman([_f(r, mcols[d]) for r in rows], y)) for d in present]
    colors = ["#2c7fb8" if "FARM" in d else "#bbbbbb" for d in present]
    ax2.bar(range(len(labels)), vals, color=colors)
    ax2.set_xticks(range(len(labels)))
    ax2.set_xticklabels(labels, rotation=25, ha="right", fontsize=8)
    ax2.set_ylabel("|Spearman| vs yield")
    ax2.set_ylim(0, 1)
    fig2.tight_layout()
    fig2.savefig(out / "fig_bars.pdf")
    plt.close(fig2)


if __name__ == "__main__":
    main()
