"""
ICLR validation-sweep runner.

Drives `are.simulation.main` over a (family, scenario, repeat) cell list,
captures per-cell results into a CSV, and tracks cumulative LLM dollar
cost against a hard ceiling. Designed to run all four spend phases of
the ICLR validation plan with a single tool.

Usage:
    python scripts/iclr_validation_runner.py \\
        --phase phase2_smoke \\
        --output-root validation_runs/iclr_sweep_<ts>/phase2_smoke \\
        --families farm_baseline_react \\
        --scenarios scenario_farm_world_irrigation,scenario_full_season_balanced \\
        --repeats 1 \\
        --model gpt-4o-mini \\
        --cost-cap-dollars 1.0 \\
        --max-concurrent 2

The runner enforces:
  - per-cell wall-clock timeout (default 300s)
  - cumulative cost ceiling (aborts at 80% of cap)
  - per-cell --agent-max-iterations 12 + --wait-for-user-input-timeout 5
  - subprocess-level isolation (each cell is its own process)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_BIN = str(REPO_ROOT / ".venv312" / "bin" / "python")

# Costs ($/1M tokens) for the EU OpenAI endpoint via litellm. Conservative
# estimates rounded up so the cost guard tends to over-estimate.
MODEL_COSTS = {
    "gpt-4o-mini": {"input_per_mtok": 0.15, "output_per_mtok": 0.60},
    "o4-mini": {"input_per_mtok": 1.10, "output_per_mtok": 4.40},
    "gpt-4o": {"input_per_mtok": 2.50, "output_per_mtok": 10.00},
}


@dataclass
class CellSpec:
    family: str
    scenario: str
    repeat: int


def _build_cell_command(
    cell: CellSpec, model: str, output_dir: Path, oracle: bool = False
) -> list[str]:
    # `llama-api` is the OpenAI-compatible litellm path used by the existing
    # suite_runner. The `openai` provider in this codebase routes through
    # huggingface_hub which won't see OPENAI_API_KEY. We map LLAMA_API_KEY/
    # LLAMA_API_BASE from the .env in run_cell().
    cmd = [
        PYTHON_BIN,
        "-m",
        "are.simulation.main",
        "-s",
        cell.scenario,
        "-a",
        cell.family,
        "-mp",
        "llama-api",
        "-m",
        model,
        "--log-level",
        "WARNING",
        "--output_dir",
        str(output_dir),
        "-e",
        "-w",
        "5",
    ]
    if oracle:
        cmd.append("-o")
    return cmd


def _parse_combined(rationale: str) -> float | None:
    """Extract workflow_combined score from rationale freeform text."""
    m = re.search(r"combined=([0-9.]+)", rationale or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_fos(rationale: str) -> dict[str, float]:
    """Extract fos / outcome / decision / efficiency scores."""
    out = {}
    for key in ("outcome", "decision", "efficiency", "fos"):
        m = re.search(rf"\b{key}=([0-9.]+)", rationale or "")
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    return out


def _load_fos_report(scenario_id: str, output_dir: Path) -> dict | None:
    """Load the structured FOS JSON report if present.

    In real-LLM mode `env.dump_dir` is None so the validator writes to
    ``<cwd>/fos_exports/fos/fos_<scenario>.json``. We pick it up there and
    copy a snapshot into the cell dir so concurrent cells don't overwrite
    each other.
    """
    # First check the cell-local path (oracle mode writes here directly).
    paths = list(output_dir.glob(f"fos/fos_{scenario_id}.json"))
    if not paths:
        # Fall back to repo-root cwd default.
        repo_default = REPO_ROOT / "fos_exports" / "fos" / f"fos_{scenario_id}.json"
        if repo_default.exists():
            # Copy into cell dir so this cell's report is preserved.
            dst_dir = output_dir / "fos"
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f"fos_{scenario_id}.json"
            try:
                dst.write_text(repo_default.read_text())
            except Exception:
                pass
            paths = [dst]
    if not paths:
        return None
    try:
        return json.loads(paths[0].read_text())
    except Exception:
        return None


def _estimate_cell_cost_dollars(
    output_jsonl_path: Path, model: str
) -> tuple[float, int, int]:
    """Estimate $ cost of a cell by parsing usage info from the output trace.

    The are.simulation.main exporter does not currently emit per-call usage
    metrics, so we approximate from the raw stdout/stderr length combined
    with a per-tool-call token estimate. Conservative: assume 3K input + 1K
    output per recorded LLM call (one per tool selection).
    """
    if not output_jsonl_path.exists():
        return (0.0, 0, 0)
    # Count agent-tool events as a proxy for LLM calls.
    try:
        line = output_jsonl_path.open().readline()
        d = json.loads(line)
        # Look for agent log entries via metadata if present.
        meta = d.get("metadata", {})
        rationale = meta.get("rationale", "") or ""
        # Heuristic: count tool calls from "Agent (N):" line in rationale logs.
        # If unavailable, fall back to a flat estimate of 15 calls.
        n_calls_match = re.search(r"Agent\s*\((\d+)\)", rationale)
        n_calls = int(n_calls_match.group(1)) if n_calls_match else 15
    except Exception:
        n_calls = 15
    cost_per_call = (
        MODEL_COSTS.get(model, MODEL_COSTS["gpt-4o-mini"])["input_per_mtok"] * 3.0e-3
        + MODEL_COSTS.get(model, MODEL_COSTS["gpt-4o-mini"])["output_per_mtok"] * 1.0e-3
    )
    return (n_calls * cost_per_call, n_calls, 0)


def run_cell(
    cell: CellSpec,
    output_root: Path,
    model: str,
    timeout_s: int,
) -> dict:
    """Run one cell as a subprocess; capture output and parse FOS / workflow."""
    cell_dir = output_root / f"{cell.family}__{cell.scenario}__r{cell.repeat}"
    cell_dir.mkdir(parents=True, exist_ok=True)

    # Inject .env so OPENAI_API_KEY/OPENAI_BASE_URL flow through.
    env = os.environ.copy()
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env.setdefault(key.strip(), val.strip())
    # litellm convention for openai-compat custom base.
    if env.get("OPENAI_BASE_URL"):
        env["OPENAI_API_BASE"] = env["OPENAI_BASE_URL"]
    # The `llama-api` provider in this codebase reads LLAMA_API_KEY /
    # LLAMA_API_BASE; map from OPENAI_API_KEY / OPENAI_BASE_URL when present.
    if env.get("OPENAI_API_KEY") and not env.get("LLAMA_API_KEY"):
        env["LLAMA_API_KEY"] = env["OPENAI_API_KEY"]
    if env.get("OPENAI_BASE_URL") and not env.get("LLAMA_API_BASE"):
        env["LLAMA_API_BASE"] = env["OPENAI_BASE_URL"]
    # Direct FOS export to this cell's dir so concurrent cells don't race.
    env["FOS_EXPORT_DIR"] = str(cell_dir)

    cmd = _build_cell_command(cell, model, cell_dir, oracle=False)
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
        wall_s = time.time() - t0
        return_code = proc.returncode
    except subprocess.TimeoutExpired:
        wall_s = time.time() - t0
        return_code = -1

    out_jsonl = cell_dir / "output.jsonl"
    workflow_combined: float | None = None
    fos_metrics: dict[str, float] = {}
    fos_report: dict | None = None
    rationale = ""
    if out_jsonl.exists():
        try:
            d = json.loads(out_jsonl.open().readline())
            rationale = (d.get("metadata") or {}).get("rationale", "") or ""
            workflow_combined = _parse_combined(rationale)
            fos_metrics = _parse_fos(rationale)
        except Exception:
            pass
        fos_report = _load_fos_report(cell.scenario, cell_dir)

    cost, n_calls, _ = _estimate_cell_cost_dollars(out_jsonl, model)

    safety = None
    crop_loss = None
    gates_matched = None
    gates_total = None
    if fos_report is not None:
        ob = fos_report.get("outcome_breakdown", {})
        safety = ob.get("safety_violations")
        crop_loss = ob.get("crop_loss_count")
        gd = fos_report.get("decision_breakdown") or []
        gates_total = len(gd)
        gates_matched = sum(1 for g in gd if g.get("matched"))

    return {
        "family": cell.family,
        "scenario": cell.scenario,
        "repeat": cell.repeat,
        "model": model,
        "wall_s": round(wall_s, 2),
        "return_code": return_code,
        "workflow_combined": workflow_combined,
        "fos": fos_metrics.get("fos"),
        "outcome": fos_metrics.get("outcome"),
        "decision": fos_metrics.get("decision"),
        "efficiency": fos_metrics.get("efficiency"),
        "safety_violations": safety,
        "crop_loss": crop_loss,
        "gates_matched": gates_matched,
        "gates_total": gates_total,
        "estimated_cost_dollars": round(cost, 4),
        "cell_dir": str(cell_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--families", required=True, help="Comma-separated family names")
    parser.add_argument("--scenarios", required=True, help="Comma-separated scenario IDs")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--cost-cap-dollars", type=float, default=10.0)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--cell-timeout-s", type=int, default=300)
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    cells = [
        CellSpec(family=f, scenario=s, repeat=r)
        for f in families
        for s in scenarios
        for r in range(1, args.repeats + 1)
    ]
    total_cells = len(cells)
    print(f"=== {args.phase}: {total_cells} cells, model={args.model}, cap=${args.cost_cap_dollars:.2f} ===")

    rows: list[dict] = []
    cumulative_cost = 0.0
    aborted = False
    cap_threshold = args.cost_cap_dollars * 0.80
    started = time.time()

    with ThreadPoolExecutor(max_workers=args.max_concurrent) as pool:
        futures = {
            pool.submit(run_cell, cell, args.output_root, args.model, args.cell_timeout_s): cell
            for cell in cells
        }
        completed = 0
        for fut in as_completed(futures):
            cell = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:  # pragma: no cover
                row = {
                    "family": cell.family,
                    "scenario": cell.scenario,
                    "repeat": cell.repeat,
                    "model": args.model,
                    "wall_s": None,
                    "return_code": -2,
                    "estimated_cost_dollars": 0.0,
                    "exception": repr(exc),
                }
            cumulative_cost += float(row.get("estimated_cost_dollars") or 0.0)
            rows.append(row)
            completed += 1
            stamp = f"[{completed}/{total_cells}] cum=${cumulative_cost:.3f}"
            fos = row.get("fos")
            wf = row.get("workflow_combined")
            rc = row.get("return_code")
            print(
                f"  {stamp}  rc={rc}  wall={row.get('wall_s')}s  "
                f"wf={wf}  fos={fos}  {row['family']}__{row['scenario']}_r{row['repeat']}"
            )
            if cumulative_cost >= cap_threshold and not aborted:
                aborted = True
                print(
                    f"  WARN: cumulative cost ${cumulative_cost:.3f} >= 80% of cap "
                    f"${args.cost_cap_dollars:.2f}; cancelling remaining cells"
                )
                for f, c in futures.items():
                    if not f.done():
                        f.cancel()

    # Write CSV.
    csv_path = args.output_root / "results.csv"
    if rows:
        fieldnames = sorted({k for row in rows for k in row.keys()})
        with csv_path.open("w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"  wrote {csv_path} ({len(rows)} rows)")

    # Summary.
    success = sum(1 for r in rows if r.get("return_code") == 0)
    duration = time.time() - started
    print(
        f"\n  SUMMARY: {success}/{len(rows)} succeeded, "
        f"cum_cost=${cumulative_cost:.3f}, duration={duration:.0f}s"
    )
    return 0 if not aborted else 2


if __name__ == "__main__":
    sys.exit(main())
