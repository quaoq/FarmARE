"""Reproduce original Disease–Drought reviewer contrasts on development world 0.

This isolated script uses the original native scenario, not the selected drought
candidate. It does not authorize paper execution or alter confirmation results.
"""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.distributed.calibration import CalibrationClock
from are.simulation.distributed.experiments import execution_source_digest
from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.native_interventions import instrument_intervention
from are.simulation.distributed.native_season import _farm_outcome
from are.simulation.scenario_runner import ScenarioRunner
from are.simulation.scenarios.config import ScenarioRunnerConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


@dataclass(frozen=True)
class OriginalDroughtPlan:
    scenario_id: str = "farm_disease_drought"
    delay_days: int = 3  # Unused: no delay treatment is assigned.
    recovery_wait_days: int = 0


def main(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    source_digest = execution_source_digest()
    plan = {
        "scenario": "farm_disease_drought",
        "scenario_revision": "original native default; no candidate weather or soil",
        "world_seed": 0,
        "arms": ["reference", "omit_mid_fungicide", "omit_irrigation"],
        "intervention": "Replace only the selected native treatment calls with "
                        "recorded no-ops, preserving queued successors and all "
                        "other reference actions. Fungicide is three native batches.",
        "harvest_recovery_added": False,
        "clock": "event_queue_only",
        "paper_eligible": False,
        "execution_source_digest": source_digest,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "reviewer_settings": "Exact settings unavailable; discrepancies retained.",
    }
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    outcomes = {}
    for arm in plan["arms"]:
        scenario = create_native_scenario(plan["scenario"], world_seed=0)
        farm = scenario.get_typed_app(FarmWorldApp)
        initial = dict(farm.get_state().get("inventory", {}))
        world_digest = stable_digest(farm.physics.dcore_exogenous_manifest)
        records = instrument_intervention(scenario, farm, OriginalDroughtPlan(), arm)
        run_dir = output / arm
        run_dir.mkdir()
        validation = ScenarioRunner(time_manager_factory=CalibrationClock).run(
            ScenarioRunnerConfig(oracle=True, export=True, output_dir=str(run_dir)),
            scenario,
        )
        result = {
            **_farm_outcome(farm, initial),
            "exogenous_world_digest": world_digest,
            "validation_success": validation.success,
            "exception": str(validation.exception) if validation.exception else None,
            "intervention_records": records,
        }
        outcomes[arm] = result
        (run_dir / "outcome.json").write_text(json.dumps(result, indent=2) + "\n")
    assert len({r["exogenous_world_digest"] for r in outcomes.values()}) == 1
    assert execution_source_digest() == source_digest
    (output / "report.json").write_text(json.dumps({
        "plan": plan,
        "source_unchanged": True,
        "exogenous_worlds_equal": True,
        "outcomes": outcomes,
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    main(parser.parse_args().output_dir)
