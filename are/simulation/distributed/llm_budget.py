"""Run-scoped aggregate LLM budgets shared by main and specialist agents."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from are.simulation.agents.llm.llm_engine import LLMEngine


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
        metadata = response[1] if isinstance(response, tuple) and len(response) == 2 else None
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
