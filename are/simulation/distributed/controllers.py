"""Controller adapters sharing one distributed decision interface."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from are.simulation.agents.agent_log import (
    LLMInputLog,
    LLMOutputThoughtActionLog,
    LLMRetryUsageLog,
    ObservationLog,
    StepLog,
    TaskLog,
)
from are.simulation.agents.default_agent.base_agent import BaseAgent
from are.simulation.agents.llm.llm_engine import LLMEngine
from are.simulation.distributed.models import (
    ActorSpec,
    AgentIntent,
    IntentKind,
    LocalView,
    stable_digest,
)
from are.simulation.tools import Tool


class AgentController(Protocol):
    def initialize(self, actor_spec: ActorSpec, local_view: LocalView) -> None: ...
    def decide(self, local_view: LocalView) -> AgentIntent: ...
    def observe(self, result: Any) -> None: ...
    def is_complete(self) -> bool: ...


class _IntentCaptureTool(Tool):
    """BaseAgent-visible tool that records a proposal without executing it."""

    name = "dcore_intent"
    description = "Propose one externally visible intent to the D-CORE runtime."
    inputs: dict[str, dict[str, str | type]] = {}
    output_type = "string"

    def __init__(
        self,
        *,
        name: str,
        description: str,
        inputs: dict[str, dict[str, str | type]],
    ):
        self.name = name
        self.description = description
        self.inputs = inputs
        self.calls: list[dict[str, Any]] = []
        super().__init__()

    def forward(self, *args: Any, **kwargs: Any) -> str:
        if args:
            raise ValueError("D-CORE intent tools require named JSON arguments")
        self.calls.append(dict(kwargs))
        return "Proposal captured but not executed; await the runtime result."


def _safe_tool_inputs(schema: dict[str, Any]) -> dict[str, dict[str, str]]:
    allowed = {"string", "integer", "number", "image", "audio", "any", "boolean"}
    result: dict[str, dict[str, str]] = {}
    for name, raw in schema.items():
        item = dict(raw) if isinstance(raw, dict) else {}
        value_type = str(item.get("type", "any"))
        if value_type not in allowed:
            value_type = "any"
        result[name] = {
            "type": value_type,
            "description": str(item.get("description", name)),
        }
    return result


class ScriptedController:
    def __init__(self, intents: Iterable[AgentIntent]):
        self._intents = deque(intents)
        self.actor_spec: ActorSpec | None = None
        self.results: list[Any] = []

    def initialize(self, actor_spec: ActorSpec, local_view: LocalView) -> None:
        self.actor_spec = actor_spec

    def decide(self, local_view: LocalView) -> AgentIntent:
        if not self._intents:
            return AgentIntent(kind=IntentKind.FINISH)
        return self._intents.popleft()

    def observe(self, result: Any) -> None:
        self.results.append(result)

    def is_complete(self) -> bool:
        return not self._intents


class ReplayController(ScriptedController):
    pass


class OracleCeilingCoordinator:
    """Shared script cursor used only by the declared human-oracle ceiling."""

    def __init__(self, steps: Iterable[tuple[str, str, AgentIntent]]):
        self.steps = deque(steps)

    def decide(self, actor_id: str) -> tuple[AgentIntent, str | None]:
        if not self.steps:
            return AgentIntent(kind=IntentKind.FINISH), "storage"
        expected_actor, phase, intent = self.steps[0]
        if expected_actor != actor_id:
            return AgentIntent(kind=IntentKind.WAIT), None
        self.steps.popleft()
        return intent, phase


class OracleCeilingController:
    """Controller for the explicit scripted ceiling; not used by model runs."""

    def __init__(
        self,
        actor_id: str,
        coordinator: OracleCeilingCoordinator,
        *,
        llm_style: bool = False,
    ):
        self.actor_id = actor_id
        self.coordinator = coordinator
        self.llm_style = llm_style
        self.decision_count = 0
        self.last_phase: str | None = None
        self.results: list[Any] = []

    def initialize(self, actor_spec: ActorSpec, local_view: LocalView) -> None:
        self.actor_spec = actor_spec

    def decide(self, local_view: LocalView) -> AgentIntent:
        intent, self.last_phase = self.coordinator.decide(self.actor_id)
        self.decision_count += 1
        if self.llm_style:
            intent = intent.model_copy(
                update={
                    "llm_input_log_id": (f"mock:{self.actor_id}:{self.decision_count}")
                }
            )
        return intent

    def observe(self, result: Any) -> None:
        self.results.append(result)

    def is_complete(self) -> bool:
        return not self.coordinator.steps


class CoordinatedMockIntentEngine(LLMEngine):
    """Offline engine that emits oracle-ceiling intents through the real adapter."""

    def __init__(self, actor_id: str, coordinator: OracleCeilingCoordinator):
        super().__init__("dcore-coordinated-mock")
        self.actor_id = actor_id
        self.coordinator = coordinator
        self.calls = 0
        self.last_phase: str | None = None

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stop_sequences=[],
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any]]:
        intent, self.last_phase = self.coordinator.decide(self.actor_id)
        self.calls += 1
        rendered = intent.model_dump_json()
        return rendered, {
            "model_name": self.model_name,
            "model_provider": "mock",
            "response_id": f"mock-response:{self.actor_id}:{self.calls}",
            "prompt_tokens": sum(
                len(str(message.get("content", ""))) // 4 for message in messages
            ),
            "completion_tokens": max(1, len(rendered) // 4),
            "total_tokens": sum(
                len(str(message.get("content", ""))) // 4 for message in messages
            )
            + max(1, len(rendered) // 4),
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "completion_duration": 0.0,
        }


class CoordinatedMockReactEngine(LLMEngine):
    """Offline oracle engine exercising FarmARE's native ReAct step path."""

    def __init__(self, actor_id: str, coordinator: OracleCeilingCoordinator):
        super().__init__("dcore-coordinated-react-mock")
        self.actor_id = actor_id
        self.coordinator = coordinator
        self.calls = 0
        self.last_phase: str | None = None

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stop_sequences=[],
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any]]:
        intent, self.last_phase = self.coordinator.decide(self.actor_id)
        self.calls += 1
        if intent.kind == IntentKind.SEND:
            action = "dcore_send"
            arguments = {
                "recipient": intent.recipient,
                "text": intent.text,
                "claim_fact_keys": list(intent.claim_fact_keys),
                "unresolved_requirements": list(intent.unresolved_requirements),
            }
        elif intent.kind == IntentKind.WAIT:
            action = "dcore_wait"
            arguments = {"wait": intent.wait}
        elif intent.kind == IntentKind.ABSTAIN:
            action = "dcore_abstain"
            arguments = {"reason": intent.text}
        elif intent.kind == IntentKind.FINISH:
            action = "dcore_finish"
            arguments = {}
        else:
            action = str(intent.action)
            arguments = intent.args
        rendered = (
            "Thought: choose the next oracle-ceiling proposal for adapter validation.\n"
            "Action:\n"
            + json.dumps({"action": action, "action_input": arguments})
            + "<end_action>"
        )
        prompt_tokens = sum(
            len(str(message.get("content", ""))) // 4 for message in messages
        )
        completion_tokens = max(1, len(rendered) // 4)
        return rendered, {
            "model_name": self.model_name,
            "model_provider": "mock",
            "response_id": f"mock-react:{self.actor_id}:{self.calls}",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "completion_duration": 0.0,
        }


class MockLLMController(ScriptedController):
    """Deterministic controller carrying LLM-style decision identifiers."""

    def __init__(self, actor_id: str, intents: Iterable[AgentIntent]):
        super().__init__(intents)
        self.actor_id = actor_id
        self._decision_count = 0

    def decide(self, local_view: LocalView) -> AgentIntent:
        intent = super().decide(local_view)
        self._decision_count += 1
        return intent.model_copy(
            update={"llm_input_log_id": f"mock:{self.actor_id}:{self._decision_count}"}
        )


class FarmARELLMController:
    """JSON-intent adapter for any existing FarmARE LLM engine.

    The engine sees only ``LocalView``. Tools are executed later by the runtime,
    so the same guard and trace semantics apply to scripted and real models.
    """

    def __init__(
        self,
        llm_engine: LLMEngine,
        *,
        system_prompt: str | None = None,
        parser: Callable[[str], AgentIntent] | None = None,
        max_decisions: int = 30,
        max_model_calls: int | None = None,
        max_retries: int = 2,
        history_window: int | None = None,
        knowledge_window: int = 120,
        message_window: int = 24,
    ):
        self.engine = llm_engine
        self.system_prompt = system_prompt or (
            "You are one actor in a distributed system. Return one JSON object with "
            "kind, action, args, recipient, text, claim_fact_keys, wait. Never assume "
            "facts absent from the supplied local view."
        )
        self.parser = parser or self._parse
        self.max_decisions = max_decisions
        self.max_model_calls = max_model_calls or max_decisions
        self.max_retries = max_retries
        self.history_window = history_window
        self.knowledge_window = knowledge_window
        self.message_window = message_window
        self.actor_spec: ActorSpec | None = None
        self.decisions = 0
        self.model_calls = 0
        self.complete = False
        self.last_input_log_id: str | None = None
        self.history: list[dict[str, str]] = []
        self.logs: list[LLMInputLog | LLMOutputThoughtActionLog] = []
        self.last_metadata: dict[str, Any] = {}
        self.last_phase: str | None = None
        self.last_prompt_item_ids: tuple[str, ...] = ()
        self.last_prompt_message_ids: tuple[str, ...] = ()
        self.last_prompt_digest: str | None = None

    def initialize(self, actor_spec: ActorSpec, local_view: LocalView) -> None:
        self.actor_spec = actor_spec
        self.history = [{"role": "system", "content": self.system_prompt}]

    def decide(self, local_view: LocalView) -> AgentIntent:
        self.decisions += 1
        local_context = self._render_local_context(local_view)
        self.history.append({"role": "user", "content": local_context})
        self._trim_history()
        cumulative = {
            key: 0
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cached_tokens",
                "reasoning_tokens",
                "completion_duration",
            )
        }
        last_error: Exception | None = None
        for retry_count in range(self.max_retries + 1):
            if self.model_calls >= self.max_model_calls:
                raise RuntimeError("per-agent model-call budget exhausted")
            input_log = LLMInputLog(
                content=list(self.history),
                timestamp=local_view.world_time,
                agent_id=local_view.actor.actor_id,
            )
            self.last_prompt_digest = stable_digest(input_log.content)
            self.logs.append(input_log)
            self.last_input_log_id = input_log.id
            self.model_calls += 1
            response = self.engine(
                list(self.history),
                additional_trace_tags=["dcore_decision"],
            )
            if isinstance(response, tuple):
                text, raw_metadata = response
                metadata = dict(raw_metadata or {})
            else:
                text = response
                metadata = {}
            self.last_phase = getattr(self.engine, "last_phase", None)
            for key in cumulative:
                cumulative[key] += metadata.get(key, 0) or 0
            self.logs.append(
                LLMOutputThoughtActionLog(
                    content=str(text),
                    timestamp=time.time(),
                    agent_id=local_view.actor.actor_id,
                    prompt_tokens=int(metadata.get("prompt_tokens", 0)),
                    completion_tokens=int(metadata.get("completion_tokens", 0)),
                    total_tokens=int(metadata.get("total_tokens", 0)),
                    cached_tokens=int(metadata.get("cached_tokens", 0)),
                    reasoning_tokens=int(metadata.get("reasoning_tokens", 0)),
                    completion_duration=float(metadata.get("completion_duration", 0.0)),
                    model_name=metadata.get("model_name"),
                    model_provider=metadata.get("model_provider"),
                )
            )
            self.history.append({"role": "assistant", "content": str(text)})
            try:
                intent = self.parser(str(text)).model_copy(
                    update={"llm_input_log_id": self.last_input_log_id}
                )
            except Exception as error:
                last_error = error
                if retry_count >= self.max_retries:
                    break
                self.history.append(
                    {
                        "role": "user",
                        "content": (
                            "Invalid AgentIntent JSON. Correct the schema and return "
                            f"one JSON object only. Validation error: {error}"
                        ),
                    }
                )
                self._trim_history()
                continue
            self.last_metadata = {
                **metadata,
                **cumulative,
                "retry_count": retry_count,
            }
            self._trim_history()
            if intent.kind == IntentKind.FINISH:
                self.complete = True
            return intent
        raise ValueError(
            f"LLM did not produce a valid AgentIntent after retries: {last_error}"
        )

    def observe(self, result: Any) -> None:
        self.history.append(
            {
                "role": "user",
                "content": "Runtime result: "
                + json.dumps(result, sort_keys=True, default=str),
            }
        )
        self._trim_history()

    def is_complete(self) -> bool:
        return (
            self.complete
            or self.decisions >= self.max_decisions
            or self.model_calls >= self.max_model_calls
        )

    def _trim_history(self) -> None:
        if self.history_window is None:
            return
        self.history = [self.history[0], *self.history[1:][-self.history_window :]]

    def _render_local_context(self, local_view: LocalView) -> str:
        """Render only actor-local information, without a LocalView dump."""

        frontier: dict[tuple[str, str], Any] = {}
        for item in local_view.knowledge:
            key = (item.fact_key, repr(item.scope))
            previous = frontier.get(key)
            if previous is None or (item.learned_at, item.item_id) > (
                previous.learned_at,
                previous.item_id,
            ):
                frontier[key] = item
        visible_knowledge = sorted(
            frontier.values(), key=lambda item: (item.learned_at, item.item_id)
        )[-self.knowledge_window :]
        self.last_prompt_item_ids = tuple(item.item_id for item in visible_knowledge)

        visible_messages = local_view.inbox[-self.message_window :]
        self.last_prompt_message_ids = tuple(
            message.message_id for message in visible_messages
        )
        payload = {
            "actor_id": local_view.actor.actor_id,
            "role": local_view.actor.role,
            "world_time": local_view.world_time,
            "permitted_actions": list(local_view.actor.permitted_actions),
            "permitted_tool_schemas": local_view.actor.tool_schemas,
            "knowledge": [item.model_dump(mode="json") for item in visible_knowledge],
            "knowledge_store_size": len(local_view.knowledge),
            "knowledge_frontier_complete": len(frontier) <= self.knowledge_window,
            "delivered_messages": [
                message.model_dump(mode="json") for message in visible_messages
            ],
            "delivered_message_count": len(local_view.inbox),
            "message_frontier_complete": len(local_view.inbox) <= self.message_window,
            "unresolved_requirements": list(local_view.unresolved),
            "previous_local_result": local_view.previous_result,
        }
        return (
            "Choose exactly one local intent from the information below. Facts not "
            "listed are unknown.\n" + json.dumps(payload, sort_keys=True, default=str)
        )

    @staticmethod
    def _parse(text: str) -> AgentIntent:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("LLM response does not contain a JSON intent")
        return AgentIntent.model_validate_json(text[start : end + 1])


