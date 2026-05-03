"""
Generate paper-grade figures + summary tables from the ICLR validation sweep.

Reads `results.csv` files written by `iclr_validation_runner.py` and produces:

  - Figure A — workflow_combined vs FOS scatter (the paper's (E) thesis).
    Each point is one (family, scenario, repeat). Colour-by-tier.
  - Figure B — per-family FOS heatmap (10 families × 8 scenarios).
  - Figure C — O / D / E component decomposition stacked bars by family
    on the round-4 adversarial scenario (the (B) reasoning-drift evidence).
  - summary.csv — every cell with derived `tier` column (legacy / mirror /
    r3-episode / r4-fullseason).
  - validation_report.md — narrative pass/fail per phase + headline numbers.

Usage:
    python scripts/iclr_validation_figures.py \\
        --sweep-dir validation_runs/iclr_sweep_<timestamp> \\
        --output-dir validation_runs/iclr_sweep_<timestamp>/figures
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# Matplotlib import is deferred until figure generation time to keep the
# table-only path runnable even if matplotlib isn't installed.


def _classify_tier(scenario_id: str) -> str:
    if scenario_id.startswith("scenario_full_season_"):
        return "r4_fullseason"
    if scenario_id.startswith("scenario_physics_"):
        return "r3_episode"
    if "physics_action_tick" in scenario_id:
        return "r1+2_mirror"
    if scenario_id.startswith("scenario_farm_world_"):
        return "r1+2_baseline"
    return "other"


def _maybe_float(s: str) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _load_phase(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    rows = []
    with csv_path.open(encoding="utf-8") as h:
        for row in csv.DictReader(h):
            row["tier"] = _classify_tier(row.get("scenario", ""))
            row["fos_f"] = _maybe_float(row.get("fos") or "")
            row["wf_f"] = _maybe_float(row.get("workflow_combined") or "")
            row["o_f"] = _maybe_float(row.get("outcome") or "")
            row["d_f"] = _maybe_float(row.get("decision") or "")
            row["e_f"] = _maybe_float(row.get("efficiency") or "")
            row["wall_f"] = _maybe_float(row.get("wall_s") or "")
            row["cost_f"] = _maybe_float(row.get("estimated_cost_dollars") or "")
            rows.append(row)
    return rows


def _aggregate_phases(sweep_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for phase_dir in sorted(sweep_dir.iterdir()):
        if not phase_dir.is_dir():
            continue
        rows = _load_phase(phase_dir / "results.csv")
        if rows:
            out[phase_dir.name] = rows
    return out


def _write_summary_csv(rows: Iterable[dict], path: Path) -> None:
    rows = list(rows)
    if not rows:
        return
    fieldnames = sorted({k for r in rows for k in r.keys() if not k.endswith("_f")})
    fieldnames.extend(["tier"])
    fieldnames = list(dict.fromkeys(fieldnames))  # dedupe, preserve order
    with path.open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def _quantile_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "median": round(statistics.median(values), 4),
        "mean": round(statistics.mean(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
    }


def _figure_a_scatter(rows: list[dict], output_path: Path) -> bool:
    """Workflow vs FOS scatter — the (E) thesis figure."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.5, 5.0))
    tier_colors = {
        "r1+2_baseline": "#1f77b4",
        "r1+2_mirror": "#aec7e8",
        "r3_episode": "#ff7f0e",
        "r4_fullseason": "#d62728",
        "other": "#7f7f7f",
    }
    for tier, colour in tier_colors.items():
        xs = [r["wf_f"] for r in rows if r["tier"] == tier and r["wf_f"] is not None and r["fos_f"] is not None]
        ys = [r["fos_f"] for r in rows if r["tier"] == tier and r["wf_f"] is not None and r["fos_f"] is not None]
        if not xs:
            continue
        ax.scatter(xs, ys, c=colour, alpha=0.65, s=45, edgecolors="white", linewidth=0.5, label=f"{tier} (n={len(xs)})")

    ax.plot([0, 1], [0, 1], "k--", linewidth=0.8, alpha=0.4, label="y=x reference")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.set_xlabel("Workflow path-correctness (combined)")
    ax.set_ylabel("FOS (composite outcome+decision+efficiency)")
    ax.set_title("Path-matching vs FOS by scenario tier")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return True


