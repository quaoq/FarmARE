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

import ast
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


@dataclass
class FarmFosV2Diagnosis:
    """Ridge-chain and parameter diagnostics for FARM-FOS-v2.

    These fields are deliberately mechanism-facing.  They are computed from
    oracle/agent workflows only; no yield or recovered-yield outcome is read.
    """

    missing_plant_ridges: int = 0
    density_error_pct: float | None = None
    depth_error_cm: float | None = None
    missing_management_actions: int = 0
    missing_harvest_ridges: int = 0
    postharvest_incomplete: bool = False
    postharvest_warning: bool = False
    early_or_late_action_days: float | None = None
    tool_error_returns: int = 0
    primary_issue: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "missing_plant_ridges": self.missing_plant_ridges,
            "density_error_pct": (
                round(self.density_error_pct, 3)
                if self.density_error_pct is not None
                else None
            ),
            "depth_error_cm": (
                round(self.depth_error_cm, 3)
                if self.depth_error_cm is not None
                else None
            ),
            "missing_management_actions": self.missing_management_actions,
            "missing_harvest_ridges": self.missing_harvest_ridges,
            "postharvest_incomplete": self.postharvest_incomplete,
            "postharvest_warning": self.postharvest_warning,
            "early_or_late_action_days": (
                round(self.early_or_late_action_days, 3)
                if self.early_or_late_action_days is not None
                else None
            ),
            "tool_error_returns": self.tool_error_returns,
            "primary_issue": self.primary_issue,
        }


