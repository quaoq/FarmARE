"""Measured cache discounts cannot reduce reservations or rewrite old charges."""

import sqlite3
from types import SimpleNamespace

import pytest

from are.simulation.distributed.pilot_budget import (
    MODEL,
    RequestContext,
    SpendingLedger,
)


@pytest.mark.parametrize(
    "cached,expected,recorded",
    [
        (800, 660, 800),
        (0, 1200, 0),
        (None, 1200, None),
        (1001, 1200, None),
        (-1, 1200, None),
        ("800", 1200, None),
    ],
)
def test_cache_settlement_uses_only_valid_provider_report(
    tmp_path, cached, expected, recorded
):
    context = RequestContext(
        tmp_path / "spend.sqlite", "development", "run", 10, 100000, 10, 100000, 100
    )
    ledger = SpendingLedger(context.ledger)
    request = ledger.reserve(context, model=MODEL, input_bound=1000)
    before = ledger.summary()["requests"][0]["reserved_microusd"]
    assert before == 1200
    ledger.settle(
        request,
        SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=100,
            prompt_tokens_details={"cached_tokens": cached},
        ),
    )
    row = ledger.summary()["requests"][0]
    assert row["reserved_microusd"] == before
    assert row["charged_microusd"] == expected
    assert row["cached_prompt_tokens"] == recorded


def test_migration_preserves_historical_charges_and_unknown_cache(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE requests (request_id TEXT PRIMARY KEY, pool TEXT, run_id TEXT, actor TEXT, model TEXT, reserved_microusd INTEGER, charged_microusd INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, status TEXT, created_utc TEXT)"
        )
        db.execute(
            "INSERT INTO requests VALUES ('old','development','historical','actor',?,1200,1200,1000,100,'settled','2026-09-13T00:00:00Z')",
            (MODEL,),
        )
    ledger = SpendingLedger(path)
    row = ledger.summary()["requests"][0]
    assert (
        row["charged_microusd"] == 1200
        and row["cached_prompt_tokens"] is None
        and row["cost_basis"] is None
    )
    assert SpendingLedger(path).summary()["requests"] == [row]
