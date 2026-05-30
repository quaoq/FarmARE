"""
FARM-FOS: a spatiotemporally-weighted, physics-calibrated path metric.

This is one *instantiation* of the paper's framing — "derive the evaluation
metric from the domain process model so it rewards spatiotemporal action-timing
sensitivity" — for our Harbin soybean farm. It is deliberately a thin function
of the SAME two workflow dicts the existing path metrics consume
(``workflow_from_oracle_events`` / ``workflow_from_event_log``), so the
comparison against CORE / KTC / BFCL is apples-to-apples: every metric sees the
identical (agent, oracle) trace; FARM-FOS simply *uses* the timestamp, target-
ridge, and agronomic-importance information that the baselines discard.

Score (see ``farm_fos_math.tex`` for the full derivation):

    FARM-FOS = clip_[0,1]( ( Σ_i w_i · o_i · k_σ(Δ_i)  −  λ Σ_j h_j ) / Σ_i w_i )

  - i ranges over oracle *decision* actions (calibration category != None)
  - w_i, σ_i are FROZEN agronomic priors (calibration.py); never fit to yield
  - o_i = |R_i ∩ R*_i| / |R*_i|         (spatial: target-ridge overlap)
  - k_σ(Δ_i) = max(0, 1 − |Δ_i|/σ_i)   (temporal: triangular timing kernel)
  - Δ_i = (agent_time − oracle_time) in days
  - h_j = physics harm prior for a spurious unmatched agent decision action
  - λ   = harm scale (default 0.25; set 0 to disable)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from are.simulation.scenarios.fos.calibration import (
    FROZEN_CALIBRATION,
    ActionCalibration,
    calibration_for_tool,
    category_for_tool,
    temporal_kernel,
)

NUM_RIDGES = 64
_SECONDS_PER_DAY = 86400.0
DEFAULT_HARM_LAMBDA = 0.25


# ---------------------------------------------------------------------------
# Ridge-target extraction (the "where")
# ---------------------------------------------------------------------------
def extract_ridges(tool_args: dict[str, Any] | None) -> set[int] | None:
    """Resolve the target ridge set from a tool call's args.

    Returns a set of ridge ids, or ``None`` for whole-field / non-spatial ops
    (which then score spatial overlap = 1.0 against any oracle target).
    """
    if not tool_args:
        return None
    a = tool_args
    # explicit id lists
    for key in ("ridge_ids", "target_ridges", "ridges"):
        v = a.get(key)
        if isinstance(v, (list, tuple)) and v:
            try:
                return {int(x) for x in v}
            except (TypeError, ValueError):
                pass
    # single ridge
    if "ridge_id" in a and a["ridge_id"] is not None:
        try:
            return {int(a["ridge_id"])}
        except (TypeError, ValueError):
            pass
    # contiguous range
    s, e = a.get("start_ridge"), a.get("end_ridge")
    if s is not None and e is not None:
        try:
            s, e = int(s), int(e)
            if e >= s:
                return set(range(s, e + 1))
        except (TypeError, ValueError):
            pass
    return None


def spatial_overlap(agent_ridges: set[int] | None, oracle_ridges: set[int] | None) -> float:
    """Coverage of the oracle's intended ridges by the agent's action."""
    if oracle_ridges is None or len(oracle_ridges) == 0:
        return 1.0  # oracle action is whole-field / non-spatial
    if agent_ridges is None:
        return 1.0  # agent acted field-wide → covers the intended ridges
    return len(agent_ridges & oracle_ridges) / len(oracle_ridges)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
@dataclass
class FarmFosActionResult:
    oracle_index: int
    category: str
    weight: float
    oracle_time_days: float
    matched: bool
    spatial_overlap: float = 0.0
    delta_days: float | None = None
    temporal_credit: float = 0.0
    credit: float = 0.0  # w · o · k

    def to_dict(self) -> dict[str, Any]:
        return {
            "oracle_index": self.oracle_index,
            "category": self.category,
            "weight": round(self.weight, 4),
            "oracle_time_days": round(self.oracle_time_days, 3),
            "matched": self.matched,
            "spatial_overlap": round(self.spatial_overlap, 4),
            "delta_days": (round(self.delta_days, 3) if self.delta_days is not None else None),
            "temporal_credit": round(self.temporal_credit, 4),
            "credit": round(self.credit, 4),
        }


@dataclass
class FarmFosReport:
    farm_fos: float | None
    n_oracle_decisions: int
    n_matched: int
    weight_total: float
    credit_total: float
    harm_total: float
    harm_lambda: float
    per_action: list[FarmFosActionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "farm_fos": (round(self.farm_fos, 4) if self.farm_fos is not None else None),
            "n_oracle_decisions": self.n_oracle_decisions,
            "n_matched": self.n_matched,
            "weight_total": round(self.weight_total, 4),
            "credit_total": round(self.credit_total, 4),
            "harm_total": round(self.harm_total, 4),
            "harm_lambda": self.harm_lambda,
            "per_action": [r.to_dict() for r in self.per_action],
        }


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------
def _advance_seconds(tool_args: dict[str, Any] | None) -> float:
    """Seconds of season-clock advance encoded by a SystemApp.advance_time call."""
    if not tool_args:
        return 0.0
    a = tool_args
    try:
        return (
            float(a.get("seconds", 0) or 0)
            + float(a.get("minutes", 0) or 0) * 60.0
            + float(a.get("hours", 0) or 0) * 3600.0
            + float(a.get("days", 0) or 0) * 86400.0
        )
    except (TypeError, ValueError):
        return 0.0


_MIN_TIME_SPAN_DAYS = 1.0


def _season_day_map(workflow: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Map each step name -> its *season-day* on a run-independent axis.

    Two bases, picked automatically:

    * **recorded-time basis** — ``(time - min_time)/86400``. Correct when the
      workflow's event_times actually span the season (the AGENT trace: its
      env clock advanced as it called advance_time, so its timestamps are real
      crop-days).
    * **advance_time-accumulation basis** — running sum of advance_time gaps in
      execution order. Needed for the ORACLE workflow, which encodes its
      schedule as advance_time gaps *between* actions while leaving absolute
      event_time pinned near the scenario start (all action times collapse to
      ~day 0). Accumulating advance_time reconstructs the intended season-day.

    We use the recorded-time basis when its decision-step span exceeds
    ``_MIN_TIME_SPAN_DAYS``; otherwise we fall back to advance_time accumulation.
    Both express "crop-days into the season", so oracle and agent are directly
    comparable regardless of clock origin or pre-roll bookkeeping.
    """
    # candidate 1: recorded time
    times = [
        step.get("time")
        for step in workflow.values()
        if step.get("time") is not None
        and category_for_tool(step.get("tool_name") or "") is not None
    ]
    time_map: dict[str, float] = {}
    if times:
        tmin = min(times)
        for name, step in workflow.items():
            t = step.get("time")
            time_map[name] = ((t - tmin) / _SECONDS_PER_DAY) if t is not None else 0.0
        span = (max(times) - tmin) / _SECONDS_PER_DAY
        if span >= _MIN_TIME_SPAN_DAYS:
            return time_map

    # candidate 2: accumulate advance_time gaps
    adv_map: dict[str, float] = {}
    clock_s = 0.0
    for name, step in workflow.items():  # dict preserves execution order
        adv_map[name] = clock_s / _SECONDS_PER_DAY
        if (step.get("tool_name") or "").endswith("advance_time"):
            clock_s += _advance_seconds(step.get("tool_args"))
    # if advance_time produced a real span use it, else fall back to time_map
    if max(adv_map.values(), default=0.0) >= _MIN_TIME_SPAN_DAYS:
        return adv_map
    return time_map or adv_map


