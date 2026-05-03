"""
FOS evaluation engine.

Public surface:
  - DEFAULT_WEIGHTS — the 0.5/0.3/0.2 dict
  - evaluate_fos(scenario, env, gates, weights=None) -> FOSReport
  - append_fos_evaluation(scenario, env, result, gates, ...) — mirror of
    `workflow_validation.append_workflow_evaluation`. Appends a `fos_eval:`
    line to result.rationale and saves the full report JSON next to the
    workflow JSONs the workflow validator already saves.

Internals (private, exposed for tests):
  - _compute_outcome(scenario, env) -> tuple[float, OutcomeBreakdown]
  - _match_gate(env, scenario, gate, scenario_start_time) -> GateResult
  - _compute_efficiency(scenario, env, oracle_tool_count) -> tuple[float, EfficiencyBreakdown]

Design notes:
  - Outcome reads from `farm_world.physics.yield_recovery.states` for the
    yield ratio. `scenario_potential_kg` is the engine-computed biological
    yield (`biological_yield_g_m2 × ridge_area_m2 / 1000`) summed across
    ridges that were planted at scenario start, so the ratio is a fair
    fraction of "what the field could have produced under perfect management."
  - Decision walks `env.event_log.list_view()` filtering on `EventType.AGENT`,
    converts event_time to "days since scenario_start_time", and matches
    each gate against the first eligible event in its window.
  - Efficiency uses the same agent-event list to count tool calls and
    redundant reads. A redundant read is the same READ-op tool called twice
    within a 1-hour window with no intervening WRITE op (heuristic: if you
    re-read without acting, the second read is wasted).
  - Safety violations are counted by scanning the agent event log for events
    whose returned dict contains a top-level 'error' key. The original tool
    layer enforces conditions like "soil too wet to spray" by erroring out;
    each such error is one attempted-but-blocked safety violation.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from are.simulation.scenarios.fos.gates import GateResult, GateSpec
from are.simulation.scenarios.fos.metrics import (
    EfficiencyBreakdown,
    FOSComponents,
    FOSReport,
    OutcomeBreakdown,
)


DEFAULT_WEIGHTS: dict[str, float] = {
    "outcome": 0.5,
    "decision": 0.3,
    "efficiency": 0.2,
}

_SECONDS_PER_DAY: float = 86400.0
_REDUNDANT_WINDOW_S: float = 3600.0  # 1 hour
_TOOL_INFLATION_CAP: float = 3.0
_DEFAULT_CROP_LOSS_THRESHOLD: float = 0.5  # ridge yield_ratio < 0.5 → crop loss
_SAFETY_PENALTY_PER_VIOLATION: float = 0.10
_CROP_LOSS_PENALTY_PER_RIDGE: float = 0.05


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_fos(
    scenario: Any,
    env: Any,
    gates: list[GateSpec],
    weights: dict[str, float] | None = None,
    crop_loss_threshold: float = _DEFAULT_CROP_LOSS_THRESHOLD,
) -> FOSReport:
    """Compute the full FOS report for a finished scenario run.

    Args:
        scenario: the running Scenario instance (must expose
            `scenario_id`, `start_time`, `events`, and `get_typed_app`).
        env: the simulation environment (must expose
            `event_log.list_view()`).
        gates: list of GateSpec — the per-scenario decision gates.
        weights: dict with keys outcome / decision / efficiency. Defaults
            to DEFAULT_WEIGHTS.
        crop_loss_threshold: yield-ratio below which a ridge counts as
            crop loss. Default 0.5.
    """
    weights = _coerce_weights(weights)
    scenario_start_time = float(getattr(scenario, "start_time", None) or 0.0)

    outcome_score, outcome_breakdown = _compute_outcome(
        scenario, env, crop_loss_threshold=crop_loss_threshold
    )
    decision_results = [
        _match_gate(env, scenario, gate, scenario_start_time) for gate in gates
    ]
    decision_score = (
        sum(1 for r in decision_results if r.matched) / max(1, len(decision_results))
    )

    oracle_tool_count = _oracle_tool_count(scenario)
    efficiency_score, efficiency_breakdown = _compute_efficiency(
        scenario, env, oracle_tool_count=oracle_tool_count
    )

    fos = (
        weights["outcome"] * outcome_score
        + weights["decision"] * decision_score
        + weights["efficiency"] * efficiency_score
    )
    return FOSReport(
        scenario_id=getattr(scenario, "scenario_id", "unknown"),
        components=FOSComponents(
            outcome=_clip01(outcome_score),
            decision=_clip01(decision_score),
            efficiency=_clip01(efficiency_score),
            fos=_clip01(fos),
        ),
        outcome_breakdown=outcome_breakdown,
        decision_breakdown=decision_results,
        efficiency_breakdown=efficiency_breakdown,
        weights=dict(weights),
    )


def append_fos_evaluation(
    scenario: Any,
    env: Any,
    result: Any,
    gates: list[GateSpec],
    weights: dict[str, float] | None = None,
    fos_subdir: str = "fos",
) -> Any:
    """Compute FOS, save the JSON report, and append a `fos_eval:` rationale line.

    Mirrors `workflow_validation.append_workflow_evaluation`'s contract so the
    suite-runner CSV captures FOS alongside `workflow_eval` without the runner
    needing changes. Returns the (possibly mutated) ScenarioValidationResult.
    """
    report = evaluate_fos(scenario, env, gates=gates, weights=weights)

    fos_dir = _default_dir(scenario, env) / fos_subdir
    fos_dir.mkdir(parents=True, exist_ok=True)
    fos_path = fos_dir / f"fos_{getattr(scenario, 'scenario_id', 'unknown')}.json"
    with fos_path.open("w", encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, ensure_ascii=False, indent=2)

    components = report.components
    fos_metric_text = (
        f"outcome={components.outcome:.4f}, "
        f"decision={components.decision:.4f}, "
        f"efficiency={components.efficiency:.4f}, "
        f"fos={components.fos:.4f}"
    )
    extras = (
        f"safety={report.outcome_breakdown.safety_violations}, "
        f"crop_loss={report.outcome_breakdown.crop_loss_count}, "
        f"tool_inflation={report.efficiency_breakdown.tool_inflation:.3f}, "
        f"gates_matched={sum(1 for g in report.decision_breakdown if g.matched)}/"
        f"{len(report.decision_breakdown)}"
    )
    parts = [getattr(result, "rationale", None)] if getattr(result, "rationale", None) else []
    parts.append(f"fos_eval: {fos_metric_text}; {extras}")
    parts.append(f"fos_report={fos_path}")
    result.rationale = "\n".join(p for p in parts if p)
    return result


# ---------------------------------------------------------------------------
# Outcome (O) — yield, crop loss, safety violations
# ---------------------------------------------------------------------------


def _compute_outcome(
    scenario: Any, env: Any, crop_loss_threshold: float
) -> tuple[float, OutcomeBreakdown]:
    """Compute yield_ratio + crop_loss + safety_violations from physics state."""
    farm_world = _try_get_farm_world(scenario)
    physics = getattr(farm_world, "_physics", None) if farm_world is not None else None

    recovered_kg = 0.0
    potential_kg = 0.0
    harvested_potential_kg = 0.0
    harvested_count = 0
    crop_loss_count = 0
    crop_loss_fraction = 0.0
    if physics is not None and getattr(physics, "engines_active", False):
        # Compute per-ridge yield ratio from yield_recovery state. Use only
        # ridges that were ever planted to avoid penalising the agent for
        # untouched borders or pre-prep state.
        from are.simulation.apps.farm_world.farm_world_app import (
            DEFAULT_RIDGE_WIDTH_M,
            FIELD_LENGTH_M,
        )

        ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
        planted_count = 0
        loss_count = 0
        for rid, yld_state in physics.yield_recovery.states.items():
            phen_state = physics.phenology.states.get(rid)
            ever_planted = phen_state is not None and phen_state.planted
            biological = yld_state.biological_yield_g_m2
            recovered = yld_state.recovered_yield_g_m2_at_market_moisture
            harvested = bool(getattr(yld_state, "harvested", False))
            if ever_planted or biological > 0.0:
                planted_count += 1
                potential_kg += biological * ridge_area_m2 / 1000.0
                recovered_kg += recovered * ridge_area_m2 / 1000.0
                if harvested:
                    harvested_count += 1
                    harvested_potential_kg += biological * ridge_area_m2 / 1000.0
                    # Crop loss is only counted when a harvested ridge
                    # recovered less than the threshold fraction. Pre-
                    # harvest ridges (mid-season scenarios) do NOT count.
                    if biological > 0.0:
                        ridge_ratio = recovered / biological
                        if ridge_ratio < crop_loss_threshold:
                            loss_count += 1
        crop_loss_count = loss_count
        if planted_count > 0:
            crop_loss_fraction = loss_count / planted_count

    if harvested_count > 0 and harvested_potential_kg > 0.0:
        # Scenario reached harvest on at least one ridge — yield_ratio
        # is recovered/potential restricted to harvested ridges, so
        # full-season scenarios that harvest the entire field score the
        # same as round-4-style ones, and partial harvests aren't
        # penalised for the un-harvested ridges.
        yield_ratio = recovered_kg / harvested_potential_kg
    else:
        # Mid-season episode (irrigation, fertilisation, drone survey, etc.)
        # — no harvest has happened yet. Outcome defaults to 1.0
        # ("no yield damage observed in this episode"); safety penalties
        # below subtract from this baseline.
        yield_ratio = 1.0

    safety_violations, safety_details = _count_safety_violations(env)

    outcome_raw = (
        yield_ratio
        - _SAFETY_PENALTY_PER_VIOLATION * safety_violations
        - _CROP_LOSS_PENALTY_PER_RIDGE * crop_loss_count
    )
    outcome_score = _clip01(outcome_raw)

    return outcome_score, OutcomeBreakdown(
        yield_ratio=yield_ratio,
        recovered_yield_kg=recovered_kg,
        scenario_potential_kg=potential_kg,
        crop_loss_count=crop_loss_count,
        crop_loss_fraction=crop_loss_fraction,
        safety_violations=safety_violations,
        safety_violation_details=safety_details,
    )


def _count_safety_violations(env: Any) -> tuple[int, list[dict[str, Any]]]:
    """Count agent-tool calls that returned an error.

    The legacy tool layer enforces real-world safety conditions by erroring
    out (e.g., 'Weather conditions do not allow spraying', 'Soil too wet for
    tractor operation'). Each such erroring agent call counts as one
    attempted-but-blocked safety violation.
    """
    from are.simulation.types import EventType

    event_log = _get_event_log(env)
    violations = 0
    details: list[dict[str, Any]] = []
    for event in event_log:
        if getattr(event, "event_type", None) != EventType.AGENT:
            continue
        action = getattr(event, "action", None)
        if action is None:
            continue
        if getattr(action, "class_name", None) == "AgentUserInterface":
            continue
        return_value = None
        metadata = getattr(event, "metadata", None)
        if metadata is not None:
            return_value = getattr(metadata, "return_value", None)
        if isinstance(return_value, dict) and "error" in return_value:
            error_msg = str(return_value["error"])
            if _looks_safety_relevant(error_msg):
                violations += 1
                details.append(
                    {
                        "tool": f"{action.class_name}__{action.function_name}",
                        "error": error_msg,
                        "event_time": getattr(event, "event_time", None),
                    }
                )
    return violations, details


_SAFETY_KEYWORDS: tuple[str, ...] = (
    "weather conditions do not allow",
    "soil too wet",
    "cannot harvest in rain",
    "wind",
    "rain",
    "moisture",
    "must exceed",
    "not mature enough",
    "not allow",
    "out of",
)


def _looks_safety_relevant(error_msg: str) -> bool:
    lowered = error_msg.lower()
    return any(keyword in lowered for keyword in _SAFETY_KEYWORDS)


# ---------------------------------------------------------------------------
# Decision (D) — gate-predicate matching
# ---------------------------------------------------------------------------


def _logical_day_of(
    event: Any,
    scenario_start_time: float,
    accumulated_advance_seconds: float,
) -> float:
    """Compute "day since scenario start" for an event, including time jumps.

    `event_time` is set when the event is scheduled by the dependency
    chain, not when it runs. ``SystemApp.advance_time`` mutates the
    simulation clock at runtime but does NOT shift subsequent event
    timestamps — so two events scheduled 1s apart, with an
    ``advance_time(hours=24)`` between them, both have ``event_time`` ~1s
    apart even though logically they sit 1 day apart.

    To restore the right semantics for FOS gate windows, we walk events
    in order and add the accumulated ``advance_time`` seconds to the
    raw event_time before converting to days.
    """
    raw_seconds = float(getattr(event, "event_time", scenario_start_time)) - scenario_start_time
    return (raw_seconds + accumulated_advance_seconds) / _SECONDS_PER_DAY


def _advance_time_seconds_from_event(event: Any) -> float:
    """If the event is a SystemApp.advance_time call, return the
    seconds it logically advanced; else 0."""
    action = getattr(event, "action", None)
    if action is None:
        return 0.0
    if (
        getattr(action, "class_name", None) == "SystemApp"
        and getattr(action, "function_name", None) == "advance_time"
    ):
        getter = getattr(event, "get_args", None)
        args = getter() if callable(getter) else getattr(action, "args", {}) or {}
        try:
            seconds = (
                int(args.get("seconds", 0) or 0)
                + int(args.get("minutes", 0) or 0) * 60
                + int(args.get("hours", 0) or 0) * 3600
                + int(args.get("days", 0) or 0) * 86400
            )
            return float(max(0, seconds))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _match_gate(
    env: Any, scenario: Any, gate: GateSpec, scenario_start_time: float
) -> GateResult:
    """Return GateResult for one gate against the agent event log.

    Time semantics: each event's "logical day since scenario start" is
    computed by accumulating the offsets from any ``SystemApp.advance_time``
    calls earlier in the event log. This makes ``window_days=(0.5, 3.0)``
    behave correctly across oracle replays where event scheduling
    timestamps don't reflect simulated-clock jumps.
    """
    from are.simulation.types import EventType

    event_log = _get_event_log(env)
    agent_events = [
        e for e in event_log if getattr(e, "event_type", None) == EventType.AGENT
    ]
    eligible = {(cls, fn) for cls, fn in gate.eligible_tools}
    start_day, end_day = gate.window_days

    # Walk events in order, accumulating advance_time offsets so each event's
    # logical day reflects the simulated-clock jumps that would have happened
    # by the time the event ran.
    logical_day_by_index: list[float] = []
    accumulated = 0.0
    for ev in agent_events:
        # advance_time itself is treated as occurring before the jump.
        logical_day_by_index.append(
            _logical_day_of(ev, scenario_start_time, accumulated)
        )
        accumulated += _advance_time_seconds_from_event(ev)

    rejection_reason: Optional[str] = "no candidate event in window"
    for idx, event in enumerate(agent_events):
        action = getattr(event, "action", None)
        if action is None:
            continue
        cls = getattr(action, "class_name", None)
        fn = getattr(action, "function_name", None)
        # DroneApp/RobotApp instances share the same Python class but each
        # carries its own logical app_name (e.g. "Matrice4T", "Mavic3M",
        # "Robot0"). Mirror workflow_validation._resolve_tool_name's pairing
        # so gates can target a specific drone/robot by name.
        app_name = None
        if cls in {"DroneApp", "RobotApp"}:
            app = getattr(action, "app", None)
            if app is not None:
                app_name = getattr(app, "name", None)
        if (cls, fn) not in eligible and (app_name, fn) not in eligible:
            continue
        # Window check (using logical day, not raw event_time).
        day = logical_day_by_index[idx]
        if not (start_day <= day <= end_day):
            rejection_reason = f"matched tool out of window (day={day:.2f})"
            continue
        # Precondition check.
        if gate.requires is not None:
            prior = [agent_events[i] for i in range(idx)]
            try:
                ok = bool(gate.requires(event, prior, scenario, env))
            except Exception as exc:  # pragma: no cover — defensive
                ok = False
                rejection_reason = f"requires() raised: {exc}"
                continue
            if not ok:
                rejection_reason = "precondition failed"
                continue
        # Match.
        return GateResult(
            gate=gate,
            matched=True,
            matched_event_id=getattr(event, "event_id", None),
            matched_at_day=day,
        )

    return GateResult(
        gate=gate,
        matched=False,
        rejection_reason=rejection_reason,
    )


# ---------------------------------------------------------------------------
# Efficiency (E) — tool inflation, redundant reads
# ---------------------------------------------------------------------------


def _compute_efficiency(
    scenario: Any, env: Any, oracle_tool_count: int
) -> tuple[float, EfficiencyBreakdown]:
    from are.simulation.types import EventType, OperationType

    event_log = _get_event_log(env)
    agent_events: list[Any] = []
    for event in event_log:
        if getattr(event, "event_type", None) != EventType.AGENT:
            continue
        action = getattr(event, "action", None)
        if action is None or getattr(action, "class_name", None) == "AgentUserInterface":
            continue
        agent_events.append(event)

    agent_count = len(agent_events)
    oracle_count = max(1, oracle_tool_count)  # avoid div by 0
    raw_inflation = agent_count / oracle_count if oracle_count > 0 else 0.0
    tool_inflation = min(raw_inflation, _TOOL_INFLATION_CAP)

    redundant = _count_redundant_reads(agent_events)
    redundant_fraction = (redundant / agent_count) if agent_count > 0 else 0.0

    # E score combines a penalty for over-tool-calling and a penalty for
    # redundant reads. Both are bounded in [0, 1] and multiplied so a perfect
    # score requires both: lean tool count AND no redundant reads.
    inflation_factor = _clip01(1.0 / max(1.0, tool_inflation))
    redundant_factor = _clip01(1.0 - redundant_fraction)
    efficiency = inflation_factor * redundant_factor

    return efficiency, EfficiencyBreakdown(
        agent_tool_calls=agent_count,
        oracle_tool_calls=oracle_count,
        tool_inflation=tool_inflation,
        redundant_reads=redundant,
        redundant_fraction=redundant_fraction,
    )


def _count_redundant_reads(agent_events: list[Any]) -> int:
    """A redundant read is the same READ tool called twice within
    `_REDUNDANT_WINDOW_S` simulated seconds with no intervening WRITE op
    on any tool.

    Counts each redundant occurrence (so 3 reads of the same sensor in a
    1-hour window with no write = 2 redundant reads).
    """
    from are.simulation.types import OperationType

    redundant = 0
    last_read_time: dict[tuple[str, str], float] = {}
    for event in agent_events:
        action = getattr(event, "action", None)
        if action is None:
            continue
        op = getattr(action, "operation_type", None)
        cls = getattr(action, "class_name", None)
        fn = getattr(action, "function_name", None)
        event_time = float(getattr(event, "event_time", 0.0))
        if op == OperationType.READ:
            key = (cls, fn)
            prev = last_read_time.get(key)
            if prev is not None and (event_time - prev) <= _REDUNDANT_WINDOW_S:
                redundant += 1
            last_read_time[key] = event_time
        elif op == OperationType.WRITE:
            # A write resets the redundancy clock for all tools (the world
            # has changed, so re-reading is now justified).
            last_read_time = {}
    return redundant


def _oracle_tool_count(scenario: Any) -> int:
    """Count oracle tool-action events on this scenario.

    Walks `scenario.events` looking for OracleEvent instances whose source
    Action has a class_name (i.e. real tool actions). Used as the denominator
    for tool_inflation.
    """
    from are.simulation.types import OracleEvent

    count = 0
    for event in getattr(scenario, "events", None) or []:
        if not isinstance(event, OracleEvent):
            continue
        try:
            source = event.make_event(None)
        except Exception:  # pragma: no cover
            continue
        action = getattr(source, "action", None)
        if action is None:
            continue
        if getattr(action, "class_name", None) == "AgentUserInterface":
            continue
        count += 1
    return count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_weights(weights: dict[str, float] | None) -> dict[str, float]:
    if weights is None:
        return dict(DEFAULT_WEIGHTS)
    out = {key: float(weights.get(key, DEFAULT_WEIGHTS[key])) for key in DEFAULT_WEIGHTS}
    total = sum(out.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    # Renormalise so weights sum to 1 exactly.
    return {k: v / total for k, v in out.items()}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _try_get_farm_world(scenario: Any):
    """Return scenario's FarmWorldApp, or None if unavailable."""
    if scenario is None:
        return None
    getter = getattr(scenario, "get_typed_app", None)
    if not callable(getter):
        return None
    try:
        from are.simulation.apps.farm_world import FarmWorldApp

        return getter(FarmWorldApp)
    except Exception:
        return None


def _get_event_log(env: Any) -> list[Any]:
    """Get the event log as a list, robust to None/missing fields."""
    if env is None:
        return []
    log = getattr(env, "event_log", None)
    if log is None:
        return []
    list_view = getattr(log, "list_view", None)
    if not callable(list_view):
        return []
    try:
        return list(list_view())
    except Exception:
        return []


def _default_dir(scenario: Any, env: Any) -> Path:
    """Mirror workflow_validation._default_workflow_dir.

    Order of fallbacks:
      1. ``env.dump_dir`` — set in oracle mode.
      2. ``scenario.working_dir`` — set when scenario explicitly directs it.
      3. ``$FOS_EXPORT_DIR`` env var — used by the validation runner to
         pin per-cell output and avoid concurrent-write collisions when
         many cells run in the same cwd.
      4. ``cwd/fos_exports`` — last-resort default.
    """
    env_dump_dir = getattr(env, "dump_dir", None)
    if env_dump_dir:
        return Path(env_dump_dir)
    working_dir = getattr(scenario, "working_dir", None)
    if working_dir:
        return Path(working_dir)
    explicit = os.environ.get("FOS_EXPORT_DIR")
    if explicit:
        return Path(explicit)
    return Path(os.getcwd()) / "fos_exports"
