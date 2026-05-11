"""
Batch re-evaluation of FOS over saved cell traces, without LLM cost.

For each cell directory under <root> (typical layout:
phase5_paper_matrix/<family>__<scenario>__rN/):

    1. Parse the trace JSON (`scenario_*.json`).
    2. Re-instantiate the scenario from the registry by `scenario_id`
       — this gets us a fresh, fully-wired set of apps including the
       physics orchestrator with its initial state.
    3. Replay the trace's `completed_events` against the fresh
       scenario's apps. The agent's tool-mutating actions
       (plant_seeds, irrigate, advance_time, harvest, ...) re-drive
       the physics engines forward. Read-only actions are no-ops on
       state but still get logged so FOS Decision/Efficiency see the
       same agent history.
    4. Build a minimal env-stub holding the replayed event log.
    5. Call `evaluate_fos(...)` with the *current* FOS code (e.g.
       Scheme B + extrapolate_to_maturity) and emit a per-cell JSON
       under <out_root>/<rel_cell_dir>/fos/fos_<scenario>.json plus
       one row in <out_root>/summary_v2.csv (v3 column schema, with agent_family,llm_model,run_level,detail_status,a2a_status plus pct_yield_loss and 100-scale metrics).

Why this matters:
    - Scheme B's `growing_loss / unharvested_mature` cannot be derived
      from the existing fos_*.json because they need physics state
      that's not in the trace. Replay reconstructs it.
    - `extrapolate_to_maturity` needs the post-replay physics state
      so the orchestrator can tick forward to R8.
    - Zero LLM dollars: we never re-call the agent. We only re-execute
      its already-recorded tool calls.

Usage:
    .venv312/bin/python scripts/rebatch_fos_from_traces.py \\
        --root validation_runs/iclr_sweep_qwen_l220260506T061339Z/phase5_paper_matrix \\
        --out-root validation_runs/iclr_sweep_qwen_reeval_v2 \\
        --extrapolate \\
        --workers 4

Caveats:
    - The replay assumes the scenario's `init_and_populate_apps()` is
      deterministic given (seed, start_time). All farm scenarios on this
      branch satisfy that.
    - Scenarios that aren't in the registry are skipped with status
      "scenario_not_in_registry".
    - Per-tool exceptions during replay are tolerated (agents in the
      original run also hit these); the event still gets logged with
      whatever return_value the trace recorded so safety-violation
      counting stays accurate.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Repo root to sys.path so `from are.simulation...` works when this
# script is run directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_DEFAULT_EXCLUDED_SWEEP_DIRS: tuple[str, ...] = (
)


# ---------------------------------------------------------------------------
# Replay primitives
# ---------------------------------------------------------------------------


def _parse_arg_value(value: Any, value_type: str | None) -> Any:
    """Recover a Python value from the trace's stringified form.

    Trace JSON stores all args as `{"name": ..., "value": <stringified>,
    "value_type": "int" | "str" | "list" | ...}`. The simplest
    round-trip is: if value_type names a numeric or bool primitive, eval
    via `ast.literal_eval`; otherwise pass through the raw value.
    """
    if value is None:
        return None
    import ast

    if value_type in {"int", "float", "bool", "list", "dict", "tuple", "NoneType"}:
        try:
            return ast.literal_eval(value if isinstance(value, str) else str(value))
        except (ValueError, SyntaxError):
            return value
    if value_type == "str":
        return value
    # Fallback: try literal_eval, else return as-is.
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    return value


def _build_completed_event(
    raw_ce: dict[str, Any],
    target_app: Any,
    method_name: str,
    args: dict[str, Any],
    return_value: Any,
    exception: Any,
):
    """Assemble a CompletedEvent that FOS can introspect.

    We bind action.app to the *real* scenario app (so .class_name and
    .app.name match what gates expect) and stash return_value on
    metadata so safety-violation counting works.
    """
    from are.simulation.types import (
        Action,
        CompletedEvent,
        EventMetadata,
        EventType,
        OperationType,
    )

    raw_action = raw_ce.get("action") or {}
    op_str = raw_action.get("operation_type") or "READ"
    try:
        op = OperationType(op_str.lower())
    except ValueError:
        op = OperationType.READ

    method = getattr(target_app, method_name, None)
    if method is None:
        # Fallback: any callable; the only consumer is FOS's class_name /
        # function_name lookups which read from action.app.__class__ and
        # the Action.function attribute name.
        method = lambda **_kw: None  # noqa: E731

    action = Action(
        function=method,
        app=target_app,
        args={k: v for k, v in args.items() if k != "self"},
        operation_type=op,
        action_id=raw_action.get("action_id"),
    )

    metadata = EventMetadata(
        return_value=return_value,
        exception=str(exception) if exception is not None else None,
        exception_stack_trace=None,
    )

    et_str = raw_ce.get("event_type") or "AGENT"
    try:
        et = EventType(et_str)
    except ValueError:
        et = EventType.AGENT

    return CompletedEvent(
        event_type=et,
        event_time=float(raw_ce.get("event_time", 0.0)),
        event_id=raw_ce.get("event_id", ""),
        action=action,
        metadata=metadata,
        dependencies=raw_ce.get("dependencies", []) or [],
    )


def _instantiate_scenario_for_replay(scenario_id: str, start_time: float, seed: int):
    """Look up the scenario class in the registry and prepare it for
    replay (apps + physics initialised, but events flow not driven)."""
    from are.simulation.scenarios.utils.registry import registry

    cls = registry.get_scenario(scenario_id)
    scenario = cls()
    if start_time is not None:
        scenario.start_time = float(start_time)
    if seed is not None:
        scenario.seed = int(seed)
    # initialize() calls init_and_populate_apps() (apps + physics layers)
    # AND build_events_flow() (scenario events). The events are
    # benign here — we don't run them through the env, only call tools
    # directly via the agent's trace.
    scenario.initialize()
    return scenario


def _replay_trace(trace_path: Path) -> tuple[Any, Any, dict[str, Any]]:
    """Returns (scenario, env, info) where env has a populated event_log.

    Raises on hard failure (scenario not registerable, malformed JSON).
    """
    from are.simulation.types import EventLog

    raw = json.loads(trace_path.read_text())
    meta = raw.get("metadata", {}).get("definition", {})
    scenario_id = meta.get("scenario_id")
    if not scenario_id:
        raise ValueError("trace has no scenario_id")
    start_time = meta.get("start_time")
    seed = meta.get("seed") or 0

    scenario = _instantiate_scenario_for_replay(scenario_id, start_time, seed)

    apps_by_name = {a.name: a for a in scenario.apps or []}
    apps_by_class = {a.__class__.__name__: a for a in scenario.apps or []}

    info = {
        "scenario_id": scenario_id,
        "start_time": start_time,
        "seed": seed,
        "n_completed_events": 0,
        "n_replayed": 0,
        "n_skipped": 0,
        "skipped_reasons": {},
        "exec_errors": 0,
    }

    rebound_events = []
    raw_completed = raw.get("completed_events", []) or []
    info["n_completed_events"] = len(raw_completed)

    for raw_ce in raw_completed:
        action = raw_ce.get("action") or {}
        # Skip ConditionCheckAction events — no app/function.
        if action.get("class_name") == "ConditionCheckAction":
            info["n_skipped"] += 1
            info["skipped_reasons"]["condition_check"] = (
                info["skipped_reasons"].get("condition_check", 0) + 1
            )
            continue
        target_app_name = action.get("app")
        fn_name = action.get("function")
        if not target_app_name or not fn_name:
            info["n_skipped"] += 1
            info["skipped_reasons"]["no_app_or_fn"] = (
                info["skipped_reasons"].get("no_app_or_fn", 0) + 1
            )
            continue
        target_app = apps_by_name.get(target_app_name) or apps_by_class.get(
            target_app_name
        )
        if target_app is None:
            info["n_skipped"] += 1
            info["skipped_reasons"][f"unknown_app:{target_app_name}"] = (
                info["skipped_reasons"].get(f"unknown_app:{target_app_name}", 0) + 1
            )
            continue

        # Parse args list of dicts -> kwargs dict.
        args: dict[str, Any] = {}
        for arg in action.get("args") or []:
            if not isinstance(arg, dict):
                continue
            name = arg.get("name")
            if name is None or name == "self":
                continue
            args[name] = _parse_arg_value(arg.get("value"), arg.get("value_type"))

        # Re-execute the tool to drive physics state. Capture exceptions
        # but do not propagate them — they're a normal part of agent
        # runs (e.g., "Cannot harvest in rain").
        exception_obj = None
        try:
            method = getattr(target_app, fn_name, None)
            if callable(method):
                method(**args)
        except Exception as exc:  # pragma: no cover — defensive
            exception_obj = exc
            info["exec_errors"] += 1

        # Build a CompletedEvent with the trace's recorded return_value
        # (so FOS sees the same return values the agent saw — including
        # safety-violation error dicts).
        return_value = (raw_ce.get("metadata") or {}).get("return_value")
        rebound = _build_completed_event(
            raw_ce, target_app, fn_name, args, return_value, exception_obj
        )
        rebound_events.append(rebound)
        info["n_replayed"] += 1

    env = SimpleNamespace(
        event_log=EventLog.from_list_view(rebound_events),
        dump_dir=None,
    )
    return scenario, env, info


# ---------------------------------------------------------------------------
# Per-cell driver
# ---------------------------------------------------------------------------


def _classify_level(scenario_id: str) -> str:
    if "fullseason" in scenario_id or "full_season" in scenario_id:
        return "level3_fullseason"
    if scenario_id.startswith("scenario_physics_") or scenario_id.startswith(
        "scenario_full_season_"
    ):
        return "level2_episode"
    if "_physics_action_tick" in scenario_id:
        return "level1_baseline"
    return "unknown"


def _gates_for_scenario(scenario: Any) -> list[Any]:
    """Look up the scenario's _gates() if present, else empty."""
    fn = getattr(scenario, "_gates", None)
    if not callable(fn):
        return []
    try:
        return list(fn())
    except Exception:
        return []