@dataclass
class FarmFosV2Report:
    """FARM-FOS-v2 decomposition.

    path is the original FARM-FOS score.  param and terminal are new mechanism
    checks for agronomically important parameters and plant/harvest chain
    closure.  total combines them without reading final yield.
    """

    farm_fos_v2_total: float | None
    farm_fos_v2_path: float | None
    farm_fos_v2_param: float
    farm_fos_v2_terminal: float
    diagnosis: FarmFosV2Diagnosis

    def to_dict(self) -> dict[str, Any]:
        return {
            "farm_fos_v2_total": (
                round(self.farm_fos_v2_total, 4)
                if self.farm_fos_v2_total is not None
                else None
            ),
            "farm_fos_v2_path": (
                round(self.farm_fos_v2_path, 4)
                if self.farm_fos_v2_path is not None
                else None
            ),
            "farm_fos_v2_param": round(self.farm_fos_v2_param, 4),
            "farm_fos_v2_terminal": round(self.farm_fos_v2_terminal, 4),
            "diagnosis": self.diagnosis.to_dict(),
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
# FARM-FOS-v2: mechanism gates and parameter diagnostics
# ---------------------------------------------------------------------------
_DEFAULT_RIDGE_WIDTH_M = 1.1
_ROWS_PER_RIDGE = 2.0
_MANAGEMENT_PARAM_CATEGORIES = {
    "irrigate",
    "fungicide",
    "pesticide",
    "herbicide",
    "fertigate",
}
_PLANT_PARAM_PHASE_WEIGHT = 0.70
_MANAGEMENT_PARAM_PHASE_WEIGHT = 0.30


def _coerce_float(value: Any) -> float | None:
    if value is None or value is True or value is False:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_content(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return ast.literal_eval(content)
        except (SyntaxError, ValueError):
            return content
    return content


def _content_has_error(content: Any) -> bool:
    content = _parse_content(content)
    if not isinstance(content, dict):
        return False
    status = str(content.get("status") or "").lower()
    return bool(content.get("error") or status in {"error", "failed", "fail"})


def _content_has_warning(content: Any) -> bool:
    content = _parse_content(content)
    if not isinstance(content, dict):
        return False
    return bool(content.get("warning") or content.get("warnings"))


def _step_succeeded(step: dict[str, Any], *, oracle: bool = False) -> bool:
    """Return whether a workflow step should count as an executed action.

    Oracle workflow steps often have ``content=None`` because they are built
    from planned OracleEvents, not completed tool returns.  Those count as
    successful intended actions.  Agent workflow steps carry the recorded
    return_value; hard errors fail the gate.  Warnings still mean the action
    occurred, but v2 records them separately as quality/closure warnings.
    """

    return not _content_has_error(step.get("content"))


def _tool_func(step: dict[str, Any]) -> str:
    return str(step.get("tool_name") or "").split("__")[-1]


def _workflow_steps_in_order(
    workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(workflow, dict):
        return list(workflow.values())
    return list(workflow)


def _planting_density(width_m: float | None, spacing_cm: float | None) -> float | None:
    width = width_m if width_m and width_m > 0.0 else _DEFAULT_RIDGE_WIDTH_M
    spacing = spacing_cm if spacing_cm and spacing_cm > 0.0 else None
    if spacing is None:
        return None
    return _ROWS_PER_RIDGE / (width * (spacing / 100.0))


def _density_credit(agent_density: float | None, oracle_density: float | None) -> float:
    if agent_density is None or oracle_density is None or oracle_density <= 0.0:
        return 1.0
    if agent_density <= 0.0:
        return 0.0
    # Density errors reduce stand/yield potential proportionally.  They should
    # not zero the bio chain unless planting itself failed.
    return min(agent_density, oracle_density) / max(agent_density, oracle_density)


def _depth_credit(agent_depth: float | None, oracle_depth: float | None) -> float:
    if agent_depth is None or oracle_depth is None:
        return 1.0
    error = abs(agent_depth - oracle_depth)
    if error <= 1.0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (error - 1.0) / 2.0))


def _numeric_similarity(agent_value: Any, oracle_value: Any) -> float | None:
    a = _coerce_float(agent_value)
    o = _coerce_float(oracle_value)
    if a is None or o is None:
        return None
    if abs(o) < 1e-9:
        return 1.0 if abs(a) < 1e-9 else 0.0
    return max(0.0, min(1.0, 1.0 - abs(a - o) / abs(o)))


def _param_credit(agent_step: dict[str, Any], oracle_step: dict[str, Any]) -> float:
    """Parameter similarity for non-planting decision actions."""

    keys = (
        "liters_per_ridge",
        "amount",
        "water_mm",
        "kg_per_ridge",
        "target_moisture_pct",
    )
    agent_args = agent_step.get("tool_args") or {}
    oracle_args = oracle_step.get("tool_args") or {}
    credits = [
        sim
        for key in keys
        if (sim := _numeric_similarity(agent_args.get(key), oracle_args.get(key)))
        is not None
    ]
    if not credits:
        return 1.0
    return sum(credits) / len(credits)


def _dry_step_effective(step: dict[str, Any], *, oracle: bool) -> bool:
    if oracle:
        return True
    content = _parse_content(step.get("content"))
    if not isinstance(content, dict):
        return True
    ridges_dried = _coerce_float(content.get("ridges_dried"))
    if ridges_dried is not None:
        return ridges_dried > 0
    batch = content.get("batch_ridge_ids")
    if isinstance(batch, list):
        return bool(batch)
    return True


def _extract_chain_records(
    workflow: dict[str, dict[str, Any]] | list[dict[str, Any]], *, oracle: bool
) -> dict[str, Any]:
    """Extract ridge-level plant/harvest and terminal-chain state."""

    day_map = _season_day_map(workflow if isinstance(workflow, dict) else {
        f"step{i}": step for i, step in enumerate(workflow)
    })
    current_width = _DEFAULT_RIDGE_WIDTH_M
    planted: dict[int, dict[str, Any]] = {}
    harvested: set[int] = set()
    has_unload = False
    has_dry = False
    has_store = False
    expects_dry = False
    errors = 0
    warnings = 0
    postharvest_warning = False

    for idx, step in enumerate(_workflow_steps_in_order(workflow)):
        fn = _tool_func(step)
        if not oracle and _content_has_error(step.get("content")):
            errors += 1
        if not oracle and _content_has_warning(step.get("content")):
            warnings += 1
            if fn in {"dry_grain", "store_grain"}:
                postharvest_warning = True
        if fn == "form_ridges" and _step_succeeded(step, oracle=oracle):
            width = _coerce_float((step.get("tool_args") or {}).get("ridge_width_m"))
            if width and width > 0.0:
                current_width = width
            continue

        if not _step_succeeded(step, oracle=oracle):
            continue

        args = step.get("tool_args") or {}
        ridges = extract_ridges(args)
        if fn in {"plant_seeds", "replant_seeds"}:
            if ridges is None:
                ridges = set(range(NUM_RIDGES))
            spacing = _coerce_float(args.get("seed_spacing_cm"))
            depth = _coerce_float(args.get("depth_cm"))
            density = _planting_density(current_width, spacing)
            for rid in ridges:
                planted[rid] = {
                    "width_m": current_width,
                    "spacing_cm": spacing,
                    "depth_cm": depth,
                    "density": density,
                    "time_day": day_map.get(f"step{idx}", 0.0),
                }
        elif fn == "harvest":
            if ridges is None:
                ridges = set(range(NUM_RIDGES))
            harvested.update(ridges)
        elif fn == "unload_grain":
            has_unload = True
        elif fn == "dry_grain":
            if _dry_step_effective(step, oracle=oracle):
                has_dry = True
            expects_dry = True
        elif fn == "store_grain":
            has_store = True

    if oracle:
        # Oracle dry requirement is inferred from its planned dry_grain calls.
        expects_dry = any(_tool_func(s) == "dry_grain" for s in _workflow_steps_in_order(workflow))

    return {
        "planted": planted,
        "harvested": harvested,
        "has_unload": has_unload,
        "has_dry": has_dry,
        "has_store": has_store,
        "expects_dry": expects_dry,
        "errors": errors,
        "warnings": warnings,
        "postharvest_warning": postharvest_warning,
    }


def _matched_decision_pairs(
    oracle_workflow: dict[str, dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """Greedy category matches mirroring FARM-FOS, with pair access."""

    oracle_day_map = _season_day_map(oracle_workflow)
    agent_day_map = _season_day_map(agent_workflow)
    oracle_steps = _decision_steps(oracle_workflow, oracle_day_map)
    agent_steps = [
        (step, day)
        for step, day in _decision_steps(agent_workflow, agent_day_map)
        if _step_succeeded(step, oracle=False)
    ]
    by_cat: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for step, day in agent_steps:
        cat = category_for_tool(step.get("tool_name") or "")
        by_cat.setdefault(cat, []).append((step, day))

    candidates: list[tuple[float, int, int, float]] = []
    oracle_meta: list[tuple[dict[str, Any], float, str, ActionCalibration]] = []
    for i, (os_, o_day) in enumerate(oracle_steps):
        cat = category_for_tool(os_.get("tool_name") or "")
        calib = FROZEN_CALIBRATION.get(cat)
        if calib is None:
            continue
        oracle_meta.append((os_, o_day, cat, calib))
        o_ridges = extract_ridges(os_.get("tool_args"))
        for j, (as_, a_day) in enumerate(by_cat.get(cat, [])):
            ov = spatial_overlap(extract_ridges(as_.get("tool_args")), o_ridges)
            delta = a_day - o_day
            k = temporal_kernel(delta, calib)
            credit = ov * k
            if credit > 0.0:
                candidates.append((credit, i, j, delta))

    candidates.sort(key=lambda c: c[0], reverse=True)
    used_o: set[int] = set()
    used_a: set[tuple[str, int]] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    for _credit, i, j, delta in candidates:
        os_, _o_day, cat, _calib = oracle_meta[i]
        if i in used_o or (cat, j) in used_a:
            continue
        agent_step = by_cat[cat][j][0]
        used_o.add(i)
        used_a.add((cat, j))
        pairs.append((os_, agent_step, delta))
    return pairs


def _decision_match_records(
    oracle_workflow: dict[str, dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any] | None, float | None, str, ActionCalibration]]:
    """Greedy category matches, retaining unmatched oracle decisions."""

    oracle_day_map = _season_day_map(oracle_workflow)
    agent_day_map = _season_day_map(agent_workflow)
    oracle_steps = _decision_steps(oracle_workflow, oracle_day_map)
    agent_steps = [
        (step, day)
        for step, day in _decision_steps(agent_workflow, agent_day_map)
        if _step_succeeded(step, oracle=False)
    ]
    by_cat: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for step, day in agent_steps:
        cat = category_for_tool(step.get("tool_name") or "")
        by_cat.setdefault(cat, []).append((step, day))

    candidates: list[tuple[float, int, int, float]] = []
    oracle_meta: list[tuple[dict[str, Any], float, str, ActionCalibration]] = []
    for i, (os_, o_day) in enumerate(oracle_steps):
        cat = category_for_tool(os_.get("tool_name") or "")
        calib = FROZEN_CALIBRATION.get(cat)
        if calib is None:
            continue
        oracle_meta.append((os_, o_day, cat, calib))
        o_ridges = extract_ridges(os_.get("tool_args"))
        for j, (as_, a_day) in enumerate(by_cat.get(cat, [])):
            ov = spatial_overlap(extract_ridges(as_.get("tool_args")), o_ridges)
            delta = a_day - o_day
            k = temporal_kernel(delta, calib)
            credit = ov * k
            if credit > 0.0:
                candidates.append((credit, i, j, delta))

    candidates.sort(key=lambda c: c[0], reverse=True)
    used_o: set[int] = set()
    used_a: set[tuple[str, int]] = set()
    best_for_oracle: dict[int, tuple[dict[str, Any], float]] = {}
    for _credit, i, j, delta in candidates:
        _os, _o_day, cat, _calib = oracle_meta[i]
        if i in used_o or (cat, j) in used_a:
            continue
        used_o.add(i)
        used_a.add((cat, j))
        best_for_oracle[i] = (by_cat[cat][j][0], delta)

    records: list[tuple[dict[str, Any], dict[str, Any] | None, float | None, str, ActionCalibration]] = []
    for i, (oracle_step, _o_day, cat, calib) in enumerate(oracle_meta):
        match = best_for_oracle.get(i)
        if match is None:
            records.append((oracle_step, None, None, cat, calib))
        else:
            agent_step, delta = match
            records.append((oracle_step, agent_step, delta, cat, calib))
    return records


def compute_farm_fos_v2(
    oracle_workflow: dict[str, dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]],
    harm_lambda: float = DEFAULT_HARM_LAMBDA,
) -> FarmFosV2Report:
    """Compute FARM-FOS-v2 from the same workflow dicts as FARM-FOS.

    The score is yield-independent: it uses only action timing, ridge targets,
    tool return success/error state, and agronomic parameters encoded in calls.
    """

    path_report = compute_farm_fos(oracle_workflow, agent_workflow, harm_lambda=harm_lambda)
    path_score = path_report.farm_fos

    oracle_chain = _extract_chain_records(oracle_workflow, oracle=True)
    agent_chain = _extract_chain_records(agent_workflow, oracle=False)

    oracle_planted = set(oracle_chain["planted"])
    agent_planted = set(agent_chain["planted"])
    missing_plant = oracle_planted - agent_planted

    plant_param_credits: list[float] = []
    density_errors: list[float] = []
    depth_errors: list[float] = []
    for rid in sorted(oracle_planted & agent_planted):
        o = oracle_chain["planted"][rid]
        a = agent_chain["planted"][rid]
        dc = _density_credit(a.get("density"), o.get("density"))
        depc = _depth_credit(a.get("depth_cm"), o.get("depth_cm"))
        # Planting parameters are yield-chain gates.  A correct depth cannot
        # rescue a badly wrong density, so compose them multiplicatively.
        plant_param_credits.append(dc * depc)
        if o.get("density"):
            density_errors.append(abs(float(a.get("density") or 0.0) - float(o["density"])) / float(o["density"]))
        if o.get("depth_cm") is not None and a.get("depth_cm") is not None:
            depth_errors.append(abs(float(a["depth_cm"]) - float(o["depth_cm"])))

    non_plant_param_credits: list[tuple[float, float]] = []
    delta_days: list[float] = []
    missing_management_actions = 0
    for oracle_step, agent_step, delta, cat, calib in _decision_match_records(oracle_workflow, agent_workflow):
        if delta is not None:
            delta_days.append(delta)
        if cat not in _MANAGEMENT_PARAM_CATEGORIES:
            continue
        if agent_step is None:
            non_plant_param_credits.append((calib.weight, 0.0))
            missing_management_actions += 1
        else:
            non_plant_param_credits.append((calib.weight, _param_credit(agent_step, oracle_step)))

    plant_param_score = None
    if oracle_planted:
        plant_param_score = (
            sum(plant_param_credits) / len(plant_param_credits)
            if plant_param_credits
            else 0.0
        )

    weighted_param_sum = 0.0
    weighted_param_total = 0.0
    for weight, credit in non_plant_param_credits:
        weighted_param_sum += weight * credit
        weighted_param_total += weight
    other_param_score = (
        weighted_param_sum / weighted_param_total
        if weighted_param_total > 0.0
        else None
    )
    if plant_param_score is not None and other_param_score is not None:
        param_score = (
            _PLANT_PARAM_PHASE_WEIGHT * plant_param_score
            + _MANAGEMENT_PARAM_PHASE_WEIGHT * other_param_score
        )
    elif plant_param_score is not None:
        param_score = plant_param_score
    elif other_param_score is not None:
        param_score = other_param_score
    else:
        param_score = 1.0
    param_score = max(0.0, min(1.0, param_score))

    oracle_harvest = set(oracle_chain["harvested"])
    agent_harvest = set(agent_chain["harvested"])
    missing_harvest = oracle_harvest - agent_harvest

    plant_coverage = (
        len(oracle_planted & agent_planted) / len(oracle_planted)
        if oracle_planted
        else 1.0
    )
    harvest_expected = bool(oracle_harvest)
    harvest_coverage = (
        len(oracle_harvest & agent_harvest) / len(oracle_harvest)
        if oracle_harvest
        else 1.0
    )
    closure = 1.0
    postharvest_incomplete = False
    if harvest_expected and harvest_coverage > 0.0:
        if not agent_chain["has_unload"]:
            closure *= 0.4
            postharvest_incomplete = True
        if not agent_chain["has_store"]:
            closure *= 0.3
            postharvest_incomplete = True
        if oracle_chain["expects_dry"] and not agent_chain["has_dry"]:
            closure *= 0.85
            postharvest_incomplete = True
        if agent_chain["postharvest_warning"]:
            closure *= 0.9
    elif harvest_expected:
        closure = 0.0
        postharvest_incomplete = True

    terminal_score = plant_coverage
    if harvest_expected:
        terminal_score = plant_coverage * harvest_coverage * closure
    terminal_score = max(0.0, min(1.0, terminal_score))

    total_score = None
    if path_score is not None:
        # Plant/harvest gates and agronomic parameters define the yield chain.
        # Path similarity should modulate that chain, but not zero a run that
        # planted, harvested, and stored with reasonable parameters.
        path_modulator = 0.6 + 0.4 * path_score
        total_score = param_score * terminal_score * path_modulator
        total_score = max(0.0, min(1.0, total_score))

    diagnosis = FarmFosV2Diagnosis(
        missing_plant_ridges=len(missing_plant),
        density_error_pct=(
            100.0 * sum(density_errors) / len(density_errors)
            if density_errors
            else None
        ),
        depth_error_cm=(
            sum(depth_errors) / len(depth_errors)
            if depth_errors
            else None
        ),
        missing_management_actions=missing_management_actions,
        missing_harvest_ridges=len(missing_harvest),
        postharvest_incomplete=postharvest_incomplete,
        postharvest_warning=bool(agent_chain["postharvest_warning"]),
        early_or_late_action_days=(
            max(delta_days, key=lambda d: abs(d)) if delta_days else None
        ),
        tool_error_returns=int(agent_chain["errors"]),
    )
    if diagnosis.missing_harvest_ridges:
        harvest_total = len(oracle_harvest)
        if harvest_total and diagnosis.missing_harvest_ridges < harvest_total:
            diagnosis.primary_issue = (
                f"partial recovered loss: {diagnosis.missing_harvest_ridges}/"
                f"{harvest_total} oracle harvest ridges were not successfully harvested"
            )
        else:
            diagnosis.primary_issue = (
                f"recovered chain broken: {diagnosis.missing_harvest_ridges} "
                "oracle harvest ridges were not successfully harvested"
            )
    elif diagnosis.postharvest_incomplete:
        diagnosis.primary_issue = "postharvest chain incomplete after harvest"
    elif diagnosis.postharvest_warning:
        diagnosis.primary_issue = "postharvest warning after storage/drying"
    elif diagnosis.missing_management_actions:
        diagnosis.primary_issue = (
            f"missing or late management actions: {diagnosis.missing_management_actions}"
        )
    elif diagnosis.missing_plant_ridges:
        diagnosis.primary_issue = (
            f"bio chain broken: {diagnosis.missing_plant_ridges} "
            "oracle planting ridges were not successfully planted"
        )
    elif diagnosis.density_error_pct is not None and diagnosis.density_error_pct >= 15.0:
        diagnosis.primary_issue = (
            f"planting density differs by {diagnosis.density_error_pct:.1f}%"
        )
    elif diagnosis.tool_error_returns >= 10:
        diagnosis.primary_issue = f"{diagnosis.tool_error_returns} tool error/warning returns"
    else:
        diagnosis.primary_issue = "no major v2 gate issue detected"

    return FarmFosV2Report(
        farm_fos_v2_total=total_score,
        farm_fos_v2_path=path_score,
        farm_fos_v2_param=param_score,
        farm_fos_v2_terminal=terminal_score,
        diagnosis=diagnosis,
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
    ff2 = compute_farm_fos_v2(oracle_workflow, agent_workflow, harm_lambda=harm_lambda)
    return {
        "farm_fos": ff.farm_fos,
        "farm_fos_v2_total": ff2.farm_fos_v2_total,
        "farm_fos_v2_path": ff2.farm_fos_v2_path,
        "farm_fos_v2_param": ff2.farm_fos_v2_param,
        "farm_fos_v2_terminal": ff2.farm_fos_v2_terminal,
        "bfcl_success": baseline_bfcl(oracle_workflow, agent_workflow),
        "core_path_correctness": base.get("path_correctness"),
        "ktc": base.get("ktc_raw"),
        "ktc_adjusted": base.get("ktc_adjusted"),
        "coverage": base.get("coverage"),
        "combined": base.get("combined"),
        "_farm_fos_report": ff.to_dict(),
        "_farm_fos_v2_report": ff2.to_dict(),
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