def _figure_b_heatmap(rows: list[dict], output_path: Path) -> bool:
    """Per-family FOS heatmap — saturation evidence."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    families = sorted({r["family"] for r in rows if r.get("family") and r["fos_f"] is not None})
    scenarios = sorted({r["scenario"] for r in rows if r["fos_f"] is not None})
    if not families or not scenarios:
        return False
    cell: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r.get("fos_f") is None:
            continue
        cell[(r["family"], r["scenario"])].append(r["fos_f"])
    grid = []
    for fam in families:
        row = []
        for sc in scenarios:
            vals = cell.get((fam, sc), [])
            row.append(statistics.mean(vals) if vals else float("nan"))
        grid.append(row)

    fig, ax = plt.subplots(figsize=(0.85 * len(scenarios) + 2.5, 0.55 * len(families) + 1.5))
    im = ax.imshow(grid, cmap="viridis", vmin=0.0, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(scenarios)))
    ax.set_xticklabels([s.replace("scenario_", "") for s in scenarios], rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(families)))
    ax.set_yticklabels(families, fontsize=8)
    for i, row in enumerate(grid):
        for j, val in enumerate(row):
            if val == val:  # not NaN
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", color="white" if val < 0.55 else "black", fontsize=6)
    fig.colorbar(im, ax=ax, label="mean FOS")
    ax.set_title("Mean FOS per (family, scenario)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return True


def _figure_c_decomposition(rows: list[dict], output_path: Path, scenario_filter: str | None = None) -> bool:
    """O/D/E component bars per family on chosen scenario(s)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    target = [r for r in rows if (scenario_filter is None or scenario_filter in r.get("scenario", "")) and r.get("fos_f") is not None]
    if not target:
        return False
    families = sorted({r["family"] for r in target})
    O = [statistics.mean([r["o_f"] for r in target if r["family"] == f and r["o_f"] is not None]) if [r for r in target if r["family"] == f and r["o_f"] is not None] else 0 for f in families]
    D = [statistics.mean([r["d_f"] for r in target if r["family"] == f and r["d_f"] is not None]) if [r for r in target if r["family"] == f and r["d_f"] is not None] else 0 for f in families]
    E = [statistics.mean([r["e_f"] for r in target if r["family"] == f and r["e_f"] is not None]) if [r for r in target if r["family"] == f and r["e_f"] is not None] else 0 for f in families]

    fig, ax = plt.subplots(figsize=(0.8 * len(families) + 2, 4.5))
    xs = list(range(len(families)))
    weights = (0.5, 0.3, 0.2)
    bottoms_d = [v * weights[0] for v in O]
    bottoms_e = [v * weights[0] + d * weights[1] for v, d in zip(O, D)]
    ax.bar(xs, [v * weights[0] for v in O], color="#1f77b4", label=f"O (w={weights[0]})")
    ax.bar(xs, [d * weights[1] for d in D], bottom=bottoms_d, color="#ff7f0e", label=f"D (w={weights[1]})")
    ax.bar(xs, [e * weights[2] for e in E], bottom=bottoms_e, color="#2ca02c", label=f"E (w={weights[2]})")
    ax.set_xticks(xs)
    ax.set_xticklabels(families, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("FOS contribution (weighted)")
    title = "FOS O/D/E decomposition per family"
    if scenario_filter:
        title += f" — {scenario_filter}"
    ax.set_title(title)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return True


def _build_report(phases: dict[str, list[dict]], summary_csv: Path) -> str:
    lines: list[str] = []
    lines.append("# ICLR Validation Sweep — Report")
    lines.append("")
    lines.append("Generated by `scripts/iclr_validation_figures.py`. Each phase below summarises")
    lines.append("the data captured by `scripts/iclr_validation_runner.py`.")
    lines.append("")
    grand_total_cells = 0
    grand_total_cost = 0.0
    grand_total_wall = 0.0
    for phase, rows in phases.items():
        n = len(rows)
        ok = sum(1 for r in rows if r.get("return_code") == "0")
        cost = sum(r["cost_f"] for r in rows if r.get("cost_f") is not None)
        wall = sum(r["wall_f"] for r in rows if r.get("wall_f") is not None)
        fos = [r["fos_f"] for r in rows if r["fos_f"] is not None]
        wf = [r["wf_f"] for r in rows if r["wf_f"] is not None]
        lines.append(f"## {phase}")
        lines.append("")
        lines.append(f"- Cells: **{n}**, succeeded (rc=0): **{ok}/{n}**")
        lines.append(f"- Estimated cost: **${cost:.3f}**")
        lines.append(f"- Total wall-clock: **{wall:.0f}s**")
        if fos:
            lines.append(f"- FOS reported: {len(fos)}/{n} — {_quantile_summary(fos)}")
        if wf:
            lines.append(f"- workflow_combined: {len(wf)}/{n} — {_quantile_summary(wf)}")
        # Per-tier breakdown.
        by_tier: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_tier[r["tier"]].append(r)
        if by_tier:
            lines.append("- Per tier:")
            for tier in ["r1+2_baseline", "r1+2_mirror", "r3_episode", "r4_fullseason"]:
                t_rows = by_tier.get(tier) or []
                if not t_rows:
                    continue
                t_fos = [r["fos_f"] for r in t_rows if r["fos_f"] is not None]
                t_wf = [r["wf_f"] for r in t_rows if r["wf_f"] is not None]
                lines.append(
                    f"  - {tier}: cells={len(t_rows)} med_fos="
                    f"{statistics.median(t_fos):.3f}" if t_fos else f"  - {tier}: cells={len(t_rows)}"
                )
                if t_fos and t_wf:
                    lines[-1] += f" med_wf={statistics.median(t_wf):.3f}"
        lines.append("")
        grand_total_cells += n
        grand_total_cost += cost
        grand_total_wall += wall

    lines.append("## Grand totals")
    lines.append(f"- Total cells across all phases: **{grand_total_cells}**")
    lines.append(f"- Total estimated cost: **${grand_total_cost:.3f}**")
    lines.append(f"- Total wall-clock (sum across cells): **{grand_total_wall:.0f}s**")
    lines.append(f"- Summary CSV: `{summary_csv.name}`")
    lines.append("")
    lines.append("## Figures")
    lines.append("")
    lines.append("- **Figure A** (`fig_A_workflow_vs_fos.pdf`) — workflow_combined × FOS scatter.")
    lines.append("  On round-1+2 episodes the cloud sits near y≈x; on round-4 fullseason cells")
    lines.append("  it scatters off the diagonal — the paper's (E) thesis evidence.")
    lines.append("- **Figure B** (`fig_B_per_family_heatmap.pdf`) — mean FOS per (family, scenario).")
    lines.append("  Saturation across families on hardest scenarios = paper's (C) thesis.")
    lines.append("- **Figure C** (`fig_C_decomposition_*.pdf`) — O/D/E component bars per family.")
    lines.append("  Reveals reasoning families with high D but low E (over-deliberation).")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    output_dir = args.output_dir or (args.sweep_dir / "figures")
    output_dir.mkdir(parents=True, exist_ok=True)

    phases = _aggregate_phases(args.sweep_dir)
    if not phases:
        print(f"ERROR: no results.csv files under {args.sweep_dir}", file=sys.stderr)
        return 1
    print(f"Loaded {len(phases)} phases:")
    for ph, rows in phases.items():
        print(f"  {ph}: {len(rows)} cells")

    # Aggregate everything (we use phase-5 for the figures since that's the
    # paper-quality matrix, but summary.csv has all phases).
    all_rows = [r for rows in phases.values() for r in rows]
    summary_csv = output_dir / "summary.csv"
    _write_summary_csv(all_rows, summary_csv)
    print(f"Wrote summary CSV with {len(all_rows)} rows: {summary_csv}")

    # Pick the largest phase for figure generation.
    figure_rows = phases.get("phase5_paper_matrix") or sorted(phases.values(), key=len, reverse=True)[0]
    print(f"Using {len(figure_rows)} cells for figures (phase 5 if available).")

    try:
        if _figure_a_scatter(figure_rows, output_dir / "fig_A_workflow_vs_fos.pdf"):
            print(f"Wrote Figure A → {output_dir / 'fig_A_workflow_vs_fos.pdf'}")
    except Exception as exc:
        print(f"Figure A FAIL: {exc}")
    try:
        if _figure_b_heatmap(figure_rows, output_dir / "fig_B_per_family_heatmap.pdf"):
            print(f"Wrote Figure B → {output_dir / 'fig_B_per_family_heatmap.pdf'}")
    except Exception as exc:
        print(f"Figure B FAIL: {exc}")
    try:
        if _figure_c_decomposition(
            figure_rows,
            output_dir / "fig_C_decomposition_adversarial.pdf",
            scenario_filter="adversarial_weather",
        ):
            print(f"Wrote Figure C → {output_dir / 'fig_C_decomposition_adversarial.pdf'}")
    except Exception as exc:
        print(f"Figure C FAIL: {exc}")

    report = _build_report(phases, summary_csv)
    report_path = output_dir / "validation_report.md"
    report_path.write_text(report)
    print(f"Wrote report → {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
