# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


from abc import ABC, abstractmethod
from typing import Any

from are.simulation.agents.are_simulation_agent_config import (
    ARESimulationResearchAgentConfig,
    ARESimulationReactAgentConfig,
    ARESimulationReactAppAgentConfig,
    ARESimulationReactBaseAgentConfig,
    ResearchAgentProfileConfig,
    ResearchCriticConfig,
    ResearchDelegationConfig,
    ResearchGraphMemoryConfig,
    ResearchMemoryConfig,
    ResearchReWooConfig,
    ResearchSkillConfig,
    ResearchTelemetryConfig,
    ResearchTreeSearchConfig,
    ResearchVerificationConfig,
    RunnableARESimulationAgentConfig,
)
from are.simulation.agents.default_agent.prompts import (
    DEFAULT_ARE_SIMULATION_APP_AGENT_REACT_JSON_SYSTEM_PROMPT,
    DEFAULT_ARE_SIMULATION_REACT_JSON_SYSTEM_PROMPT,
    FARM_WORLD_REACT_JSON_SYSTEM_PROMPT,
)


class AbstractAgentConfigBuilder(ABC):
    """
    Abstract class for building agent configs.
    """

    @abstractmethod
    def build(
        self,
        agent_name: str,
    ) -> Any:
        """
        Method to build a config.
        :param agent_name: Name of the agent that affects the config type.
        :returns: An instance of the config.
        """


class AgentConfigBuilder(AbstractAgentConfigBuilder):
    def build(
        self,
        agent_name: str,
    ) -> RunnableARESimulationAgentConfig:
        planning_addendum = (
            "\n\n<research_family_instructions>\n"
            "Before calling tools, create a compact plan with explicit milestones.\n"
            "After each tool call, check whether the plan still holds; if not, replan before continuing.\n"
            "End by reporting completion status and unresolved risks.\n"
            "</research_family_instructions>"
        )
        reflective_addendum = (
            "\n\n<research_family_instructions>\n"
            "Keep a short reflection loop: track mistakes, assumptions, and fixes across turns.\n"
            "Before deciding the next action, consult your memory notes and avoid repeated mistakes.\n"
            "</research_family_instructions>"
        )
        skill_addendum = (
            "\n\n<research_family_instructions>\n"
            "You may receive retrieved skill snippets before each task.\n"
            "Use them as reusable workflows; adapt them to the current farm state instead of copying blindly.\n"
            "</research_family_instructions>"
        )
        delegation_addendum = (
            "\n\n<research_family_instructions>\n"
            "Use specialist reasoning modes (weather, soil, equipment, operations) before committing to action.\n"
            "Synthesize specialist viewpoints into one final executable action plan.\n"
            "</research_family_instructions>"
        )
        verifier_addendum = (
            "\n\n<research_family_instructions>\n"
            "Use adaptive compute: short plan for straightforward cases, deeper verification for uncertain/high-risk actions.\n"
            "When uncertainty is high, verify preconditions before irreversible actions.\n"
            "</research_family_instructions>"
        )
        rewoo_addendum = (
            "\n\n<research_family_instructions>\n"
            "Follow a modular plan-work-solve pattern.\n"
            "First draft structured steps, then execute one step at a time with intermediate evidence, and finally synthesize results.\n"
            "</research_family_instructions>"
        )
        tree_search_addendum = (
            "\n\n<research_family_instructions>\n"
            "Generate multiple candidate plans, score them with a risk-aware rubric, execute the top one, and backtrack once if it fails.\n"
            "</research_family_instructions>"
        )
        critic_addendum = (
            "\n\n<research_family_instructions>\n"
            "Run an explicit actor-critic refinement cycle before high-impact actions.\n"
            "The critic must verify preconditions, safety constraints, and inventory/tool readiness.\n"
            "</research_family_instructions>"
        )
        graph_memory_addendum = (
            "\n\n<research_family_instructions>\n"
            "Maintain a compact graph memory of objectives, actions, outcomes, and dependencies.\n"
            "Retrieve relevant graph snippets before choosing the next action and check for contradictions.\n"
            "</research_family_instructions>"
        )

        match agent_name:
            case "default":
                return ARESimulationReactAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(
                            DEFAULT_ARE_SIMULATION_REACT_JSON_SYSTEM_PROMPT
                        ),
                        max_iterations=80,
                    ),
                )

            case "farm_world":
                return ARESimulationReactAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(
                            FARM_WORLD_REACT_JSON_SYSTEM_PROMPT
                        ),
                        max_iterations=80,
                    ),
                )
            case "farm_baseline_react":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT),
                        max_iterations=80,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="react",
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_planner_executor":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + planning_addendum,
                        max_iterations=100,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="planner_executor",
                        replan_on_tool_error=True,
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_reflective_memory":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + reflective_addendum,
                        max_iterations=100,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="reflective",
                        reflection=ResearchMemoryConfig(
                            enabled=True, max_items=12, injection_top_k=4
                        ),
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_skill_rag":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + skill_addendum,
                        max_iterations=100,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="skill_rag",
                        skills=ResearchSkillConfig(
                            enabled=True,
                            library_path="are/simulation/agents/research_suite/skills",
                            top_k=3,
                            min_score=0.1,
                        ),
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_multi_specialist":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + delegation_addendum,
                        max_iterations=110,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="multi_specialist",
                        delegation=ResearchDelegationConfig(
                            enabled=True,
                            specialists=[
                                "weather_specialist",
                                "soil_specialist",
                                "equipment_specialist",
                                "operations_specialist",
                            ],
                            max_delegations_per_turn=4,
                        ),
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_adaptive_verifier":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + verifier_addendum,
                        max_iterations=110,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="adaptive_verifier",
                        replan_on_tool_error=True,
                        verification=ResearchVerificationConfig(enabled=True),
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_rewoo_modular":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + rewoo_addendum,
                        max_iterations=110,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="rewoo_modular",
                        replan_on_tool_error=True,
                        rewoo=ResearchReWooConfig(
                            enabled=True,
                            max_plan_steps=8,
                            max_replans=1,
                        ),
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_tree_search":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + tree_search_addendum,
                        max_iterations=110,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="tree_search",
                        tree_search=ResearchTreeSearchConfig(
                            enabled=True,
                            branch_factor=3,
                            search_depth=2,
                            max_expansions=6,
                        ),
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_critic_refiner":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + critic_addendum,
                        max_iterations=110,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="critic_refiner",
                        critic=ResearchCriticConfig(
                            enabled=True,
                            max_revision_cycles=2,
                            enforce_precondition_checks=True,
                        ),
                        verification=ResearchVerificationConfig(enabled=True),
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )
            case "farm_graph_memory":
                return ARESimulationResearchAgentConfig(
                    agent_name=agent_name,
                    base_agent_config=ARESimulationReactBaseAgentConfig(
                        system_prompt=str(FARM_WORLD_REACT_JSON_SYSTEM_PROMPT)
                        + graph_memory_addendum,
                        max_iterations=110,
                    ),
                    research_profile=ResearchAgentProfileConfig(
                        family_id=agent_name,
                        planning_mode="graph_memory",
                        graph_memory=ResearchGraphMemoryConfig(
                            enabled=True,
                            max_nodes=60,
                            retrieval_top_k=5,
                            contradiction_check=True,
                        ),
                        telemetry=ResearchTelemetryConfig(enabled=True),
                    ),
                )

            case _:
                raise ValueError(f"Agent {agent_name} not found")


