"""Controller adapters sharing one distributed decision interface."""

from __future__ import annotations

import json
import math
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
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
from are.simulation.distributed.knowledge import knowledge_frontier
from are.simulation.distributed.models import (
    ActorSpec,
    AgentIntent,
    IntentKind,
    LocalView,
    stable_digest,
)
from are.simulation.tools import Tool

PROMPT_CONTEXT_TARGET_TOKENS = 11_800


def _knowledge_prompt_order(item: Any) -> tuple[Any, ...]:
    """Order equivalent evidence independently of generated item identifiers."""

    status = getattr(item.status, "value", item.status)
    return (
        float(item.observed_at),
        float(item.learned_at),
        str(item.fact_key),
        repr(item.scope),
        str(item.source_actor),
        str(status),
    )


def _compact_prompt_message(message: Any) -> dict[str, Any]:
    """Represent delivery without duplicating transmitted fact records.

    Claim values, scopes, validity, provenance and local evidence identifiers
    appear in the actor's knowledge frontier.  Full envelopes remain in the
    immutable trace and durable journal.
    """

    claims = tuple(getattr(message, "claims", ()))
    return {
        "message_id": message.message_id,
        "envelope_type": message.envelope_type,
        "sender": message.sender,
        "recipient": message.recipient,
        "text": getattr(message, "text", ""),
        "send_time": message.send_time,
        "claim_count": len(claims),
        "claim_fact_keys": list(dict.fromkeys(claim.fact_key for claim in claims)),
        "unresolved": list(getattr(message, "unresolved", ())),
    }


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


class TraceReplayCoordinator:
    """Consume the original global proposal order during deterministic replay."""

    def __init__(
        self,
        decisions: Iterable[Any],
        *,
        complete_when_exhausted: Iterable[str] = (),
    ):
        self.decisions = deque(decisions)
        self.complete_when_exhausted = frozenset(complete_when_exhausted)

    def decide(self, actor_id: str) -> tuple[AgentIntent, str | None]:
        if not self.decisions:
            return AgentIntent(kind=IntentKind.FINISH), "storage"
        decision = self.decisions[0]
        if decision.actor_id != actor_id:
            raise RuntimeError(
                "replay activation order diverged: "
                f"expected {decision.actor_id!r}, received {actor_id!r}"
            )
        self.decisions.popleft()
        return decision.proposed_intent, decision.season_phase


class CoordinatedReplayController:
    def __init__(self, actor_id: str, coordinator: TraceReplayCoordinator):
        self.actor_id = actor_id
        self.coordinator = coordinator
        self.complete = False
        self.last_phase: str | None = None
        self.results: list[Any] = []

    def initialize(self, actor_spec: ActorSpec, local_view: LocalView) -> None:
        self.actor_spec = actor_spec

    def decide(self, local_view: LocalView) -> AgentIntent:
        intent, self.last_phase = self.coordinator.decide(self.actor_id)
        self.complete = intent.kind == IntentKind.FINISH
        return intent

    def observe(self, result: Any) -> None:
        self.results.append(result)

    def is_complete(self) -> bool:
        return self.complete or (
            not self.coordinator.decisions
            and self.actor_id in self.coordinator.complete_when_exhausted
        )


