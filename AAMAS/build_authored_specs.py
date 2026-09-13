"""Regenerate author-defined specifications; does not approve paper execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from are.simulation.distributed.authored_specs import author_process
from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5
from are.simulation.distributed.teams import (
    build_builtin_team,
    built_in_role_refinement,
    refine_process_for_team,
)
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    FARM_SCENARIOS,
    create_native_scenario,
)

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "AAMAS/authored_specifications"
    output.mkdir(exist_ok=True)
    files = {}

    def save(name, value):
        data = (
            json.dumps(value.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        path = output / name
        path.write_text(data)
        files[name] = hashlib.sha256(data.encode()).hexdigest()

    processes = {s: author_process(s) for s in FARM_SCENARIOS}
    for scenario, process in processes.items():
        save(f"{scenario}.process.json", process)
        # Freeze a stricter freshness alternative before confirmation. This
        # changes the admissible evidence age, not observations or native TTLs.
        data = process.model_dump(mode="json")

        def tighten(value):
            if isinstance(value, dict):
                if value.get("max_age") == 86400:
                    value["max_age"] = 43200.0
                for nested in value.values():
                    tighten(nested)
            elif isinstance(value, list):
                for nested in value:
                    tighten(nested)

        tighten(data)
        choices = data["metadata"]["authored_choices"]
        choices["freshness_alternative"] = (
            "12-hour evidence age versus primary 24-hour age; native expiry remains binding"
        )
        data["review_digest"] = stable_digest(choices)
        data["occurrence_net"]["metadata"]["authored_choices_digest"] = stable_digest(
            choices
        )
        save(
            f"{scenario}.freshness12h.process.json",
            FarmProcessSpecV5.model_validate(data),
        )
    native = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    for team_id in ("wetjune_2agent", "wetjune_3agent", "wetjune_4agent"):
        team = build_builtin_team(team_id, native.get_tools())
        team = team.model_copy(
            update={
                "expert_review_status": "author_defined",
                "metadata": {
                    **team.metadata,
                    "authorship": "author_defined",
                    "professor_approved": False,
                },
            }
        )
        save(f"{team_id}.team.json", team)
        if team_id == "wetjune_2agent":
            continue
        refinement = built_in_role_refinement(team, "farm_wetjune_recheck")
        refinement = refinement.model_copy(
            update={
                "expert_review_status": "author_defined",
                "reviewer_rationale": "Author-defined ownership and legal route refinement; professor approval pending.",
                "module_weight_budgets": {
                    m.module_id: m.weight_budget
                    for m in processes["farm_wetjune_recheck"].occurrence_net.modules
                },
            }
        )
        save(f"{team_id}.refinement.json", refinement)
        refined = refine_process_for_team(
            processes["farm_wetjune_recheck"], team, refinement
        )
        save(f"farm_wetjune_recheck.{team_id}.process.json", refined)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "dcore_authored_specification_inventory_v1",
                "files": files,
                "authorship": "author_defined",
                "professor_approved": False,
                "scientific_gates_passed": False,
                "paper_execution_enabled": False,
                "protocol_sha256": hashlib.sha256(
                    (
                        ROOT / "are/simulation/distributed/EXPERIMENT_PROTOCOL.md"
                    ).read_bytes()
                ).hexdigest(),
                "inventory_digest": stable_digest(files),
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"Authored {len(files)} specification/team/refinement artifacts; approval and gates pending."
    )


if __name__ == "__main__":
    main()
