from __future__ import annotations

from collections import defaultdict

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from are.simulation.distributed.cli import main
from are.simulation.distributed.clock import ClockRelation, compare
from are.simulation.distributed.experiments import resolve_manifest
from are.simulation.distributed.knowledge import KnowledgeStore
from are.simulation.distributed.models import (
    ActorSpec,
    AgentIntent,
    AgentTeamSpec,
    CommunicationTopologySpec,
    DistributedRunnerConfig,
    IntentKind,
)
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.petri import validate_petri_net
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.distributed.scientific_v5 import engineering_process_from_v4
from are.simulation.distributed.teams import (
    FOUR_AGENT_TEAM_ID,
    PRIMARY_TEAM_ID,
    THREE_AGENT_TEAM_ID,
    build_builtin_team,
    built_in_role_refinement,
    load_role_refinement,
    recipients_for,
    refine_petri_for_team,
    refine_process_for_team,
    team_digest,
)
from are.simulation.distributed.trace import CausalTraceRecorder
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    compile_paper_petri_net,
    create_native_scenario,
)


def _teams():
    scenario = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    return {
        team_id: build_builtin_team(team_id, scenario.get_tools())
        for team_id in (
            PRIMARY_TEAM_ID,
            THREE_AGENT_TEAM_ID,
            FOUR_AGENT_TEAM_ID,
        )
    }


def test_builtin_teams_preserve_aggregate_capability_and_have_stable_digests():
    teams = _teams()
    action_sets = [
        {action for actor in team.actors for action in actor.permitted_actions}
        for team in teams.values()
    ]
    assert len({frozenset(actions) for actions in action_sets}) == 1
    assert [len(teams[key].actors) for key in teams] == [2, 3, 4]
    assert len({team_digest(team) for team in teams.values()}) == 3


def test_team_contract_rejects_duplicate_ownership_and_invalid_topology():
    actor = ActorSpec(actor_id="a", permitted_actions=("tool",))
    with pytest.raises(ValidationError, match="multiple owners"):
        AgentTeamSpec(
            team_id="bad",
            actors=(actor, ActorSpec(actor_id="b", permitted_actions=("tool",))),
            topology=CommunicationTopologySpec(
                topology_id="direct", kind="explicit", edges=(("a", "b"),)
            ),
            time_authority=("b",),
        )
    with pytest.raises(ValidationError, match="unknown actors"):
        AgentTeamSpec(
            team_id="bad-edge",
            actors=(actor, ActorSpec(actor_id="b")),
            topology=CommunicationTopologySpec(
                topology_id="direct", kind="explicit", edges=(("a", "missing"),)
            ),
            time_authority=("b",),
        )


def test_four_actor_clocks_keep_independent_events_incomparable():
    actors = ("a", "b", "c", "d")
    recorder = CausalTraceRecorder("run", "task", actors)
    left = recorder.record("observation", "a", 1.0)  # type: ignore[arg-type]
    right = recorder.record("observation", "d", 1.0)  # type: ignore[arg-type]
    assert set(left.vector_clock) == {*actors, "world"}
    assert compare(left.vector_clock, right.vector_clock) == ClockRelation.CONCURRENT


def test_broadcast_expands_to_stable_topology_checked_unicasts():
    team = _teams()[FOUR_AGENT_TEAM_ID]
    fully_connected = AgentTeamSpec.model_validate(
        {
            **team.model_dump(mode="python"),
            "topology": {
                "topology_id": "all",
                "kind": "fully_connected",
                "edges": [],
            },
        }
    )
    actor = fully_connected.actors[0].actor_id
    stores = {
        item.actor_id: KnowledgeStore(item.actor_id) for item in fully_connected.actors
    }
    envelopes = NativeDistributedSeasonRunner._build_envelopes(
        config=DistributedRunnerConfig(),
        team=fully_connected,
        actor_id=actor,
        intent=AgentIntent(kind=IntentKind.SEND, recipient="*", text="status"),
        stores=stores,
        message_versions=defaultdict(int),
        world_time=1.0,
    )
    assert tuple(item.recipient for item in envelopes) == recipients_for(
        fully_connected, actor
    )
    assert len({item.message_id.split(":to:", 1)[0] for item in envelopes}) == 1


@pytest.mark.parametrize("team_id", [THREE_AGENT_TEAM_ID, FOUR_AGENT_TEAM_ID])
def test_refined_wetjune_oracles_are_perfect_and_yield_equivalent(
    team_id, tmp_path
):
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            team_id=team_id,
            controller_mode="scripted",
            max_logical_steps=6000,
            output_dir=str(tmp_path / team_id),
        )
    )
    assert result.metrics["event_fidelity"] == 1.0
    assert result.metrics["causal_conformance"] == 1.0
    assert result.metrics["dcore_score"] == 1.0
    assert result.trace.outcome["marketable_yield_kg"] == 8872.28
    assert result.metrics["team_profile"]["team_size"] in {3, 4}
    assert result.metrics["team_profile"]["n_local_scored"] == len(
        result.trace.actors
    )
    assert result.metrics["team_profile"]["fact_handoff_depth_max"] >= 2


