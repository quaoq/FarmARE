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

# Default location for cached per-scenario oracle baselines. A baseline
# file at ``oracle_baselines/<scenario_id>.json`` (relative to
# ORACLE_BASELINE_ENV_VAR or the repo root) lets evaluate_fos compute the
# attribution metric `crop_loss_pct = 1 - agent_biological / oracle_biological`.
ORACLE_BASELINE_ENV_VAR: str = "FOS_ORACLE_BASELINE_DIR"
ORACLE_BASELINE_DEFAULT_SUBDIR: str = "oracle_baselines"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_fos(
    scenario: Any,
    env: Any,
    gates: list[GateSpec],
    weights: dict[str, float] | None = None,
    crop_loss_threshold: float = _DEFAULT_CROP_LOSS_THRESHOLD,
    extrapolate_to_maturity: bool = False,
    extrapolation_max_days: int = 180,
    oracle_baseline_dir: str | Path | None = None,
    expects_agent_harvest: bool | None = None,
    focus_ridge_ids: list[int] | None = None,
    donothing_biological_kg: float | None = None,
    donothing_per_ridge_g_m2: list[float] | None = None,
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
        extrapolate_to_maturity: if True, before computing Outcome the
            physics engine is ticked forward from its current sim_time
            until either (a) all planted ridges have reached R8 or (b)
            `extrapolation_max_days` have elapsed. The agent gets no
            further opportunities — only the engines tick. Weather during
            extrapolation comes from the scenario's WeatherApp; once the
            forecast is exhausted the orchestrator carries the last-set
            weather day forward (`_build_weather_inputs` fallback path),
            which keeps the metric deterministic per (scenario, agent
            trace). Use this to re-base mid-season episodes onto a
            common "what would have happened if the agent's choices
            played out to harvest" footing so crop_loss / yield_ratio
            aren't artefacts of where the agent stopped.
        extrapolation_max_days: hard cap on how many days the engine is
            allowed to tick post-agent. 180 covers the longest soybean
            season (planting → R8 ≈ 130–150 days) with margin.
        oracle_baseline_dir: directory containing
            ``<scenario_id>.json`` oracle-baseline files. When a baseline
            exists, Outcome's headline metric becomes
            ``crop_loss_pct = 1 - agent_biological / oracle_biological``,
            i.e. the *attributable* crop loss vs. an optimal-play baseline.
            When None, falls back to the env var
            ``FOS_ORACLE_BASELINE_DIR``, then to ``./oracle_baselines/``.
            When no baseline file is found, the legacy ``yield_ratio``
            carries the signal (with a warning in the breakdown).
        expects_agent_harvest: whether the scenario's task includes
            actually executing harvest. True for round-1+2 harvest
            scenarios, round-4 fullseason, and post-harvest drying;
            False for mid-season episodes (irrigation, pesticide,
            disease scouting, etc.) where the agent's job is to
            *preserve* yield potential rather than extract it. When
            None (default), reads the ``expects_agent_harvest``
            attribute from the scenario class (default True). Only
            affects Outcome score's `unharvested_mature` penalty —
            False suppresses it because the agent wasn't asked to
            harvest in the first place.
    """
    weights = _coerce_weights(weights)
    scenario_start_time = float(getattr(scenario, "start_time", None) or 0.0)
    if expects_agent_harvest is None:
        expects_agent_harvest = bool(
            getattr(scenario, "expects_agent_harvest", True)
        )

    extrapolation_status: dict[str, Any] | None = None
    if extrapolate_to_maturity:
        farm_world = _try_get_farm_world(scenario)
        if farm_world is not None:
            extrapolation_status = _extrapolate_physics_to_maturity(
                farm_world, max_days=extrapolation_max_days
            )

    # Oracle baselines are produced *with* extrapolation to R8. Comparing
    # an agent run that stopped mid-season against an R8 baseline would
    # report bogus crop_loss_pct ≈ 100% even for a perfect oracle replay.
    # So we only consult the baseline when the caller has also asked for
    # extrapolation (apples-to-apples). Without extrapolation, fall back
    # to the legacy yield_ratio so existing oracle audits stay green.
    scenario_id = getattr(scenario, "scenario_id", None)
    oracle_biological_kg = (
        _load_oracle_baseline_biological_kg(scenario_id, oracle_baseline_dir)
        if extrapolate_to_maturity
        else None
    )
    # donothing_biological_kg may be passed in directly (e.g. computed inline
    # during trace replay without a pre-built baseline JSON).  Fall back to
    # loading from the JSON file only when the caller hasn't supplied a value.
    if donothing_biological_kg is None:
        donothing_biological_kg = (
            _load_donothing_baseline_biological_kg(scenario_id, oracle_baseline_dir)
            if extrapolate_to_maturity
            else None
        )

    # Auto-detect focus ridges from scenario if not explicitly provided.
    # Scenarios that target a specific subset of ridges can expose a
    # ``_focus_ridge_ids()`` method returning list[int].
    effective_focus_ridge_ids = focus_ridge_ids
    if effective_focus_ridge_ids is None:
        fn = getattr(scenario, "_focus_ridge_ids", None)
        if callable(fn):
            try:
                result = fn()
                effective_focus_ridge_ids = list(result) if result is not None else None
            except Exception:
                effective_focus_ridge_ids = None

    outcome_score, outcome_breakdown = _compute_outcome(
        scenario,
        env,
        crop_loss_threshold=crop_loss_threshold,
        extrapolation_status=extrapolation_status,
        oracle_biological_kg=oracle_biological_kg,
        expects_agent_harvest=expects_agent_harvest,
        donothing_biological_kg=donothing_biological_kg,
        focus_ridge_ids=effective_focus_ridge_ids,
        oracle_baseline_dir=oracle_baseline_dir,
        donothing_per_ridge_g_m2=donothing_per_ridge_g_m2,
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
    extrapolate_to_maturity: bool = False,
    extrapolation_max_days: int = 180,
    oracle_baseline_dir: str | Path | None = None,
    expects_agent_harvest: bool | None = None,
) -> Any:
    """Compute FOS, save the JSON report, and append a `fos_eval:` rationale line.

    Mirrors `workflow_validation.append_workflow_evaluation`'s contract so the
    suite-runner CSV captures FOS alongside `workflow_eval` without the runner
    needing changes. Returns the (possibly mutated) ScenarioValidationResult.

    `extrapolate_to_maturity` / `extrapolation_max_days` are forwarded to
    `evaluate_fos`. Mid-season scenarios (round-3 episodes, irrigation-only
    scenarios that stop before R8) should pass `True` so Outcome reflects
    the latent yield of the agent's choices played out to harvest, rather
    than the partial-episode state at agent stop time.

    `oracle_baseline_dir` and `expects_agent_harvest` are forwarded to
    `evaluate_fos`. See its docstring for semantics.
    """
    report = evaluate_fos(
        scenario,
        env,
        gates=gates,
        weights=weights,
        extrapolate_to_maturity=extrapolate_to_maturity,
        extrapolation_max_days=extrapolation_max_days,
        oracle_baseline_dir=oracle_baseline_dir,
        expects_agent_harvest=expects_agent_harvest,
    )

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
    ob = report.outcome_breakdown
    extras = (
        f"safety={ob.safety_violations}, "
        f"crop_loss={ob.crop_loss_count}, "
        f"growing_loss={ob.growing_loss_count}, "
        f"unharvested_mature={ob.unharvested_mature_count}, "
        f"tool_inflation={report.efficiency_breakdown.tool_inflation:.3f}, "
        f"gates_matched={sum(1 for g in report.decision_breakdown if g.matched)}/"
        f"{len(report.decision_breakdown)}"
    )
    if ob.crop_loss_pct is not None:
        extras += (
            f", crop_loss_pct={ob.crop_loss_pct:.4f}"
            f", yield_preserved={ob.yield_preserved_ratio:.4f}"
            f", agent_kg={ob.agent_biological_kg:.1f}"
            f", oracle_kg={ob.oracle_biological_kg:.1f}"
        )
    extras += f", expects_harvest={ob.expects_agent_harvest}"
    if ob.extrapolation is not None:
        extras += (
            f", extrap_days={ob.extrapolation.get('days_ticked', 0)}"
            f"/{ob.extrapolation.get('status', '?')}"
        )
    parts = [getattr(result, "rationale", None)] if getattr(result, "rationale", None) else []
    parts.append(f"fos_eval: {fos_metric_text}; {extras}")
    parts.append(f"fos_report={fos_path}")
    result.rationale = "\n".join(p for p in parts if p)
    return result


# ---------------------------------------------------------------------------
# Outcome (O) — yield, crop loss, safety violations
# ---------------------------------------------------------------------------


def _resolve_oracle_baseline_dir(
    explicit: str | Path | None,
) -> Path | None:
    """Resolve the directory holding ``<scenario_id>.json`` oracle baselines.

    Priority: explicit arg → FOS_ORACLE_BASELINE_DIR env var →
    ``<repo>/oracle_baselines``. Returns None only if no candidate
    directory exists on disk.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env_path = os.environ.get(ORACLE_BASELINE_ENV_VAR)
    if env_path:
        candidates.append(Path(env_path))
    # Repo-root default. evaluation.py lives at
    # are/simulation/scenarios/fos/evaluation.py — five parents up is repo root.
    repo_root = Path(__file__).resolve().parents[4]
    candidates.append(repo_root / ORACLE_BASELINE_DEFAULT_SUBDIR)
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _load_donothing_baseline_biological_kg(
    scenario_id: str | None,
    explicit_dir: str | Path | None,
) -> float | None:
    """Load the do-nothing biological yield from the oracle baseline JSON.

    The ``donothing`` sub-object is written by ``build_oracle_baselines.py
    --include-donothing``. Returns None if not present so callers can fall
    back gracefully.
    """
    if not scenario_id:
        return None
    baseline_dir = _resolve_oracle_baseline_dir(explicit_dir)
    if baseline_dir is None:
        return None
    fp = baseline_dir / f"{scenario_id}.json"
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    dn = data.get("donothing")
    if not isinstance(dn, dict):
        return None
    total = dn.get("biological_yield_kg_total")
    if isinstance(total, (int, float)) and total >= 0.0:
        return float(total)
    return None


def _load_focus_ridge_baselines(
    scenario_id: str | None,
    explicit_dir: str | Path | None,
    focus_ridge_ids: list[int],
    ridge_area_m2: float,
) -> tuple[float | None, float | None]:
    """Load per-ridge oracle and do-nothing biological yields summed over
    the focus ridge subset.  Returns (oracle_focus_kg, donothing_focus_kg).
    """
    if not scenario_id or not focus_ridge_ids:
        return None, None
    baseline_dir = _resolve_oracle_baseline_dir(explicit_dir)
    if baseline_dir is None:
        return None, None
    fp = baseline_dir / f"{scenario_id}.json"
    if not fp.is_file():
        return None, None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None

    oracle_focus_kg: float | None = None
    bio_per_ridge = data.get("biological_yield_g_m2_per_ridge")
    if isinstance(bio_per_ridge, list) and ridge_area_m2 > 0:
        focus_set = set(focus_ridge_ids)
        # Ridge IDs are 0-indexed positions in the list.
        oracle_sum = sum(
            float(bio_per_ridge[i])
            for i in range(len(bio_per_ridge))
            if i in focus_set and isinstance(bio_per_ridge[i], (int, float))
        )
        oracle_focus_kg = oracle_sum * ridge_area_m2 / 1000.0

    donothing_focus_kg: float | None = None
    dn = data.get("donothing")
    if isinstance(dn, dict):
        dn_per_ridge = dn.get("biological_yield_g_m2_per_ridge")
        if isinstance(dn_per_ridge, list) and ridge_area_m2 > 0:
            focus_set = set(focus_ridge_ids)
            dn_sum = sum(
                float(dn_per_ridge[i])
                for i in range(len(dn_per_ridge))
                if i in focus_set and isinstance(dn_per_ridge[i], (int, float))
            )
            donothing_focus_kg = dn_sum * ridge_area_m2 / 1000.0

    return oracle_focus_kg, donothing_focus_kg


def compute_donothing_per_ridge_yields(
    scenario_id: str,
    start_time: float | None = None,
    seed: int = 0,
    extrapolation_max_days: int = 180,
) -> tuple[float | None, list[float] | None]:
    """Replay-friendly do-nothing baseline that ALSO returns per-ridge yields.

    Same construction as :func:`compute_donothing_biological_kg` (fresh
    scenario, no agent events, physics ticked to R8), but returns:

        (total_biological_kg,
         per_ridge_biological_yield_g_m2)  # list of length n_ridges

    The per-ridge list is indexed by ridge_id (0-based); ridges that were
    never planted contribute 0.0 — useful when the caller wants to sum
    over a *focus subset* instead of the whole field.

    Returns (None, None) if the physics engine was never ticked.

    This function is the inline counterpart to a pre-built
    ``oracle_baselines/<scenario_id>.json`` file's
    ``donothing.biological_yield_g_m2_per_ridge`` field — when no baseline
    JSON is available, callers can pipe this output into
    ``evaluate_fos(donothing_per_ridge_g_m2=...)`` to score focus-ridge
    subsets without any precomputed artefact.
    """
    try:
        from are.simulation.scenarios.utils.registry import registry
        from are.simulation.apps.farm_world.farm_world_app import (
            DEFAULT_RIDGE_WIDTH_M,
            FIELD_LENGTH_M,
        )

        cls = registry.get_scenario(scenario_id)
        dn_scenario = cls()
        if start_time is not None:
            dn_scenario.start_time = float(start_time)
        dn_scenario.seed = int(seed)
        dn_scenario.initialize()

        farm_world = _try_get_farm_world(dn_scenario)
        physics = getattr(farm_world, "_physics", None) if farm_world is not None else None
        if physics is None or not getattr(physics, "engines_active", False):
            return None, None

        _extrapolate_physics_to_maturity(farm_world, max_days=extrapolation_max_days)

        ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M

        # Determine field width by scanning known ridge ids; ridges not in
        # the state dict contribute 0.0.  Use 0..max_id+1 as the canonical
        # length so per-ridge index aligns with ridge_id.
        all_ids: list[int] = []
        for rid in physics.yield_recovery.states.keys():
            all_ids.append(rid)
        for rid in physics.phenology.states.keys():
            all_ids.append(rid)
        if not all_ids:
            return 0.0, []
        max_id = max(all_ids)
        per_ridge_g_m2 = [0.0] * (max_id + 1)

        total_kg = 0.0
        for rid, yld_state in physics.yield_recovery.states.items():
            phen_state = physics.phenology.states.get(rid)
            ever_planted = phen_state is not None and phen_state.planted
            biological = float(yld_state.biological_yield_g_m2)
            per_ridge_g_m2[rid] = biological
            if not (ever_planted or biological > 0.0):
                continue
            total_kg += biological * ridge_area_m2 / 1000.0
        return total_kg, per_ridge_g_m2
    except Exception:
        return None, None


def compute_donothing_biological_kg(
    scenario_id: str,
    start_time: float | None = None,
    seed: int = 0,
    extrapolation_max_days: int = 180,
) -> float | None:
    """Compute the "do-nothing" biological yield inline — no pre-built JSON needed.

    This is the **replay-friendly** alternative to loading a pre-built
    ``build_oracle_baselines.py --include-donothing`` file.  It:

      1. Looks up ``scenario_id`` in the registry and creates a fresh
         instance with ``(start_time, seed)`` — exactly like the replay
         driver does for the agent trace.
      2. Calls :func:`_extrapolate_physics_to_maturity` on the *unmodified*
         physics state (no agent events replayed) so every planted ridge
         reaches R8 under weather alone.
      3. Reads ``biological_yield_g_m2 * ridge_area_m2 / 1000`` for each
         planted ridge and returns the total kg.

    Returns ``None`` if the scenario has no active physics (e.g. the
    physics engine was never ticked during oracle initialisation — this
    normally shouldn't happen for any wired scenario).

    This function is deliberately **side-effect free** on any existing
    state: it creates its own scenario instance.
    """
    try:
        from are.simulation.scenarios.utils.registry import registry
        from are.simulation.apps.farm_world.farm_world_app import (
            DEFAULT_RIDGE_WIDTH_M,
            FIELD_LENGTH_M,
        )

        cls = registry.get_scenario(scenario_id)
        dn_scenario = cls()
        if start_time is not None:
            dn_scenario.start_time = float(start_time)
        dn_scenario.seed = int(seed)
        dn_scenario.initialize()

        farm_world = _try_get_farm_world(dn_scenario)
        physics = getattr(farm_world, "_physics", None) if farm_world is not None else None
        if physics is None or not getattr(physics, "engines_active", False):
            return None

        _extrapolate_physics_to_maturity(farm_world, max_days=extrapolation_max_days)

        ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
        total_kg = 0.0
        for rid, yld_state in physics.yield_recovery.states.items():
            phen_state = physics.phenology.states.get(rid)
            ever_planted = phen_state is not None and phen_state.planted
            biological = yld_state.biological_yield_g_m2
            # Mirror _compute_outcome: include ridges that are planted at the
            # phenology level OR already have non-zero biological yield set
            # directly by the scenario's _configure_physics_layers().
            if not (ever_planted or biological > 0.0):
                continue
            total_kg += biological * ridge_area_m2 / 1000.0
        return total_kg
    except Exception:
        return None


def _load_oracle_baseline_biological_kg(
    scenario_id: str | None,
    explicit_dir: str | Path | None,
) -> float | None:
    """Load the cached oracle biological-yield baseline for ``scenario_id``.

    The baseline JSON is produced by ``scripts/build_oracle_baselines.py``
    and looks like::

        {
            "scenario_id": "...",
            "biological_yield_g_m2_per_ridge": [...],
            "biological_yield_kg_total": 6398.2,
            ...
        }

    Returns the total biological yield in kg, or None if no baseline is
    available (treat as "no attribution metric available — fall back to
    legacy yield_ratio").
    """
    if not scenario_id:
        return None
    baseline_dir = _resolve_oracle_baseline_dir(explicit_dir)
    if baseline_dir is None:
        return None
    fp = baseline_dir / f"{scenario_id}.json"
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    total = data.get("biological_yield_kg_total")
    if isinstance(total, (int, float)) and total > 0.0:
        return float(total)
    # Fallback: derive from per-ridge values.
    per_ridge = data.get("biological_yield_g_m2_per_ridge")
    area = data.get("ridge_area_m2")
    if isinstance(per_ridge, list) and isinstance(area, (int, float)) and area > 0:
        s = sum(float(v) for v in per_ridge if isinstance(v, (int, float)))
        return s * float(area) / 1000.0 if s > 0.0 else None
    return None


def _extrapolate_physics_to_maturity(
    farm_world_app: Any, max_days: int
) -> dict[str, Any]:
    """Tick physics forward day-by-day until all planted ridges hit R8.

    Uses the orchestrator's existing daily-tick primitive (so we go
    through the same engine path live runs do — not a shortcut). Stops
    early as soon as every planted ridge has `r8_reached`. Caps at
    `max_days` to bound runtime even on pathological scenarios.

    Returns a status dict reporting:
      - status: "skipped" / "noop" / "advanced"
      - reason: why "skipped" (physics_inactive / never_ticked) or "noop"
                (already_at_r8 / nothing_planted)
      - days_ticked: how many days were rolled forward (0 for skip/noop)
      - reached_maturity: bool — True iff all planted ridges hit R8
    """
    physics = getattr(farm_world_app, "_physics", None)
    if physics is None or not getattr(physics, "engines_active", False):
        return {"status": "skipped", "reason": "physics_inactive", "days_ticked": 0}
    if physics.last_physics_sim_time is None:
        return {"status": "skipped", "reason": "physics_never_ticked", "days_ticked": 0}

    def planted_ridge_ids() -> list[int]:
        return [
            rid
            for rid, phen in physics.phenology.states.items()
            if phen.planted
        ]

    def all_planted_mature() -> bool:
        ids = planted_ridge_ids()
        if not ids:
            return False
        return all(
            (physics.yield_recovery.states.get(rid) is not None
             and physics.yield_recovery.states[rid].r8_reached)
            for rid in ids
        )

    if not planted_ridge_ids():
        return {"status": "noop", "reason": "nothing_planted", "days_ticked": 0}
    if all_planted_mature():
        return {"status": "noop", "reason": "already_at_r8", "days_ticked": 0,
                "reached_maturity": True}

    # Lazy import to avoid a circular dep at module load.
    from are.simulation.apps.farm_world.physics_orchestrator import (
        advance_physics_time as _orch_advance,
    )

    one_day = 86400.0
    target = float(physics.last_physics_sim_time)
    days_ticked = 0
    for _ in range(max_days):
        target += one_day
        try:
            _orch_advance(farm_world_app, target)
        except Exception as exc:  # pragma: no cover — defensive
            return {
                "status": "error",
                "error": f"orchestrator_raised: {exc!r}",
                "days_ticked": days_ticked,
                "reached_maturity": False,
            }
        days_ticked += 1
        if all_planted_mature():
            break

    return {
        "status": "advanced",
        "days_ticked": days_ticked,
        "reached_maturity": all_planted_mature(),
        "max_days": max_days,
    }


def _compute_outcome(
    scenario: Any,
    env: Any,
    crop_loss_threshold: float,
    extrapolation_status: dict[str, Any] | None = None,
    oracle_biological_kg: float | None = None,
    expects_agent_harvest: bool = True,
    donothing_biological_kg: float | None = None,
    focus_ridge_ids: list[int] | None = None,
    oracle_baseline_dir: str | Path | None = None,
    donothing_per_ridge_g_m2: list[float] | None = None,
) -> tuple[float, OutcomeBreakdown]:
    """Compute the Outcome component plus a structured breakdown.

    Headline (when ``oracle_biological_kg`` is provided):
        crop_loss_pct = 1 - (agent_biological_kg / oracle_biological_kg)
    This is the *attributable* yield loss vs. an oracle-baseline run on
    the same scenario, the number a paper-reader actually wants to read.

    Fallback (no oracle baseline): legacy ``yield_ratio`` is used as the
    Outcome main term — this is recovered_kg / accountable_potential_kg
    where accountable_potential adds in unharvested-mature ridges at
    zero recovery so "knew the day, never harvested" failures don't hide.

    Penalty terms (subtracted from the main term, clipped to [0, 1]):
      * safety_violations: `_SAFETY_PENALTY_PER_VIOLATION` per error.
      * growing_loss_count: `_CROP_LOSS_PENALTY_PER_RIDGE` per ridge —
        always applied because mid-season collapse is on the agent
        regardless of whether harvest was in their mandate.
      * harvest_loss_count: `_CROP_LOSS_PENALTY_PER_RIDGE` per ridge —
        always applied. If the agent did execute harvest, they own
        the quality outcome.
      * unharvested_mature_count: `2 * _CROP_LOSS_PENALTY_PER_RIDGE` per
        ridge — *only* when ``expects_agent_harvest`` is True. For
        mid-season episodes (e.g., irrigation-only, disease-scouting),
        leaving a ripe field unharvested isn't on the agent because
        the agent wasn't asked to harvest in the first place.
    """
    farm_world = _try_get_farm_world(scenario)
    physics = getattr(farm_world, "_physics", None) if farm_world is not None else None

    recovered_kg = 0.0
    potential_kg = 0.0
    harvested_potential_kg = 0.0
    unharvested_mature_potential_kg = 0.0
    harvested_count = 0
    growing_loss_count = 0
    harvest_loss_count = 0
    unharvested_mature_count = 0
    crop_loss_fraction = 0.0
    if physics is not None and getattr(physics, "engines_active", False):
        from are.simulation.apps.farm_world.farm_world_app import (
            DEFAULT_RIDGE_WIDTH_M,
            FIELD_LENGTH_M,
        )

        ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
        planted_count = 0

        # Field-median biological yield, used as the relative reference for
        # detecting "growing-stage collapse" without baking in a brittle
        # absolute g/m^2 cutoff. Only computed across ridges that actually
        # set seed (biological > 0); empty fields skip the bucket.
        bios = [
            s.biological_yield_g_m2
            for s in physics.yield_recovery.states.values()
            if s.biological_yield_g_m2 > 0.0
        ]
        ridge_potential_g_m2 = (
            sorted(bios)[len(bios) // 2] if bios else 0.0
        )

        for rid, yld_state in physics.yield_recovery.states.items():
            phen_state = physics.phenology.states.get(rid)
            ever_planted = phen_state is not None and phen_state.planted
            biological = yld_state.biological_yield_g_m2
            recovered = yld_state.recovered_yield_g_m2_at_market_moisture
            harvested = bool(getattr(yld_state, "harvested", False))
            r8_reached = bool(getattr(yld_state, "r8_reached", False))
            if not (ever_planted or biological > 0.0):
                continue
            planted_count += 1
            potential_kg += biological * ridge_area_m2 / 1000.0

            # Mutually-exclusive bucket assignment.
            if harvested:
                recovered_kg += recovered * ridge_area_m2 / 1000.0
                harvested_count += 1
                harvested_potential_kg += biological * ridge_area_m2 / 1000.0
                if biological > 0.0 and recovered / biological < crop_loss_threshold:
                    harvest_loss_count += 1
            elif r8_reached:
                unharvested_mature_count += 1
                unharvested_mature_potential_kg += biological * ridge_area_m2 / 1000.0
            elif ridge_potential_g_m2 > 0.0:
                if biological < crop_loss_threshold * ridge_potential_g_m2:
                    growing_loss_count += 1

        if planted_count > 0:
            crop_loss_fraction = (
                growing_loss_count + harvest_loss_count + unharvested_mature_count
            ) / planted_count

    # Legacy yield_ratio: same as before Scheme B, used as a fallback when
    # no oracle baseline is available. Denominator includes both
    # harvested ridges AND unharvested-but-mature ridges (at zero recovered
    # yield) so "knew the day, never harvested" is visible.
    accountable_potential_kg = harvested_potential_kg + unharvested_mature_potential_kg
    if accountable_potential_kg > 0.0:
        yield_ratio = recovered_kg / accountable_potential_kg
    else:
        # Mid-season episode that didn't reach R8 on any ridge. Without
        # an oracle baseline, default to 1.0 ("no yield damage observed
        # in this window"); safety / crop-loss penalties still apply.
        yield_ratio = 1.0

    # Causal-attribution metric: agent vs. oracle on the *latent
    # biological* yield. This is what the paper's headline reports.
    agent_biological_kg = potential_kg
    yield_preserved_ratio: float | None = None
    crop_loss_pct: float | None = None
    if oracle_biological_kg is not None and oracle_biological_kg > 0.0:
        raw_preserved = agent_biological_kg / oracle_biological_kg
        # Cap at 1.0 — an agent can't "do better than oracle" by getting
        # weather lucky on a different stochastic trace; the metric
        # measures preservation of the oracle's yield potential.
        yield_preserved_ratio = max(0.0, min(1.0, raw_preserved))
        crop_loss_pct = max(0.0, min(1.0, 1.0 - raw_preserved))

    safety_violations, safety_details = _count_safety_violations(env)

    # Headline term: prefer the oracle-attribution preservation ratio
    # whenever a baseline is available; fall back to the legacy
    # yield_ratio only when it isn't.
    outcome_main = (
        yield_preserved_ratio if yield_preserved_ratio is not None else yield_ratio
    )

    # Penalty weights:
    #   * harvest_loss & growing_loss share _CROP_LOSS_PENALTY_PER_RIDGE.
    #     growing_loss is *always* on the agent (pre-harvest neglect),
    #     and harvest_loss is *always* on the agent (if you harvested,
    #     you own the quality outcome).
    #   * unharvested_mature carries a 2x penalty BUT only when the
    #     scenario actually expected the agent to harvest. Mid-season
    #     episodes whose mandate is irrigation/scouting/spraying don't
    #     get penalised for "the field eventually ripened and nobody
    #     came back to harvest it" — that wasn't their job.
    unharvested_penalty_count = (
        unharvested_mature_count if expects_agent_harvest else 0
    )
    outcome_raw = (
        outcome_main
        - _SAFETY_PENALTY_PER_VIOLATION * safety_violations
        - _CROP_LOSS_PENALTY_PER_RIDGE * harvest_loss_count
        - _CROP_LOSS_PENALTY_PER_RIDGE * growing_loss_count
        - 2.0 * _CROP_LOSS_PENALTY_PER_RIDGE * unharvested_penalty_count
    )
    outcome_score = _clip01(outcome_raw)

    crop_loss_count = harvest_loss_count  # legacy alias

    # ---- Normalized yield score (agent vs do-nothing vs oracle) ----------
    # normalized = (agent_bio - donothing_bio) / (oracle_bio - donothing_bio)
    # 0 = no improvement over doing nothing; 1 = matches oracle; >1 capped.
    normalized_yield_score: float | None = None
    if (
        oracle_biological_kg is not None
        and donothing_biological_kg is not None
        and oracle_biological_kg > donothing_biological_kg
    ):
        raw_norm = (agent_biological_kg - donothing_biological_kg) / (
            oracle_biological_kg - donothing_biological_kg
        )
        normalized_yield_score = max(0.0, min(1.0, raw_norm))

    # ---- Focus-ridge subset metrics -------------------------------------
    focus_agent_bio: float | None = None
    focus_oracle_bio: float | None = None
    focus_dn_bio: float | None = None
    focus_ypr: float | None = None
    focus_nys: float | None = None

    if focus_ridge_ids and physics is not None and getattr(physics, "engines_active", False):
        from are.simulation.apps.farm_world.farm_world_app import (
            DEFAULT_RIDGE_WIDTH_M,
            FIELD_LENGTH_M,
        )
        ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
        focus_set = set(focus_ridge_ids)
        focus_agent_bio = sum(
            yld_state.biological_yield_g_m2 * ridge_area_m2 / 1000.0
            for rid, yld_state in physics.yield_recovery.states.items()
            if rid in focus_set
        )
        # Load per-ridge oracle + donothing for this subset.
        focus_oracle_bio, focus_dn_bio = _load_focus_ridge_baselines(
            getattr(scenario, "scenario_id", None),
            oracle_baseline_dir,
            focus_ridge_ids,
            ridge_area_m2,
        )
        # Inline per-ridge donothing OVERRIDES the JSON-loaded value when
        # provided — replay-friendly path that doesn't need a pre-built
        # oracle_baselines/<scenario_id>.json file.
        if donothing_per_ridge_g_m2 is not None and ridge_area_m2 > 0:
            inline_dn_sum = sum(
                float(donothing_per_ridge_g_m2[i])
                for i in focus_set
                if 0 <= i < len(donothing_per_ridge_g_m2)
                and isinstance(donothing_per_ridge_g_m2[i], (int, float))
            )
            focus_dn_bio = inline_dn_sum * ridge_area_m2 / 1000.0
        if focus_oracle_bio is not None and focus_oracle_bio > 0.0:
            focus_ypr = max(0.0, min(1.0, focus_agent_bio / focus_oracle_bio))
        if (
            focus_oracle_bio is not None
            and focus_dn_bio is not None
            and focus_oracle_bio > focus_dn_bio
        ):
            raw_fnys = (focus_agent_bio - focus_dn_bio) / (
                focus_oracle_bio - focus_dn_bio
            )
            focus_nys = max(0.0, min(1.0, raw_fnys))

    return outcome_score, OutcomeBreakdown(
        yield_ratio=yield_ratio,
        recovered_yield_kg=recovered_kg,
        scenario_potential_kg=potential_kg,
        agent_biological_kg=agent_biological_kg,
        oracle_biological_kg=oracle_biological_kg,
        yield_preserved_ratio=yield_preserved_ratio,
        crop_loss_pct=crop_loss_pct,
        donothing_biological_kg=donothing_biological_kg,
        normalized_yield_score=normalized_yield_score,
        growing_loss_count=growing_loss_count,
        harvest_loss_count=harvest_loss_count,
        unharvested_mature_count=unharvested_mature_count,
        crop_loss_count=crop_loss_count,
        crop_loss_fraction=crop_loss_fraction,
        safety_violations=safety_violations,
        expects_agent_harvest=expects_agent_harvest,
        safety_violation_details=safety_details,
        extrapolation=extrapolation_status,
        focus_ridge_ids=focus_ridge_ids if focus_ridge_ids else None,
        focus_agent_biological_kg=focus_agent_bio,
        focus_oracle_biological_kg=focus_oracle_bio,
        focus_donothing_biological_kg=focus_dn_bio,
        focus_yield_preserved_ratio=focus_ypr,
        focus_normalized_yield_score=focus_nys,
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
