"""Scenario bundle contract consumed by the D-CORE runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from are.simulation.distributed.controllers import AgentController
from are.simulation.distributed.models import DistributedTaskSpec, KnowledgeItem

ToolHandler = Callable[[dict[str, Any], float], dict[str, Any]]
WorldHandler = Callable[[float], dict[str, Any]]
ContinuationHandler = Callable[[], dict[str, Any]]


@dataclass
class WorldEventDefinition:
    logical_time: float
    name: str
    handler: WorldHandler


@dataclass
class DistributedScenarioBundle:
    spec: DistributedTaskSpec
    controllers: dict[str, AgentController]
    tool_handlers: dict[str, ToolHandler]
    initial_knowledge: dict[str, list[KnowledgeItem]] = field(default_factory=dict)
    world_events: list[WorldEventDefinition] = field(default_factory=list)
    outcome: Callable[[], dict[str, Any]] = lambda: {}
    continuation: ContinuationHandler | None = None
    source_trace: str | None = None
    source_trace_builder: Callable[[], str] | None = None
