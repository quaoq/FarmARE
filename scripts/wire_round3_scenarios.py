"""
One-shot patcher: add FOS-evaluation wiring to the round-3 scenario scaffolds
under are/simulation/scenarios/scenario_farm_worldpp_physics/.

For each scenario:
  - inserts the FOS imports (GateSpec, append_fos_evaluation, predicates)
  - inserts `farm_world.attach_system_app(system)` and `_configure_physics_layers()`
    calls into init_and_populate_apps after _configure_initial_state
  - appends a `_configure_physics_layers` method (per-scenario) before `validate`
  - appends a `_gates` method (per-scenario) before `validate`
  - replaces the old `validate` body with a chain of append_workflow_evaluation
    + append_fos_evaluation

Per-scenario gate definitions live in the GATES_BY_SCENARIO dict below; they
encode the agronomic decision points that round-3 episodes test.

Usage: python scripts/wire_round3_scenarios.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


SCENARIO_DIR = Path(
    "/Users/panosmichelakis/Desktop/Research/farm_paper/FarmARE/are/simulation/scenarios/scenario_farm_worldpp_physics"
)


# =============================================================================
# Per-scenario gates and physics-layer initialisations.
# Each entry contains:
#   - profile_name:   short-id passed to configure_physics_profile
#   - scenario_type:  used in profile metadata
#   - physics_init:   Python source-string installed inside _configure_physics_layers
#   - gates:          Python source-string returned from _gates
# =============================================================================


GATES_BY_SCENARIO: dict[str, dict[str, str]] = {
    "scenario_physics_planting_window_reschedule": {
        "profile_name": "physics_planting_window",
        "scenario_type": "planting_window_reschedule",
        "physics_init": (
            '        # Cold-wet conditions: low temp + high VWC at scenario start.\n'
            '        # Engine state is seeded from RidgeState by the orchestrator.\n'
            '        physics = farm_world.physics\n'
            '        for i in range(64):\n'
            '            soil = physics.soil.states[i]\n'
            '            ridge = farm_world._ridges[i]\n'
            '            soil.top_vwc = float(ridge.soil_vwc)\n'
            '            soil.root_vwc = float(ridge.soil_vwc)\n'
            '            soil.top_temp_c = float(ridge.soil_temp_c)\n'
            '            soil.root_temp_c = float(ridge.soil_temp_c)\n'
        ),
        "gates": '''        return [
            GateSpec(
                name="G1_check_initial_weather",
                intent="confirm cold/wet conditions before deciding to wait",
                window_days=(0.0, 0.5),
                eligible_tools=[("WeatherApp", "get_current_weather")],
            ),
            GateSpec(
                name="G2_check_forecast_for_warming",
                intent="forecast must be consulted to find a planting window",
                window_days=(0.0, 0.5),
                eligible_tools=[("WeatherApp", "get_forecast")],
            ),
            GateSpec(
                name="G3_observe_seedbed",
                intent="read soil sensors to confirm cold/wet seedbed",
                window_days=(0.0, 1.0),
                eligible_tools=[("SensorApp", "read_soil_sensors")],
            ),
            GateSpec(
                name="G4_advance_through_unsuitable_period",
                intent="agent must wait (advance_time) for conditions to improve",
                window_days=(0.0, 3.0),
                eligible_tools=[("SystemApp", "advance_time")],
            ),
            GateSpec(
                name="G5_plant_when_ready",
                intent="plant_seeds called after warming; depth in 3-5cm range",
                window_days=(0.0, 3.0),
                eligible_tools=[("TractorApp", "plant_seeds")],
                requires=and_(
                    after_observation("SystemApp", "advance_time"),
                    min_arg("depth_cm", 3.0),
                ),
            ),
        ]
''',
    },
    "scenario_physics_emergence_replant_decision": {
        "profile_name": "physics_emergence_replant",
        "scenario_type": "emergence_replant_decision",
        "physics_init": (
            '        # Some ridges already planted with low stand_fraction\n'
            '        # representing failed emergence; agent must detect and replant.\n'
            '        physics = farm_world.physics\n'
            '        for i in range(64):\n'
            '            soil = physics.soil.states[i]\n'
            '            ridge = farm_world._ridges[i]\n'
            '            soil.top_vwc = float(ridge.soil_vwc)\n'
            '            soil.root_vwc = float(ridge.soil_vwc)\n'
        ),
        "gates": '''        return [
            GateSpec(
                name="G1_observe_emergence",
                intent="robot inspects emergence on suspect block",
                window_days=(0.0, 1.0),
                eligible_tools=[("Robot0", "inspect_emergence")],
            ),
            GateSpec(
                name="G2_check_weather_window",
                intent="forecast confirms planting window still open",
                window_days=(0.0, 1.5),
                eligible_tools=[("WeatherApp", "get_forecast")],
            ),
            GateSpec(
                name="G3_load_seeds",
                intent="load fresh seeds before replanting",
                window_days=(0.0, 2.0),
                eligible_tools=[("TractorApp", "load_seeds")],
            ),
            GateSpec(
                name="G4_replant_failed_block",
                intent="replant the failed ridges (do NOT replant healthy block)",
                window_days=(0.0, 3.0),
                eligible_tools=[("TractorApp", "replant_seeds")],
                requires=after_observation("Robot0", "inspect_emergence"),
            ),
        ]
''',
    },
    "scenario_physics_differential_diagnosis_fertigation": {
        "profile_name": "physics_diff_diag_fertigation",
        "scenario_type": "differential_diagnosis_fertigation",
        "physics_init": (
            '        # Nutrient stress patch: low nutrient_index, normal soil/biotic\n'
            '        physics = farm_world.physics\n'
            '        for i in range(64):\n'
            '            soil = physics.soil.states[i]\n'
            '            ridge = farm_world._ridges[i]\n'
            '            soil.top_vwc = float(ridge.soil_vwc)\n'
            '            soil.root_vwc = float(ridge.soil_vwc)\n'
        ),
        "gates": '''        return [
            GateSpec(
                name="G1_drone_ndvi",
                intent="drone NDVI flags low-canopy zone",
                window_days=(0.0, 0.5),
                eligible_tools=[("Mavic3M", "fly_survey")],
            ),
            GateSpec(
                name="G2_thermal_check",
                intent="thermal drone rules out water stress",
                window_days=(0.0, 1.0),
                eligible_tools=[("Matrice4T", "fly_survey")],
                requires=after_observation("Mavic3M", "fly_survey"),
            ),
            GateSpec(
                name="G3_robot_health_inspect",
                intent="ground robot rules out pest/disease and confirms canopy",
                window_days=(0.0, 1.5),
                eligible_tools=[("Robot0", "inspect_crop_health")],
            ),
            GateSpec(
                name="G4_fertigate_not_irrigate_or_spray",
                intent="agent applies fertigation (not pure irrigation, not spray)",
                window_days=(0.0, 2.0),
                eligible_tools=[("FarmWorldApp", "apply_fertigation")],
                requires=after_any_of([
                    ("Mavic3M", "fly_survey"),
                    ("Matrice4T", "fly_survey"),
                    ("Robot0", "inspect_crop_health"),
                ]),
            ),
            GateSpec(
                name="G5_followup_observation",
                intent="agent re-reads canopy after delayed fertigation response",
                window_days=(0.0, 4.0),
                eligible_tools=[("SensorApp", "read_canopy_sensors")],
                requires=after_observation("FarmWorldApp", "apply_fertigation"),
            ),
        ]
''',
    },
    "scenario_physics_disease_after_rain_fungicide": {
        "profile_name": "physics_disease_after_rain",
        "scenario_type": "disease_after_rain_fungicide",
        "physics_init": (
            '        # Post-rain disease pressure on a localized block.\n'
            '        physics = farm_world.physics\n'
            '        for i in range(64):\n'
            '            soil = physics.soil.states[i]\n'
            '            ridge = farm_world._ridges[i]\n'
            '            soil.top_vwc = float(ridge.soil_vwc)\n'
            '            soil.root_vwc = float(ridge.soil_vwc)\n'
            '            biotic = physics.biotic.states[i]\n'
            '            biotic.disease_pressure = max(\n'
            '                biotic.disease_pressure,\n'
            '                float(getattr(ridge, "disease_pressure_base", 0.0)),\n'
            '            )\n'
        ),
        "gates": '''        return [
            GateSpec(
                name="G1_post_rain_weather_check",
                intent="confirm spray window after rain has passed",
                window_days=(0.0, 0.5),
                eligible_tools=[("WeatherApp", "get_current_weather")],
            ),
            GateSpec(
                name="G2_observe_canopy",
                intent="canopy sensors detect low NDVI on disease block",
                window_days=(0.0, 1.0),
                eligible_tools=[("SensorApp", "read_canopy_sensors")],
            ),
            GateSpec(
                name="G3_robot_disease_confirm",
                intent="robot confirms disease via ground inspection",
                window_days=(0.0, 1.5),
                eligible_tools=[("Robot0", "inspect_crop_health")],
            ),
            GateSpec(
                name="G4_load_fungicide",
                intent="load fungicide (not insecticide) before spray",
                window_days=(0.0, 2.0),
                eligible_tools=[("TractorApp", "load_fungicide")],
            ),
            GateSpec(
                name="G5_apply_fungicide_in_window",
                intent="apply fungicide on diseased block in dry sprayable window",
                window_days=(0.0, 2.0),
                eligible_tools=[("TractorApp", "apply_fungicide")],
                requires=after_observation("TractorApp", "load_fungicide"),
            ),
        ]
''',
    },
    "scenario_physics_threshold_pest_monitoring": {
        "profile_name": "physics_threshold_pest",
        "scenario_type": "threshold_pest_monitoring",
        "physics_init": (
            '        # Pest pressure on hotspot block; ground-truth grows over time.\n'
            '        physics = farm_world.physics\n'
            '        for i in range(64):\n'
            '            soil = physics.soil.states[i]\n'
            '            ridge = farm_world._ridges[i]\n'
            '            soil.top_vwc = float(ridge.soil_vwc)\n'
            '            soil.root_vwc = float(ridge.soil_vwc)\n'
            '            biotic = physics.biotic.states[i]\n'
            '            biotic.insect_pressure = max(\n'
            '                biotic.insect_pressure,\n'
            '                float(getattr(ridge, "pest_pressure_base", 0.0)),\n'
            '            )\n'
        ),
        "gates": '''        return [
            GateSpec(
                name="G1_initial_pest_observation",
                intent="agent observes pest pressure (drone+sensor)",
                window_days=(0.0, 0.5),
                eligible_tools=[("Mavic3M", "fly_survey"), ("Matrice4T", "fly_survey")],
            ),
            GateSpec(
                name="G2_advance_time_for_trend",
                intent="agent waits ~1 day to observe trend before deciding",
                window_days=(0.0, 2.0),
                eligible_tools=[("SystemApp", "advance_time")],
            ),
            GateSpec(
                name="G3_re_observe_after_wait",
                intent="re-observe pest pressure after the trend wait",
                window_days=(0.5, 3.0),
                eligible_tools=[("Mavic3M", "fly_survey"), ("Matrice4T", "fly_survey")],
                requires=after_observation("SystemApp", "advance_time"),
            ),
            GateSpec(
                name="G4_robot_threshold_confirm",
                intent="robot ground-confirms pest before spray",
                window_days=(0.5, 3.0),
                eligible_tools=[("Robot0", "inspect_pests")],
            ),
            GateSpec(
                name="G5_targeted_spray",
                intent="spray only confirmed hotspot, not the whole field",
                window_days=(0.5, 3.0),
                eligible_tools=[("TractorApp", "spray_pesticide"), ("TractorApp", "apply_pesticide")],
                requires=after_observation("Robot0", "inspect_pests"),
            ),
        ]
''',
    },
    "scenario_physics_harvest_moisture_timing": {
        "profile_name": "physics_harvest_moisture",
        "scenario_type": "harvest_moisture_timing",
        "physics_init": (
            '        # R8 ridges with grain moisture above safe storage; needs drydown.\n'
            '        physics = farm_world.physics\n'
            '        for i in range(64):\n'
            '            soil = physics.soil.states[i]\n'
            '            ridge = farm_world._ridges[i]\n'
            '            soil.top_vwc = float(ridge.soil_vwc)\n'
            '            soil.root_vwc = float(ridge.soil_vwc)\n'
            '            yld = physics.yield_recovery.states[i]\n'
            '            yld.r8_reached = ridge.growth_stage == "R8"\n'
            '            yld.grain_moisture_frac = float(ridge.grain_moisture_pct) / 100.0\n'
            '            yld.biological_yield_g_m2 = 350.0\n'
        ),
        "gates": '''        return [
            GateSpec(
                name="G1_check_grain_status",
                intent="agent inspects ridge state for moisture readiness",
                window_days=(0.0, 0.5),
                eligible_tools=[("FarmWorldApp", "get_farm_overview")],
            ),
            GateSpec(
                name="G2_check_weather_for_dry_window",
                intent="forecast must show dry harvest window",
                window_days=(0.0, 0.5),
                eligible_tools=[("WeatherApp", "get_forecast")],
            ),
            GateSpec(
                name="G3_advance_time_for_drydown",
                intent="wait at least one day for grain dry-down",
                window_days=(0.0, 3.0),
                eligible_tools=[("SystemApp", "advance_time")],
            ),
            GateSpec(
                name="G4_harvest_in_window",
                intent="harvest after drydown when moisture in 13-18% window",
                window_days=(0.5, 4.0),
                eligible_tools=[("TractorApp", "harvest")],
                requires=after_observation("SystemApp", "advance_time"),
            ),
            GateSpec(
                name="G5_unload_grain",
                intent="agent unloads grain to warehouse after harvest",
                window_days=(0.5, 4.0),
                eligible_tools=[("TractorApp", "unload_grain")],
                requires=after_observation("TractorApp", "harvest"),
            ),
        ]
''',
    },
    "scenario_physics_postharvest_drying_storage": {
        "profile_name": "physics_postharvest",
        "scenario_type": "postharvest_drying_storage",
        "physics_init": (
            '        # Post-harvest state: ridges marked harvested with grain in bin.\n'
            '        physics = farm_world.physics\n'
            '        for i in range(64):\n'
            '            yld = physics.yield_recovery.states[i]\n'
            '            yld.harvested = True\n'
            '            yld.grain_moisture_frac = float(getattr(ridge_i := farm_world._ridges[i], "grain_moisture_pct", 17.0)) / 100.0\n'
            '            yld.biological_yield_g_m2 = 350.0\n'
            '            yld.recovered_yield_g_m2_at_market_moisture = 320.0\n'
        ),
        "gates": '''        return [
            GateSpec(
                name="G1_inspect_inventory",
                intent="agent checks current grain inventory state",
                window_days=(0.0, 0.5),
                eligible_tools=[("FarmWorldApp", "get_inventory")],
            ),
            GateSpec(
                name="G2_dry_grain",
                intent="dry grain to safe storage moisture (~13-14%)",
                window_days=(0.0, 1.0),
                eligible_tools=[("FarmWorldApp", "dry_grain")],
                requires=max_arg("target_moisture_pct", 14.0),
            ),
            GateSpec(
                name="G3_store_grain",
                intent="finalize storage step",
                window_days=(0.0, 2.0),
                eligible_tools=[("FarmWorldApp", "store_grain")],
                requires=after_observation("FarmWorldApp", "dry_grain"),
            ),
            GateSpec(
                name="G4_incorporate_residue",
                intent="incorporate residue rather than burn",
                window_days=(0.0, 3.0),
                eligible_tools=[("TractorApp", "incorporate_residue")],
            ),
        ]
''',
    },
}


HEADER_REPLACE_OLD = """from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer"""

HEADER_REPLACE_NEW = """from are.simulation.apps.system import SystemApp
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
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer"""


# After self._configure_initial_state() add the physics layer call.
INIT_INSERT_OLD_PATTERN = re.compile(
    r"^(\s+)self\._configure_initial_state\(\)\s*\n", re.MULTILINE
)


def _new_init_insert(match: re.Match) -> str:
    indent = match.group(1)
    return (
        f"{indent}self._configure_initial_state()\n"
        f"{indent}farm_world.attach_system_app(system)\n"
        f"{indent}self._configure_physics_layers()\n"
    )


VALIDATE_REPLACE_OLD = """    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(
            success=True,
            rationale="scaffold scenario: oracle/evaluation hooks to be implemented after tool integration",
        )
        return append_workflow_evaluation(self, env, result)"""


def _build_validate_block(profile_name: str, scenario_type: str, physics_init: str, gates: str) -> str:
    return f'''    def _configure_physics_layers(self) -> None:
        """Activate physics for this round-3 episode."""
        farm_world = self.get_typed_app(FarmWorldApp)
        farm_world.configure_physics_profile(
            profile_name="{profile_name}",
            location="Harbin/Heilongjiang",
            scenario_type="{scenario_type}",
        )
{physics_init}
    def _gates(self) -> list[GateSpec]:
        """FOS Decision-component gates for this episode."""
{gates}
    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="round-3 episode")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result'''


def patch(scenario_file: Path, scenario_id: str) -> bool:
    text = scenario_file.read_text()

    # 1. Replace import header.
    if HEADER_REPLACE_OLD not in text:
        print(f"  SKIP {scenario_id}: import header pattern not found (already patched?)")
        return False
    text = text.replace(HEADER_REPLACE_OLD, HEADER_REPLACE_NEW, 1)

    # 2. Insert _configure_physics_layers + attach_system_app calls in init_and_populate_apps.
    if "self._configure_physics_layers()" in text:
        print(f"  SKIP {scenario_id}: already calls _configure_physics_layers")
        return False
    text = INIT_INSERT_OLD_PATTERN.sub(_new_init_insert, text, count=1)

    # 3. Replace validate body with chained workflow + fos + new methods.
    if VALIDATE_REPLACE_OLD not in text:
        print(f"  SKIP {scenario_id}: validate pattern not found")
        return False
    spec = GATES_BY_SCENARIO[scenario_id]
    new_block = _build_validate_block(
        spec["profile_name"], spec["scenario_type"], spec["physics_init"], spec["gates"]
    )
    text = text.replace(VALIDATE_REPLACE_OLD, new_block, 1)

    scenario_file.write_text(text)
    print(f"  OK {scenario_id}")
    return True


def main() -> int:
    skipped = 0
    patched = 0
    for scenario_id, _spec in GATES_BY_SCENARIO.items():
        scenario_file = SCENARIO_DIR / f"{scenario_id}.py"
        if not scenario_file.exists():
            print(f"  MISSING {scenario_id}")
            skipped += 1
            continue
        if patch(scenario_file, scenario_id):
            patched += 1
        else:
            skipped += 1
    print(f"\nPatched {patched} / Skipped {skipped} of {len(GATES_BY_SCENARIO)}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
