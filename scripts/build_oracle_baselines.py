"""Generate cached oracle biological-yield baselines for FOS attribution.

For each requested scenario this script:
  1. Looks up the scenario class in the registry.
  2. Runs it under oracle mode (queue-based loop, no LLM agent) — i.e.
     plays back the scenario's hard-coded "perfect-management" oracle
     events through the live physics engine.
  3. Extrapolates physics forward to R8 maturity (or `--max-days` cap)
     so the latent biological yield reflects the full season, not the
     partial state at scenario stop time.
  4. Reads `physics.yield_recovery.states[*].biological_yield_g_m2`
     plus per-ridge metadata.
  5. Writes ``oracle_baselines/<scenario_id>.json`` with totals and
     per-ridge details.

The resulting JSON is consumed by `evaluate_fos(...)` (via
``_load_oracle_baseline_biological_kg``) to compute the headline
attribution metric:

    crop_loss_pct = 1 - agent_biological_kg / oracle_biological_kg

This is the "how much yield potential did the agent's decisions
preserve, vs. an optimal-play baseline" number that the FOS Outcome
component reports.

Usage
-----
    python scripts/build_oracle_baselines.py \\
        --scenarios scenario_irrigate_now_physics_action_tick,scenario_physics_disease_after_rain_fungicide \\
        --output-dir oracle_baselines

    # or "all" for every registered farm scenario
    python scripts/build_oracle_baselines.py --scenarios all --output-dir oracle_baselines

Re-running on an existing baseline file is idempotent unless `--force`.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("build_oracle_baselines")


# Match the per-scenario list the FOS validation paper sweep covers, plus
# every round-3 / round-4 scenario that has a registered oracle so the
# baseline directory is paper-complete out of the box.
DEFAULT_SCENARIO_GROUPS: dict[str, list[str]] = {
    # round-1+2 baseline (8)
    "round12": [
        "scenario_farm_world_drone_survey_physics_action_tick",
        "scenario_farm_world_fertilizer_physics_action_tick",
        "scenario_farm_world_field_prep_physics_action_tick",
        "scenario_farm_world_harvest_physics_action_tick",
        "scenario_farm_world_irrigation_physics_action_tick",
        "scenario_farm_world_pesticide_outbreak_physics_action_tick",
        "scenario_farm_world_pesticide_physics_action_tick",
        "scenario_farm_world_planting_physics_action_tick",
    ],
    # round-3 mid-season episodes (8)
    "round3": [
        "scenario_physics_differential_diagnosis_fertigation",
        "scenario_physics_disease_after_rain_fungicide",
        "scenario_physics_emergence_replant_decision",
        "scenario_physics_harvest_moisture_timing",
        "scenario_physics_planting_window_reschedule",
        "scenario_physics_pod_fill_drought_irrigation",
        "scenario_physics_postharvest_drying_storage",
        "scenario_physics_threshold_pest_monitoring",
    ],
    # round-4 fullseason (10)
    "round4": [
        "scenario_full_season_adversarial_weather",
        "scenario_full_season_aphid_threshold",
        "scenario_full_season_balanced",
        "scenario_full_season_cold_spring",
        "scenario_full_season_dry_pod_fill",
        "scenario_full_season_late_harvest_rain_risk",
        "scenario_full_season_mixed_stress_trap",
        "scenario_full_season_nutrient_differential",
        "scenario_full_season_resource_limited",
        "scenario_full_season_wet_june_disease",
    ],
    "tangyan5": [
        "scenario_tangyan5_expert_baseline_full_season",
        "scenario_tangyan5_stress_free_oracle_full_season",
    ],
}


def _all_default_scenarios() -> list[str]:
    out: list[str] = []
    for group in DEFAULT_SCENARIO_GROUPS.values():
        out.extend(group)
    return out


@dataclass
class BaselineResult:
    scenario_id: str
    biological_yield_g_m2_per_ridge: list[float]
    biological_yield_kg_total: float
    recovered_yield_g_m2_per_ridge: list[float]
    recovered_yield_kg_total: float
    ridges_planted: int
    ridges_r8: int
    ridges_harvested: int
    extrapolation: dict[str, Any]
    duration_s: float
    donothing: dict[str, Any] | None = None


def _build_donothing_biological_kg(
    scenario_id: str,
    extrapolation_max_days: int,
) -> dict[str, Any]:
    """Initialize scenario without any oracle events and extrapolate to R8.

    This gives the "what would the field produce with zero management" lower
    bound. For scenarios that start from bare soil (planting is an oracle
    action) this will be 0 kg, which is the correct answer — nothing was
    planted. For mid-season scenarios that start with a pre-planted field
    (set up in ``init_and_populate_apps``), this represents weather-driven
    yield with no irrigation, fertilization, or pest control.

    Returns a dict with keys:
        biological_yield_kg_total, biological_yield_g_m2_per_ridge,
        ridges_planted, ridges_r8, extrapolation, duration_s
    """
    from are.simulation.scenarios.fos.evaluation import (
        _extrapolate_physics_to_maturity,
        _try_get_farm_world,
    )
    from are.simulation.scenarios.utils.registry import registry
    from are.simulation.apps.farm_world.farm_world_app import (
        DEFAULT_RIDGE_WIDTH_M,
        FIELD_LENGTH_M,
    )

    cls = registry.get_scenario(scenario_id)
    scenario = cls()
    scenario.initialize()  # apps + physics init, NO oracle events run

    farm_world = _try_get_farm_world(scenario)
    if farm_world is None:
        return {"error": "no_farm_world"}

    t0 = time.time()
    extrap = _extrapolate_physics_to_maturity(farm_world, max_days=extrapolation_max_days)

    physics = getattr(farm_world, "_physics", None)
    if physics is None or not getattr(physics, "engines_active", False):
        # Physics not active: bare-field scenario with no initial ticking.
        return {
            "biological_yield_kg_total": 0.0,
            "biological_yield_g_m2_per_ridge": [],
            "ridges_planted": 0,
            "ridges_r8": 0,
            "extrapolation": extrap,
            "duration_s": round(time.time() - t0, 2),
        }

    ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
    bio_per: list[float] = []
    n_planted = 0
    n_r8 = 0
    for rid in sorted(physics.yield_recovery.states.keys()):
        yld = physics.yield_recovery.states[rid]
        phen = physics.phenology.states.get(rid)
        bio_per.append(round(float(yld.biological_yield_g_m2), 4))
        if phen is not None and getattr(phen, "planted", False):
            n_planted += 1
        if getattr(yld, "r8_reached", False):
            n_r8 += 1

    bio_total_kg = sum(bio_per) * ridge_area_m2 / 1000.0
    return {
        "biological_yield_kg_total": round(bio_total_kg, 2),
        "biological_yield_g_m2_per_ridge": bio_per,
        "ridges_planted": n_planted,
        "ridges_r8": n_r8,
        "extrapolation": extrap,
        "duration_s": round(time.time() - t0, 2),
    }


def _build_one_baseline(
    scenario_id: str,
    extrapolation_max_days: int,
    include_donothing: bool = False,
) -> BaselineResult:
    """Run scenario in oracle mode + extrapolation, return per-ridge yields."""
    from are.simulation.environment import Environment, EnvironmentConfig
    from are.simulation.notification_system import VerboseNotificationSystem
    from are.simulation.types import EnvironmentType
    from are.simulation.scenarios.fos.evaluation import (
        _extrapolate_physics_to_maturity,
        _try_get_farm_world,
    )
    from are.simulation.scenarios.utils.registry import registry

    cls = registry.get_scenario(scenario_id)
    scenario = cls()
    scenario.initialize()

    env_config = EnvironmentConfig(
        oracle_mode=True,
        queue_based_loop=True,
        time_increment_in_seconds=getattr(scenario, "time_increment_in_seconds", 1),
        exit_when_no_events=True,
    )
    if getattr(scenario, "start_time", None) and scenario.start_time > 0:
        env_config.start_time = scenario.start_time

    env = Environment(
        environment_type=EnvironmentType.CLI,
        config=env_config,
        notification_system=VerboseNotificationSystem(),
    )

    t0 = time.time()
    env.run(scenario, wait_for_end=False)
    env.join()

    farm_world = _try_get_farm_world(scenario)
    if farm_world is None:
        raise RuntimeError(
            f"Scenario {scenario_id} has no FarmWorldApp — cannot build baseline"
        )

    extrap = _extrapolate_physics_to_maturity(
        farm_world, max_days=extrapolation_max_days
    )

    # Read per-ridge biological + recovered, compute totals in kg.
    from are.simulation.apps.farm_world.farm_world_app import (
        DEFAULT_RIDGE_WIDTH_M,
        FIELD_LENGTH_M,
    )

    physics = getattr(farm_world, "_physics", None)
    if physics is None or not getattr(physics, "engines_active", False):
        raise RuntimeError(
            f"Scenario {scenario_id} has no active physics engine post-run"
        )

    ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M

    bio_per: list[float] = []
    rec_per: list[float] = []
    n_planted = 0
    n_r8 = 0
    n_harv = 0

    for rid in sorted(physics.yield_recovery.states.keys()):
        yld = physics.yield_recovery.states[rid]
        phen = physics.phenology.states.get(rid)
        bio_per.append(round(float(yld.biological_yield_g_m2), 4))
        rec_per.append(round(float(yld.recovered_yield_g_m2_at_market_moisture), 4))
        if phen is not None and getattr(phen, "planted", False):
            n_planted += 1
        if getattr(yld, "r8_reached", False):
            n_r8 += 1
        if getattr(yld, "harvested", False):
            n_harv += 1

    bio_total_kg = sum(bio_per) * ridge_area_m2 / 1000.0
    rec_total_kg = sum(rec_per) * ridge_area_m2 / 1000.0

    duration = time.time() - t0

    try:
        env.stop()
    except Exception:
        pass

    # Optional do-nothing sub-baseline (separate initialization).
    donothing: dict[str, Any] | None = None
    if include_donothing:
        try:
            donothing = _build_donothing_biological_kg(
                scenario_id, extrapolation_max_days
            )
        except Exception as exc:
            donothing = {"error": str(exc)}

    return BaselineResult(
        scenario_id=scenario_id,
        biological_yield_g_m2_per_ridge=bio_per,
        biological_yield_kg_total=round(bio_total_kg, 2),
        recovered_yield_g_m2_per_ridge=rec_per,
        recovered_yield_kg_total=round(rec_total_kg, 2),
        ridges_planted=n_planted,
        ridges_r8=n_r8,
        ridges_harvested=n_harv,
        extrapolation=extrap,
        duration_s=round(duration, 2),
        donothing=donothing,
    )


def _baseline_to_dict(b: BaselineResult) -> dict[str, Any]:
    from are.simulation.apps.farm_world.farm_world_app import (
        DEFAULT_RIDGE_WIDTH_M,
        FIELD_LENGTH_M,
    )

    d: dict[str, Any] = {
        "scenario_id": b.scenario_id,
        "schema_version": 2,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ridge_area_m2": round(FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M, 4),
        "ridges_total": len(b.biological_yield_g_m2_per_ridge),
        "ridges_planted": b.ridges_planted,
        "ridges_r8": b.ridges_r8,
        "ridges_harvested": b.ridges_harvested,
        "biological_yield_g_m2_per_ridge": b.biological_yield_g_m2_per_ridge,
        "biological_yield_kg_total": b.biological_yield_kg_total,
        "recovered_yield_g_m2_per_ridge": b.recovered_yield_g_m2_per_ridge,
        "recovered_yield_kg_total": b.recovered_yield_kg_total,
        "extrapolation": b.extrapolation,
        "build_duration_s": b.duration_s,
    }
    if b.donothing is not None:
        d["donothing"] = b.donothing
    return d


def _resolve_scenarios(spec: str) -> list[str]:
    spec = spec.strip()
    if spec == "all":
        return _all_default_scenarios()
    if spec in DEFAULT_SCENARIO_GROUPS:
        return list(DEFAULT_SCENARIO_GROUPS[spec])
    return [s.strip() for s in spec.split(",") if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--scenarios",
        default="all",
        help=(
            "Comma-separated scenario IDs, or one of: 'all', 'round12', "
            "'round3', 'round4'. Default: all."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "oracle_baselines"),
        help="Where to write <scenario_id>.json files (default: ./oracle_baselines).",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=180,
        help="Cap on extrapolation days. 180 covers any soybean season.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-build even if a baseline file already exists.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Log + skip a scenario on failure rather than aborting the whole run.",
    )
    parser.add_argument(
        "--include-donothing",
        action="store_true",
        default=False,
        help=(
            "Also build a do-nothing sub-baseline: initialize the scenario "
            "without running any oracle events, then extrapolate to R8. "
            "Stored as baseline['donothing']['biological_yield_kg_total']. "
            "Enables the normalized yield score in evaluate_fos(): "
            "  (agent_bio - donothing_bio) / (oracle_bio - donothing_bio). "
            "For bare-soil scenarios (planting is an oracle action) this "
            "will be 0 kg; for mid-season pre-planted scenarios it gives "
            "the weather-only yield floor."
        ),
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    targets = _resolve_scenarios(args.scenarios)
    if not targets:
        logger.error("No scenarios to build (got --scenarios=%s)", args.scenarios)
        return 2

    logger.info(
        "Building %d oracle baselines into %s (max_days=%d, force=%s)",
        len(targets), output_dir, args.max_days, args.force,
    )

    n_ok = 0
    n_skip = 0
    n_fail = 0
    for sid in targets:
        out_path = output_dir / f"{sid}.json"
        if out_path.exists() and not args.force:
            logger.info("[SKIP] %s — baseline exists at %s", sid, out_path)
            n_skip += 1
            continue
        try:
            logger.info("[BUILD] %s", sid)
            baseline = _build_one_baseline(
                sid,
                extrapolation_max_days=args.max_days,
                include_donothing=args.include_donothing,
            )
            payload = _baseline_to_dict(baseline)
            out_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            dn_info = ""
            if baseline.donothing is not None:
                dn_kg = baseline.donothing.get("biological_yield_kg_total", "?")
                dn_info = f", donothing_bio={dn_kg:.1f}kg" if isinstance(dn_kg, float) else f", donothing={dn_kg}"
            logger.info(
                "  -> bio_total=%.1f kg, ridges_planted=%d, R8=%d, "
                "harv=%d, extrap_days=%d%s (%.2fs)",
                baseline.biological_yield_kg_total,
                baseline.ridges_planted,
                baseline.ridges_r8,
                baseline.ridges_harvested,
                baseline.extrapolation.get("days_ticked", 0),
                dn_info,
                baseline.duration_s,
            )
            n_ok += 1
        except Exception as exc:
            logger.exception("[FAIL] %s: %s", sid, exc)
            n_fail += 1
            if not args.continue_on_error:
                return 1

    logger.info(
        "Done. ok=%d, skip=%d, fail=%d, out=%s", n_ok, n_skip, n_fail, output_dir
    )
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
