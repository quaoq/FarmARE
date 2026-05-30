"""
Behavior demonstration for FARM-FOS on a REAL scenario's oracle workflow.

This does NOT need agent traces or LLM cost. It takes an actual L3 scenario,
runs oracle mode to get the timestamped gold workflow, then builds controlled
"agent" variants by degrading the oracle (timing jitter on decision actions,
omissions, mis-targeting) and shows how each metric responds.

Claim demonstrated: as an agent's *timing* drifts off the oracle's windows,
FARM-FOS degrades smoothly, while BFCL (timing-blind), CORE (order edit
distance), and vanilla KTC (order rank) stay high — i.e. the baselines cannot
see the degradation that, on a real farm, costs yield.

This is a metric-*behavior* demo (controlled degradation), not the yield-
correlation scatter; the latter needs per-run yields from agent traces or a
physics-perturbation harness.
"""

from __future__ import annotations

import argparse
import copy
import random

from are.simulation.scenarios.fos.calibration import category_for_tool
from are.simulation.scenarios.fos.spatiotemporal import (
    baseline_bfcl,
    compute_farm_fos,
)
from are.simulation.scenarios.utils.registry import registry
from are.simulation.scenarios.workflow_validation import (
    ensure_oracle_workflow,
    evaluate_workflows,
)

DAY = 86400.0


def _metrics(oracle_wf, agent_wf):
    base = evaluate_workflows(oracle_wf, agent_wf)
    ff = compute_farm_fos(oracle_wf, agent_wf)
    return {
        "FARM-FOS": ff.farm_fos if ff.farm_fos is not None else float("nan"),
        "BFCL": baseline_bfcl(oracle_wf, agent_wf),
        "CORE": base["path_correctness"],
        "KTC": base["ktc_raw"],
    }


def _jitter_decisions(oracle_wf, max_days, drop_p, rng):
    """Make an 'agent' variant: jitter *mid/late-season* decision-action times
    by up to +/- max_days and drop each with prob drop_p. Planting actions are
    left untouched — they define the season anchor (real agents plant near day
    0, same as the oracle), so jittering them would just shift the whole axis
    rather than model a realistic timing error. Observation actions pass
    through unchanged."""
    agent = {}
    for name, step in oracle_wf.items():
        s = copy.deepcopy(step)
        cat = category_for_tool(s.get("tool_name") or "")
        if cat is not None and cat != "plant":
            if rng.random() < drop_p:
                continue  # agent omits this decision action
            if s.get("time") is not None and max_days > 0:
                s["time"] = s["time"] + rng.uniform(-max_days, max_days) * DAY
        agent[name] = s
    return agent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenario_full_season_heinong60_high_density_baseline")
    ap.add_argument("--reps", type=int, default=12, help="variants per jitter level")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    s = registry.get_scenario(args.scenario)()
    s.initialize()
    oracle_wf = ensure_oracle_workflow(s)  # runs oracle mode -> real timestamps
    n_dec = sum(1 for st in oracle_wf.values() if category_for_tool(st.get("tool_name") or ""))
    n_times = sum(1 for st in oracle_wf.values() if st.get("time") is not None)
    print(f"scenario: {args.scenario}")
    print(f"  oracle steps={len(oracle_wf)}  decision actions={n_dec}  timestamped={n_times}\n")

    # sanity: oracle vs itself = 1.0
    perfect = _metrics(oracle_wf, oracle_wf)
    print("  oracle-vs-oracle (sanity):  " +
          "  ".join(f"{k}={v:.3f}" for k, v in perfect.items()))
    print()

    # Sweep 1: pure timing jitter (no drops) -> isolates the temporal axis
    print("=== Sweep A: timing jitter only (no omissions) ===")
    print(f"  {'jitter(±d)':>10} | {'FARM-FOS':>9} {'BFCL':>6} {'CORE':>6} {'KTC':>6}")
    for jit in [0, 3, 7, 14, 21, 30]:
        agg = {k: [] for k in ["FARM-FOS", "BFCL", "CORE", "KTC"]}
        for _ in range(args.reps):
            agent = _jitter_decisions(oracle_wf, jit, 0.0, rng)
            for k, v in _metrics(oracle_wf, agent).items():
                agg[k].append(v)
        means = {k: sum(v) / len(v) for k, v in agg.items()}
        print(f"  {jit:>10} | {means['FARM-FOS']:>9.3f} {means['BFCL']:>6.3f} "
              f"{means['CORE']:>6.3f} {means['KTC']:>6.3f}")

    # Sweep 2: timing jitter + omissions -> realistic degraded agent
    print("\n=== Sweep B: jitter ±7d + omission probability ===")
    print(f"  {'drop_p':>10} | {'FARM-FOS':>9} {'BFCL':>6} {'CORE':>6} {'KTC':>6}")
    for dp in [0.0, 0.1, 0.25, 0.5, 0.75]:
        agg = {k: [] for k in ["FARM-FOS", "BFCL", "CORE", "KTC"]}
        for _ in range(args.reps):
            agent = _jitter_decisions(oracle_wf, 7, dp, rng)
            for k, v in _metrics(oracle_wf, agent).items():
                agg[k].append(v)
        means = {k: sum(v) / len(v) for k, v in agg.items()}
        print(f"  {dp:>10} | {means['FARM-FOS']:>9.3f} {means['BFCL']:>6.3f} "
              f"{means['CORE']:>6.3f} {means['KTC']:>6.3f}")

    print("\nReading: FARM-FOS falls monotonically as timing drifts / actions are")
    print("dropped. BFCL stays ~1.0 under pure timing jitter (it is timing-blind);")
    print("CORE/KTC barely move under jitter because they score order, not clock.")


if __name__ == "__main__":
    main()
