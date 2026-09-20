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
from dataclasses import asdict
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


def instrument_water_balance(
    farm: FarmWorldApp, scope: tuple[int, int]
) -> list[dict[str, Any]]:
    """Read-only daily hydrology telemetry; native update and return are unchanged.

    Daily irrigation is reported separately from subdaily management pulses.
    Differences in cumulative stores capture drainage/ET; these records do not
    assert a complete subdaily water balance.
    """
    records: list[dict[str, Any]] = []
    soil = farm.physics.soil
    original = soil.update_day

    @wraps(original)
    def update(*args, **kwargs):
        weather = kwargs.get("weather") or args[0]
        before = {r: asdict(soil.states[r]) for r in range(scope[0], scope[1] + 1)}
        result = original(*args, **kwargs)
        day_results = {item.ridge_id: asdict(item) for item in result}
        for ridge, state in before.items():
            records.append(
                {
                    "day": weather.day.isoformat(),
                    "ridge_id": ridge,
                    "rain_mm": weather.rain_mm,
                    "daily_irrigation_input_mm": (
                        kwargs.get("irrigation_mm_by_ridge") or {}
                    ).get(ridge, 0),
                    "weather": asdict(weather),
                    "parameters": asdict(soil.params_for_ridge(ridge)),
                    "before": state,
                    "after": asdict(soil.states[ridge]),
                    "native_daily_water_balance": day_results[ridge],
                    "phenology_before_crop_tick": asdict(
                        farm.physics.phenology.states[ridge]
                    ),
                    "canopy_before_crop_tick": asdict(
                        farm.physics.canopy.states[ridge]
                    ),
                }
            )
        return result

    soil.update_day = update
    return records


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
                "soil_before_native_action": {
                    str(ridge): asdict(soil.states[ridge]) for ridge in ridges
                },
                # ``self`` is the bound app instance and its repr contains a
                # process-local memory address. Preserve only the external
                # request arguments so saved scientific digests are stable.
                "requested_arguments": {
                    key: value
                    for key, value in source.action.args.items()
                    if key != "self"
                },
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
                record["soil_after_native_action"] = {
                    str(ridge): asdict(soil.states[ridge]) for ridge in ridges
                }
                return result
            except Exception as error:
                record["error"] = str(error)
                raise

        source.action.function = execute
        return source

    event.make_event = make


def instrument_harvest_opening(scenario, opening):
    """Shorten only reference harvest waits; never rewind or alter farm actions."""
    records = []
    selected = [
        event
        for event in scenario.events
        if event.event_id.startswith("o_wait_harvest_advance_day_")
    ]
    if len(selected) != 39:
        raise ValueError("reference harvest waiting sequence changed")
    for event in selected:
        factory = event.make_event
        action = factory(None).action
        declared = {k: v for k, v in action.args.items() if k != "self"}
        if action.function_name != "advance_time" or declared != {
            "seconds": 0,
            "minutes": 0,
            "hours": 0,
            "days": 1,
        }:
            raise ValueError("unexpected reference wait")

        def make(env, factory=factory, event_id=event.event_id):
            source = copy(factory(env))
            if env is None:
                return source
            source.action = copy(source.action)
            now = env.time_manager.time()
            seconds = min(86400, max(1, math.ceil(opening - now)))
            source.action.args = {
                **source.action.args,
                "days": 0,
                "hours": 0,
                "minutes": 0,
                "seconds": seconds,
            }
            records.append(
                {
                    "event_id": event_id,
                    "world_time": now,
                    "original_seconds": 86400,
                    "executed_seconds": seconds,
                    "opening_world_time": opening,
                }
            )
            return source

        event.make_event = make
    return records


