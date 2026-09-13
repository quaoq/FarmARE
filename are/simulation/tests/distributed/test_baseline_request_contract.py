"""Regressions from bounded live A2A compatibility traces."""

import json
from types import SimpleNamespace

from are.simulation.agents.default_agent.tools.action_executor import BaseActionExecutor
from are.simulation.distributed.llm_budget import team_llm_budget


def test_structured_specialist_final_answer_keeps_values_without_attribute_error():
    logs = []
    answer = {"scope": [20, 43], "moisture": 0.22, "unit": "m3/m3", "refused": False}
    BaseActionExecutor._append_final_answer(
        None, answer, logs.append, lambda: 123, "specialist"
    )
    assert json.loads(logs[0].content) == answer
    assert logs[0].agent_id == "specialist"


def test_same_generation_settings_reach_main_and_nested_provider_calls():
    received = []

    def provider(**kwargs):
        received.append(kwargs)
        return SimpleNamespace(usage={"prompt_tokens": 10, "completion_tokens": 5})

    with team_llm_budget(2, 100000) as budget:
        budget.request_parameters = {"temperature": 0.0, "max_completion_tokens": 1024}
        for name in ["main", "specialist"]:
            budget.provider_call(
                provider, model=name, messages=[], temperature=0.1, max_tokens=8192
            )
        assert budget.calls == 2 and budget.tokens == 30
    assert all(
        r["temperature"] == 0.0
        and r["max_completion_tokens"] == 1024
        and "max_tokens" not in r
        for r in received
    )
    with team_llm_budget(1, 100000) as legacy:
        legacy.provider_call(
            provider, model="legacy", messages=[], temperature=0.1, max_tokens=8192
        )
    assert received[-1]["temperature"] == 0.1 and received[-1]["max_tokens"] == 8192