def _parse_run_slug(cell_dir: Path, out_root: Path) -> dict[str, Any]:
    """Extract llm, level, detail, a2a from the run-slug directory.

    The run-slug sits one level above the cell in the ``phase5_paper_matrix/``
    layout, e.g.::

        validation_runs/iclr_<ts>/phase5_paper_matrix/deepseek_level1_detail_false_a2a_off/<cell>/

    When the cell_dir already lives under *out_root* (after a previous
    rebatch run), the run-slug info is not available from the path; we leave
    those fields empty so the caller can supply them via CLI override.
    """
    parts = Path(cell_dir).parts
    slug = ""
    for i, part in enumerate(parts):
        if part == "phase5_paper_matrix":
            if i + 1 < len(parts):
                slug = parts[i + 1]
            break
    if not slug:
        return {"llm_model": "", "run_level": "", "detail_status": "", "a2a_status": ""}

    # e.g. "deepseek_level1_detail_false_a2a_off"
    mapping = {
        "llm_model": "", "run_level": "", "detail_status": "", "a2a_status": "",
    }

    slug_lower = slug.lower()

    # Level
    for lvl in ("level4", "level3", "level2", "level1"):
        if lvl in slug_lower:
            mapping["run_level"] = lvl
            break

    # Model (ordered by specificity)
    if slug_lower.startswith("qwen"):
        mapping["llm_model"] = "Qwen"
    elif slug_lower.startswith("deepseek"):
        mapping["llm_model"] = "DeepSeek"
    elif slug_lower.startswith("gpt"):
        mapping["llm_model"] = "GPT"

    # Detail
    if "detail_true" in slug_lower:
        mapping["detail_status"] = "True"
    elif "detail_false" in slug_lower:
        mapping["detail_status"] = "False"
    elif "detail_kwoo" in slug_lower:
        mapping["detail_status"] = "Kwoo"
    else:
        mapping["detail_status"] = "unknown"

    # A2A
    if "a2a_on" in slug_lower or "a2a_true" in slug_lower:
        mapping["a2a_status"] = "on"
    elif "a2a_off" in slug_lower or "a2a_false" in slug_lower:
        mapping["a2a_status"] = "off"
    else:
        mapping["a2a_status"] = "unknown"

    return mapping


