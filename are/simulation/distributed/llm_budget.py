"""Run-scoped aggregate LLM budgets shared by main and specialist agents."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
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

    def before_call(self) -> None:
        if self.calls >= self.max_calls or (
            self.max_tokens is not None and self.tokens >= self.max_tokens
        ):
            self.exhausted = True
            raise RuntimeError("team LLM budget exhausted before next model call")
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
        self.budget.before_call()
        response = self.engine.chat_completion(messages, stop_sequences, **kwargs)
        metadata = (
            response[1] if isinstance(response, tuple) and len(response) == 2 else None
        )
        self.budget.after_call(metadata)
        return response

    def simple_call(self, prompt: str) -> str:
        self.budget.before_call()
        response = self.engine.simple_call(prompt)
        self.budget.after_call(None)
        return response


def wrap_with_active_budget(engine: LLMEngine) -> LLMEngine:
    budget = _ACTIVE_BUDGET.get()
    return BudgetedLLMEngine(engine, budget) if budget is not None else engine


@contextmanager
def team_llm_budget(max_calls: int, max_tokens: int | None) -> Iterator[TeamLLMBudget]:
    budget = TeamLLMBudget(max_calls=max_calls, max_tokens=max_tokens)
    token = _ACTIVE_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE_BUDGET.reset(token)


__all__ = ["TeamLLMBudget", "team_llm_budget", "wrap_with_active_budget"]
