# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


import pytest

from are.simulation.agents.agent_config_builder import (
    AgentConfigBuilder,
    AppAgentConfigBuilder,
)
from are.simulation.agents.are_simulation_agent_config import (
    ARESimulationResearchAgentConfig,
    ARESimulationReactAgentConfig,
    ARESimulationReactAppAgentConfig,
    RunnableARESimulationAgentConfig,
)


@pytest.fixture
def agent_config_builder() -> AgentConfigBuilder:
    return AgentConfigBuilder()


@pytest.fixture
def app_agent_config_builder() -> AppAgentConfigBuilder:
    return AppAgentConfigBuilder()


def test_build_default(
    agent_config_builder: AgentConfigBuilder,
):
    agent_config = agent_config_builder.build("default")
    assert isinstance(agent_config, RunnableARESimulationAgentConfig)
    assert isinstance(agent_config, ARESimulationReactAgentConfig)


def test_build_invalid_agent(agent_config_builder: AgentConfigBuilder):
    with pytest.raises(ValueError, match="Agent invalid_agent not found"):
        agent_config_builder.build("invalid_agent")


def test_build_research_family(agent_config_builder: AgentConfigBuilder):
    config = agent_config_builder.build("farm_adaptive_verifier")
    assert isinstance(config, RunnableARESimulationAgentConfig)
    assert isinstance(config, ARESimulationResearchAgentConfig)
    assert config.research_profile.family_id == "farm_adaptive_verifier"


@pytest.mark.parametrize(
    ("family_id", "planning_mode"),
    [
        ("farm_rewoo_modular", "rewoo_modular"),
        ("farm_tree_search", "tree_search"),
        ("farm_critic_refiner", "critic_refiner"),
        ("farm_graph_memory", "graph_memory"),
    ],
)
def test_build_new_research_families(
    agent_config_builder: AgentConfigBuilder,
    family_id: str,
    planning_mode: str,
):
    config = agent_config_builder.build(family_id)
    assert isinstance(config, ARESimulationResearchAgentConfig)
    assert config.research_profile.family_id == family_id
    assert config.research_profile.planning_mode == planning_mode


def test_build_new_research_family_flags(agent_config_builder: AgentConfigBuilder):
    rewoo = agent_config_builder.build("farm_rewoo_modular")
    tree = agent_config_builder.build("farm_tree_search")
    critic = agent_config_builder.build("farm_critic_refiner")
    graph = agent_config_builder.build("farm_graph_memory")

    assert rewoo.research_profile.rewoo.enabled is True
    assert tree.research_profile.tree_search.enabled is True
    assert critic.research_profile.critic.enabled is True
    assert graph.research_profile.graph_memory.enabled is True


@pytest.mark.parametrize(
    "agent_name",
    [
        "default_app_agent",
        "weather_expert_app_agent",
        "sensor_expert_app_agent",
        "machinery_expert_app_agent",
        "operations_expert_app_agent",
    ],
)
def test_build_app_agent_profiles(
    app_agent_config_builder: AppAgentConfigBuilder, agent_name: str
):
    config = app_agent_config_builder.build(agent_name)
    assert isinstance(config, ARESimulationReactAppAgentConfig)
    assert config.agent_name == agent_name
