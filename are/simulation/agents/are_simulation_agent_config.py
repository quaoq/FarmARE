# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from are.simulation.agents.llm.models import ALL_MODELS
from are.simulation.types import SimulatedGenerationTimeConfig


class LLMEngineConfig(BaseModel):
    model_name: str = Field(
        default=ALL_MODELS[0] if ALL_MODELS else "",
        examples=ALL_MODELS,
    )
    provider: str | None = None
    endpoint: str | None = None


class ARESimulationBaseAgentConfig(BaseModel):
    llm_engine_config: LLMEngineConfig = Field(default_factory=LLMEngineConfig)
    simulated_generation_time_config: SimulatedGenerationTimeConfig | None = Field(
        default=None
    )
    use_custom_logger: bool = Field(default=True)


class ARESimulationReactBaseAgentConfig(ARESimulationBaseAgentConfig):
    system_prompt: str = Field(default="")
    max_iterations: int = Field(default=80)


class ResearchSkillConfig(BaseModel):
    enabled: bool = Field(default=False)
    library_path: str | None = Field(default=None)
    top_k: int = Field(default=3)
    min_score: float = Field(default=0.1)


class ResearchMemoryConfig(BaseModel):
    enabled: bool = Field(default=False)
    max_items: int = Field(default=8)
    injection_top_k: int = Field(default=3)


class ResearchDelegationConfig(BaseModel):
    enabled: bool = Field(default=False)
    specialists: list[str] = Field(default_factory=list)
    max_delegations_per_turn: int = Field(default=0)


class ResearchVerificationConfig(BaseModel):
    enabled: bool = Field(default=False)
    uncertainty_keywords: list[str] = Field(
        default_factory=lambda: ["maybe", "uncertain", "not sure", "risk", "verify"]
    )


class ResearchReWooConfig(BaseModel):
    enabled: bool = Field(default=False)
    max_plan_steps: int = Field(default=8)
    max_replans: int = Field(default=1)


class ResearchTreeSearchConfig(BaseModel):
    enabled: bool = Field(default=False)
    branch_factor: int = Field(default=3)
    search_depth: int = Field(default=2)
    max_expansions: int = Field(default=6)


class ResearchCriticConfig(BaseModel):
    enabled: bool = Field(default=False)
    max_revision_cycles: int = Field(default=2)
    enforce_precondition_checks: bool = Field(default=True)


class ResearchGraphMemoryConfig(BaseModel):
    enabled: bool = Field(default=False)
    max_nodes: int = Field(default=60)
    retrieval_top_k: int = Field(default=5)
    contradiction_check: bool = Field(default=True)


class ResearchTelemetryConfig(BaseModel):
    enabled: bool = Field(default=True)
    emit_to_result_metadata: bool = Field(default=True)


class ResearchAgentProfileConfig(BaseModel):
    family_id: str = Field(default="farm_baseline_react")
    planning_mode: str = Field(default="react")
    replan_on_tool_error: bool = Field(default=False)
    reflection: ResearchMemoryConfig = Field(default_factory=ResearchMemoryConfig)
    skills: ResearchSkillConfig = Field(default_factory=ResearchSkillConfig)
    delegation: ResearchDelegationConfig = Field(default_factory=ResearchDelegationConfig)
    verification: ResearchVerificationConfig = Field(
        default_factory=ResearchVerificationConfig
    )
    rewoo: ResearchReWooConfig = Field(default_factory=ResearchReWooConfig)
    tree_search: ResearchTreeSearchConfig = Field(
        default_factory=ResearchTreeSearchConfig
    )
    critic: ResearchCriticConfig = Field(default_factory=ResearchCriticConfig)
    graph_memory: ResearchGraphMemoryConfig = Field(
        default_factory=ResearchGraphMemoryConfig
    )
    telemetry: ResearchTelemetryConfig = Field(default_factory=ResearchTelemetryConfig)


class RunnableARESimulationAgentConfig(ABC):
    # Handle common config fields.
    @abstractmethod
    def get_agent_name(self) -> str | None:
        pass

    @abstractmethod
    def get_base_agent_config(self) -> ARESimulationBaseAgentConfig:
        pass

    # Handle model dump and schema.
    @abstractmethod
    def get_model_dump(self) -> dict[str, Any]:
        pass

    @abstractmethod
    def get_model_json_schema(self) -> dict[str, Any]:
        pass

    @abstractmethod
    def validate_model(
        self, agent_config_dict: dict[str, Any]
    ) -> "RunnableARESimulationAgentConfig":
        pass


class MainAgentConfig(BaseModel):
    agent_name: str = Field(default="")
    max_turns: int | None = Field(default=None)

    def get_agent_name(self) -> str | None:
        return self.agent_name

    def get_model_dump(self) -> dict[str, Any]:
        return self.model_dump()

    def get_model_json_schema(self) -> dict[str, Any]:
        return self.model_json_schema()


class ARESimulationReactAgentConfig(MainAgentConfig, RunnableARESimulationAgentConfig):
    # Exact subclass type declaration is required for pydantic deserialization.
    base_agent_config: ARESimulationReactBaseAgentConfig = Field(
        default_factory=ARESimulationReactBaseAgentConfig
    )

    def get_base_agent_config(self) -> ARESimulationBaseAgentConfig:
        return self.base_agent_config

    def validate_model(
        self, agent_config_dict: dict[str, Any]
    ) -> RunnableARESimulationAgentConfig:
        return type(self).model_validate(agent_config_dict)


class ARESimulationResearchAgentConfig(ARESimulationReactAgentConfig):
    research_profile: ResearchAgentProfileConfig = Field(
        default_factory=ResearchAgentProfileConfig
    )


class ARESimulationReactAppAgentConfig(
    MainAgentConfig, ARESimulationReactBaseAgentConfig
):
    max_iterations: int = Field(default=40)
