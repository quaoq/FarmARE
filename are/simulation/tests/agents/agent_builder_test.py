# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


from unittest.mock import Mock

import pytest

from are.simulation.agents.agent_builder import AgentBuilder, AppAgentBuilder
from are.simulation.agents.agent_config_builder import AgentConfigBuilder
from are.simulation.agents.are_simulation_agent import RunnableARESimulationAgent
from are.simulation.agents.default_agent.are_simulation_main import ARESimulationAgent
from are.simulation.agents.research_suite.research_agent import ResearchARESimulationAgent
from are.simulation.environment import Environment


@pytest.fixture
def agent_config_builder() -> AgentConfigBuilder:
    return AgentConfigBuilder()


@pytest.fixture
def agent_builder() -> AgentBuilder:
    return AgentBuilder()


@pytest.fixture
def app_agent_builder() -> AppAgentBuilder:
    return AppAgentBuilder()


@pytest.fixture
def env_mock():
    mock = Mock(spec=Environment)
    mock.time_manager = Mock()
    return mock


def test_list_agents(agent_builder):
    expected_agents = {
        "default",
        "farm_world",
        "farm_baseline_react",
        "farm_planner_executor",
        "farm_reflective_memory",
        "farm_skill_rag",
        "farm_multi_specialist",
        "farm_adaptive_verifier",
        "farm_rewoo_modular",
        "farm_tree_search",
        "farm_critic_refiner",
        "farm_graph_memory",
    }
    agents = set(agent_builder.list_agents())
    assert expected_agents.issubset(agents)


def test_build_default(
    agent_config_builder: AgentConfigBuilder,
    agent_builder: AgentBuilder,
    env_mock,
):
    agent_config = agent_config_builder.build("default")
    agent_config.get_base_agent_config().llm_engine_config.provider = "huggingface"
    agent = agent_builder.build(agent_config, env=env_mock)
    assert isinstance(agent, RunnableARESimulationAgent)
    assert isinstance(agent, ARESimulationAgent)


def test_build_invalid_agent(agent_config_builder: AgentConfigBuilder):
    with pytest.raises(ValueError, match="Agent invalid_agent not found"):
        agent_config_builder.build("invalid_agent")


def test_build_research_family(
    agent_config_builder: AgentConfigBuilder,
    agent_builder: AgentBuilder,
    env_mock,
):
    agent_config = agent_config_builder.build("farm_skill_rag")
    agent_config.get_base_agent_config().llm_engine_config.provider = "mock"
    agent = agent_builder.build(agent_config, env=env_mock)
    assert isinstance(agent, RunnableARESimulationAgent)
    assert isinstance(agent, ResearchARESimulationAgent)


def test_list_app_agents(app_agent_builder: AppAgentBuilder):
    expected_app_agents = {
        "default_app_agent",
        "weather_expert_app_agent",
        "sensor_expert_app_agent",
        "machinery_expert_app_agent",
        "operations_expert_app_agent",
    }
    app_agents = set(app_agent_builder.list_agents())
    assert expected_app_agents.issubset(app_agents)
