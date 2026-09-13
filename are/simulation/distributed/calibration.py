"""Paired no-model drought calibration, isolated from paper observations.

The omission replaces exactly one oracle action with a recorded no-op while
preserving its successors and all other scripted decisions. This estimates a
fixed-workflow intervention, not an adaptive agent's response to a fault.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from copy import copy
from functools import wraps
from pathlib import Path
from typing import Any

from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.distributed.farm_adapter import scope_from_args
from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.native_season import _farm_outcome
from are.simulation.scenario_runner import ScenarioRunner
from are.simulation.scenarios.config import ScenarioRunnerConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario
from are.simulation.time_manager import TimeManager
from are.simulation.types import Action, OracleEvent

SCENARIO_ID = "farm_disease_drought"


class CalibrationClock(TimeManager):
    """Queue-driven simulation time; wall-clock overhead cannot change a pair."""

    def time_passed(self) -> float:
        return self.offset + (self.pause_offset if self.is_paused else 0.0)

    def pause(self) -> None:
        if not self.is_paused:
            self.is_paused = True
            self.pause_offset = 0.0

    def resume(self) -> None:
        if self.is_paused:
            self.offset += self.pause_offset
            self.pause_offset = 0.0
            self.is_paused = False

    def reset(self, start_time: float | None = None) -> None:
        super().reset(start_time)
        self.pause_offset = 0.0


def _finite_number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _verified_stress_fraction(record: dict[str, Any]) -> float | None:
    """Recompute from complete per-ridge measurements, not a claimed summary."""
    scope = record.get("scope")
    if (
        not isinstance(scope, (list, tuple))
        or len(scope) != 2
        or any(type(value) is not int for value in scope)
        or not 0 <= scope[0] <= scope[1] < 64
    ):
        return None
    root, thresholds = (
        record.get("root_vwc_by_ridge"),
        record.get("stress_threshold_by_ridge"),
    )
    expected = {str(ridge) for ridge in range(scope[0], scope[1] + 1)}
    if (
        not isinstance(root, dict)
        or not isinstance(thresholds, dict)
        or set(root) != expected
        or set(thresholds) != expected
    ):
        return None
    if any(
        not _finite_number(value) or not 0 <= value <= 1
        for value in (*root.values(), *thresholds.values())
    ):
        return None
    fraction = sum(root[ridge] < thresholds[ridge] for ridge in expected) / len(
        expected
    )
    summary = record.get("stressed_fraction")
    if not _finite_number(summary) or not math.isclose(
        summary, fraction, abs_tol=1e-12
    ):
        return None
    return fraction


def drought_target(scenario: Any) -> tuple[OracleEvent, tuple[int, int]]:
    candidates = []
    for event in scenario.events:
        if not isinstance(event, OracleEvent):
            continue
        action = getattr(event.make_event(None), "action", None)
        if (
            isinstance(action, Action)
            and action.function_name == "irrigate"
            and "r5" in event.event_id
        ):
            scope = scope_from_args(action.args)
            if scope is None:
                raise ValueError("irrigation target has no explicit ridge scope")
            candidates.append((event, scope))
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one R5 irrigation target, found {len(candidates)}"
        )
    return candidates[0]


def instrument_target(
    event: OracleEvent,
    farm: FarmWorldApp,
    scope: tuple[int, int],
    *,
    omit: bool,
    records: list[dict[str, Any]],
) -> None:
    original_make = event.make_event

    def make(env):
        source = copy(original_make(env))
        source.action = copy(source.action)
        original_function = source.action.function

        @wraps(original_function)
        def execute(*args, **kwargs):
            # Native irrigation synchronizes lazy physics before checking water.
            # Apply the same synchronization in both arms before measuring it.
            farm.advance_physics_time(env.time_manager.time())
            soil = farm.physics.soil
            ridges = list(range(scope[0], scope[1] + 1))
            root = [float(soil.states[ridge].root_vwc) for ridge in ridges]
            thresholds = [
                float(soil.params_for_ridge(ridge).water_stress_vwc) for ridge in ridges
            ]
            record = {
                "target_event_id": event.event_id,
                "scope": list(scope),
                "world_time": env.time_manager.time(),
                "root_vwc_by_ridge": dict(zip(map(str, ridges), root)),
                "stress_threshold_by_ridge": dict(zip(map(str, ridges), thresholds)),
                "mean_root_vwc": statistics.mean(root),
                "stressed_fraction": sum(
                    v < threshold for v, threshold in zip(root, thresholds)
                )
                / len(root),
                "omitted": omit,
                "accepted": False,
            }
            records.append(record)
            if omit:
                return {"calibration_omitted": True, "target_event_id": event.event_id}
            try:
                result = original_function(*args, **kwargs)
                record["accepted"] = not (
                    isinstance(result, dict) and result.get("error")
                )
                record["result_digest"] = stable_digest(result)
                return result
            except Exception as error:
                record["error"] = str(error)
                raise

        source.action.function = execute
        return source

    event.make_event = make


def instrument_harvest_windows(scenario, farm, *, max_wait_days):
    """Exploratory oracle repair: wait after rain rejection, within a season cap.

    Native weather and harvest checks remain authoritative. Every rejected
    attempt and time advance is recorded; other error kinds are not retried.
    """
    records, waited = [], [0]
    for event in scenario.events:
        if not isinstance(event, OracleEvent):
            continue
        action = getattr(event.make_event(None), "action", None)
        if not isinstance(action, Action) or action.function_name != "harvest":
            continue
        original_make, event_id = event.make_event, event.event_id

        def make(env, factory=original_make, target_id=event_id):
            source = copy(factory(env))
            source.action = copy(source.action)
            original = source.action.function

            @wraps(original)
            def execute(*args, **kwargs):
                while True:
                    farm.advance_physics_time(env.time_manager.time())
                    result = original(*args, **kwargs)
                    error = result.get("error") if isinstance(result, dict) else None
                    records.append(
                        {
                            "event_id": target_id,
                            "world_time": env.time_manager.time(),
                            "error": error,
                            "wait_days_used": waited[0],
                        }
                    )
                    if (
                        error != "Cannot harvest in rainy conditions"
                        or waited[0] >= max_wait_days
                    ):
                        return result
                    env.time_manager.add_offset(86400)
                    waited[0] += 1

            source.action.function = execute
            return source

        event.make_event = make
    return records


def assess_calibration(
    pairs: list[dict[str, Any]],
    *,
    expected_seeds: list[int],
    min_shortfall: float,
    min_stressed_fraction: float,
) -> dict[str, Any]:
    """Fail closed on missing, duplicate, nonfinite or physically inactive pairs."""
    if (
        not expected_seeds
        or any(type(seed) is not int for seed in expected_seeds)
        or len(set(expected_seeds)) != len(expected_seeds)
    ):
        raise ValueError("expected seeds must be nonempty and unique")
    if not 0 < min_shortfall <= 1 or not 0 < min_stressed_fraction <= 1:
        raise ValueError("calibration thresholds must be in (0, 1]")
    seeds = [pair["world_seed"] for pair in pairs]
    complete = sorted(seeds) == sorted(expected_seeds)
    checks = []
    for pair in pairs:
        control, omission = pair["control"], pair["omission"]
        reasons = []
        if control.get("exogenous_world_digest") != omission.get(
            "exogenous_world_digest"
        ) or not control.get("exogenous_world_digest"):
            reasons.append("exogenous_world_mismatch")
        if (
            len(control.get("target_records", [])) != 1
            or len(omission.get("target_records", [])) != 1
        ):
            reasons.append("target_not_executed_exactly_once")
        else:
            before, omitted = (
                control["target_records"][0],
                omission["target_records"][0],
            )
            if (
                before.get("accepted") is not True
                or before.get("omitted") is not False
                or omitted.get("omitted") is not True
            ):
                reasons.append("inactive_intervention")
            for key in (
                "target_event_id",
                "scope",
                "world_time",
                "root_vwc_by_ridge",
                "stress_threshold_by_ridge",
            ):
                if key not in before or before.get(key) != omitted.get(key):
                    reasons.append(f"preintervention_mismatch:{key}")
            stress = _verified_stress_fraction(before)
            if stress is None or not min_stressed_fraction <= stress <= 1:
                reasons.append("insufficient_root_zone_stress")
        if control.get("validation_success") is not True:
            reasons.append("control_workflow_failed")
        for condition in (control, omission):
            if (
                not condition.get("harvest_complete")
                or not condition.get("storage_complete")
                or condition.get("exception")
            ):
                reasons.append("incomplete_or_failed_season")
        yields = [
            condition.get("marketable_yield_kg") for condition in (control, omission)
        ]
        shortfall = None
        if any(not _finite_number(y) or y < 0 for y in yields) or yields[0] == 0:
            reasons.append("invalid_yield")
        else:
            shortfall = (yields[0] - yields[1]) / yields[0]
            if shortfall < min_shortfall:
                reasons.append("insufficient_omission_loss")
        checks.append(
            {
                "world_seed": pair["world_seed"],
                "passed": not reasons,
                "shortfall": shortfall,
                "reasons": reasons,
            }
        )
    return {
        "schema_version": "farm_drought_calibration_v1",
        "paper_eligible": False,
        "complete": complete,
        "multi_world": len(expected_seeds) >= 5,
        "engineering_acceptance_passed": complete
        and len(expected_seeds) >= 5
        and all(row["passed"] for row in checks),
        "min_shortfall": min_shortfall,
        "min_stressed_fraction": min_stressed_fraction,
        "checks": checks,
        "pairs_digest": stable_digest(pairs),
        "interpretation": "Fixed-workflow irrigation omission; expert review and a new frozen scenario are required before paper use.",
    }


def validate_release_sensitivity(
    report_path: Path,
    expected_digest: str,
    *,
    world_seed: int | None = None,
    exogenous_digest: str | None = None,
) -> dict[str, Any]:
    """Verify saved results before accepting a drought release attestation."""
    raw = report_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError("scenario sensitivity report digest mismatch")
    report = json.loads(raw)
    plan = report.get("plan", {})
    if plan.get("clock_mode") != "event_queue_only":
        raise ValueError(
            "scenario sensitivity requires a deterministic calibration clock"
        )
    if plan.get("scenario_id") != SCENARIO_ID or plan.get("candidate") is not False:
        raise ValueError(
            "sensitivity evidence must validate the released default scenario, not a candidate"
        )
    if plan.get("harvest_retry_days", 0):
        raise ValueError(
            "exploratory harvest-retry evidence cannot validate the unchanged released workflow"
        )
    if report.get("plan_digest") != stable_digest(plan):
        raise ValueError("scenario sensitivity plan digest mismatch")
    recomputed = assess_calibration(
        report.get("pairs", []),
        expected_seeds=plan.get("world_seeds", []),
        min_shortfall=plan.get("min_shortfall", 0),
        min_stressed_fraction=plan.get("min_stressed_fraction", 0),
    )
    if not recomputed["engineering_acceptance_passed"]:
        raise ValueError(
            "scenario sensitivity evidence fails recomputed acceptance checks"
        )
    if any(report.get(key) != value for key, value in recomputed.items()):
        raise ValueError(
            "scenario sensitivity report disagrees with its underlying measurements"
        )
    if world_seed is not None:
        pair = next(
            (pair for pair in report["pairs"] if pair["world_seed"] == world_seed), None
        )
        if pair is None:
            raise ValueError(
                "scenario sensitivity evidence does not cover this world seed"
            )
        if (
            exogenous_digest is not None
            and pair["control"]["exogenous_world_digest"] != exogenous_digest
        ):
            raise ValueError(
                "scenario sensitivity evidence does not match the released world"
            )
    return report


def run_drought_calibration(
    output_dir: Path,
    *,
    world_seeds: list[int],
    candidate: bool,
    min_shortfall: float = 0.01,
    min_stressed_fraction: float = 0.5,
    dry_run: bool = False,
    harvest_retry_days: int = 0,
) -> dict[str, Any]:
    # Validate even in dry-run mode, before creating files or running seasons.
    assess_calibration(
        [],
        expected_seeds=world_seeds,
        min_shortfall=min_shortfall,
        min_stressed_fraction=min_stressed_fraction,
    )
    if type(harvest_retry_days) is not int or not 0 <= harvest_retry_days <= 7:
        raise ValueError("harvest_retry_days must be an integer between zero and seven")
    plan = {
        "schema_version": "farm_drought_calibration_plan_v1",
        "scenario_id": SCENARIO_ID,
        "world_seeds": world_seeds,
        "candidate": candidate,
        "clock_mode": "event_queue_only",
        "season_count": 2 * len(world_seeds),
        "model_calls": 0,
        "min_shortfall": min_shortfall,
        "min_stressed_fraction": min_stressed_fraction,
        "paper_eligible": False,
    }
    if harvest_retry_days:
        plan.update(
            {
                "harvest_retry_days": harvest_retry_days,
                "workflow_variant": "bounded_rain_harvest_retry_v1",
            }
        )
    if dry_run:
        return plan
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    pairs = []
    for seed in world_seeds:
        pair: dict[str, Any] = {"world_seed": seed}
        for name, omit in (("control", False), ("omission", True)):
            scenario = create_native_scenario(
                SCENARIO_ID, world_seed=seed, calibration_candidate=candidate
            )
            farm = scenario.get_typed_app(FarmWorldApp)
            initial = dict(farm.get_state().get("inventory", {}))
            exogenous = farm.physics.dcore_exogenous_manifest
            target, scope = drought_target(scenario)
            records: list[dict[str, Any]] = []
            instrument_target(target, farm, scope, omit=omit, records=records)
            harvest_attempts = (
                instrument_harvest_windows(
                    scenario, farm, max_wait_days=harvest_retry_days
                )
                if harvest_retry_days
                else None
            )
            run_dir = output_dir / f"world_{seed}" / name
            run_dir.mkdir(parents=True)
            validation = ScenarioRunner(time_manager_factory=CalibrationClock).run(
                ScenarioRunnerConfig(oracle=True, export=True, output_dir=str(run_dir)),
                scenario,
            )
            result = {
                **_farm_outcome(farm, initial),
                "validation_success": validation.success,
                "exception": str(validation.exception)
                if validation.exception
                else None,
                "exogenous_world_digest": stable_digest(exogenous),
                "target_records": records,
            }
            if harvest_attempts is not None:
                result["harvest_attempts"] = harvest_attempts
            pair[name] = result
            (run_dir / "calibration_row.json").write_text(
                json.dumps(result, indent=2, default=str), encoding="utf-8"
            )
        pairs.append(pair)
    report = assess_calibration(
        pairs,
        expected_seeds=world_seeds,
        min_shortfall=min_shortfall,
        min_stressed_fraction=min_stressed_fraction,
    )
    report.update({"plan": plan, "plan_digest": stable_digest(plan), "pairs": pairs})
    (output_dir / "calibration_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return report