def test_role_refinement_preserves_module_budgets_and_validity():
    base = compile_paper_petri_net("farm_wetjune_recheck", world_seed=0)
    budgets = {item.module_id: item.weight_budget for item in base.modules}
    for team in _teams().values():
        refinement = built_in_role_refinement(team, "farm_wetjune_recheck")
        refined = refine_petri_for_team(base, team, refinement)
        assert {item.module_id: item.weight_budget for item in refined.modules} == budgets
        report = validate_petri_net(refined)
        assert report["terminal_reachable"] is True


@pytest.mark.parametrize("team_id", [THREE_AGENT_TEAM_ID, FOUR_AGENT_TEAM_ID])
def test_v5_process_refinement_is_deterministic_and_preserves_scientific_budget(
    team_id,
):
    base_net = compile_paper_petri_net("farm_wetjune_recheck", world_seed=0)
    base = engineering_process_from_v4(base_net)
    team = _teams()[team_id]
    refinement = built_in_role_refinement(team, "farm_wetjune_recheck")
    assert refinement is not None
    first = refine_process_for_team(base, team, refinement)
    second = refine_process_for_team(base, team, refinement)
    assert first == second
    assert first.digest == second.digest
    assert first.occurrence_net.actors == tuple(actor.actor_id for actor in team.actors)
    assert {
        item.module_id: item.weight_budget for item in first.occurrence_net.modules
    } == {item.module_id: item.weight_budget for item in base.occurrence_net.modules}
    assert all(
        item.transition_id in {row.transition_id for row in first.acceptance}
        for item in first.occurrence_net.transitions
        if item.required and item.actor_id != "world"
    )


def test_reviewed_role_refinement_is_loaded_by_digest_bound_path(tmp_path):
    team = _teams()[THREE_AGENT_TEAM_ID]
    draft = built_in_role_refinement(team, "farm_wetjune_recheck")
    assert draft is not None
    reviewed = draft.model_copy(
        update={
            "expert_review_status": "confirmed",
            "confirmation_digest": "a" * 64,
        }
    )
    path = tmp_path / "reviewed-role-refinement.json"
    path.write_text(reviewed.model_dump_json(indent=2), encoding="utf-8")
    config = DistributedRunnerConfig(
        scenario_id="farm_wetjune_recheck",
        team_id=THREE_AGENT_TEAM_ID,
        role_refinement_path=str(path),
    )
    assert load_role_refinement(config, team, config.scenario_id) == reviewed

    mismatch = config.model_copy(update={"scenario_id": "farm_disease_drought"})
    with pytest.raises(ValueError, match="scenario does not match"):
        load_role_refinement(mismatch, team, mismatch.scenario_id)


def test_scalability_matrix_is_bounded_and_grouped_by_team():
    manifest = {
        "schema_version": "farm_dcore_matrix_v1",
        "scenarios": ["farm_wetjune_recheck"],
        "team_ids": [PRIMARY_TEAM_ID, THREE_AGENT_TEAM_ID, FOUR_AGENT_TEAM_ID],
        "conditions": [
            {
                "id": "scale",
                "execution": "dcore",
                "scalability": True,
                "faults": ["none", "mixed"],
            }
        ],
    }
    rows = resolve_manifest(manifest)
    assert len(rows) == 6
    assert {row["team_id"] for row in rows} == {
        PRIMARY_TEAM_ID,
        THREE_AGENT_TEAM_ID,
        FOUR_AGENT_TEAM_ID,
    }
    manifest["conditions"][0]["faults"] = ["none", "drop"]
    with pytest.raises(ValueError, match="allow_full_team_cartesian"):
        resolve_manifest(manifest)


def test_explicit_controller_profiles_expand_without_hidden_cartesian_axes():
    rows = resolve_manifest(
        {
            "schema_version": "farm_dcore_matrix_v1",
            "scenarios": ["farm_wetjune_recheck"],
            "controller_profiles": [
                {
                    "id": "react",
                    "agent_family_by_actor": {
                        "field_intelligence": "farm_baseline_react",
                        "operations": "farm_baseline_react",
                    },
                },
                {
                    "id": "critic",
                    "agent_family_by_actor": {
                        "field_intelligence": "farm_critic_refiner",
                        "operations": "farm_critic_refiner",
                    },
                },
            ],
            "conditions": [
                {"id": "reliable", "execution": "dcore", "faults": ["none"]}
            ],
        }
    )
    assert len(rows) == 2
    assert {row["controller_profile_id"] for row in rows} == {"react", "critic"}
    assert len({row["model_configuration_id"] for row in rows}) == 2


def test_matrix_preflight_reports_and_rejects_unresolved_placeholders(tmp_path):
    manifest = tmp_path / "matrix.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": "farm_dcore_matrix_v1",
                "scenarios": ["farm_wetjune_recheck"],
                "controller_mode": "llm",
                "model_by_actor": {
                    "field_intelligence": "REPLACE_WITH_MODEL",
                    "operations": "REPLACE_WITH_MODEL",
                },
                "conditions": [
                    {"id": "reliable", "execution": "dcore", "faults": ["none"]}
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    dry = runner.invoke(
        main,
        [
            "matrix",
            str(manifest),
            "--output-dir",
            str(tmp_path / "dry"),
            "--dry-run",
        ],
    )
    assert dry.exit_code == 0
    assert '"execution_preflight_ready": false' in dry.output
    assert '"paper_experiment_ready": false' in dry.output
    execute = runner.invoke(
        main,
        ["matrix", str(manifest), "--output-dir", str(tmp_path / "run")],
    )
    assert execute.exit_code != 0
    assert "unresolved placeholders" in execute.output