class OracleCeilingCoordinator:
    """Shared script cursor used only by the declared human-oracle ceiling."""

    def __init__(self, steps: Iterable[tuple[str, str, AgentIntent]]):
        self.steps = deque(steps)
        self.harvest_started: set[str] = set()

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
        harvest_deadlines: dict[str, float] | None = None,
        harvest_clock_actor: str | None = None,
        harvest_openings: dict[str, float] | None = None,
        retry_wet_soil: bool = False,
    ):
        self.actor_id = actor_id
        self.coordinator = coordinator
        self.llm_style = llm_style
        self.decision_count = 0
        self.last_phase: str | None = None
        self.results: list[Any] = []
        self.harvest_deadlines = harvest_deadlines or {}
        self.harvest_openings = harvest_openings or {}
        self.retry_wet_soil = retry_wet_soil
        self.harvest_clock_actor = harvest_clock_actor or actor_id
        self.last_intent: AgentIntent | None = None
        self.last_world_time = 0.0

    def initialize(self, actor_spec: ActorSpec, local_view: LocalView) -> None:
        self.actor_spec = actor_spec

    def decide(self, local_view: LocalView) -> AgentIntent:
        intent, self.last_phase = self.coordinator.decide(self.actor_id)
        opening = self.harvest_openings.get(self.last_phase or "")
        if (
            opening is not None
            and self.last_phase not in self.coordinator.harvest_started
            and intent.action == "SystemApp__advance_time"
        ):
            requested = sum(
                float(intent.args.get(key, 0)) * scale
                for key, scale in (
                    ("days", 86400),
                    ("hours", 3600),
                    ("minutes", 60),
                    ("seconds", 1),
                )
            )
            intent = intent.model_copy(
                update={
                    "args": {
                        "seconds": max(
                            1,
                            math.ceil(
                                min(requested, max(0, opening - local_view.world_time))
                            ),
                        ),
                        "minutes": 0,
                        "hours": 0,
                        "days": 0,
                    }
                }
            )
        if intent.action == "TractorApp__harvest" and self.last_phase is not None:
            self.coordinator.harvest_started.add(self.last_phase)
        self.last_intent = intent
        self.last_world_time = local_view.world_time
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
        # Declared scripted reference policy only. Model controllers never use
        # this class and never receive these oracle retry proposals.
        intent = self.last_intent
        deadline = self.harvest_deadlines.get(self.last_phase or "")
        if intent is None or intent.action != "TractorApp__harvest" or deadline is None:
            return
        native = result.get("result") if isinstance(result, dict) else None
        error = native.get("error") if isinstance(native, dict) else None
        retryable = isinstance(error, str) and (
            error == "Cannot harvest in rainy conditions"
            or error.startswith("Ridges are not mature enough for harvest:")
            or error.startswith("Grain moisture too high for harvest")
            or (self.retry_wet_soil and error.startswith("Soil too wet for harvest"))
        )
        if retryable and self.last_world_time + 86400 < deadline:
            self.coordinator.steps.appendleft((self.actor_id, self.last_phase, intent))
            self.coordinator.steps.appendleft(
                (
                    self.harvest_clock_actor,
                    self.last_phase,
                    AgentIntent(
                        kind=IntentKind.ACT,
                        action="SystemApp__advance_time",
                        args={"days": 1},
                    ),
                )
            )

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
                "claim_item_ids": list(intent.claim_item_ids),
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


class RecordedReactResponseEngine(LLMEngine):
    """Replay flushed provider responses through the ordinary ReAct parser.

    The engine never contacts a provider. Each call consumes the next journaled
    response for one actor, preserving rejected formatting attempts as well as
    accepted proposals. Prompt digests are retained for audit; checkpoint and
    semantic-trace verification remain authoritative because generated evidence
    identifiers can legitimately differ between fresh environments.
    """

    def __init__(self, actor_id: str, exchanges: Iterable[dict[str, Any]]):
        super().__init__("dcore-recorded-response-replay")
        self.actor_id = actor_id
        self.exchanges = deque(dict(item) for item in exchanges)
        self.calls = 0
        self.last_phase: str | None = None
        self.prompt_checks: list[dict[str, Any]] = []

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stop_sequences=[],
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any]]:
        if not self.exchanges:
            raise RuntimeError(
                f"recorded responses exhausted for actor {self.actor_id!r}"
            )
        exchange = self.exchanges.popleft()
        if exchange.get("actor_id") != self.actor_id:
            raise RuntimeError("recorded response actor does not match replay engine")
        self.calls += 1
        self.last_phase = exchange.get("season_phase")
        current_digest = stable_digest(messages)
        self.prompt_checks.append(
            {
                "call": self.calls,
                "source_prompt_digest": exchange.get("prompt_digest"),
                "replay_prompt_digest": current_digest,
                "exact_match": current_digest == exchange.get("prompt_digest"),
            }
        )
        metadata = dict(exchange.get("metadata") or {})
        metadata.update(
            {
                "model_name": metadata.get("model_name")
                or "dcore-recorded-response-replay",
                "model_provider": "recorded-replay",
                "response_id": f"recorded:{self.actor_id}:{self.calls}",
                "completion_duration": 0.0,
            }
        )
        return str(exchange.get("response", "")), metadata


