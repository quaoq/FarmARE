from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from are.simulation.distributed.pilot_budget import (
    _CONTEXT,
    MODEL,
    RequestBudgetExceeded,
    RequestContext,
    SpendingLedger,
    actor_request_scope,
    provider_completion,
)


@pytest.fixture
def context(tmp_path):
    return RequestContext(
        tmp_path / "spend.sqlite", "development", "run", 10, 1000000, 5, 500000, 100
    )


def test_underlying_calls_and_unknown_usage_survive_resume(context):
    ledger = SpendingLedger(context.ledger)
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise TimeoutError("uncertain response")
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        )

    token = _CONTEXT.set(replace(context, max_calls=2))
    try:
        with actor_request_scope("planner"):
            provider_completion(provider, model=MODEL, messages=[], max_tokens=2000)
        with pytest.raises(TimeoutError):
            provider_completion(provider, model=MODEL, messages=[])
        with pytest.raises(RequestBudgetExceeded):
            provider_completion(provider, model=MODEL, messages=[])
    finally:
        _CONTEXT.reset(token)
    rows = SpendingLedger(context.ledger).summary()["requests"]
    assert len(rows) == len(calls) == 2
    assert {r["status"] for r in rows} == {"settled", "usage_unknown"}
    assert calls[0]["max_completion_tokens"] == 100
    assert "max_tokens" not in calls[0]
    assert calls[0]["num_retries"] == 0
    assert ledger.summary()["accounted_usd"] > 0


def test_token_reservations_block_overshoot_and_unknown_retry(context):
    ledger = SpendingLedger(context.ledger)
    context = replace(context, max_tokens=1000)
    request = ledger.reserve(context, model=MODEL, input_bound=800)
    ledger.fail(request)
    with pytest.raises(RequestBudgetExceeded):
        ledger.reserve(context, model=MODEL, input_bound=100)
    assert len(ledger.summary()["requests"]) == 1


def test_concurrent_reservations_cannot_exceed_pool(context):
    ledger = SpendingLedger(context.ledger)
    context = replace(
        context, max_calls=100, actor_calls=None, max_tokens=10**10, actor_tokens=None
    )

    def reserve(_):
        try:
            return ledger.reserve(context, model=MODEL, input_bound=20_000_000)
        except RequestBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(r is not None for r in results) == 2
    assert ledger.summary()["accounted_usd"] <= 40
    confirmation = replace(context, pool="confirmation", run_id="confirmation")
    ledger.reserve(confirmation, model=MODEL, input_bound=20_000_000)


def test_actor_limits_do_not_hide_specialist_calls(context):
    ledger = SpendingLedger(context.ledger)
    context = replace(context, actor_calls=1)
    with actor_request_scope("observer"):
        ledger.reserve(context, model=MODEL, input_bound=10)
        with pytest.raises(RequestBudgetExceeded):
            ledger.reserve(context, model=MODEL, input_bound=10)
    with actor_request_scope("operations"):
        ledger.reserve(context, model=MODEL, input_bound=10)
    assert {r["actor"] for r in ledger.summary()["requests"]} == {
        "observer",
        "operations",
    }


def test_prompt_rejection_makes_no_paid_request(context):
    token = _CONTEXT.set(replace(context, max_prompt_tokens=1))
    try:
        with pytest.raises(RequestBudgetExceeded):
            provider_completion(
                lambda **kwargs: pytest.fail("provider was called"),
                model=MODEL,
                messages=[],
            )
    finally:
        _CONTEXT.reset(token)
    assert SpendingLedger(context.ledger).summary()["requests"] == []