def _pct(v: Any) -> str:
    """Convert a 0-1 decimal to 100-scale with 2 decimal places.

    Returns a string like ``97.88`` (no % suffix). Non-numeric or out-of-range
    values pass through as empty string.
    """
    if v is None or v == "" or v is True or v is False:
        return ""
    try:
        fv = float(v)
    except (ValueError, TypeError):
        return ""
    return f"{fv * 100:.2f}"


def _agent_family_from_cell(cell_name: str) -> str:
    """Extract the agent family name from the cell directory name.

    E.g. ``farm_adaptive_verifier__scenario_xxx__r1`` → ``adaptive_verifier``.
    """
    if "__" not in cell_name:
        return ""
    return cell_name.split("__", 1)[0].removeprefix("farm_")


def replay_one_cell(
    cell_dir: Path,
    out_root: Path,
    extrapolate: bool,
    extrapolation_max_days: int,
    oracle_baseline_dir: Path | None = None,
    donothing_inline: bool = False,
    path_correctness_v2: bool = True,
    pc2_tol: float = 2.0,
    focus_ridges_config: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    """Replay one cell + re-eval. Returns a CSV-ready row dict."""
    rel = cell_dir.name
    slug_info = _parse_run_slug(cell_dir, out_root)
    row: dict[str, Any] = {
        "cell": rel,
        "cell_dir": str(cell_dir),
        "agent_family": _agent_family_from_cell(rel),
        "status": "ok",
    }
    row.update(slug_info)
    trace_path = next(iter(cell_dir.glob("scenario_*.json")), None)
    if trace_path is None:
        row["status"] = "no_trace"
        return row

    # ---- Inline do-nothing baseline (optional, no pre-built JSON needed) ----
    # Computed BEFORE the agent replay from a fresh scenario instance that
    # shares the same (scenario_id, start_time, seed) so the physics seed
    # is identical.  We read the raw trace metadata to get these fields
    # before calling _replay_trace, which destroys the fresh state.
    inline_donothing_kg: float | None = None
    inline_donothing_per_ridge: list[float] | None = None
    _trace_scenario_id: str | None = None
    if donothing_inline and extrapolate:
        try:
            raw_meta = json.loads(trace_path.read_text()).get("metadata", {}).get("definition", {})
            _dn_scenario_id = raw_meta.get("scenario_id")
            _trace_scenario_id = _dn_scenario_id
            _dn_start_time = raw_meta.get("start_time")
            _dn_seed = int(raw_meta.get("seed") or 0)
            if _dn_scenario_id:
                # Use the per-ridge variant so we get both the total kg AND
                # the per-ridge yield array — needed to score focus subsets
                # without a pre-built baseline JSON.
                from are.simulation.scenarios.fos.evaluation import (
                    compute_donothing_per_ridge_yields,
                )
                total, per_ridge = compute_donothing_per_ridge_yields(
                    _dn_scenario_id,
                    start_time=_dn_start_time,
                    seed=_dn_seed,
                    extrapolation_max_days=extrapolation_max_days,
                )
                inline_donothing_kg = total
                inline_donothing_per_ridge = per_ridge
        except Exception:
            inline_donothing_kg = None
            inline_donothing_per_ridge = None

    try:
        scenario, env, info = _replay_trace(trace_path)
    except KeyError as exc:
        row["status"] = "scenario_not_in_registry"
        row["error"] = str(exc)
        return row
    except Exception as exc:
        row["status"] = "replay_failed"
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc(limit=3)
        return row

    row["scenario"] = info["scenario_id"]
    row["level"] = _classify_level(info["scenario_id"])
    row["replayed_events"] = info["n_replayed"]
    row["replay_skipped"] = info["n_skipped"]
    row["replay_exec_errors"] = info["exec_errors"]
    if inline_donothing_kg is not None:
        row["donothing_biological_kg_inline"] = round(inline_donothing_kg, 2)

    # Per-cell FOS export dir to avoid cross-process write collisions.
    cell_out = out_root / rel
    fos_dir = cell_out / "fos"
    fos_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FOS_EXPORT_DIR"] = str(fos_dir.parent)

    gates = _gates_for_scenario(scenario)

    # Resolve focus ridge ids from the per-scenario JSON config (if provided).
    # Looked up by scenario_id; missing keys mean "no focus subset for this cell".
    cell_focus_ridge_ids: list[int] | None = None
    if focus_ridges_config:
        sid = info["scenario_id"]
        raw_ids = focus_ridges_config.get(sid)
        if isinstance(raw_ids, list):
            try:
                cell_focus_ridge_ids = [int(x) for x in raw_ids]
            except (TypeError, ValueError):
                cell_focus_ridge_ids = None

    try:
        from are.simulation.scenarios.fos.evaluation import evaluate_fos

        report = evaluate_fos(
            scenario,
            env,
            gates=gates,
            extrapolate_to_maturity=extrapolate,
            extrapolation_max_days=extrapolation_max_days,
            oracle_baseline_dir=oracle_baseline_dir,
            donothing_biological_kg=inline_donothing_kg,
            donothing_per_ridge_g_m2=inline_donothing_per_ridge,
            focus_ridge_ids=cell_focus_ridge_ids,
        )
    except Exception as exc:
        row["status"] = "eval_failed"
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc(limit=3)
        return row

    # Persist the structured report.
    fos_path = fos_dir / f"fos_{info['scenario_id']}.json"
    fos_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    row["fos_path"] = str(fos_path)

    comp = report.components
    ob = report.outcome_breakdown
    eb = report.efficiency_breakdown

    # Extract yield-preserved ratio as raw decimal (for pct_yield_loss calc)
    ypr_raw = ob.yield_preserved_ratio

    row.update(
        {
            "outcome(%)": _pct(comp.outcome),
            "decision(%)": _pct(comp.decision),
            "efficiency(%)": _pct(comp.efficiency),
            "fos(%)": _pct(comp.fos),
            "yield_ratio(%)": _pct(ob.yield_ratio),
            "yield_loss(%)": (
                _pct(1.0 - ypr_raw)
                if ypr_raw is not None and ypr_raw != ""
                else ""
            ),
            "recovered_yield_kg": round(ob.recovered_yield_kg, 2),
            "scenario_potential_kg": round(ob.scenario_potential_kg, 2),
            "agent_biological_kg": round(ob.agent_biological_kg, 2),
            "oracle_biological_kg": (
                round(ob.oracle_biological_kg, 2)
                if ob.oracle_biological_kg is not None
                else ""
            ),
            "donothing_biological_kg": (
                round(ob.donothing_biological_kg, 2)
                if ob.donothing_biological_kg is not None
                else ""
            ),
            "normalized_yield_score(%)": (
                _pct(ob.normalized_yield_score)
                if ob.normalized_yield_score is not None
                else ""
            ),
            "yield_preserved_ratio(%)": (
                _pct(ypr_raw)
                if ypr_raw is not None and ypr_raw != ""
                else ""
            ),
            "crop_loss_pct(%)": (
                _pct(ob.crop_loss_pct)
                if ob.crop_loss_pct is not None
                else ""
            ),
            "expects_agent_harvest": ob.expects_agent_harvest,
            "growing_loss": ob.growing_loss_count,
            "harvest_loss": ob.harvest_loss_count,
            "unharvested_mature": ob.unharvested_mature_count,
            "crop_loss": ob.crop_loss_count,
            "crop_loss_fraction(%)": (
                _pct(ob.crop_loss_fraction) if ob.crop_loss_fraction is not None else ""
            ),
            "safety_violations": ob.safety_violations,
            "tool_inflation": round(eb.tool_inflation, 4),
            "redundant_reads": eb.redundant_reads,
            "agent_tool_calls": eb.agent_tool_calls,
            "oracle_tool_calls": eb.oracle_tool_calls,
            "gates_matched": sum(1 for g in report.decision_breakdown if g.matched),
            "gates_total": len(report.decision_breakdown),
            "extrapolation_status": (
                ob.extrapolation.get("status") if ob.extrapolation else ""
            ),
            "extrapolation_days": (
                ob.extrapolation.get("days_ticked") if ob.extrapolation else ""
            ),
            "focus_ridge_ids": (
                ",".join(str(i) for i in ob.focus_ridge_ids)
                if ob.focus_ridge_ids
                else ""
            ),
            "focus_agent_biological_kg": (
                round(ob.focus_agent_biological_kg, 2)
                if ob.focus_agent_biological_kg is not None
                else ""
            ),
            "focus_oracle_biological_kg": (
                round(ob.focus_oracle_biological_kg, 2)
                if ob.focus_oracle_biological_kg is not None
                else ""
            ),
            "focus_donothing_biological_kg": (
                round(ob.focus_donothing_biological_kg, 2)
                if ob.focus_donothing_biological_kg is not None
                else ""
            ),
            "focus_yield_preserved_ratio(%)": (
                _pct(ob.focus_yield_preserved_ratio)
                if ob.focus_yield_preserved_ratio is not None
                else ""
            ),
            "focus_normalized_yield_score(%)": (
                _pct(ob.focus_normalized_yield_score)
                if ob.focus_normalized_yield_score is not None
                else ""
            ),
            "extrapolation_reached_r8": (
                ob.extrapolation.get("reached_maturity") if ob.extrapolation else ""
            ),
        }
    )

    # ---- Optional: legacy v1 + relaxed-numeric v2 path_correctness -------
    # When --path-correctness-v2 is set, we emit BOTH:
    #   * v1 (legacy): path_correctness / coverage / ktc_raw / ktc_adjusted /
    #     combined — produced by workflow_validation.evaluate_workflows
    #     (byte-identical args alphabet).
    #   * v2: path_correctness_v2 / coverage_v2 / ktc_raw_v2 /
    #     ktc_adjusted_v2 / combined_v2 — same formulas, but the alphabet
    #     treats numeric args within the tol-ratio window as one symbol,
    #     and same-tool oracle anchors are matched closest-first.
    # The pair is written together so v1 vs v2 can be diff'd per cell.
    if path_correctness_v2:
        try:
            from are.simulation.scenarios.fos.path_correctness_v2 import (
                evaluate_path_correctness_v2,
            )
            from are.simulation.scenarios.workflow_validation import (
                evaluate_workflows,
                workflow_from_event_log,
                workflow_from_oracle_events,
            )

            oracle_wf = workflow_from_oracle_events(scenario)
            agent_wf = workflow_from_event_log(list(env.event_log.list_view()))

            v1_metrics = evaluate_workflows(oracle_wf, agent_wf)
            # Convert v1 workflow metrics (0-1) to 100-scale.
            # for k in ("path_correctness", "coverage", "ktc_raw", "ktc_adjusted", "combined"):
            #     row[k] = _pct(v1_metrics.get(k))

            pc2 = evaluate_path_correctness_v2(
                oracle_wf, agent_wf, tol_ratio=pc2_tol
            )



            row["path_correctness(%)"] = _pct(pc2.get("path_correctness_v2"))
            row["coverage(%)"] = _pct(pc2.get("coverage_v2"))
            row["ktc_score(%)"] = _pct(pc2.get("ktc_raw_v2"))
            row["ktc_adjusted(%)"] = _pct(pc2.get("ktc_adjusted_v2"))
            row["combined(%)"] = _pct(pc2.get("combined_v2"))
            # row["pc2_n_oracle_steps"] = pc2["n_oracle_steps"]
            # row["pc2_n_agent_steps"] = pc2["n_agent_steps"]
            # row["pc2_levenshtein"] = pc2["levenshtein_distance"]
            # row["pc2_tol_ratio"] = pc2["tol_ratio"]
            # row["pc2_n_agent_reused"] = pc2["n_agent_reused"]
            # row["pc2_n_agent_fresh"] = pc2["n_agent_fresh"]
            # row["pc2_path"] = str(pc2_detail_path)
        except Exception as exc:
            row["path_correctness_v2_error"] = f"{type(exc).__name__}: {exc}"

    return row


def _replay_one_cell_safe(*args, **kwargs) -> dict[str, Any]:
    """ProcessPool entrypoint: never raises, always returns a row."""
    try:
        return replay_one_cell(*args, **kwargs)
    except Exception as exc:
        return {
            "status": "uncaught_exception",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=4),
            "cell_dir": str(args[0]) if args else "",
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


# Column order for summary_v2.csv: human-readable groups left-to-right,
# then any per-row extras (sorted) appended for forward compatibility.
# v3 adds: agent_family / llm_model / run_level / detail_status / a2a_status
#          at the front, plus pct_yield_loss near yield_preserved_ratio,
#          and converts all 0-1 metrics to 100-scale (string "97.88").
_SUMMARY_COLUMN_ORDER: list[str] = [
    # Identity / scenario
    "status",
    "cell",
    "agent_family",
    "scenario",
    "level",
    "llm_model",
    "run_level",
    "detail_status",
    "a2a_status",
    "cell_dir",
    # FOS summary (100-scale)
    "fos(%)",
    "outcome(%)",
    "decision(%)",
    "efficiency(%)",
    # Yield — biology / oracle / baseline / recovery
    "agent_biological_kg",
    "oracle_biological_kg",
    "donothing_biological_kg",
    "donothing_biological_kg_inline",
    "scenario_potential_kg",
    "recovered_yield_kg",
    "yield_preserved_ratio(%)",
    "yield_loss(%)",
    "crop_loss_pct(%)",
    "normalized_yield_score(%)",
    "yield_ratio(%)",
    # Loss buckets & mandate
    "expects_agent_harvest",
    "growing_loss",
    "harvest_loss",
    "unharvested_mature",
    "crop_loss",
    "crop_loss_fraction(%)",
    "safety_violations",
    # Physics extrapolation (post-replay maturity)
    "extrapolation_status",
    "extrapolation_days",
    "extrapolation_reached_r8",
    # Efficiency / gates
    "agent_tool_calls",
    "oracle_tool_calls",
    "tool_inflation",
    "redundant_reads",
    "gates_matched",
    "gates_total",
    # Workflow path: v1 (exact-args) then v2 (numeric window)
    "path_correctness(%)",
    "coverage(%)",
    "ktc_score(%)",
    "ktc_adjusted(%)",
    "combined(%)",
    # Artefact paths & replay bookkeeping
    "fos_path",
    "replayed_events",
    "replay_skipped",
    "replay_exec_errors",
    # Failure columns
    "path_correctness_v2_error",
    "error",
    "traceback",
]


def _summary_csv_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    union = {k for r in rows for k in r.keys()}
    ordered = [k for k in _SUMMARY_COLUMN_ORDER if k in union]
    rest = sorted(union.difference(ordered))
    return ordered + rest


def _discover_cells(root: Path, recursive: bool = False) -> list[Path]:
    """Find cell directories under *root*.

    A cell directory is any directory that contains at least one
    ``scenario_*.json`` trace file.

    When ``recursive=False`` (default), only the immediate children of
    *root* are checked — this is the fast path for a well-structured sweep
    output like ``phase5_paper_matrix/``.

    When ``recursive=True``, the entire subtree under *root* is walked so
    you can point at a top-level ``validation_runs/`` directory and the
    script will find all cells regardless of how many nesting levels the
    sweep runner added (e.g.
    ``validation_runs/iclr_sweep_<ts>/phase5_paper_matrix/<cell>/``).
    """
    cells = []
    if recursive:
        # rglob "scenario_*.json" then take the parent dirs, de-dup, sort.
        seen: set[Path] = set()
        for match in sorted(root.rglob("scenario_*.json")):
            parent = match.parent
            if parent not in seen:
                seen.add(parent)
                cells.append(parent)
        cells.sort()
    else:
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            if any(p.glob("scenario_*.json")):
                cells.append(p)
    return cells


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _normalize_excluded_dirs(excluded_dirs: list[str]) -> list[Path]:
    normalized: list[Path] = []
    for raw in excluded_dirs:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_REPO_ROOT / p).resolve()
        else:
            p = p.resolve()
        normalized.append(p)
    return normalized


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-evaluate FOS over a sweep of saved cell traces."
    )
    ap.add_argument(
        "--root",
        required=True,
        type=Path,
        help="Sweep dir (e.g. .../phase5_paper_matrix/<grouping>/) "
             "containing one subdir per cell with a scenario_*.json trace.",
    )
    ap.add_argument(
        "--out-root",
        required=True,
        type=Path,
        help="Where to write per-cell fos_*.json + summary_v2.csv.",
    )
    ap.add_argument(
        "--extrapolate",
        action="store_true",
        help="If set, ticks physics forward to R8 before computing Outcome. "
             "Recommended for mid-season episodes (round-3) and full-season "
             "scenarios; for round-1+2 baselines this is mostly a no-op.",
    )
    ap.add_argument(
        "--extrapolation-max-days",
        type=int,
        default=180,
        help="Hard cap on post-replay tick days (default: 180).",
    )
    ap.add_argument(
        "--oracle-baselines",
        type=Path,
        default=None,
        help=(
            "Directory holding cached oracle baselines "
            "(<scenario_id>.json from scripts/build_oracle_baselines.py). "
            "When provided, FOS Outcome reports the headline metric "
            "crop_loss_pct = 1 - agent_biological / oracle_biological, "
            "and yield_preserved_ratio replaces yield_ratio as the main "
            "Outcome term. Without it we fall back to legacy yield_ratio."
        ),
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 4) // 2),
        help="Parallel cell workers (default: half of CPU count).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N cells (debug).",
    )
    ap.add_argument(
        "--cells",
        nargs="*",
        default=None,
        help="Process only these specific cell dirnames (debug).",
    )
    ap.add_argument(
        "--recursive",
        action="store_true",
        default=False,
        help=(
            "Walk the entire subtree under --root to find cell directories "
            "(any dir containing a scenario_*.json file). Use this when "
            "--root points to a top-level directory such as validation_runs/ "
            "that contains multiple sweep subdirectories. Without this flag "
            "only the immediate children of --root are checked."
        ),
    )
    ap.add_argument(
        "--donothing-inline",
        action="store_true",
        default=False,
        help=(
            "Compute the 'do-nothing' biological yield baseline inline "
            "during replay, without a pre-built oracle baseline JSON. "
            "For each cell, a second fresh scenario instance is created "
            "and physics is extrapolated to R8 with no agent events — the "
            "resulting yield is the do-nothing floor. Requires --extrapolate. "
            "Adds donothing_biological_kg and normalized_yield_score columns."
        ),
    )
    ap.add_argument(
        "--path-correctness-v2",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Compute the path-correctness pair: legacy v1 (path_correctness, "
            "coverage, ktc_raw, ktc_adjusted, combined — exact-args alphabet) "
            "AND the relaxed-numeric v2 (path_correctness_v2, coverage_v2, "
            "ktc_raw_v2, ktc_adjusted_v2, combined_v2 — numeric args within "
            "the --pc2-tol ratio window share an alphabet symbol; non-numeric "
            "args still require exact equality; same-tool oracle anchors are "
            "matched closest-first to repeated agent calls). Both sets are "
            "written together to summary_v2.csv and per-cell "
            "path_corr_v2_<scenario>.json. v1 is untouched, v2 is additive. "
            "Default is on. Pass --no-path-correctness-v2 to skip."
        ),
    )
    ap.add_argument(
        "--pc2-tol",
        type=float,
        default=2.0,
        help=(
            "Tolerance ratio for path_correctness_v2 numeric matching "
            "(default 2.0). max(|o|,|a|)/min(|o|,|a|) <= tol counts as a "
            "match. tol=2.0 corresponds to the user's '200%% relaxation': "
            "oracle=1 accepts agent in [0.5, 2.0]."
        ),
    )
    ap.add_argument(
        "--focus-ridges-config",
        type=Path,
        default=None,
        help=(
            "JSON file mapping scenario_id -> [ridge_ids] for per-scenario "
            "focus subsets. Example contents: "
            '{"scenario_physics_pod_fill_drought_irrigation": [10,11,12,13]}. '
            "When a cell's scenario matches a key, focus_agent_biological_kg "
            "is computed from the agent replay's per-ridge biological yield "
            "summed over those ridges. focus_oracle_biological_kg requires "
            "either an oracle baseline JSON (--oracle-baselines) or is left "
            "blank. focus_donothing_biological_kg uses the inline per-ridge "
            "do-nothing yields when --donothing-inline is on, otherwise "
            "falls back to the oracle baseline JSON. Cells whose scenario_id "
            "is not in the config are scored without a focus subset (existing "
            "behaviour). Outputs 5 new CSV columns: focus_ridge_ids, "
            "focus_agent_biological_kg, focus_oracle_biological_kg, "
            "focus_donothing_biological_kg, focus_yield_preserved_ratio, "
            "focus_normalized_yield_score."
        ),
    )
    ap.add_argument(
        "--exclude-dir",
        action="append",
        default=None,
        help=(
            "Directory to exclude from replay discovery. "
            "Can be passed multiple times. "
            "Relative paths are resolved from repo root. "
            "Defaults already exclude specific deepseek sweeps."
        ),
    )
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"ERROR: --root {args.root} is not a directory", file=sys.stderr)
        return 2

    cells = _discover_cells(args.root, recursive=args.recursive)
    excluded_raw = list(_DEFAULT_EXCLUDED_SWEEP_DIRS)
    if args.exclude_dir:
        excluded_raw.extend(args.exclude_dir)
    excluded_dirs = _normalize_excluded_dirs(excluded_raw)
    if excluded_dirs:
        cells_before_exclude = len(cells)
        cells = [
            c
            for c in cells
            if not any(_is_relative_to(c.resolve(), ex) for ex in excluded_dirs)
        ]
        excluded_count = cells_before_exclude - len(cells)
        if excluded_count > 0:
            print(
                f"Excluded {excluded_count} cell(s) from {len(excluded_dirs)} "
                "excluded dir(s)."
            )
    if args.cells:
        wanted = set(args.cells)
        cells = [c for c in cells if c.name in wanted]
    if args.limit:
        cells = cells[: args.limit]
    if not cells:
        print("No cells found.", file=sys.stderr)
        return 0

    args.out_root.mkdir(parents=True, exist_ok=True)
    oracle_dir = (
        args.oracle_baselines.expanduser().resolve()
        if args.oracle_baselines is not None
        else None
    )
    if oracle_dir is not None and not oracle_dir.is_dir():
        print(
            f"  [warn] --oracle-baselines={oracle_dir} is not a directory; "
            f"running without baselines (fallback to legacy yield_ratio).",
            file=sys.stderr,
        )
        oracle_dir = None
    # Load focus ridges config (optional). Validated up-front so a typo'd
    # path or malformed JSON aborts before we start the per-cell sweep.
    focus_ridges_config: dict[str, list[int]] | None = None
    if args.focus_ridges_config is not None:
        cfg_path = args.focus_ridges_config.expanduser().resolve()
        if not cfg_path.is_file():
            print(
                f"ERROR: --focus-ridges-config {cfg_path} is not a file",
                file=sys.stderr,
            )
            return 2
        try:
            raw_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"ERROR: cannot parse --focus-ridges-config {cfg_path}: {exc}",
                file=sys.stderr,
            )
            return 2
        if not isinstance(raw_cfg, dict):
            print(
                f"ERROR: --focus-ridges-config {cfg_path} must be a JSON object "
                "mapping scenario_id -> list[int]",
                file=sys.stderr,
            )
            return 2
        focus_ridges_config = {
            str(k): [int(x) for x in (v or [])]
            for k, v in raw_cfg.items()
            if isinstance(v, list)
        }
        print(
            f"  Loaded focus-ridges config from {cfg_path}: "
            f"{len(focus_ridges_config)} scenario(s)"
        )

    print(
        f"Re-evaluating {len(cells)} cells with {args.workers} worker(s) "
        f"(extrapolate={args.extrapolate}, max_days={args.extrapolation_max_days}, "
        f"oracle_baselines={oracle_dir}, "
        f"donothing_inline={args.donothing_inline}, "
        f"path_correctness_v2={args.path_correctness_v2}, "
        f"pc2_tol={args.pc2_tol}, "
        f"focus_ridges_config={'on' if focus_ridges_config else 'off'})"
    )

    rows: list[dict[str, Any]] = []
    if args.workers <= 1:
        for c in cells:
            row = _replay_one_cell_safe(
                c,
                args.out_root,
                args.extrapolate,
                args.extrapolation_max_days,
                oracle_dir,
                args.donothing_inline,
                args.path_correctness_v2,
                args.pc2_tol,
                focus_ridges_config,
            )
            rows.append(row)
            dn_part = (
                f"  dn_kg={row.get('donothing_biological_kg_inline', '-')}"
                f"  norm_yield={row.get('normalized_yield_score(%)', '-')}"
                if args.donothing_inline
                else ""
            )
            pc2_part = (
                f"  pc={row.get('path_correctness(%)', '-')} "
                f"comb={row.get('combined(%)', '-')} "
                if args.path_correctness_v2
                else ""
            )
            focus_part = (
                f"  focus_kg={row.get('focus_agent_biological_kg', '-')}"
                f"  focus_dn={row.get('focus_donothing_biological_kg', '-')}"
                f"  focus_nys={row.get('focus_normalized_yield_score(%)', '-')}"
                if focus_ridges_config and row.get("focus_ridge_ids")
                else ""
            )
            print(
                f"  [{row.get('status', '?'):24s}] {c.name}  "
                f"fos={row.get('fos(%)', '-')} "
                f"unharv_mature={row.get('unharvested_mature', '-')}"
                f"{dn_part}{pc2_part}{focus_part}"
            )
    else:
        # ProcessPool first (true parallelism). Fall back to ThreadPool
        # if the runtime denies semaphore allocation (e.g. sandboxed
        # environments). The replay path holds the GIL most of the
        # time during _orch_advance, so threading still helps for the
        # I/O-bound JSON parse / FOS export portions.
        try:
            executor = ProcessPoolExecutor(max_workers=args.workers)
        except (PermissionError, OSError) as exc:
            from concurrent.futures import ThreadPoolExecutor

            print(
                f"  [warn] ProcessPoolExecutor unavailable ({exc!r}); "
                f"falling back to ThreadPoolExecutor"
            )
            executor = ThreadPoolExecutor(max_workers=args.workers)
        with executor as ex:
            fut_to_cell = {
                ex.submit(
                    _replay_one_cell_safe,
                    c,
                    args.out_root,
                    args.extrapolate,
                    args.extrapolation_max_days,
                    oracle_dir,
                    args.donothing_inline,
                    args.path_correctness_v2,
                    args.pc2_tol,
                    focus_ridges_config,
                ): c
                for c in cells
            }
            for fut in as_completed(fut_to_cell):
                row = fut.result()
                rows.append(row)
                cell = fut_to_cell[fut]
                dn_part = (
                    f"  dn_kg={row.get('donothing_biological_kg_inline', '-')}"
                    f"  norm_yield={row.get('normalized_yield_score(%)', '-')}"
                    if args.donothing_inline
                    else ""
                )
                pc2_part = (
                    f"  pc={row.get('path_correctness(%)', '-')} "
                    f"comb={row.get('combined(%)', '-')} "
                    if args.path_correctness_v2
                    else ""
                )
                focus_part = (
                    f"  focus_kg={row.get('focus_agent_biological_kg', '-')}"
                    f"  focus_dn={row.get('focus_donothing_biological_kg', '-')}"
                    f"  focus_nys={row.get('focus_normalized_yield_score(%)', '-')}"
                    if focus_ridges_config and row.get("focus_ridge_ids")
                    else ""
                )
                print(
                    f"  [{row.get('status', '?'):24s}] {cell.name}  "
                    f"fos={row.get('fos(%)', '-')} "
                    f"unharv_mature={row.get('unharvested_mature', '-')}"
                    f"{dn_part}{pc2_part}{focus_part}"
                )

    # Emit summary_v2.csv. Primary columns follow a fixed semantic order;
    # any unexpected keys fall back to sorted suffix (forward-compatible).
    csv_path = args.out_root / "summary_v2.csv"
    fieldnames = _summary_csv_fieldnames(rows)
    with csv_path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nWrote {csv_path} ({len(rows)} rows)")

    # Print a quick aggregate summary.
    ok = [r for r in rows if r.get("status") == "ok"]
    if ok:
        def _f(key: str, r: dict) -> float:
            v = r.get(key, 0)
            if isinstance(v, str) and v != "":
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return 0.0
            return float(v) if isinstance(v, (int, float)) else 0.0

        avg_fos = sum(_f("fos(%)", r) for r in ok) / len(ok)
        with_extrap = [r for r in ok if r.get("extrapolation_days") not in ("", None)]
        avg_extrap_days = (
            sum(_f("extrapolation_days", r) for r in with_extrap) / len(with_extrap)
            if with_extrap else 0.0
        )
        n_unharv = sum(int(r.get("unharvested_mature") or 0) for r in ok)
        n_growing = sum(int(r.get("growing_loss") or 0) for r in ok)
        n_harvest = sum(int(r.get("harvest_loss") or 0) for r in ok)
        print(
            f"\nAggregate: n_ok={len(ok)}  avg_fos={avg_fos:.4f}  "
            f"avg_extrap_days={avg_extrap_days:.1f}  "
            f"sum(growing/harvest/unharv_mature)={n_growing}/{n_harvest}/{n_unharv}"
        )
        if focus_ridges_config:
            focus_ok = [r for r in ok if r.get("focus_ridge_ids")]
            if focus_ok:
                n = len(focus_ok)

                def _focus_avg(key: str) -> float | None:
                    vs = [
                        r[key]
                        for r in focus_ok
                        if isinstance(r.get(key), (int, float)) and r.get(key) != ""
                    ]
                    return sum(vs) / len(vs) if vs else None

                avg_agent = _focus_avg("focus_agent_biological_kg")
                avg_dn = _focus_avg("focus_donothing_biological_kg")
                avg_oracle = _focus_avg("focus_oracle_biological_kg")
                avg_ypr = _focus_avg("focus_yield_preserved_ratio(%)")
                avg_nys = _focus_avg("focus_normalized_yield_score(%)")
                fmt = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "-"
                fmt4 = lambda v: f"{v:.4f}" if isinstance(v, (int, float)) else "-"
                print(
                    f"Focus:     n={n}  "
                    f"avg_focus_agent_kg={fmt(avg_agent)}  "
                    f"avg_focus_donothing_kg={fmt(avg_dn)}  "
                    f"avg_focus_oracle_kg={fmt(avg_oracle)}  "
                    f"avg_focus_ypr={fmt4(avg_ypr)}  "
                    f"avg_focus_nys={fmt4(avg_nys)}"
                )

        if args.path_correctness_v2:
            pc2_ok = [r for r in ok if "path_correctness(%)" in r]
            if pc2_ok:
                n = len(pc2_ok)

                def _avg(key: str) -> float:
                    def _to_float(v: Any) -> float:
                        if isinstance(v, (int, float)):
                            return float(v)
                        if isinstance(v, str) and v.strip():
                            try:
                                return float(v.strip())
                            except ValueError:
                                return 0.0
                        return 0.0
                    return sum(_to_float(r.get(key, 0.0)) for r in pc2_ok) / n

                avg_pc1 = _avg("path_correctness(%)")
                avg_cov1 = _avg("coverage(%)")
                avg_comb1 = _avg("combined(%)")
                avg_pc2 = _avg("path_correctness_v2")
                avg_cov2 = _avg("coverage_v2")
                avg_comb2 = _avg("combined_v2")
                avg_reused = _avg("pc2_n_agent_reused")
                avg_fresh = _avg("pc2_n_agent_fresh")
                print(
                    f"V1 (exact-args):  n={n}  "
                    f"avg_path_correctness={avg_pc1:.4f}  "
                    f"avg_coverage={avg_cov1:.4f}  "
                    f"avg_combined={avg_comb1:.4f}"
                )
                print(
                    f"V2 (tol={args.pc2_tol}):    n={n}  "
                    f"avg_path_correctness_v2={avg_pc2:.4f}  "
                    f"avg_coverage_v2={avg_cov2:.4f}  "
                    f"avg_combined_v2={avg_comb2:.4f}  "
                    f"(lift vs v1: pc {avg_pc2 - avg_pc1:+.4f}, "
                    f"comb {avg_comb2 - avg_comb1:+.4f})  "
                    f"avg_agent_reused={avg_reused:.2f}  "
                    f"avg_agent_fresh={avg_fresh:.2f}"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
