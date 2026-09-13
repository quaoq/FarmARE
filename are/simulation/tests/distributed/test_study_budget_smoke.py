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
