"""
Reusable precondition predicates for FOS gate matching.

These are factory functions returning `PreconditionPredicate`s suitable for the
`requires` field of `GateSpec`. Each is a pure function over the candidate
event, prior events, scenario, and env — no side effects.

Use these to compose gates that need more than tool-name matching, e.g.:

    GateSpec(
        name="irrigate_after_drought_observation",
        intent="...",
        window_days=(60, 95),
        eligible_tools=[("FieldOpsApp", "irrigate_range")],
        requires=and_(
            after_observation("SensorApp", "read_soil_sensors"),
            targets_ridges_overlap(20, 43),
            min_arg("duration_hours", 1.0),
        ),
    )
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from are.simulation.scenarios.fos.gates import PreconditionPredicate


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------


def and_(*predicates: PreconditionPredicate) -> PreconditionPredicate:
    """Composite predicate that returns True iff every input returns True."""
    def composite(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        return all(p(event, prior, scenario, env) for p in predicates)
    return composite


def or_(*predicates: PreconditionPredicate) -> PreconditionPredicate:
    """Composite predicate that returns True iff any input returns True."""
    def composite(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        return any(p(event, prior, scenario, env) for p in predicates)
    return composite


def not_(predicate: PreconditionPredicate) -> PreconditionPredicate:
    """Negates a predicate."""
    def composite(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        return not predicate(event, prior, scenario, env)
    return composite


# ---------------------------------------------------------------------------
# History-based predicates
# ---------------------------------------------------------------------------


def _action_matches_class(action: Any, class_name: str) -> bool:
    """Match a tool's class — handles DroneApp/RobotApp instances whose
    logical app_name (e.g. 'Mavic3M', 'Matrice4T', 'Robot0') is what
    scenarios refer to. Mirrors `_match_gate` in evaluation.py.
    """
    if getattr(action, "class_name", None) == class_name:
        return True
    cls = getattr(action, "class_name", None)
    if cls in {"DroneApp", "RobotApp"}:
        app = getattr(action, "app", None)
        if app is not None and getattr(app, "name", None) == class_name:
            return True
    return False


def after_observation(
    class_name: str, function_name: str | None = None
) -> PreconditionPredicate:
    """Match only if the agent invoked `class_name.function_name` earlier.

    If ``function_name`` is None, any tool on that app counts as the
    precondition.

    Useful for gates like "irrigate after observing low soil moisture": the
    agent must have called read_soil_sensors before the irrigate tool.
    For DroneApp/RobotApp, ``class_name`` may be the logical instance name
    (e.g. ``"Mavic3M"``, ``"Robot0"``) rather than the Python class.
    """
    def predicate(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        for prev in prior:
            action = getattr(prev, "action", None)
            if action is None:
                continue
            if not _action_matches_class(action, class_name):
                continue
            if function_name is None:
                return True
            if getattr(action, "function_name", None) == function_name:
                return True
        return False
    return predicate


def after_any_of(
    tool_pairs: Iterable[tuple[str, str | None]],
) -> PreconditionPredicate:
    """Match only if any of the given (class_name, function_name?) pairs was
    seen earlier. None for function_name means any tool on that class.
    Handles DroneApp/RobotApp app_name aliasing the same way as after_observation.
    """
    pairs = list(tool_pairs)

    def predicate(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        for prev in prior:
            action = getattr(prev, "action", None)
            if action is None:
                continue
            fn = getattr(action, "function_name", None)
            for want_cls, want_fn in pairs:
                if not _action_matches_class(action, want_cls):
                    continue
                if want_fn is None or want_fn == fn:
                    return True
        return False
    return predicate


# ---------------------------------------------------------------------------
# Argument-based predicates
# ---------------------------------------------------------------------------


def _candidate_args(event: Any) -> dict[str, Any]:
    """Extract args from a CompletedEvent, handling the optional get_args()."""
    getter = getattr(event, "get_args", None)
    if callable(getter):
        try:
            args = getter()
            if isinstance(args, dict):
                return args
        except Exception:
            pass
    action = getattr(event, "action", None)
    if action is not None:
        resolved = getattr(action, "resolved_args", None) or getattr(action, "args", None)
        if isinstance(resolved, dict):
            return resolved
    return {}


def targets_ridges_overlap(start: int, end: int) -> PreconditionPredicate:
    """Match only if the candidate event's ridge range overlaps [start, end].

    Looks at args ``start_ridge``/``end_ridge``, ``start``/``end``, or a single
    ``ridge_id``. Returns True if there's any overlap with [start, end].
    """
    def predicate(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        args = _candidate_args(event)
        # Ridge-range tools
        s = args.get("start_ridge", args.get("start"))
        e = args.get("end_ridge", args.get("end"))
        if s is not None and e is not None:
            try:
                return int(e) >= start and int(s) <= end
            except (TypeError, ValueError):
                return False
        # Single-ridge tools
        rid = args.get("ridge_id")
        if rid is not None:
            try:
                return start <= int(rid) <= end
            except (TypeError, ValueError):
                return False
        return False
    return predicate


def min_arg(arg_name: str, threshold: float) -> PreconditionPredicate:
    """Match only if event's ``arg_name`` ≥ threshold (numeric coerce)."""
    def predicate(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        args = _candidate_args(event)
        if arg_name not in args:
            return False
        try:
            return float(args[arg_name]) >= float(threshold)
        except (TypeError, ValueError):
            return False
    return predicate


def max_arg(arg_name: str, threshold: float) -> PreconditionPredicate:
    """Match only if event's ``arg_name`` ≤ threshold."""
    def predicate(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        args = _candidate_args(event)
        if arg_name not in args:
            return False
        try:
            return float(args[arg_name]) <= float(threshold)
        except (TypeError, ValueError):
            return False
    return predicate


def arg_equals(arg_name: str, value: Any) -> PreconditionPredicate:
    """Match only if event's ``arg_name`` == value."""
    def predicate(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        args = _candidate_args(event)
        return args.get(arg_name) == value
    return predicate


def succeeded() -> PreconditionPredicate:
    """Match only if the candidate event did not raise an exception.

    Useful for distinguishing 'agent attempted irrigate but it errored out'
    from 'agent successfully irrigated'.
    """
    def predicate(event: Any, prior: list[Any], scenario: Any, env: Any) -> bool:
        failed_method = getattr(event, "failed", None)
        if callable(failed_method):
            try:
                return not failed_method()
            except Exception:
                return True
        return True
    return predicate
