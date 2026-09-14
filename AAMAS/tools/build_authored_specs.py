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

ROOT = Path(__file__).resolve().parents[2]

# The primary estimand keeps 700 calls and 24M tokens per team.  Allocations are
# frozen by role workload: the clock/action owner receives the largest share,
# while observation and routing roles retain enough capacity for season-long
# evidence collection.  The equal-per-agent-compute sensitivity remains a
# separate condition in the scalability manifest.
TEAM_BUDGETS = {
    "wetjune_2agent": {
        "calls": {"field_intelligence": 200, "operations": 500},
        "tokens": {"field_intelligence": 6_000_000, "operations": 18_000_000},
    },
    "wetjune_3agent": {
        "calls": {"scouting": 160, "agronomy": 140, "operations": 400},
        "tokens": {
            "scouting": 5_500_000,
            "agronomy": 4_500_000,
            "operations": 14_000_000,
        },
    },
    "wetjune_4agent": {
        "calls": {
            "scouting": 140,
            "agronomy": 110,
            "resource_management": 130,
            "operations": 320,
        },
        "tokens": {
            "scouting": 4_500_000,
            "agronomy": 3_500_000,
            "resource_management": 4_500_000,
            "operations": 11_500_000,
        },
    },
}


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

    processes = {
        s: author_process(
            s,
            **(
                {
                    "scenario_revision": "drought_water_balance_v5",
                    "calibration_candidate": True,
                    "reference_harvest_calendar": True,
                    "reference_harvest_opening": True,
                }
                if s == "farm_disease_drought"
                else {}
            ),
        )
        for s in FARM_SCENARIOS
    }
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
        budgets = TEAM_BUDGETS[team_id]
        team = team.model_copy(
            update={
                "expert_review_status": "author_defined",
                "team_call_budget": 700,
                "per_agent_call_budget": budgets["calls"],
                "team_token_budget": 24_000_000,
                "per_agent_token_budget": budgets["tokens"],
                "metadata": {
                    **team.metadata,
                    "authorship": "author_defined",
                    "professor_approved": False,
                    "budget_allocation_basis": (
                        "author-prespecified role workload allocation; total-team "
                        "compute fixed; equal-per-agent compute is a separate sensitivity"
                    ),
                },
            }
        )
        if team_id == "wetjune_2agent":
            save(f"{team_id}.team.json", team)
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
        team = team.model_copy(
            update={
                "role_refinement_digest": stable_digest(
                    refinement.model_dump(mode="json")
                )
            }
        )
        save(f"{team_id}.team.json", team)
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