class RecordedThenLiveEngine(LLMEngine):
    """Replay a verified prefix, then irreversibly switch to a live engine."""

    def __init__(
        self,
        actor_id: str,
        recorded: RecordedReactResponseEngine,
        live: LLMEngine,
    ) -> None:
        super().__init__(f"dcore-prefix-then-{live.model_name}")
        self.actor_id = actor_id
        self.recorded = recorded
        self.live = live
        self.live_suffix = False
        self.discarded_response_count = 0
        self.last_phase: str | None = None

    def begin_live_suffix(self) -> int:
        if not self.live_suffix:
            self.discarded_response_count = len(self.recorded.exchanges)
            self.recorded.exchanges.clear()
            self.live_suffix = True
        return self.discarded_response_count

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stop_sequences=[],
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any]]:
        engine = self.live if self.live_suffix else self.recorded
        response, metadata = engine.chat_completion(
            messages, stop_sequences=stop_sequences, **kwargs
        )
        self.last_phase = getattr(engine, "last_phase", None)
        return response, metadata


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
            "kind, action, args, recipient, text, claim_fact_keys, claim_item_ids, "
            "wait. Never assume facts absent from the supplied local view."
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
        self.last_prompt_payload: list[dict[str, Any]] | None = None
        self.last_response_content: str | None = None

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

        frontier = tuple(
            sorted(
                knowledge_frontier(local_view.knowledge), key=_knowledge_prompt_order
            )
        )
        forced_ids = set(getattr(self, "forced_prompt_item_ids", ()))
        forced = [item for item in local_view.knowledge if item.item_id in forced_ids]
        visible_knowledge = [
            item
            for item in frontier[-self.knowledge_window :]
            if item.item_id not in forced_ids
        ] + forced
        self.forced_prompt_item_ids = ()
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
                _compact_prompt_message(message) for message in visible_messages
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
        self.base_agent.deduplicate_local_state = True
        self.accepted_write_receipts: deque[dict[str, Any]] = deque(maxlen=32)
        # At most three action kinds × 64 ridges. Time/status calls cannot evict
        # historical field coverage. This is actor-local memory, not farm truth.
        self.accepted_field_work: dict[tuple[str, int], dict[str, Any]] = {}
        self.accepted_postharvest_work: dict[str, dict[str, Any]] = {}
        self.recent_failures: deque[dict[str, Any]] = deque(maxlen=8)
        self.persistent_failures: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.base_agent.invalid_format_retries = min(
            self.base_agent.invalid_format_retries, 2
        )
        self.max_decisions = max_decisions
        self.max_model_calls = max_model_calls
        self.max_total_tokens = max_total_tokens
        self.knowledge_window = knowledge_window
        self.message_window = message_window
        self.actor_spec: ActorSpec | None = None
        self.decisions = 0
        self.intent_kind_counts: Counter[str] = Counter()
        self.complete = False
        self.capture_tools: dict[str, _IntentCaptureTool] = {}
        self.last_input_log_id: str | None = None
        self.last_prompt_item_ids: tuple[str, ...] = ()
        self.last_prompt_message_ids: tuple[str, ...] = ()
        self.last_prompt_digest: str | None = None
        self.last_metadata: dict[str, Any] = {}
        self.last_phase: str | None = None
        self.season_start_world_time: float | None = None

    def initialize(self, actor_spec: ActorSpec, local_view: LocalView) -> None:
        self.actor_spec = actor_spec
        self.base_agent.agent_id = actor_spec.actor_id
        self.base_agent.conditional_pre_steps = []
        self.capture_tools = self._build_capture_tools(actor_spec)
        self.base_agent.tools = dict(self.capture_tools)
        if hasattr(self.farmare_agent, "_reset_research_state"):
            self.farmare_agent._reset_research_state()
        self.base_agent.initialize()
        self.season_start_world_time = local_view.world_time

    def decide(self, local_view: LocalView) -> AgentIntent:
        if (
            self.decisions >= self.max_decisions
            or self._model_call_count() >= self.max_model_calls
            or (
                self.max_total_tokens is not None
                and self._token_count() >= self.max_total_tokens
            )
        ):
            from are.simulation.distributed.pilot_budget import RequestBudgetExceeded

            raise RequestBudgetExceeded(
                "actor decision, request or token allocation exhausted"
            )
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
        # One request per attempt here gives format and schema errors a shared
        # ceiling of two corrective retries. Every rejected output remains in logs.
        self.base_agent.invalid_format_retries = 0
        try:
            from are.simulation.agents.llm.llm_engine import InvalidProposalResponse
            from are.simulation.exceptions import AgentError, InvalidToolCallError

            for attempt in range(min(3, remaining_calls)):
                try:
                    self.base_agent.step()
                    proposals = [
                        (name, args)
                        for name, tool in self.capture_tools.items()
                        for args in tool.calls
                    ]
                    if len(proposals) != 1:
                        raise InvalidToolCallError(
                            "activation must propose exactly one role-owned tool"
                        )
                    intent = self._intent_from_capture(*proposals[0])
                    break
                except (
                    AgentError,
                    InvalidToolCallError,
                    InvalidProposalResponse,
                    ValueError,
                ) as error:
                    for tool in self.capture_tools.values():
                        tool.calls.clear()
                    self.observe(
                        {
                            "status": "rejected_proposal",
                            "rejected_response": getattr(
                                error, "response_content", None
                            ),
                            "provider_metadata": getattr(error, "metadata", None),
                            "error_type": type(error).__name__,
                            "error": str(error),
                            "corrective_retry": attempt + 1,
                            "feedback": "Use exactly one tool owned by your role, with the declared JSON argument schema. If another role owns the required action, request it through a permitted handoff.",
                        }
                    )
                    if attempt == min(3, remaining_calls) - 1:
                        raise
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
        self.last_prompt_payload = input_log.content
        self.last_response_content = (
            output_log.content if output_log is not None else None
        )
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
        self.intent_kind_counts[intent.kind.value] += 1
        if intent.kind == IntentKind.FINISH:
            self.complete = True
        return intent.model_copy(update={"llm_input_log_id": self.last_input_log_id})

    def observe(self, result: Any) -> None:
        self._remember_field_work(result)
        self._remember_failure(result)
        if isinstance(result, dict) and result.get("selected_action"):
            receipt = result.get("execution_receipt") or {}
            if (
                receipt.get("status") == "accepted"
                and result.get("intent_kind") == "act"
            ):
                self.accepted_write_receipts.append(receipt)
                for failure in self.recent_failures:
                    if failure.get("selected_action") == result[
                        "selected_action"
                    ] and failure.get(
                        "arguments", failure.get("args", {})
                    ) == result.get("arguments", result.get("args", {})):
                        failure["accepted_retry"] = {
                            "receipt_digest": receipt.get("receipt_digest"),
                            "intent_id": result.get("intent_id"),
                            "result_world_time": result.get("result_world_time"),
                        }
            if result.get("error") or result.get("executed") is False:
                self.recent_failures.append(
                    {
                        key: value
                        for key, value in result.items()
                        if key
                        in {
                            "selected_action",
                            "arguments",
                            "args",
                            "error",
                            "executed",
                            "guard_reasons",
                            "guard_verdict",
                            "execution_receipt",
                            "intent_id",
                            "result_world_time",
                            "season_phase",
                        }
                    }
                )
        self.base_agent.append_agent_log(
            ObservationLog(
                content="D-CORE runtime result: "
                + json.dumps(result, sort_keys=True, default=str),
                timestamp=self.base_agent.make_timestamp(),
                agent_id=self.base_agent.agent_id,
            )
        )

    def _remember_failure(self, result: Any) -> None:
        if not isinstance(result, dict) or not result.get("selected_action"):
            return
        if not hasattr(self, "persistent_failures"):
            self.persistent_failures = {}
        action = str(result["selected_action"])
        receipt = result.get("execution_receipt") or {}
        if receipt.get("status") == "accepted" and result.get("executed") is True:
            accepted_arguments = dict(result.get("arguments", result.get("args", {})))
            for row in self.persistent_failures.values():
                if (
                    row["action"] == action
                    and row["active"]
                    and row.get("last_arguments") == accepted_arguments
                ):
                    row["active"] = False
                    row["recovered_by_receipt_digest"] = receipt.get("receipt_digest")
                    row["recovered_at_world_time"] = result.get("result_world_time")
                    row["recovery_mode"] = "exact_arguments"
            # A rejected over-wide range can only be repaired by legal batches.
            # Mark it recovered only after accepted receipts cover every ridge in
            # the originally requested range; one unrelated batch is insufficient.
            memory = getattr(self, "accepted_field_work", {})
            for row in self.persistent_failures.values():
                arguments = row.get("last_arguments", {})
                start = arguments.get("start_ridge")
                end = arguments.get("end_ridge")
                if not (
                    row["action"] == action
                    and row["active"]
                    and type(start) is int
                    and type(end) is int
                    and 0 <= start <= end < 64
                    and all(
                        (action, ridge) in memory for ridge in range(start, end + 1)
                    )
                ):
                    continue
                row["active"] = False
                row["recovered_by_receipt_digest"] = receipt.get("receipt_digest")
                row["recovered_at_world_time"] = result.get("result_world_time")
                row["recovery_mode"] = "accepted_scope_coverage"
            return
        if not (result.get("error") or result.get("executed") is False):
            return
        guard_reasons = tuple(str(item) for item in result.get("guard_reasons", ()))
        error = str(
            result.get("error")
            or ("; ".join(guard_reasons) if guard_reasons else None)
            or "execution_not_accepted"
        )
        arguments = dict(result.get("arguments", result.get("args", {})))
        key = (action, stable_digest(arguments), error)
        if key not in self.persistent_failures and len(self.persistent_failures) >= 24:
            del self.persistent_failures[next(iter(self.persistent_failures))]
        row = self.persistent_failures.setdefault(
            key,
            {
                "action": action,
                "error": error,
                "count": 0,
                "first_world_time": result.get("result_world_time"),
            },
        )
        row.update(
            count=row["count"] + 1,
            active=True,
            last_arguments=arguments,
            last_world_time=result.get("result_world_time"),
            source_receipt_digest=receipt.get("receipt_digest"),
            recovered_by_receipt_digest=None,
            recovered_at_world_time=None,
        )
        if guard_reasons:
            row["guard_reasons"] = guard_reasons
        if result.get("guard_verdict") is not None:
            row["guard_verdict"] = result["guard_verdict"]
        if result.get("season_phase") is not None:
            row["season_phase"] = result["season_phase"]

    def _failure_memory(self) -> list[dict[str, Any]]:
        return sorted(
            getattr(self, "persistent_failures", {}).values(),
            key=lambda row: (row["active"], row.get("last_world_time") or 0),
        )

    def _remember_field_work(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        receipt = result.get("execution_receipt") or {}
        action = receipt.get("action")
        postharvest_actions = {
            "TractorApp__unload_grain",
            "FarmWorldApp__dry_grain",
            "FarmWorldApp__store_grain",
        }
        if not hasattr(self, "accepted_postharvest_work"):
            self.accepted_postharvest_work = {}
        if action in postharvest_actions:
            if (
                receipt.get("status") == "accepted"
                and result.get("executed") is True
                and not result.get("error")
                and receipt.get("actor_id") == self.base_agent.agent_id
                and receipt.get("intent_id") == result.get("intent_id")
                and action == result.get("selected_action")
                and receipt.get("arguments", {})
                == result.get("arguments", result.get("args", {}))
                and receipt.get("receipt_digest")
            ):
                # These receipts form an ordered completion chain. New trailer
                # contents invalidate drying/storage for the previous batch;
                # new drying invalidates a previous store receipt. Keeping an
                # ever-succeeded Boolean here made later harvests look fully
                # postharvest-complete in the agent's final prompt.
                downstream = {
                    "TractorApp__unload_grain": {
                        "FarmWorldApp__dry_grain",
                        "FarmWorldApp__store_grain",
                    },
                    "FarmWorldApp__dry_grain": {"FarmWorldApp__store_grain"},
                    "FarmWorldApp__store_grain": set(),
                }[action]
                for stale_action in downstream:
                    self.accepted_postharvest_work.pop(stale_action, None)
                self.accepted_postharvest_work[action] = {
                    "action": action,
                    "arguments": dict(receipt.get("arguments", {})),
                    "receipt_digest": receipt["receipt_digest"],
                    "result_world_time": result.get("result_world_time"),
                }
            return
        if action not in {
            "TractorApp__plant_seeds",
            "TractorApp__replant_seeds",
            "TractorApp__harvest",
        }:
            return
        args = receipt.get("arguments", {})
        start, end = args.get("start_ridge"), args.get("end_ridge")
        if not (
            receipt.get("status") == "accepted"
            and result.get("executed") is True
            and not result.get("error")
            and receipt.get("actor_id") == self.base_agent.agent_id
            and receipt.get("intent_id") == result.get("intent_id")
            and action == result.get("selected_action")
            and args == result.get("arguments")
            and receipt.get("receipt_digest")
            and type(start) is int
            and type(end) is int
            and 0 <= start <= end < 64
        ):
            return
        if not hasattr(self, "accepted_field_work"):
            self.accepted_field_work = {}
        record = {
            "action": action,
            "arguments": dict(args),
            "receipt_digest": receipt["receipt_digest"],
            "result_world_time": result.get("result_world_time"),
        }
        if action == "TractorApp__harvest":
            # Grain has re-entered the combine. Any unload/dry/store sequence
            # completed before this receipt no longer proves that all current
            # grain has reached safe storage.
            self.accepted_postharvest_work.clear()
        for ridge in range(start, end + 1):
            self.accepted_field_work[action, ridge] = record

    def _field_work_memory(self) -> list[dict[str, Any]]:
        records = {
            row["receipt_digest"]: row
            for row in getattr(self, "accepted_field_work", {}).values()
        }
        return list(records.values()) + list(
            getattr(self, "accepted_postharvest_work", {}).values()
        )

    def _field_work_coverage(self) -> dict[str, Any]:
        """Summarize this actor's accepted receipts without inferring world state."""

        def ranges(values: set[int]) -> list[list[int]]:
            if not values:
                return []
            ordered = sorted(values)
            output: list[list[int]] = []
            start = previous = ordered[0]
            for ridge in ordered[1:]:
                if ridge != previous + 1:
                    output.append([start, previous])
                    start = ridge
                previous = ridge
            output.append([start, previous])
            return output

        permitted = set(getattr(getattr(self, "actor_spec", None), "tool_schemas", {}))
        if not permitted and getattr(self.base_agent, "agent_id", None) == "operations":
            permitted = {
                "TractorApp__plant_seeds",
                "TractorApp__replant_seeds",
                "TractorApp__harvest",
            }
        memory = getattr(self, "accepted_field_work", {})
        rows: dict[str, Any] = {}
        work = {
            "planting": {
                "TractorApp__plant_seeds",
                "TractorApp__replant_seeds",
            },
            "harvest": {"TractorApp__harvest"},
        }
        full_scope = set(range(64))
        for label, actions in work.items():
            if not (actions & permitted):
                continue
            accepted = {ridge for (action, ridge) in memory if action in actions}
            rows[label] = {
                "accepted_ridge_count": len(accepted),
                "accepted_ranges": ranges(accepted),
                "missing_receipt_ranges": ranges(full_scope - accepted),
            }
        postharvest = getattr(self, "accepted_postharvest_work", {})
        postharvest_tools = {
            "unload": "TractorApp__unload_grain",
            "dry": "FarmWorldApp__dry_grain",
            "store": "FarmWorldApp__store_grain",
        }
        if set(postharvest_tools.values()) & permitted:
            rows["postharvest"] = {
                name: action in postharvest
                for name, action in postharvest_tools.items()
                if action in permitted
            }
        return {
            "task_ridge_scope": [0, 63],
            "basis": "this actor's accepted native receipts only",
            "operations": rows,
        }

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
        if getattr(self, "season_start_world_time", None) is None:
            self.season_start_world_time = local_view.world_time
        requests_used = self._model_call_count()
        elapsed_days = max(
            0.0, (local_view.world_time - self.season_start_world_time) / 86400
        )
        frontier = knowledge_frontier(local_view.knowledge)
        # Prefer scoped typed facts and native receipts over large raw tool
        # observations. Stable tie-breaks make omissions reproducible.
        prioritized = sorted(
            frontier,
            key=lambda item: (
                0
                if item.fact_key.startswith("tool_observation:")
                else 1
                if isinstance(item.value, dict)
                else 2,
                *_knowledge_prompt_order(item),
            ),
        )
        forced_ids = set(getattr(self, "forced_prompt_item_ids", ()))
        forced = [item for item in local_view.knowledge if item.item_id in forced_ids]
        visible = [
            item
            for item in prioritized[-self.knowledge_window :]
            if item.item_id not in forced_ids
        ] + forced
        self.forced_prompt_item_ids = ()
        self.last_prompt_item_ids = tuple(item.item_id for item in visible)
        visible_messages = local_view.inbox[-self.message_window :]
        self.last_prompt_message_ids = tuple(
            message.message_id for message in visible_messages
        )
        payload = {
            "actor_id": local_view.actor.actor_id,
            "role": local_view.actor.role,
            "world_time": local_view.world_time,
            "world_date_utc": datetime.fromtimestamp(
                local_view.world_time, timezone.utc
            ).isoformat(),
            "knowledge": [item.model_dump(mode="json") for item in visible],
            "knowledge_store_size": len(local_view.knowledge),
            "knowledge_frontier_complete": len(frontier) <= self.knowledge_window,
            "delivered_messages": [
                _compact_prompt_message(message) for message in visible_messages
            ],
            "delivered_message_count": len(local_view.inbox),
            "message_frontier_complete": len(local_view.inbox) <= self.message_window,
            "unresolved_requirements": list(local_view.unresolved),
            "historical_accepted_field_work": self._field_work_memory(),
            "accepted_field_work_coverage": self._field_work_coverage(),
            "persistent_action_failures": self._failure_memory(),
            "season_cadence": {
                "world_days_elapsed": round(elapsed_days, 3),
                "actor_requests_used": requests_used,
                "intent_kind_counts": dict(
                    sorted(getattr(self, "intent_kind_counts", {}).items())
                ),
                "world_days_per_actor_request": (
                    round(elapsed_days / requests_used, 4) if requests_used else None
                ),
            },
            "remaining_actor_requests": max(0, self.max_model_calls - requests_used),
            "recent_accepted_write_receipts": list(
                getattr(self, "accepted_write_receipts", ())
            ),
            "recent_rejections_may_have_later_recovery": list(
                getattr(self, "recent_failures", ())
            ),
        }
        from are.simulation.distributed.pilot_budget import deterministic_prompt_units

        omitted_items = [
            item.item_id
            for item in prioritized[: -self.knowledge_window]
            if item.item_id not in forced_ids
        ]
        omitted_messages = [
            item.message_id for item in local_view.inbox[: -self.message_window]
        ]
        # Current scoped evidence is the most important mutable input.  Remove
        # redundant historical detail before evidence; complete records remain
        # in the immutable trace and durable journal.
        while (
            deterministic_prompt_units(payload) > PROMPT_CONTEXT_TARGET_TOKENS
            and len(payload["delivered_messages"]) > 4
        ):
            omitted_messages.append(payload["delivered_messages"].pop(0)["message_id"])
        omitted_receipts: list[str | None] = []
        while (
            deterministic_prompt_units(payload) > PROMPT_CONTEXT_TARGET_TOKENS
            and payload["recent_accepted_write_receipts"]
        ):
            omitted_receipts.append(
                payload["recent_accepted_write_receipts"].pop(0).get("receipt_digest")
            )
        while (
            deterministic_prompt_units(payload) > PROMPT_CONTEXT_TARGET_TOKENS
            and payload["historical_accepted_field_work"]
        ):
            omitted_receipts.append(
                payload["historical_accepted_field_work"].pop(0).get("receipt_digest")
            )
        while deterministic_prompt_units(
            payload
        ) > PROMPT_CONTEXT_TARGET_TOKENS and any(
            not item.get("active", False)
            for item in payload["persistent_action_failures"]
        ):
            removable = next(
                index
                for index, item in enumerate(payload["persistent_action_failures"])
                if not item.get("active", False)
            )
            omitted_receipts.append(
                payload["persistent_action_failures"]
                .pop(removable)
                .get("source_receipt_digest")
            )
        # Keep the four most recent active failures.  Earlier active failures
        # remain in controller memory and the trace, but must not evict all
        # current evidence from the next decision prompt.
        while (
            deterministic_prompt_units(payload) > PROMPT_CONTEXT_TARGET_TOKENS
            and len(payload["persistent_action_failures"]) > 4
        ):
            omitted_receipts.append(
                payload["persistent_action_failures"]
                .pop(0)
                .get("source_receipt_digest")
            )
        while (
            deterministic_prompt_units(payload) > PROMPT_CONTEXT_TARGET_TOKENS
            and payload["recent_rejections_may_have_later_recovery"]
        ):
            omitted_receipts.append(
                (
                    payload["recent_rejections_may_have_later_recovery"]
                    .pop(0)
                    .get("execution_receipt")
                    or {}
                ).get("receipt_digest")
            )
        # A current request can determine which observation is useful.  Retain
        # the newest delivery while reducing older messages before evidence.
        while (
            deterministic_prompt_units(payload) > PROMPT_CONTEXT_TARGET_TOKENS
            and len(payload["delivered_messages"]) > 1
        ):
            omitted_messages.append(payload["delivered_messages"].pop(0)["message_id"])
        while (
            deterministic_prompt_units(payload) > PROMPT_CONTEXT_TARGET_TOKENS
            and payload["knowledge"]
            and payload["knowledge"][0]["fact_key"].startswith("tool_observation:")
            and payload["knowledge"][0]["item_id"] not in forced_ids
        ):
            omitted_items.append(payload["knowledge"].pop(0)["item_id"])
        while deterministic_prompt_units(
            payload
        ) > PROMPT_CONTEXT_TARGET_TOKENS and any(
            item["item_id"] not in forced_ids for item in payload["knowledge"]
        ):
            removable = next(
                index
                for index, item in enumerate(payload["knowledge"])
                if item["item_id"] not in forced_ids
            )
            omitted_items.append(payload["knowledge"].pop(removable)["item_id"])
        while (
            deterministic_prompt_units(payload) > PROMPT_CONTEXT_TARGET_TOKENS
            and payload["persistent_action_failures"]
        ):
            omitted_receipts.append(
                payload["persistent_action_failures"]
                .pop(0)
                .get("source_receipt_digest")
            )
        self.last_prompt_omissions = {
            "item_ids": tuple(omitted_items),
            "message_ids": tuple(omitted_messages),
            "receipt_digests": tuple(omitted_receipts),
        }
        # Full omission identities belong to the immutable decision trace.
        # Repeating an unbounded ID list would itself exhaust the prompt.
        payload["omitted_evidence"] = {
            "counts": {
                key: len(ids) for key, ids in self.last_prompt_omissions.items()
            },
            "digest": stable_digest(self.last_prompt_omissions),
        }
        if omitted_items:
            payload["knowledge_frontier_complete"] = False
        if omitted_messages:
            payload["message_frontier_complete"] = False
        self.last_prompt_item_ids = tuple(
            item["item_id"] for item in payload["knowledge"]
        )
        self.last_prompt_message_ids = tuple(
            item["message_id"] for item in payload["delivered_messages"]
        )
        rendered = (
            "Choose exactly one role-owned tool from this actor-local state. "
            "Facts not listed are unknown. The tool only proposes the intent; "
            "D-CORE will return the authoritative execution result.\n"
            "Your assignment continues through harvest and safe storage. An empty "
            "unresolved_requirements list means no reported handoff requirements; "
            "it is not a season-completion certificate. Observers remain available "
            "for new observations as the season progresses. dcore_finish is permanent. "
            "dcore_wait only defers scheduling and cannot grow crops or change weather. "
            "When waiting for a future farm date, the clock owner must explicitly "
            "advance farm time; other roles can request this through a permitted handoff. "
            "Use observation and result times: retained failures are historical, "
            "not new observations. Continue the stated farm task using fresh evidence.\n"
            "Historical accepted field work records only this actor's executed "
            "planting/replanting/harvest requests, with exact scope and provenance. "
            "It does not establish current crop state or work by teammates. "
            "The coverage summary compresses those same receipts; missing receipt "
            "ranges are unfinished or unverified, not hidden world state. "
            "Reconcile the full task scope with these receipts and fresh observations; "
            "a calendar phase does not certify that earlier work was completed. "
            "If this role owns planting and its planting receipt coverage has gaps, "
            "prioritize completing those ranges or repairing and rechecking the exact "
            "current native blocker before later-season monitoring, treatment or "
            "harvest work. Any farm-time advance while planting is incomplete must be "
            "a bounded response to that blocker, not a jump to a later season phase. "
            "Plan observation frequency and explicit time advances within remaining "
            "requests while retaining checks for consequential decisions. Use the "
            "cadence summary to cover the whole season: when only calendar progression "
            "remains, choose a justified multi-day advance instead of defaulting to one "
            "day per request. Communication is for missing or newly changed evidence "
            "needed by a teammate's pending decision. A role that owns farm actions and "
            "the clock should request only evidence its teammate can observe, and should "
            "not narrate its own actions or receipts. An observing role should send new "
            "decision-relevant evidence or answer a specific request, and should not "
            "repeatedly announce unfinished work already visible in the recipient's own "
            "receipts. When nothing relevant changed, wait instead of sending another "
            "message. Use intent_kind_counts to notice if communication is displacing "
            "role-owned work. Avoid repeating unchanged status reads, observations or "
            "handoffs. Do not repeat an active persistent failure until a recovery action "
            "or fresh relevant evidence makes success plausible. dcore_finish is valid "
            "only when accepted results and coverage establish that all work owned by "
            "this role is complete; an active range failure remains unresolved until "
            "the same request succeeds or accepted receipts cover its entire scope.\n"
            "For a causal handoff about a specific observation, pass its exact visible "
            "knowledge item_id in dcore_send.claim_item_ids. Use claim_fact_keys only "
            "when every current regional version of that fact is intentionally needed. "
            "Do not describe one scope while attaching evidence from other scopes.\n"
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
                "Send a handoff using exact local evidence item IDs when the message "
                "concerns a specific observation. Fact-key selection intentionally "
                "transmits every current regional version of that fact. The configured "
                "condition determines free-text or causal encoding."
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
                    "description": "JSON list of fact keys whose current regional versions must all be transmitted",
                },
                "claim_item_ids": {
                    "type": "any",
                    "description": "JSON list of exact item_id values from visible local knowledge",
                },
                "unresolved_requirements": {
                    "type": "any",
                    "description": "JSON list of unresolved requirements",
                },
            },
        )
        tools["dcore_wait"] = _IntentCaptureTool(
            name="dcore_wait",
            description=(
                "Remain responsible for your season-long role while deferring the next "
                "activation by the requested scheduler interval. A new handoff may "
                "wake you earlier. This does not advance farm time. Use when there "
                "is no immediate work but later observations or actions remain."
            ),
            inputs={"wait": {"type": "number", "description": "nonnegative interval"}},
        )
        tools["dcore_finish"] = _IntentCaptureTool(
            name="dcore_finish",
            description=(
                "Permanently end this actor for the entire season, with no later "
                "activations. Use only when all seasonal duties are complete or "
                "you deliberately abandon them (which remains a failed season). "
                "Completing the current observation or handoff does not complete a "
                "season-long role. Active failures and missing accepted field-work "
                "coverage are not completion."
            ),
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
            claim_item_ids = arguments.get("claim_item_ids", [])
            unresolved = arguments.get("unresolved_requirements", [])
            return AgentIntent(
                kind=IntentKind.SEND,
                recipient=str(arguments.get("recipient", "")) or None,
                recipients=tuple(str(item) for item in arguments.get("recipients", ())),
                text=str(arguments.get("text", "")),
                claim_fact_keys=tuple(str(item) for item in claim_keys or ()),
                claim_item_ids=tuple(str(item) for item in claim_item_ids or ()),
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