def _decision_steps(
    workflow: dict[str, dict[str, Any]], day_map: dict[str, float]
) -> list[tuple[dict[str, Any], float]]:
    """(step, season_day) pairs for steps that carry yield weight (calibration
    category != None), sorted by season-day. Observation/logistics steps drop."""
    steps: list[tuple[dict[str, Any], float]] = []
    for name, step in workflow.items():
        if category_for_tool(step.get("tool_name") or "") is None:
            continue
        steps.append((step, day_map.get(name, 0.0)))
    steps.sort(key=lambda s: s[1])
    return steps


def compute_farm_fos(
    oracle_workflow: dict[str, dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]],
    calibration: dict[str, ActionCalibration] | None = None,
    harm_lambda: float = DEFAULT_HARM_LAMBDA,
) -> FarmFosReport:
    """Compute FARM-FOS from two workflow dicts (each step has tool_name,
    tool_args, time). Frozen-calibration in, score out — no yield touched."""
    calib_table = calibration or FROZEN_CALIBRATION

    oracle_day_map = _season_day_map(oracle_workflow)
    agent_day_map = _season_day_map(agent_workflow)
    oracle_steps = _decision_steps(oracle_workflow, oracle_day_map)
    agent_steps = _decision_steps(agent_workflow, agent_day_map)

    # day-0 reference = earliest oracle decision action (season-day axis)
    t0_day = oracle_steps[0][1] if oracle_steps else 0.0

    # pre-extract agent decision actions per category, carrying season-day
    agent_by_cat: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for s, sday in agent_steps:
        cat = category_for_tool(s.get("tool_name") or "")
        agent_by_cat.setdefault(cat, []).append((s, sday))

    # build all candidate (credit, oracle_i, agent_j) triples, then greedily
    # assign each agent action to at most one oracle action (descending credit).
    # delta is in season-days (agent_day - oracle_day).
    oracle_meta: list[tuple[int, str, ActionCalibration, set[int] | None, float]] = []
    candidates: list[tuple[float, int, int, float, float, float]] = []
    for i, (os_, o_day) in enumerate(oracle_steps):
        cat = category_for_tool(os_.get("tool_name") or "")
        calib = calib_table.get(cat)
        if calib is None:
            continue
        o_ridges = extract_ridges(os_.get("tool_args"))
        oracle_meta.append((i, cat, calib, o_ridges, o_day))
        for j, (as_, a_day) in enumerate(agent_by_cat.get(cat, [])):
            a_ridges = extract_ridges(as_.get("tool_args"))
            ov = spatial_overlap(a_ridges, o_ridges)
            delta = a_day - o_day
            k = temporal_kernel(delta, calib)
            credit_unit = ov * k  # before weight
            if credit_unit > 0.0:
                candidates.append((credit_unit, i, j, ov, k, delta))

    candidates.sort(key=lambda c: c[0], reverse=True)
    used_oracle: set[int] = set()
    used_agent: set[tuple[str, int]] = set()
    best_for_oracle: dict[int, tuple[float, float, float]] = {}  # i -> (ov, k, delta)
    # need category to key agent usage; recompute per candidate
    # rebuild cat lookup by oracle index
    cat_by_i = {m[0]: m[1] for m in oracle_meta}
    for credit_unit, i, j, ov, k, delta in candidates:
        cat = cat_by_i[i]
        if i in used_oracle or (cat, j) in used_agent:
            continue
        used_oracle.add(i)
        used_agent.add((cat, j))
        best_for_oracle[i] = (ov, k, delta)

    per_action: list[FarmFosActionResult] = []
    weight_total = 0.0
    credit_total = 0.0
    n_matched = 0
    for i, cat, calib, o_ridges, o_day in oracle_meta:
        w = calib.weight
        weight_total += w
        if i in best_for_oracle:
            ov, k, delta = best_for_oracle[i]
            credit = w * ov * k
            credit_total += credit
            n_matched += 1
            per_action.append(FarmFosActionResult(
                oracle_index=i, category=cat, weight=w,
                oracle_time_days=o_day - t0_day,
                matched=True, spatial_overlap=ov, delta_days=delta,
                temporal_credit=k, credit=credit,
            ))
        else:
            per_action.append(FarmFosActionResult(
                oracle_index=i, category=cat, weight=w,
                oracle_time_days=o_day - t0_day,
                matched=False,
            ))

    # harm: agent decision actions matched to no oracle action
    harm_total = 0.0
    for cat, lst in agent_by_cat.items():
        calib = calib_table.get(cat)
        if calib is None:
            continue
        for j in range(len(lst)):
            if (cat, j) not in used_agent:
                harm_total += calib.weight

    if weight_total <= 0.0:
        return FarmFosReport(
            farm_fos=None, n_oracle_decisions=0, n_matched=0,
            weight_total=0.0, credit_total=0.0, harm_total=harm_total,
            harm_lambda=harm_lambda, per_action=per_action,
        )

    raw = (credit_total - harm_lambda * harm_total) / weight_total
    score = max(0.0, min(1.0, raw))
    return FarmFosReport(
        farm_fos=score,
        n_oracle_decisions=len(oracle_meta),
        n_matched=n_matched,
        weight_total=weight_total,
        credit_total=credit_total,
        harm_total=harm_total,
        harm_lambda=harm_lambda,
        per_action=per_action,
    )


