"""Run-scoped aggregate LLM budgets shared by main and specialist agents."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from are.simulation.agents.llm.llm_engine import LLMEngine


def summarize_budget_usage(
    usage_by_actor: dict[str, dict[str, Any]],
    *,
    max_calls: int,
    max_tokens: int | None,
    per_agent_calls: dict[str, int],
    per_agent_tokens: dict[str, int],
) -> dict[str, Any]:
    """Report both team and actor caps from recorded usage, without rerunning.

    A role may reach its allocation while the other role finishes early. The
    combined budget flag must retain that truncation even below the team cap.
    This reports cap attainment, not the controller's motivation for finishing.
    """
    actor_count = max(1, len(usage_by_actor))
    actors = {}
    for actor, usage in usage_by_actor.items():
        calls = int(usage.get("model_call_count", 0))
        tokens = int(usage.get("total_tokens", 0))
        call_cap = per_agent_calls.get(actor, max(1, max_calls // actor_count))
        token_cap = per_agent_tokens.get(
            actor, max(1, max_tokens // actor_count) if max_tokens is not None else None
        )
        actors[actor] = {
            "model_calls": calls,
            "total_tokens": tokens,
            "call_budget": call_cap,
            "token_budget": token_cap,
            "call_budget_exhausted": calls >= call_cap,
            "token_budget_exhausted": token_cap is not None and tokens >= token_cap,
            "token_budget_overshoot": max(0, tokens - token_cap)
            if token_cap is not None
            else None,
        }
    total_calls = sum(a["model_calls"] for a in actors.values())
    total_tokens = sum(a["total_tokens"] for a in actors.values())
    team_calls = total_calls >= max_calls
    team_tokens = max_tokens is not None and total_tokens >= max_tokens
    return {
        "call_budget_exhausted": team_calls
        or any(a["call_budget_exhausted"] for a in actors.values()),
        "token_budget_exhausted": team_tokens
        or any(a["token_budget_exhausted"] for a in actors.values()),
        "team_call_budget_exhausted": team_calls,
        "team_token_budget_exhausted": team_tokens,
        "token_budget_overshoot": max(0, total_tokens - max_tokens)
        if max_tokens is not None
        else None,
        "per_agent_budget_status": actors,
        "token_budget_policy": "stop_before_next_model_call",
    }


@dataclass
class TeamLLMBudget:
    max_calls: int
    max_tokens: int | None = None
    calls: int = 0
    tokens: int = 0
    exhausted: bool = False
    provider_records: list[dict[str, Any]] = field(default_factory=list)
    per_actor_calls: dict[str, int] = field(default_factory=dict)
    per_actor_tokens: dict[str, int] = field(default_factory=dict)
    request_parameters: dict[str, Any] = field(default_factory=dict)

    def provider_call(self, completion, **kwargs):
        """Account raw usage before adapters parse or reject the response."""
        import json

        from are.simulation.distributed.pilot_budget import (
            RequestBudgetExceeded,
            current_request_actor,
        )

        if "max_completion_tokens" in self.request_parameters:
            kwargs.pop("max_tokens", None)
        kwargs.update(self.request_parameters)
        output_cap = int(
            kwargs.get("max_completion_tokens") or kwargs.get("max_tokens") or 4096
        )
        reserve = (
            len(json.dumps(kwargs.get("messages", []), ensure_ascii=False).encode())
            + 4096
            + output_cap
        )
        if self.max_tokens is not None and self.tokens + reserve > self.max_tokens:
            self.exhausted = True
            raise RequestBudgetExceeded(
                "team token reservation exceeds remaining allocation"
            )
        actor = current_request_actor()
        actor_prefix = actor.split(":", 1)[0]
        purpose = (
            actor_prefix
            if actor_prefix
            in {
                "verifier",
                "diagnosis",
                "repair_selection",
                "reconsideration",
                "continuation",
            }
            else "controller"
        )
        actor_records = [r for r in self.provider_records if r.get("actor") == actor]
        if (
            actor in self.per_actor_calls
            and len(actor_records) >= self.per_actor_calls[actor]
        ):
            raise RequestBudgetExceeded("actor provider-call allocation exhausted")
        actor_used = sum(
            (r["prompt_tokens"] + r["completion_tokens"])
            if r["status"] == "settled"
            else r["reserved_tokens"]
            for r in actor_records
        )
        if (
            actor in self.per_actor_tokens
            and actor_used + reserve > self.per_actor_tokens[actor]
        ):
            raise RequestBudgetExceeded(
                "actor token reservation exceeds remaining allocation"
            )
        self.before_call()
        record = {
            "actor": actor,
            "purpose": purpose,
            "status": "usage_unknown",
            "reserved_tokens": reserve,
            "prompt_tokens": None,
            "completion_tokens": None,
        }
        self.provider_records.append(record)
        self.tokens += reserve
        from are.simulation.distributed.journal import journal_provider_event
        from are.simulation.distributed.models import stable_digest

        provider_request_id = stable_digest(
            [actor, self.calls, len(self.provider_records), kwargs.get("messages", ())]
        )[:24]
        journal_provider_event(
            "provider_request_intent",
            {
                "provider_request_id": provider_request_id,
                "actor_id": actor,
                "purpose": purpose,
                "model": kwargs.get("model"),
                "messages": kwargs.get("messages", ()),
                "max_output_tokens": output_cap,
                "reserved_tokens": reserve,
                "usage_status": "pending",
            },
        )
        kwargs["num_retries"] = 0
        kwargs.setdefault("timeout", 60)
        try:
            response = completion(**kwargs)
        except RequestBudgetExceeded:
            # The persistent spending guard can reject before an HTTP request.
            self.tokens -= reserve
            self.calls -= 1
            self.provider_records.remove(record)
            journal_provider_event(
                "provider_error_receipt",
                {
                    "provider_request_id": provider_request_id,
                    "actor_id": actor,
                    "purpose": purpose,
                    "error_type": "RequestBudgetExceeded",
                    "usage_status": "rejected_before_provider_dispatch",
                },
            )
            raise
        except Exception as error:
            journal_provider_event(
                "provider_error_receipt",
                {
                    "provider_request_id": provider_request_id,
                    "actor_id": actor,
                    "purpose": purpose,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "usage_status": "unknown",
                },
            )
            raise
        usage = getattr(response, "usage", None)
        getter = (
            usage.get
            if isinstance(usage, dict)
            else lambda key: getattr(usage, key, None)
        )
        prompt, output = getter("prompt_tokens"), getter("completion_tokens")
        if type(prompt) is int and type(output) is int and prompt >= 0 and output >= 0:
            self.tokens += prompt + output - reserve
            record.update(
                status="settled", prompt_tokens=prompt, completion_tokens=output
            )
        serializable_response = (
            response.model_dump(mode="json")
            if hasattr(response, "model_dump")
            else str(response)
        )
        journal_provider_event(
            "provider_response_receipt",
            {
                "provider_request_id": provider_request_id,
                "actor_id": actor,
                "purpose": purpose,
                "response": serializable_response,
                "prompt_tokens": prompt,
                "completion_tokens": output,
                "usage_status": record["status"],
                "reserved_tokens": reserve,
            },
        )
        self.exhausted = self.calls >= self.max_calls or bool(
            self.max_tokens is not None and self.tokens >= self.max_tokens
        )
        return response

    def before_call(self) -> None:
        if self.calls >= self.max_calls or (
            self.max_tokens is not None and self.tokens >= self.max_tokens
        ):
            self.exhausted = True
            from are.simulation.distributed.pilot_budget import RequestBudgetExceeded

            raise RequestBudgetExceeded(
                "team LLM budget exhausted before next model call"
            )
        self.calls += 1

    def after_call(self, metadata: dict[str, Any] | None) -> None:
        self.tokens += int((metadata or {}).get("total_tokens", 0) or 0)
        self.exhausted = self.calls >= self.max_calls or bool(
            self.max_tokens is not None and self.tokens >= self.max_tokens
        )


_ACTIVE_BUDGET: ContextVar[TeamLLMBudget | None] = ContextVar(
    "farm_dcore_team_llm_budget", default=None
)


class BudgetedLLMEngine(LLMEngine):
    def __init__(self, engine: LLMEngine, budget: TeamLLMBudget):
        super().__init__(engine.model_name)
        self.engine = engine
        self.budget = budget

    def chat_completion(self, messages, stop_sequences=[], **kwargs):  # noqa: B006
        from are.simulation.distributed.pilot_budget import current_request_actor

        self.budget.before_call()
        response = self.engine.chat_completion(messages, stop_sequences, **kwargs)
        metadata = (
            response[1] if isinstance(response, tuple) and len(response) == 2 else None
        )
        self.budget.after_call(metadata)
        actor = current_request_actor()
        prompt_tokens = (metadata or {}).get("prompt_tokens")
        completion_tokens = (metadata or {}).get("completion_tokens")
        self.budget.provider_records.append(
            {
                "actor": actor,
                "purpose": (
                    actor.split(":", 1)[0]
                    if actor.split(":", 1)[0]
                    in {
                        "verifier",
                        "diagnosis",
                        "repair_selection",
                        "reconsideration",
                        "continuation",
                    }
                    else "controller"
                ),
                "status": (
                    "settled"
                    if type(prompt_tokens) is int and type(completion_tokens) is int
                    else "usage_unknown"
                ),
                "reserved_tokens": 0,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
        )
        return response

    def simple_call(self, prompt: str) -> str:
        self.budget.before_call()
        response = self.engine.simple_call(prompt)
        self.budget.after_call(None)
        return response


def wrap_with_active_budget(engine: LLMEngine) -> LLMEngine:
    budget = _ACTIVE_BUDGET.get()
    # LiteLLM and its JSON adapters are metered at the raw provider boundary,
    # including retries and simple_call; wrapping again would double count.
    from are.simulation.agents.llm.litellm.litellm_engine import LiteLLMEngine

    if isinstance(engine, LiteLLMEngine):
        return engine
    return BudgetedLLMEngine(engine, budget) if budget is not None else engine


def active_team_budget() -> TeamLLMBudget | None:
    return _ACTIVE_BUDGET.get()


@contextmanager
def team_llm_budget(max_calls: int, max_tokens: int | None) -> Iterator[TeamLLMBudget]:
    budget = TeamLLMBudget(max_calls=max_calls, max_tokens=max_tokens)
    token = _ACTIVE_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE_BUDGET.reset(token)


__all__ = ["TeamLLMBudget", "team_llm_budget", "wrap_with_active_budget"]
