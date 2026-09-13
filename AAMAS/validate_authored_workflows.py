"""Native reference validation, excluded from paper evidence; no model requests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from are.simulation.distributed.experiments import execution_source_digest
from are.simulation.distributed.models import DistributedRunnerConfig
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    source = execution_source_digest()
    for scenario in (
        "farm_wetjune_recheck",
        "farm_disease_drought",
        "farm_three_cultivar",
    ):
        raw = (
            ROOT / f"AAMAS/authored_specifications/{scenario}.process.json"
        ).read_bytes()
        spec_path = args.output_dir / f"{scenario}.process.json"
        spec_path.write_bytes(raw)
        process = FarmProcessSpecV5.model_validate_json(raw)
        result = DistributedScenarioRunner().run(
            DistributedRunnerConfig(
                scenario_id=scenario,
                scientific_contract="v5",
                enforcement_mode="audit",
                petri_spec_path=str(spec_path),
                world_seed=0,
            )
        )
        (args.output_dir / f"{scenario}.trace.json").write_text(
            result.trace.model_dump_json(indent=2)
        )
        (args.output_dir / f"{scenario}.metrics.json").write_text(
            json.dumps(result.metrics, indent=2)
        )
        outcome = result.trace.outcome
        row = dict(
            scenario=scenario,
            world_seed=0,
            specification_digest=process.digest,
            specification_sha256=hashlib.sha256(raw).hexdigest(),
            harvest_complete=outcome.get("harvest_complete"),
            storage_complete=outcome.get("storage_complete"),
            event_fidelity=result.metrics["event_fidelity"],
            causal_conformance=result.metrics["causal_conformance"],
            paper_eligible=False,
        )
        rows.append(row)
        report = dict(
            source_digest_at_start=source,
            source_unchanged=source == execution_source_digest(),
            complete=len(rows) == 3,
            rows=rows,
            provider_calls=0,
            professor_approved=False,
            interpretation="Native work completion and separate specification-relative diagnostics; not paper evidence.",
        )
        (args.output_dir / "summary.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
