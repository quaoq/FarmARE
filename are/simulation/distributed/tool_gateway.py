"""Role-isolated execution of native FarmARE application tools."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from are.simulation.agents.default_agent.tools.argument_normalizer import (
    normalize_tool_arguments,
)
from are.simulation.distributed.models import AgentTeamSpec, stable_digest
from are.simulation.distributed.teams import owner_for_team_tool
from are.simulation.environment import Environment
from are.simulation.tool_utils import AppTool, AppToolAdapter
from are.simulation.types import CompletedEvent


def owner_for_tool(tool: AppTool) -> str:
    """Return the sole role allowed to see and invoke ``tool``."""
    return owner_for_team_tool(tool, "wetjune_2agent")


def is_observation_tool(tool: AppTool) -> bool:
    return bool(
        tool.write_operation is False
        or tool.class_name in {"DroneApp", "RobotApp"}
        and tool.func_name
        and (tool.func_name == "fly_survey" or tool.func_name.startswith("inspect_"))
    )


def is_high_impact_tool(tool: AppTool) -> bool:
    if not tool.write_operation:
        return False
    return tool.func_name not in {
        "charge",
        "attach_implement",
        "detach_implement",
        "load_seeds",
        "load_fertilizer",
        "load_fungicide",
        "load_pesticide",
        "refill_pesticide_tank",
        "refuel",
        "unload_grain",
        "dry_grain",
        "store_grain",
    }


@dataclass(frozen=True)
class ToolExecution:
    actor_id: str
    action: str
    arguments: dict[str, Any]
    result: Any
    completed_event: CompletedEvent | None
    error: str | None = None
    duplicate: bool = False

    def receipt(self, intent_id: str) -> dict[str, Any]:
        """An audit receipt binds a request to its native event and result.

        This is a content digest, not a cryptographic signature or proof of
        agronomic success. Missing native events are explicitly unverified.
        """
        payload = {
            "schema_version": "farm_tool_receipt_v1",
            "intent_id": intent_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "arguments": deepcopy(self.arguments),
            "farmare_event_id": (
                self.completed_event.event_id if self.completed_event else None
            ),
            "status": "error"
            if self.error
            else ("accepted" if self.completed_event else "unverified"),
            "error": self.error,
            "result_digest": stable_digest(self.result),
        }
        return {
            **payload,
            "receipt_digest": stable_digest(payload),
            "duplicate": self.duplicate,
        }


class RoleToolGateway:
    """The only path from a distributed controller to mutable FarmARE state."""

    def __init__(
        self,
        environment: Environment,
        tools: list[AppTool],
        team: AgentTeamSpec | None = None,
    ):
        self.environment = environment
        self._tools = {
            tool.name: (tool, AppToolAdapter(tool))
            for tool in tools
            if not tool.name.startswith("AgentUserInterface__")
        }
        self._owners = (
            {
                action: actor.actor_id
                for actor in team.actors
                for action in actor.permitted_actions
            }
            if team is not None
            else {name: owner_for_tool(tool) for name, (tool, _) in self._tools.items()}
        )
        missing = set(self._tools) - set(self._owners)
        unknown = set(self._owners) - set(self._tools)
        if missing:
            raise ValueError(f"team leaves FarmARE tools unowned: {sorted(missing)}")
        if unknown:
            raise ValueError(f"team grants unknown FarmARE tools: {sorted(unknown)}")
        self._executed_intents: dict[str, ToolExecution] = {}

    def permitted_actions(self, actor_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name, (tool, _) in self._tools.items()
                if self._owners[name] == actor_id
            )
        )

    def permitted_tool_schemas(self, actor_id: str) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "description": adapter.description,
                "arguments": adapter.inputs,
                "observation": is_observation_tool(tool),
                "write": bool(tool.write_operation),
                "high_impact": is_high_impact_tool(tool),
            }
            for name, (tool, adapter) in sorted(self._tools.items())
            if self._owners[name] == actor_id
        }

    def tool(self, action: str) -> AppTool:
        if action not in self._tools:
            raise ValueError(f"unknown FarmARE action {action!r}")
        return self._tools[action][0]

    def metadata(self, action: str) -> dict[str, Any]:
        tool = self.tool(action)
        return {
            "owner": self._owners[action],
            "observation": is_observation_tool(tool),
            "high_impact": is_high_impact_tool(tool),
            "write": bool(tool.write_operation),
        }

    def execute(
        self,
        *,
        actor_id: str,
        intent_id: str,
        action: str,
        arguments: dict[str, Any],
    ) -> ToolExecution:
        if action not in self._tools:
            raise ValueError(f"unknown FarmARE action {action!r}")
        tool, adapter = self._tools[action]
        owner = self._owners[action]
        if owner != actor_id:
            raise PermissionError(
                f"actor {actor_id!r} cannot invoke {action!r}; owner is {owner!r}"
            )
        normalized = normalize_tool_arguments(adapter, arguments)
        if not isinstance(normalized, dict):
            raise ValueError(f"FarmARE action {action!r} requires named arguments")
        if not intent_id:
            raise ValueError("intent_id must be nonempty")
        if intent_id in self._executed_intents:
            previous = self._executed_intents[intent_id]
            if (
                previous.actor_id != actor_id
                or previous.action != action
                or stable_digest(previous.arguments) != stable_digest(normalized)
            ):
                raise ValueError("intent_id was already used for a different request")
            return replace(
                previous,
                arguments=deepcopy(previous.arguments),
                result=deepcopy(previous.result),
                duplicate=True,
            )
        # Preserve the request even if a native tool mutates its inputs.
        request_arguments = deepcopy(normalized)
        before = len(self.environment.event_log.list_view())
        result: Any = None
        error: str | None = None
        try:
            result = tool(**normalized)
        except Exception as exc:  # the native wrapper still records the failure
            error = str(exc)
        if isinstance(result, dict) and result.get("error"):
            error = str(result["error"])
        new_events = self.environment.event_log.list_view()[before:]
        completed = next(
            (
                event
                for event in reversed(new_events)
                if isinstance(event, CompletedEvent)
                and event.action is not None
                and event.function_name() == tool.func_name
                and event.app_name() == tool.app_name
            ),
            None,
        )
        if completed is not None and completed.metadata.exception:
            error = completed.metadata.exception
        execution = ToolExecution(
            actor_id=actor_id,
            action=action,
            arguments=request_arguments,
            result=result,
            completed_event=completed,
            error=error,
        )
        self._executed_intents[intent_id] = replace(
            execution,
            arguments=deepcopy(execution.arguments),
            result=deepcopy(execution.result),
        )
        return execution
