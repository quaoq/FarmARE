"""Manifest-bound native intervention checks, excluded from paper evidence.

The fixed reference workflow is preserved except for the declared intervention.
A delay shifts subsequent operations too; this is not an adaptive-policy effect.
"""

from __future__ import annotations

import hashlib
import json
from copy import copy
from functools import wraps
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.distributed.calibration import CalibrationClock
from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.native_season import _farm_outcome
from are.simulation.scenario_runner import ScenarioRunner
from are.simulation.scenarios.config import ScenarioRunnerConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    create_native_scenario,
    phase_for_event,
)
from are.simulation.types import OracleEvent

ARMS = {
    "farm_wetjune_recheck": {
        "reference",
        "omit_mid_fungicide",
        "omit_r5_fungicide",
        "delay_mid_fungicide",
        "delay_mid_fungicide_with_recovery",
    },
    "farm_three_cultivar": {
        "reference",
        "omit_mid_fungicide",
        "omit_irrigation",
        "swap_treatment_regions",
    },
}


class InterventionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["dcore_native_intervention_plan_v1"] = (
        "dcore_native_intervention_plan_v1"
    )
    scenario_id: Literal["farm_wetjune_recheck", "farm_three_cultivar"]
    world_seeds: tuple[int, ...]
    arms: tuple[str, ...]
    delay_days: int = Field(default=3, ge=1, le=7)
    recovery_wait_days: int = Field(default=7, ge=0, le=7)
    paper_eligible: Literal[False] = False
    rationale: str = Field(min_length=20)

    @model_validator(mode="after")
    def validate_development_plan(self):
        if (
            not self.world_seeds
            or len(set(self.world_seeds)) != len(self.world_seeds)
            or any(seed not in range(10) for seed in self.world_seeds)
        ):
            raise ValueError(
                "native development interventions require distinct worlds 0–9"
            )
        if (
            "reference" not in self.arms
            or len(set(self.arms)) != len(self.arms)
            or not set(self.arms) <= ARMS[self.scenario_id]
        ):
            raise ValueError(
                "declare one reference and distinct scenario-supported arms"
            )
        return self


def selected_treatments(scenario):
    """Use concrete function and checkpoint phase, never a fabricated action."""
    selected = []
    for event in scenario.events:
        if not isinstance(event, OracleEvent):
            continue
        action = event.make_event(None).action
        if action.function_name in {"apply_fungicide", "irrigate"}:
            selected.append(
                (event, phase_for_event(event.event_id, action), action.function_name)
            )
    return selected


def instrument_intervention(scenario, farm, plan: InterventionPlan, arm: str):
    records, delayed, recovery_waited = [], [False], [0]
    selected = selected_treatments(scenario)
    expected = 6 if plan.scenario_id == "farm_wetjune_recheck" else 4
    if len(selected) != expected:
        raise ValueError(
            "native treatment inventory changed; revise the explicit plan before execution"
        )
    for event, phase, function in selected:
        original_make = event.make_event

        def make(
            env,
            factory=original_make,
            event_id=event.event_id,
            checkpoint=phase,
            operation=function,
        ):
            source = copy(factory(env))
            source.action = copy(source.action)
            source.action.args = dict(source.action.args)
            original_function = source.action.function
            original_arguments = {
                k: v for k, v in source.action.args.items() if k != "self"
            }
            omit = (
                (
                    arm == "omit_mid_fungicide"
                    and checkpoint == "midseason"
                    and operation == "apply_fungicide"
                )
                or (
                    arm == "omit_r5_fungicide"
                    and checkpoint == "r5"
                    and operation == "apply_fungicide"
                )
                or (arm == "omit_irrigation" and operation == "irrigate")
            )
            is_delayed_group = (
                arm.startswith("delay_mid_fungicide")
                and checkpoint == "midseason"
                and operation == "apply_fungicide"
            )
            if arm == "swap_treatment_regions":
                if operation == "apply_fungicide":
                    # Map B's three native batches onto the whole C zone. The
                    # last batch has one ridge, so treated area changes 22→21.
                    source.action.args["start_ridge"] += 22
                    source.action.args["end_ridge"] = min(
                        63, source.action.args["end_ridge"] + 22
                    )
                elif operation == "irrigate":
                    source.action.args.update(start=21, end=42)
            requested = {k: v for k, v in source.action.args.items() if k != "self"}

            @wraps(original_function)
            def execute(*args, **kwargs):
                if is_delayed_group and not delayed[0]:
                    env.time_manager.add_offset(plan.delay_days * 86400)
                    delayed[0] = True
                while True:
                    farm.advance_physics_time(env.time_manager.time())
                    record = {
                        "event_id": event_id,
                        "phase": checkpoint,
                        "operation": operation,
                        "world_time": env.time_manager.time(),
                        "original_arguments": original_arguments,
                        "requested_arguments": requested,
                        "omitted": omit,
                        "accepted": False,
                        "recovery_wait_days_used": recovery_waited[0],
                    }
                    records.append(record)
                    if omit:
                        return {
                            "calibration_omitted": True,
                            "target_event_id": event_id,
                        }
                    try:
                        result = original_function(*args, **kwargs)
                    except Exception as error:
                        record.update(error_type=type(error).__name__, error=str(error))
                        raise
                    error = result.get("error") if isinstance(result, dict) else None
                    record.update(
                        error=error,
                        accepted=not bool(error),
                        result_digest=stable_digest(result),
                    )
                    recoverable = (
                        error
                        == "Weather conditions do not allow spraying (rain or wind above spray limit)"
                        or (
                            isinstance(error, str)
                            and error.startswith("Soil too wet for")
                        )
                    )
                    if not (
                        arm == "delay_mid_fungicide_with_recovery"
                        and is_delayed_group
                        and recoverable
                        and recovery_waited[0] < plan.recovery_wait_days
                    ):
                        return result
                    env.time_manager.add_offset(86400)
                    recovery_waited[0] += 1

            source.action.function = execute
            return source

        event.make_event = make
    return records