# ---------------------------------------------------------------------------
# Baselines (computed from the SAME workflow dicts, for a fair comparison)
# ---------------------------------------------------------------------------
def _make_key(tool_name: str, tool_args: dict[str, Any] | None) -> tuple:
    if not tool_args:
        return (tool_name,)
    return (tool_name, tuple(sorted((k, str(v)) for k, v in tool_args.items())))


def baseline_bfcl(
    oracle_workflow: dict[str, dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]],
) -> float:
    """BFCL-style success rate: fraction of oracle (tool, args) calls the agent
    made, order- and time-agnostic (set overlap on the call signature).

    This is the degenerate case of FARM-FOS with o≡1, k≡1, uniform weights,
    λ=0, scored as exact (tool,args) match coverage of the oracle set.
    """
    def keyset(wf):
        ks = set()
        for step in wf.values():
            tool = step.get("tool_name")
            if not tool or step.get("op_type") == "USER":
                continue
            ks.add(_make_key(tool, step.get("tool_args")))
        return ks

    oracle_keys = keyset(oracle_workflow)
    if not oracle_keys:
        return 0.0
    agent_keys = keyset(agent_workflow)
    return len(oracle_keys & agent_keys) / len(oracle_keys)


def all_path_metrics(
    oracle_workflow: dict[str, dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]],
    harm_lambda: float = DEFAULT_HARM_LAMBDA,
) -> dict[str, Any]:
    """Compute FARM-FOS and the three baselines from one (oracle, agent) pair.

    CORE (path_correctness) and vanilla KTC (ktc_raw) come from the existing
    evaluator so we never reimplement them; BFCL and FARM-FOS are added here.
    """
    from are.simulation.scenarios.workflow_validation import evaluate_workflows

    base = evaluate_workflows(oracle_workflow, agent_workflow)
    ff = compute_farm_fos(oracle_workflow, agent_workflow, harm_lambda=harm_lambda)
    return {
        "farm_fos": ff.farm_fos,
        "bfcl_success": baseline_bfcl(oracle_workflow, agent_workflow),
        "core_path_correctness": base.get("path_correctness"),
        "ktc": base.get("ktc_raw"),
        "ktc_adjusted": base.get("ktc_adjusted"),
        "coverage": base.get("coverage"),
        "combined": base.get("combined"),
        "_farm_fos_report": ff.to_dict(),
    }


def compute_farm_fos_from_env(scenario: Any, env: Any, harm_lambda: float = DEFAULT_HARM_LAMBDA) -> FarmFosReport:
    """Convenience wrapper: extract both workflows from a live/replayed env."""
    from are.simulation.scenarios.workflow_validation import (
        ensure_oracle_workflow,
        workflow_from_event_log,
    )

    oracle_wf = ensure_oracle_workflow(scenario)
    agent_wf = workflow_from_event_log(env.event_log.list_view())
    return compute_farm_fos(oracle_wf, agent_wf, harm_lambda=harm_lambda)