class AppAgentConfigBuilder(AbstractAgentConfigBuilder):
    def build(
        self,
        agent_name: str,
    ) -> ARESimulationReactAppAgentConfig:
        weather_addendum = (
            "\n\n<app_expert_instructions>\n"
            "You are the weather and field-conditions expert.\n"
            "Prioritize: current weather, short forecast, wind/rain risk, and go/no-go recommendations for field operations.\n"
            "If conditions are unsafe, explicitly block action and provide safe alternatives.\n"
            "</app_expert_instructions>"
        )
        sensor_addendum = (
            "\n\n<app_expert_instructions>\n"
            "You are the sensor interpretation expert.\n"
            "Prioritize: soil moisture/temperature, canopy indicators, anomalies, and confidence notes.\n"
            "Always report measured evidence before recommending actions.\n"
            "</app_expert_instructions>"
        )
        machinery_addendum = (
            "\n\n<app_expert_instructions>\n"
            "You are the machinery and implement operations expert.\n"
            "Prioritize: equipment status, precondition checks, attachment compatibility, fuel/consumable readiness, and safe execution order.\n"
            "Do not execute irreversible actions unless readiness checks pass.\n"
            "</app_expert_instructions>"
        )
        operations_addendum = (
            "\n\n<app_expert_instructions>\n"
            "You are the farm operations coordination expert.\n"
            "Prioritize: operational sequencing, ridge-level targeting, inventory consistency, and completion evidence.\n"
            "Return a concise completion summary with unresolved risks if any remain.\n"
            "</app_expert_instructions>"
        )

        match agent_name:
            case "default_app_agent":
                return ARESimulationReactAppAgentConfig(
                    agent_name=agent_name,
                    system_prompt=str(
                        DEFAULT_ARE_SIMULATION_APP_AGENT_REACT_JSON_SYSTEM_PROMPT
                    ),
                    max_iterations=80,
                )
            case "weather_expert_app_agent":
                return ARESimulationReactAppAgentConfig(
                    agent_name=agent_name,
                    system_prompt=str(
                        DEFAULT_ARE_SIMULATION_APP_AGENT_REACT_JSON_SYSTEM_PROMPT
                    )
                    + weather_addendum,
                    max_iterations=60,
                )
            case "sensor_expert_app_agent":
                return ARESimulationReactAppAgentConfig(
                    agent_name=agent_name,
                    system_prompt=str(
                        DEFAULT_ARE_SIMULATION_APP_AGENT_REACT_JSON_SYSTEM_PROMPT
                    )
                    + sensor_addendum,
                    max_iterations=60,
                )
            case "machinery_expert_app_agent":
                return ARESimulationReactAppAgentConfig(
                    agent_name=agent_name,
                    system_prompt=str(
                        DEFAULT_ARE_SIMULATION_APP_AGENT_REACT_JSON_SYSTEM_PROMPT
                    )
                    + machinery_addendum,
                    max_iterations=70,
                )
            case "operations_expert_app_agent":
                return ARESimulationReactAppAgentConfig(
                    agent_name=agent_name,
                    system_prompt=str(
                        DEFAULT_ARE_SIMULATION_APP_AGENT_REACT_JSON_SYSTEM_PROMPT
                    )
                    + operations_addendum,
                    max_iterations=70,
                )
            case _:
                raise ValueError(f"Sub agent {agent_name} not found")
