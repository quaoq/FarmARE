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

# These tools mutate only the simulation clock/scheduler.  They remain READ
# operations in the process model so that causal and conformance evaluators do
# not interpret waiting as an agricultural management transition.  The native
# runner still journals them as state changes for crash recovery.
DURABLE_STATE_CHANGING_READ_ACTIONS = frozenset(
    {
        "SystemApp__advance_time",
        "SystemApp__wait_for_notification",
    }
)


def requires_durable_native_journal(action: str, *, write_operation: bool) -> bool:
    """Return whether native execution needs intent/receipt journaling."""

    return bool(write_operation or action in DURABLE_STATE_CHANGING_READ_ACTIONS)


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
        "advance_time",
        "wait_for_notification",
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


@dataclass(frozen=True)
class NativeOperationEstimate:
    action: str
    normalized_arguments: dict[str, Any]
    owner: str
    duration_seconds: float | None
    resource_effects: dict[str, Any]
    feasible: bool | None
    blocking_reasons: tuple[str, ...]
    estimator_source: str
    native_state_digest: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "schema_version": "native_operation_estimate_v1",
            "action": self.action,
            "normalized_arguments": deepcopy(self.normalized_arguments),
            "owner": self.owner,
            "duration_seconds": self.duration_seconds,
            "resource_effects": deepcopy(self.resource_effects),
            "feasible": self.feasible,
            "blocking_reasons": list(self.blocking_reasons),
            "estimator_source": self.estimator_source,
            "native_state_digest": self.native_state_digest,
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

    def estimate(
        self, *, action: str, arguments: dict[str, Any]
    ) -> NativeOperationEstimate:
        """Estimate one native operation without invoking or mutating it."""

        if action not in self._tools:
            raise ValueError(f"unknown FarmARE action {action!r}")
        tool, adapter = self._tools[action]
        normalized = normalize_tool_arguments(adapter, arguments)
        if not isinstance(normalized, dict):
            raise ValueError(f"FarmARE action {action!r} requires named arguments")
        instance = tool.class_instance
        state_projection = {
            "class": tool.class_name,
            "app": tool.app_name,
            "battery_pct": getattr(instance, "_battery_pct", None),
            "charging": getattr(instance, "_charging", None),
            "configuration": {
                key: getattr(instance, key, None)
                for key in (
                    "speed_ms",
                    "effective_ridges_per_pass",
                    "takeoff_overhead_s",
                    "min_battery_pct",
                    "battery_pct_per_ridge",
                )
                if hasattr(instance, key)
            },
        }
        estimate: dict[str, Any]
        if tool.func_name == "fly_survey" and hasattr(instance, "estimate_fly_survey"):
            estimate = instance.estimate_fly_survey(**normalized)
            source = f"{tool.class_name}.estimate_fly_survey"
        elif tool.func_name in {
            "inspect_pests",
            "inspect_crop_health",
            "inspect_emergence",
        } and hasattr(instance, "estimate_inspection"):
            estimate = instance.estimate_inspection(**normalized)
            source = f"{tool.class_name}.estimate_inspection"
        elif is_observation_tool(tool) and tool.write_operation is False:
            estimate = {
                "duration_seconds": 0.0,
                "resource_effects": {},
                "feasible": True,
                "blocking_reasons": (),
            }
            source = "read_only_native_tool"
        else:
            estimate = {
                "duration_seconds": None,
                "resource_effects": {},
                "feasible": None,
                "blocking_reasons": ("native_estimator_unavailable",),
            }
            source = "unavailable"
        return NativeOperationEstimate(
            action=action,
            normalized_arguments=deepcopy(normalized),
            owner=self._owners[action],
            duration_seconds=estimate.get("duration_seconds"),
            resource_effects=dict(estimate.get("resource_effects") or {}),
            feasible=estimate.get("feasible"),
            blocking_reasons=tuple(estimate.get("blocking_reasons") or ()),
            estimator_source=source,
            native_state_digest=stable_digest(state_projection),
        )

    def feasibility_state(self) -> dict[str, Any]:
        """Return only equipment/configuration state needed for repair estimates."""

        apps: dict[str, dict[str, Any]] = {}
        for tool, _ in self._tools.values():
            instance = tool.class_instance
            key = str(tool.app_name)
            if key in apps:
                continue
            apps[key] = {
                "class": tool.class_name,
                "battery_pct": getattr(instance, "_battery_pct", None),
                "charging": getattr(instance, "_charging", None),
                "configuration": {
                    name: getattr(instance, name, None)
                    for name in (
                        "speed_ms",
                        "effective_ridges_per_pass",
                        "takeoff_overhead_s",
                        "min_battery_pct",
                        "battery_pct_per_ridge",
                    )
                    if hasattr(instance, name)
                },
            }
        payload = {"schema_version": "native_feasibility_state_v1", "apps": apps}
        return {**payload, "state_digest": stable_digest(payload)}

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
        advance_native_operation = getattr(
            self.environment.time_manager, "advance_native_operation", None
        )
        if advance_native_operation is not None:
            advance_native_operation()
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