class FarmAREBaseAgentController:
    """Step-wise adapter around FarmARE's native ``BaseAgent.step()``.

    Farm tools are represented by non-mutating capture proxies.  The native
    agent therefore performs its normal ReAct parsing, family prompting,
    history, and logging, but the distributed runtime remains the sole place
    that can authorize and execute a FarmARE action.
    """

    def __init__(
        self,
        farmare_agent: Any,
        *,
        max_decisions: int,
        max_model_calls: int,
        max_total_tokens: int | None = None,
        knowledge_window: int = 120,
        message_window: int = 24,
    ):
        self.farmare_agent = farmare_agent
        self.base_agent: BaseAgent = farmare_agent.react_agent
        self.max_decisions = max_decisions
        self.max_model_calls = max_model_calls
        self.max_total_tokens = max_total_tokens
        self.knowledge_window = knowledge_window
        self.message_window = message_window
        self.actor_spec: ActorSpec | None = None
        self.decisions = 0
        self.complete = False
        self.capture_tools: dict[str, _IntentCaptureTool] = {}
        self.last_input_log_id: str | None = None
        self.last_prompt_item_ids: tuple[str, ...] = ()
        self.last_prompt_message_ids: tuple[str, ...] = ()
        self.last_prompt_digest: str | None = None
        self.last_metadata: dict[str, Any] = {}
        self.last_phase: str | None = None

    def initialize(self, actor_spec: ActorSpec, local_view: LocalView) -> None:
        self.actor_spec = actor_spec
        self.base_agent.agent_id = actor_spec.actor_id
        self.base_agent.conditional_pre_steps = []
        self.capture_tools = self._build_capture_tools(actor_spec)
        self.base_agent.tools = dict(self.capture_tools)
        if hasattr(self.farmare_agent, "_reset_research_state"):
            self.farmare_agent._reset_research_state()
        self.base_agent.initialize()

    def decide(self, local_view: LocalView) -> AgentIntent:
        if (
            self.decisions >= self.max_decisions
            or self._model_call_count() >= self.max_model_calls
            or (
                self.max_total_tokens is not None
                and self._token_count() >= self.max_total_tokens
            )
        ):
            self.complete = True
            return AgentIntent(kind=IntentKind.FINISH)
        for tool in self.capture_tools.values():
            tool.calls.clear()
        context = self._render_local_context(local_view)
        if hasattr(self.farmare_agent, "_consume_new_logs_for_metrics"):
            self.farmare_agent._consume_new_logs_for_metrics()
            research = self.farmare_agent._build_research_context(context)
            if research:
                context = f"{context}\n\n{research}"
        self.base_agent.append_agent_log(
            TaskLog(
                content=context,
                timestamp=local_view.world_time,
                agent_id=local_view.actor.actor_id,
            )
        )
        self.base_agent.append_agent_log(
            StepLog(
                iteration=self.decisions,
                timestamp=local_view.world_time,
                agent_id=local_view.actor.actor_id,
            )
        )
        start = len(self.base_agent.logs)
        remaining_calls = self.max_model_calls - self._model_call_count()
        original_retry_limit = self.base_agent.invalid_format_retries
        self.base_agent.invalid_format_retries = min(
            original_retry_limit, max(0, remaining_calls - 1)
        )
        try:
            self.base_agent.step()
        finally:
            self.base_agent.invalid_format_retries = original_retry_limit
        self.last_phase = getattr(self.base_agent.llm_engine, "last_phase", None)
        self.base_agent.iterations += 1
        self.base_agent.planning_counter += 1
        self.decisions += 1
        new_logs = self.base_agent.logs[start:]
        input_log = next(
            (log for log in new_logs if isinstance(log, LLMInputLog)), None
        )
        output_log = next(
            (
                log
                for log in reversed(new_logs)
                if isinstance(log, LLMOutputThoughtActionLog)
            ),
            None,
        )
        if input_log is None:
            raise RuntimeError("BaseAgent.step() emitted no LLMInputLog")
        self.last_input_log_id = input_log.id
        self.last_prompt_digest = stable_digest(input_log.content)
        if output_log is not None:
            usage_logs = [
                log
                for log in new_logs
                if isinstance(log, (LLMOutputThoughtActionLog, LLMRetryUsageLog))
            ]
            self.last_metadata = {
                "model_name": output_log.model_name,
                "model_provider": output_log.model_provider,
                "response_id": output_log.response_id,
                "system_fingerprint": output_log.system_fingerprint,
                "prompt_tokens": sum(log.prompt_tokens for log in usage_logs),
                "completion_tokens": sum(log.completion_tokens for log in usage_logs),
                "total_tokens": sum(log.total_tokens for log in usage_logs),
                "cached_tokens": sum(log.cached_tokens for log in usage_logs),
                "reasoning_tokens": sum(log.reasoning_tokens for log in usage_logs),
                "completion_duration": sum(
                    log.completion_duration for log in usage_logs
                ),
                "retry_count": sum(
                    isinstance(log, LLMRetryUsageLog) for log in new_logs
                ),
                "adapter": "native_base_agent_step_v1",
            }
        called = [
            (name, tool.calls[-1])
            for name, tool in self.capture_tools.items()
            if tool.calls
        ]
        if len(called) != 1:
            raise ValueError(
                "BaseAgent activation must propose exactly one D-CORE intent; "
                f"captured {len(called)}"
            )
        action, arguments = called[0]
        intent = self._intent_from_capture(action, arguments)
        if intent.kind == IntentKind.FINISH:
            self.complete = True
        return intent.model_copy(update={"llm_input_log_id": self.last_input_log_id})

    def observe(self, result: Any) -> None:
        self.base_agent.append_agent_log(
            ObservationLog(
                content="D-CORE runtime result: "
                + json.dumps(result, sort_keys=True, default=str),
                timestamp=self.base_agent.make_timestamp(),
                agent_id=self.base_agent.agent_id,
            )
        )

    def is_complete(self) -> bool:
        return (
            self.complete
            or self.decisions >= self.max_decisions
            or self._model_call_count() >= self.max_model_calls
            or (
                self.max_total_tokens is not None
                and self._token_count() >= self.max_total_tokens
            )
        )

    def _model_call_count(self) -> int:
        return sum(
            isinstance(log, (LLMOutputThoughtActionLog, LLMRetryUsageLog))
            for log in self.base_agent.logs
        )

    def _token_count(self) -> int:
        return sum(
            int(log.total_tokens or 0)
            for log in self.base_agent.logs
            if isinstance(log, (LLMOutputThoughtActionLog, LLMRetryUsageLog))
        )

    def _render_local_context(self, local_view: LocalView) -> str:
        frontier: dict[tuple[str, str], Any] = {}
        for item in local_view.knowledge:
            key = (item.fact_key, repr(item.scope))
            previous = frontier.get(key)
            if previous is None or (item.learned_at, item.item_id) > (
                previous.learned_at,
                previous.item_id,
            ):
                frontier[key] = item
        visible = sorted(
            frontier.values(), key=lambda item: (item.learned_at, item.item_id)
        )[-self.knowledge_window :]
        self.last_prompt_item_ids = tuple(item.item_id for item in visible)
        visible_messages = local_view.inbox[-self.message_window :]
        self.last_prompt_message_ids = tuple(
            message.message_id for message in visible_messages
        )
        payload = {
            "actor_id": local_view.actor.actor_id,
            "role": local_view.actor.role,
            "world_time": local_view.world_time,
            "knowledge": [item.model_dump(mode="json") for item in visible],
            "knowledge_store_size": len(local_view.knowledge),
            "knowledge_frontier_complete": len(frontier) <= self.knowledge_window,
            "delivered_messages": [
                message.model_dump(mode="json") for message in visible_messages
            ],
            "delivered_message_count": len(local_view.inbox),
            "message_frontier_complete": len(local_view.inbox) <= self.message_window,
            "unresolved_requirements": list(local_view.unresolved),
            "previous_local_result": local_view.previous_result,
        }
        rendered = (
            "Choose exactly one role-owned tool from this actor-local state. "
            "Facts not listed are unknown. The tool only proposes the intent; "
            "D-CORE will return the authoritative execution result.\n"
            + json.dumps(payload, sort_keys=True, default=str)
        )
        self.last_prompt_digest = stable_digest(rendered)
        return rendered

    @staticmethod
    def _build_capture_tools(
        actor_spec: ActorSpec,
    ) -> dict[str, _IntentCaptureTool]:
        tools: dict[str, _IntentCaptureTool] = {}
        for name, schema in actor_spec.tool_schemas.items():
            tools[name] = _IntentCaptureTool(
                name=name,
                description=str(schema.get("description", name)),
                inputs=_safe_tool_inputs(dict(schema.get("arguments", {}))),
            )
        tools["dcore_send"] = _IntentCaptureTool(
            name="dcore_send",
            description=(
                "Send a handoff using only fact keys in your local knowledge. "
                "The configured condition determines free-text or causal encoding."
            ),
            inputs={
                "recipient": {"type": "string", "description": "recipient actor"},
                "recipients": {
                    "type": "any",
                    "description": "explicit recipients for a topology-valid broadcast",
                },
                "text": {"type": "string", "description": "brief explanation"},
                "claim_fact_keys": {
                    "type": "any",
                    "description": "JSON list of local fact keys to transmit",
                },
                "unresolved_requirements": {
                    "type": "any",
                    "description": "JSON list of unresolved requirements",
                },
            },
        )
        tools["dcore_wait"] = _IntentCaptureTool(
            name="dcore_wait",
            description="Defer and request reactivation after a logical-time interval.",
            inputs={"wait": {"type": "number", "description": "nonnegative interval"}},
        )
        tools["dcore_finish"] = _IntentCaptureTool(
            name="dcore_finish",
            description="Finish only when this actor's seasonal duties are complete.",
            inputs={},
        )
        tools["dcore_abstain"] = _IntentCaptureTool(
            name="dcore_abstain",
            description=(
                "Abstain from the current high-impact operation when its reviewed "
                "deadline has closed or prerequisites are contradicted."
            ),
            inputs={
                "reason": {
                    "type": "string",
                    "description": "local evidence-based reason for abstention",
                }
            },
        )
        return tools

    def _intent_from_capture(
        self, action: str, arguments: dict[str, Any]
    ) -> AgentIntent:
        if action == "dcore_send":
            claim_keys = arguments.get("claim_fact_keys", [])
            unresolved = arguments.get("unresolved_requirements", [])
            return AgentIntent(
                kind=IntentKind.SEND,
                recipient=str(arguments.get("recipient", "")) or None,
                recipients=tuple(str(item) for item in arguments.get("recipients", ())),
                text=str(arguments.get("text", "")),
                claim_fact_keys=tuple(str(item) for item in claim_keys or ()),
                unresolved_requirements=tuple(str(item) for item in unresolved or ()),
            )
        if action == "dcore_wait":
            return AgentIntent(
                kind=IntentKind.WAIT,
                wait=max(0.0, float(arguments.get("wait", 0.0) or 0.0)),
            )
        if action == "dcore_finish":
            return AgentIntent(kind=IntentKind.FINISH)
        if action == "dcore_abstain":
            return AgentIntent(
                kind=IntentKind.ABSTAIN,
                text=str(arguments.get("reason", "")),
            )
        schema = (self.actor_spec.tool_schemas if self.actor_spec else {}).get(
            action, {}
        )
        kind = IntentKind.OBSERVE if bool(schema.get("observation")) else IntentKind.ACT
        return AgentIntent(kind=kind, action=action, args=arguments)
