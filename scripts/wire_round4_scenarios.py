"""
One-shot patcher for the 5 prioritised round-4 full-season scenarios.

For each scenario:
  - inserts FOS imports
  - attaches SystemApp -> FarmWorldApp before configure_physics_profile
    (so SystemApp.advance_time can fire the orchestrator)
  - rewrites the configure_physics_profile call to drop the legacy
    try/except (now always succeeds)
  - appends a `_gates(self)` method
  - updates `validate(self, env)` to chain append_fos_evaluation

Per-scenario gates are inlined below.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


SCENARIO_DIR = Path(
    "/Users/panosmichelakis/Desktop/Research/farm_paper/FarmARE/are/simulation/scenarios/scenario_farm_world_fullseason"
)


PRIORITISED_SCENARIOS = {
    "scenario_full_season_baseline_balanced_season.py": {
        "gates": '''        return [
            GateSpec(
                name="G1_plant_in_window",
                intent="plant within first 14 days of season",
                window_days=(0.0, 14.0),
                eligible_tools=[("TractorApp", "plant_seeds")],
                requires=after_observation("WeatherApp", "get_current_weather"),
            ),
            GateSpec(
                name="G2_emergence_check",
                intent="observe emergence between days 8-25",
                window_days=(8.0, 25.0),
                eligible_tools=[
                    ("Mavic3M", "fly_survey"),
                    ("Robot0", "inspect_emergence"),
                    ("SensorApp", "read_canopy_sensors"),
                ],
            ),
            GateSpec(
                name="G3_midseason_observation",
                intent="midseason check (V4-R1) for nutrient/water/pest",
                window_days=(35.0, 80.0),
                eligible_tools=[
                    ("Mavic3M", "fly_survey"),
                    ("Matrice4T", "fly_survey"),
                    ("SensorApp", "read_soil_sensors"),
                    ("SensorApp", "read_canopy_sensors"),
                ],
            ),
            GateSpec(
                name="G4_pod_fill_check",
                intent="pod-fill window (R5/R6) observation",
                window_days=(70.0, 110.0),
                eligible_tools=[
                    ("Matrice4T", "fly_survey"),
                    ("SensorApp", "read_soil_sensors"),
                    ("Robot0", "inspect_crop_health"),
                ],
            ),
            GateSpec(
                name="G5_harvest_at_maturity",
                intent="harvest within R8 + grain dry-down window",
                window_days=(110.0, 160.0),
                eligible_tools=[("TractorApp", "harvest")],
            ),
        ]
''',
    },
    "scenario_full_season_dry_pod_fill_yield_protection.py": {
        "gates": '''        return [
            GateSpec(
                name="G1_plant_in_window",
                intent="plant in first 14 days",
                window_days=(0.0, 14.0),
                eligible_tools=[("TractorApp", "plant_seeds")],
            ),
            GateSpec(
                name="G2_drought_detection_pod_fill",
                intent="detect dry pod-fill conditions via sensors+thermal",
                window_days=(70.0, 100.0),
                eligible_tools=[
                    ("SensorApp", "read_soil_sensors"),
                    ("Matrice4T", "fly_survey"),
                ],
            ),
            GateSpec(
                name="G3_irrigation_in_pod_fill",
                intent="irrigate the pod-fill block when stressed",
                window_days=(70.0, 110.0),
                eligible_tools=[("FieldOpsApp", "irrigate"), ("FieldOpsApp", "irrigate_range")],
                requires=after_observation("SensorApp", "read_soil_sensors"),
            ),
            GateSpec(
                name="G4_post_irrigation_verify",
                intent="re-read sensors after irrigation",
                window_days=(70.0, 115.0),
                eligible_tools=[("SensorApp", "read_soil_sensors")],
                requires=after_any_of([
                    ("FieldOpsApp", "irrigate"),
                    ("FieldOpsApp", "irrigate_range"),
                ]),
            ),
            GateSpec(
                name="G5_harvest_in_window",
                intent="harvest after grain matures",
                window_days=(110.0, 160.0),
                eligible_tools=[("TractorApp", "harvest")],
            ),
        ]
''',
    },
    "scenario_full_season_aphid_threshold_trend.py": {
        "gates": '''        return [
            GateSpec(
                name="G1_plant_in_window",
                intent="plant within first 14 days",
                window_days=(0.0, 14.0),
                eligible_tools=[("TractorApp", "plant_seeds")],
            ),
            GateSpec(
                name="G2_initial_pest_observation",
                intent="midseason pest observation",
                window_days=(50.0, 80.0),
                eligible_tools=[
                    ("Mavic3M", "fly_survey"),
                    ("SensorApp", "read_canopy_sensors"),
                ],
            ),
            GateSpec(
                name="G3_threshold_trend_wait",
                intent="agent waits to confirm pest trend",
                window_days=(50.0, 90.0),
                eligible_tools=[("SystemApp", "advance_time")],
            ),
            GateSpec(
                name="G4_robot_ground_confirm",
                intent="robot ground confirmation before spray",
                window_days=(55.0, 95.0),
                eligible_tools=[("Robot0", "inspect_pests")],
            ),
            GateSpec(
                name="G5_targeted_spray",
                intent="spray pesticide once threshold confirmed",
                window_days=(55.0, 100.0),
                eligible_tools=[
                    ("TractorApp", "spray_pesticide"),
                    ("TractorApp", "apply_pesticide"),
                ],
                requires=after_observation("Robot0", "inspect_pests"),
            ),
        ]
''',
    },
    "scenario_full_season_mixed_stress_wrong_action_trap.py": {
        "gates": '''        return [
            GateSpec(
                name="G1_plant_in_window",
                intent="plant within first 14 days",
                window_days=(0.0, 14.0),
                eligible_tools=[("TractorApp", "plant_seeds")],
            ),
            GateSpec(
                name="G2_drought_irrigation_response",
                intent="irrigate dry stretch (early-July → mid-July)",
                window_days=(50.0, 95.0),
                eligible_tools=[
                    ("FieldOpsApp", "irrigate"),
                    ("FieldOpsApp", "irrigate_range"),
                ],
            ),
            GateSpec(
                name="G3_post_rain_diagnosis",
                intent="diagnose post-rain situation (sensors + drone)",
                window_days=(85.0, 125.0),
                eligible_tools=[
                    ("Mavic3M", "fly_survey"),
                    ("Matrice4T", "fly_survey"),
                    ("SensorApp", "read_canopy_sensors"),
                ],
            ),
            GateSpec(
                name="G4_disease_response",
                intent="apply fungicide once disease confirmed (not pesticide)",
                window_days=(85.0, 130.0),
                eligible_tools=[("TractorApp", "apply_fungicide")],
            ),
            GateSpec(
                name="G5_harvest_in_window",
                intent="harvest after maturity",
                window_days=(110.0, 160.0),
                eligible_tools=[("TractorApp", "harvest")],
            ),
        ]
''',
    },
    "scenario_full_season_full_adversarial_weather_season.py": {
        "gates": '''        return [
            GateSpec(
                name="G1_late_plant_after_cold",
                intent="plant only after cold spell ends (warmth check)",
                window_days=(0.0, 25.0),
                eligible_tools=[("TractorApp", "plant_seeds")],
                requires=after_observation("WeatherApp", "get_forecast"),
            ),
            GateSpec(
                name="G2_post_rain_disease_response",
                intent="apply fungicide after wet stretch",
                window_days=(35.0, 80.0),
                eligible_tools=[("TractorApp", "apply_fungicide")],
            ),
            GateSpec(
                name="G3_pod_fill_irrigation",
                intent="irrigate during dry pod-fill",
                window_days=(70.0, 115.0),
                eligible_tools=[
                    ("FieldOpsApp", "irrigate"),
                    ("FieldOpsApp", "irrigate_range"),
                ],
            ),
            GateSpec(
                name="G4_pre_harvest_drydown",
                intent="advance_time for grain drydown",
                window_days=(110.0, 150.0),
                eligible_tools=[("SystemApp", "advance_time")],
            ),
            GateSpec(
                name="G5_harvest_before_late_rain",
                intent="harvest before the late-season rain front",
                window_days=(110.0, 145.0),
                eligible_tools=[("TractorApp", "harvest")],
            ),
        ]
''',
    },
}


HEADER_OLD = """from are.simulation.apps.system import SystemApp"""

HEADER_NEW = """from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.fos import GateSpec, append_fos_evaluation
from are.simulation.scenarios.fos.predicates import (
    after_any_of,
    after_observation,
    and_,
    arg_equals,
    max_arg,
    min_arg,
    or_,
    targets_ridges_overlap,
)"""


# Insert attach_system_app right after the existing init_and_populate_apps assigns self.apps.
APPS_ASSIGN_PATTERN = re.compile(
    r"^(\s+)self\.apps\s*=\s*\[\s*\n((?:\s+\w+,?\s*\n)+)\s+\]\s*\n",
    re.MULTILINE,
)


def _insert_attach_after_apps(text: str) -> str:
    """Add farm_world.attach_system_app(system) after self.apps = [...] block."""
    if "farm_world.attach_system_app(system)" in text:
        return text
    # Insert right after `self._configure_initial_state()` call.
    pat = re.compile(r"^(\s+)self\._configure_initial_state\(\)\s*\n", re.MULTILINE)
    return pat.sub(
        lambda m: f"{m.group(1)}self._configure_initial_state()\n{m.group(1)}farm_world.attach_system_app(system)\n",
        text,
        count=1,
    )


VALIDATE_OLD = """    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(
            success=True,
            rationale="full-season scaffold: implement physics-aware queue/oracle validation after tool integration",
        )
        return append_workflow_evaluation(self, env, result)"""


def _validate_new(gates_block: str) -> str:
    return f'''    def _gates(self) -> list[GateSpec]:
        """FOS Decision-component gates for this full-season scenario."""
{gates_block}
    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="round-4 full season")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result'''


def patch(scenario_file: Path, gates_block: str) -> bool:
    text = scenario_file.read_text()
    if "append_fos_evaluation" in text:
        print(f"  SKIP {scenario_file.name}: already patched")
        return False

    # 1. Replace import header.
    if HEADER_OLD not in text:
        print(f"  SKIP {scenario_file.name}: SystemApp import not found")
        return False
    text = text.replace(HEADER_OLD, HEADER_NEW, 1)

    # 2. Insert attach_system_app after _configure_initial_state.
    text = _insert_attach_after_apps(text)

    # 3. Replace validate.
    if VALIDATE_OLD not in text:
        print(f"  SKIP {scenario_file.name}: validate pattern not found")
        return False
    text = text.replace(VALIDATE_OLD, _validate_new(gates_block), 1)

    scenario_file.write_text(text)
    print(f"  OK {scenario_file.name}")
    return True


def main() -> int:
    skipped = 0
    patched = 0
    for filename, spec in PRIORITISED_SCENARIOS.items():
        f = SCENARIO_DIR / filename
        if not f.exists():
            print(f"  MISSING {filename}")
            skipped += 1
            continue
        if patch(f, spec["gates"]):
            patched += 1
        else:
            skipped += 1
    print(f"\nPatched {patched} / Skipped {skipped} of {len(PRIORITISED_SCENARIOS)}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