def run_native_interventions(manifest: Path, output_dir: Path, *, dry_run=False):
    plan = InterventionPlan.model_validate(yaml.safe_load(manifest.read_text()))
    serialized = plan.model_dump(mode="json")
    binding = {
        "plan": serialized,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "season_count": len(plan.world_seeds) * len(plan.arms),
        "provider_requests": 0,
        "clock": "event_queue_only",
        "paper_eligible": False,
    }
    if dry_run:
        return binding
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "plan.json").write_text(json.dumps(binding, indent=2) + "\n")
    rows = []
    for seed in plan.world_seeds:
        for arm in plan.arms:
            scenario = create_native_scenario(plan.scenario_id, world_seed=seed)
            farm = scenario.get_typed_app(FarmWorldApp)
            initial = dict(farm.get_state().get("inventory", {}))
            records = instrument_intervention(scenario, farm, plan, arm)
            run = output_dir / f"world_{seed}" / arm
            run.mkdir(parents=True)
            validation = ScenarioRunner(time_manager_factory=CalibrationClock).run(
                ScenarioRunnerConfig(oracle=True, export=True, output_dir=str(run)),
                scenario,
            )
            outcome = _farm_outcome(farm, initial)
            complete = bool(outcome["harvest_complete"] and outcome["storage_complete"])
            row = {
                "world_seed": seed,
                "arm": arm,
                "scenario_id": plan.scenario_id,
                "exogenous_world_digest": stable_digest(
                    farm.physics.dcore_exogenous_manifest
                ),
                "native_validation_success": validation.success,
                "outcome": outcome,
                "complete_outcome": complete,
                "final_marketable_yield_kg": outcome["marketable_yield_kg"]
                if complete
                else None,
                "treatment_attempts": records,
                "paper_eligible": False,
            }
            rows.append(row)
            (run / "intervention_row.json").write_text(json.dumps(row, indent=2) + "\n")
    comparisons = []
    for seed in plan.world_seeds:
        control = next(
            r for r in rows if r["world_seed"] == seed and r["arm"] == "reference"
        )
        for row in (
            r for r in rows if r["world_seed"] == seed and r["arm"] != "reference"
        ):
            paired = row["exogenous_world_digest"] == control["exogenous_world_digest"]
            complete = (
                paired and row["complete_outcome"] and control["complete_outcome"]
            )
            base, value = (
                control["final_marketable_yield_kg"],
                row["final_marketable_yield_kg"],
            )
            comparisons.append(
                {
                    "world_seed": seed,
                    "arm": row["arm"],
                    "paired_world": paired,
                    "complete_pair": complete,
                    "shortfall": (base - value) / base
                    if complete and base and base > 0
                    else None,
                    "native_treatment_rejections": sum(
                        bool(r.get("error")) for r in row["treatment_attempts"]
                    ),
                }
            )
    report = {
        **binding,
        "rows": rows,
        "comparisons": comparisons,
        "limitations": [
            "Development fixed-workflow interventions are not paper evidence or adaptive-agent effects.",
            "The swap changes treated areas (fungicide 22 to 21 ridges; irrigation 21 to 22), so it is not a pure equal-area location contrast.",
            "Delay shifts subsequent reference actions; a window miss is asserted only when native rejections establish it.",
            "Incomplete harvest amounts remain unavailable as final yield; weak and null effects are retained.",
        ],
    }
    (output_dir / "intervention_report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return report