def instrument_harvest_windows(
    scenario,
    farm,
    *,
    max_wait_days,
    retry_immaturity=False,
    retry_wet_grain=False,
    retry_wet_soil=False,
    deadline_world_time: float | None = None,
):
    """Exploratory oracle repair: wait after rain rejection, within a season cap.

    Native weather and harvest checks remain authoritative. Every rejected
    attempt and time advance is recorded. Each additional retry class requires
    an explicit separately recorded workflow variant.
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
                    if not (
                        error == "Cannot harvest in rainy conditions"
                        or (
                            retry_immaturity
                            and isinstance(error, str)
                            and error.startswith(
                                "Ridges are not mature enough for harvest:"
                            )
                        )
                        or (
                            retry_wet_grain
                            and isinstance(error, str)
                            and error.startswith("Grain moisture too high for harvest")
                        )
                        or (
                            retry_wet_soil
                            and isinstance(error, str)
                            and error.startswith("Soil too wet for harvest")
                        )
                    ) or (
                        env.time_manager.time() + 86400 >= deadline_world_time
                        if deadline_world_time is not None
                        else waited[0] >= max_wait_days
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
    confirmation_binding: dict[str, Any] | None = None,
    native_scenario: dict[str, Any] | None = None,
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
    binding = None
    if confirmation_binding is not None:
        from are.simulation.distributed.scenario_confirmation import (
            DroughtConfirmationBinding,
        )

        binding = DroughtConfirmationBinding.model_validate(confirmation_binding)
        binding.verify_current_source()
        binding.verify_plan(plan)
        if plan.get("confirmation_binding") != binding.model_dump(mode="json"):
            raise ValueError(
                "report is not bound to this prospective confirmation design"
            )
        if report.get("confirmation_source_unchanged") is not True:
            raise ValueError("confirmation execution source changed during measurement")
        if native_scenario is not None:
            binding.verify_native_scenario(native_scenario)
        if world_seed is not None and world_seed not in (
            binding.study_worlds + binding.live_smoke_worlds
        ):
            raise ValueError(
                "world seed is outside the frozen study/live smoke cohorts"
            )
    elif (
        plan.get("scenario_id") != SCENARIO_ID
        or plan.get("candidate") is not False
        or plan.get("scenario_revision") is not None
        or plan.get("confirmation_binding") is not None
        or (
            native_scenario
            and (
                native_scenario.get("scenario_revision") is not None
                or native_scenario.get("calibration_candidate") is not False
            )
        )
    ):
        raise ValueError(
            "sensitivity evidence must validate the released default scenario, not a candidate"
        )
    if binding is None and plan.get("harvest_retry_days", 0):
        raise ValueError(
            "exploratory harvest-retry evidence cannot validate the unchanged released workflow"
        )
    if report.get("plan_digest") != stable_digest(plan):
        raise ValueError("scenario sensitivity plan digest mismatch")
    if (
        not _finite_number(plan.get("min_shortfall"))
        or plan["min_shortfall"] < 0.01
        or not _finite_number(plan.get("min_stressed_fraction"))
        or plan["min_stressed_fraction"] < 0.5
    ):
        raise ValueError(
            "release evidence cannot weaken the 1% loss / 50% stress screening criteria"
        )
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
    if world_seed is not None and binding is None:
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
    scenario_revision: str | None = None,
    retry_immaturity: bool = False,
    retry_wet_grain: bool = False,
    retry_wet_soil: bool = False,
    confirmation_manifest: Path | None = None,
    reference_process_path: Path | None = None,
) -> dict[str, Any]:
    # Validate even in dry-run mode, before creating files or running seasons.
    if confirmation_manifest is None and any(
        seed not in range(10) for seed in world_seeds
    ):
        raise ValueError(
            "worlds outside development 0–9 require a prospective confirmation manifest"
        )
    assess_calibration(
        [],
        expected_seeds=world_seeds,
        min_shortfall=min_shortfall,
        min_stressed_fraction=min_stressed_fraction,
    )
    if type(harvest_retry_days) is not int or not 0 <= harvest_retry_days <= (
        21
        if (retry_immaturity and retry_wet_grain)
        else (14 if (retry_immaturity or retry_wet_grain) else 7)
    ):
        raise ValueError(
            "harvest_retry_days exceeds the declared workflow cap "
            "(7 rain-only; 14 maturity; 21 maturity/moisture)"
        )
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
                "workflow_variant": "bounded_three_week_harvest_retry_v4"
                if harvest_retry_days > 14
                else "bounded_rain_maturity_moisture_retry_v3"
                if retry_wet_grain
                else (
                    "bounded_rain_and_maturity_retry_v2"
                    if retry_immaturity
                    else "bounded_rain_harvest_retry_v1"
                ),
                "retry_immaturity": retry_immaturity,
                "retry_wet_grain": retry_wet_grain,
            }
        )
    if scenario_revision:
        plan["scenario_revision"] = scenario_revision
    reference_raw = None
    deadline = None
    opening = None
    if reference_process_path is not None:
        from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5

        if harvest_retry_days or not (retry_immaturity and retry_wet_grain):
            raise ValueError(
                "authored-calendar reference requires rain/maturity/moisture retries and no relative-day cap"
            )
        reference_raw = reference_process_path.read_bytes()
        process = FarmProcessSpecV5.model_validate_json(reference_raw)
        settings = process.metadata.get(
            "native_scenario",
            {
                "scenario_revision": None,
                "calibration_candidate": False,
            },
        )
        if process.scenario_id != SCENARIO_ID or settings != {
            "scenario_revision": scenario_revision,
            "calibration_candidate": candidate,
        }:
            raise ValueError("reference process native scenario mismatch")
        windows = [w for w in process.phase_windows if w.phase == "harvest"]
        if len(windows) != 1 or windows[0].end_world_time is None:
            raise ValueError("reference process requires one bounded harvest window")
        horizon = process.metadata.get("scenario_horizon")
        if horizon is None:
            horizon = process.occurrence_net.metadata.get("scenario_horizon")
        deadline = min(
            windows[0].end_world_time,
            float(horizon) if horizon is not None else windows[0].end_world_time,
        )
        declared_deadline = process.occurrence_net.metadata.get(
            "reference_harvest_deadlines", {}
        ).get("harvest")
        if declared_deadline is not None and float(declared_deadline) != deadline:
            raise ValueError(
                "reference harvest deadline conflicts with the native scenario horizon"
            )
        policy = process.metadata.get("authored_choices", {}).get(
            "reference_harvest_policy", "authored_harvest_calendar_v5"
        )
        if policy not in {
            "authored_harvest_calendar_v5",
            "authored_opening_harvest_v6",
        }:
            raise ValueError("unknown reference harvest policy")
        if policy == "authored_opening_harvest_v6":
            opening = windows[0].start_world_time
            if opening is None:
                raise ValueError("opening reference requires a finite start")
            if not retry_wet_soil:
                raise ValueError("opening reference requires wet-soil recovery")
            plan["harvest_opening_world_time"] = opening
        plan.update(
            workflow_variant=policy,
            harvest_deadline_world_time=deadline,
            reference_process_digest=process.digest,
            retry_immaturity=True,
            retry_wet_grain=True,
            retry_wet_soil=retry_wet_soil,
        )
    binding = None
    if confirmation_manifest is not None:
        from are.simulation.distributed.scenario_confirmation import (
            DroughtConfirmationBinding,
        )

        binding = DroughtConfirmationBinding.model_validate_json(
            confirmation_manifest.read_text(encoding="utf-8")
        )
        binding.verify_current_source()
        binding.verify_plan(plan)
        plan["confirmation_binding"] = binding.model_dump(mode="json")
    if dry_run:
        return plan
    output_dir.mkdir(parents=True, exist_ok=False)
    if reference_raw is not None:
        (output_dir / "reference.process.json").write_bytes(reference_raw)
    (output_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    pairs = []
    for seed in world_seeds:
        pair: dict[str, Any] = {"world_seed": seed}
        for name, omit in (("control", False), ("omission", True)):
            scenario = create_native_scenario(
                SCENARIO_ID,
                world_seed=seed,
                calibration_candidate=candidate,
                scenario_revision=scenario_revision,
            )
            farm = scenario.get_typed_app(FarmWorldApp)
            initial = dict(farm.get_state().get("inventory", {}))
            exogenous = farm.physics.dcore_exogenous_manifest
            target, scope = drought_target(scenario)
            water_balance = instrument_water_balance(farm, scope)
            records: list[dict[str, Any]] = []
            instrument_target(target, farm, scope, omit=omit, records=records)
            harvest_schedule = (
                instrument_harvest_opening(scenario, opening)
                if opening is not None
                else None
            )
            harvest_attempts = (
                instrument_harvest_windows(
                    scenario,
                    farm,
                    max_wait_days=harvest_retry_days,
                    retry_immaturity=retry_immaturity,
                    retry_wet_grain=retry_wet_grain,
                    retry_wet_soil=retry_wet_soil,
                    deadline_world_time=deadline,
                )
                if harvest_retry_days or deadline is not None
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
            if harvest_schedule is not None:
                result["harvest_schedule_changes"] = harvest_schedule
            pair[name] = result
            (run_dir / "calibration_row.json").write_text(
                json.dumps(result, indent=2, default=str), encoding="utf-8"
            )
            (run_dir / "water_balance.json").write_text(
                json.dumps(
                    {
                        "schema_version": "farm_water_balance_diagnostics_v1",
                        "units": {"vwc": "m3/m3", "water": "mm", "root_depth": "m"},
                        "sampling": "native daily soil tick; phenology/canopy sampled before their daily tick",
                        "rows": water_balance,
                    },
                    default=str,
                ),
                encoding="utf-8",
            )
        pairs.append(pair)
    report = assess_calibration(
        pairs,
        expected_seeds=world_seeds,
        min_shortfall=min_shortfall,
        min_stressed_fraction=min_stressed_fraction,
    )
    report.update({"plan": plan, "plan_digest": stable_digest(plan), "pairs": pairs})
    if binding is not None:
        from are.simulation.distributed.experiments import execution_source_digest

        report["confirmation_source_unchanged"] = (
            execution_source_digest() == binding.execution_source_digest
        )
    (output_dir / "calibration_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return report
