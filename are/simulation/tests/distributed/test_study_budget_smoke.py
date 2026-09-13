"""Manifest-backed smoke must admit the existing study limits, without lifting legacy caps."""

import pytest

from are.simulation.distributed.models import DistributedRunnerConfig


def configuration(**overrides):
    return DistributedRunnerConfig(
        **{
            "scenario_id": "farm_wetjune_recheck",
            "scientific_contract": "v5",
            "controller_mode": "llm",
            "engineering_llm_pilot": True,
            "pilot_manifest_path": "explicit-study-smoke.yaml",
            "pilot_budget_ledger": "spending.sqlite",
            "max_model_calls": 700,
            "max_output_tokens": 4096,
            "team_token_budget": 24000000,
            **overrides,
        }
    )


def test_explicit_smoke_admits_existing_study_caps_without_paper_eligibility():
    config = configuration()
    assert config.max_model_calls == 700 and config.max_output_tokens == 4096
    assert not config.paper_mode and not config.bounded_llm_smoke


@pytest.mark.parametrize("size", [2, 3, 4])
def test_study_caps_survive_actual_team_resolution(size):
    from are.simulation.distributed.teams import load_team_spec

    config = configuration(
        team_id=f"wetjune_{size}agent",
        team_call_budget=700,
        per_agent_call_budget=700 // size,
        per_agent_token_budget=24000000 // size,
    )
    team = load_team_spec(config, ())
    assert team.team_token_budget == 24000000
    assert sum(team.per_agent_token_budget.values()) == 24000000


def test_resolved_actor_allocations_cannot_exceed_validated_team_cap():
    from are.simulation.distributed.teams import load_team_spec

    config = configuration(per_agent_token_budget=12000001)
    with pytest.raises(ValueError, match="token budgets exceed"):
        load_team_spec(config, ())


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_model_calls": 701},
        {"max_output_tokens": 4097},
        {"team_token_budget": 24000001},
        {"pilot_manifest_path": None},
        {"pilot_budget_ledger": None},
        {"paper_mode": True},
        {"bounded_llm_smoke": True},
    ],
)
def test_study_smoke_still_rejects_unbounded_or_false_release_requests(overrides):
    with pytest.raises(ValueError):
        configuration(**overrides)
