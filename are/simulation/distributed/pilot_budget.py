"""Persistent, conservative reservations at the actual provider-request boundary.

No credentials, prompts or model output are stored. Failed requests retain their
reservation when usage is unknown. SQLite transactions serialize reservations
across processes; budgets cannot be reset by resuming the matrix.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

MODEL = "gpt-5.4-mini-2026-03-17"
# Pools remain useful accounting labels. Monetary stops were removed for the
# engineering miniature; request/token caps in each manifest remain mandatory
# scientific controls.
POOL_LIMITS = {"development": None, "confirmation": None}


class RequestBudgetExceeded(RuntimeError):
    """No provider request was made because a declared budget was exhausted."""


@dataclass
class RequestContext:
    ledger: Path
    pool: str
    run_id: str
    max_calls: int
    max_tokens: int
    actor_calls: int | None
    actor_tokens: int | None
    max_output_tokens: int
    max_prompt_tokens: int = 32768
    actor_call_allocations: dict[str, int] | None = None
    actor_token_allocations: dict[str, int] | None = None


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken
    from tiktoken.load import load_tiktoken_bpe

    mergeable_ranks = load_tiktoken_bpe(
        str(Path(__file__).parent / "data" / "o200k_base.tiktoken"),
        expected_hash="446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    )
    special_tokens = {"<|endoftext|>": 199999, "<|endofprompt|>": 200018}
    # This regex could be made more efficient. If I was the one working on this encoding, I would
    # have done a few other things differently too, e.g. I think you can allocate tokens more
    # efficiently across languages.
    pat_str = "|".join(
        [
            r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
            r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
            r"""\p{N}{1,3}""",
            r""" ?[^\s\p{L}\p{N}]+[\r\n/]*""",
            r"""\s*[\r\n]+""",
            r"""\s+(?!\S)""",
            r"""\s+""",
        ]
    )
    return tiktoken.Encoding(
        **{
            "name": "o200k_base",
            "pat_str": pat_str,
            "mergeable_ranks": mergeable_ranks,
            "special_tokens": special_tokens,
        }
    )


def estimate_tokens(value: Any) -> int:
    return (
        len(
            _encoding().encode(
                json.dumps(value, ensure_ascii=False, default=str),
                disallowed_special=(),
            )
        )
        + 1024
    )


_CONTEXT: ContextVar[RequestContext | None] = ContextVar("dcore_requests", default=None)
_ACTOR: ContextVar[str] = ContextVar("dcore_request_actor", default="baseline")


class SpendingLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, pool TEXT NOT NULL, run_id TEXT NOT NULL,
                actor TEXT NOT NULL, model TEXT NOT NULL, reserved_microusd INTEGER NOT NULL,
                charged_microusd INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
                status TEXT NOT NULL DEFAULT 'reserved', created_utc TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(requests)")}
            if "reserved_tokens" not in columns:
                db.execute(
                    "ALTER TABLE requests ADD COLUMN reserved_tokens INTEGER NOT NULL DEFAULT 0"
                )
            for name, kind in (
                ("cached_prompt_tokens", "INTEGER"),
                ("cost_basis", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE requests ADD COLUMN {name} {kind}")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def reserve(self, context: RequestContext, *, model: str, input_bound: int) -> str:
        if context.pool not in POOL_LIMITS:
            raise ValueError("unknown pilot budget pool")
        actor = _ACTOR.get()
        normalized_model = model.removeprefix("openai/")
        priced_model = normalized_model == MODEL
        reserve = (
            math.ceil(input_bound * 0.75 + context.max_output_tokens * 4.5)
            if priced_model
            else 0
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            calls, tokens = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(COALESCE(prompt_tokens+completion_tokens,reserved_tokens)),0) FROM requests WHERE run_id=?",
                (context.run_id,),
            ).fetchone()
            actor_calls, actor_tokens = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(COALESCE(prompt_tokens+completion_tokens,reserved_tokens)),0) FROM requests WHERE run_id=? AND actor=?",
                (context.run_id, actor),
            ).fetchone()
            request_tokens = input_bound + context.max_output_tokens
            actor_call_limit = (context.actor_call_allocations or {}).get(
                actor, context.actor_calls
            )
            actor_token_limit = (context.actor_token_allocations or {}).get(
                actor, context.actor_tokens
            )
            if (
                calls >= context.max_calls
                or tokens + request_tokens > context.max_tokens
            ):
                raise RequestBudgetExceeded(
                    "team request budget exhausted before request"
                )
            if (actor_call_limit is not None and actor_calls >= actor_call_limit) or (
                actor_token_limit is not None
                and actor_tokens + request_tokens > actor_token_limit
            ):
                raise RequestBudgetExceeded(
                    "actor request budget exhausted before request"
                )
            identifier = uuid.uuid4().hex
            db.execute(
                "INSERT INTO requests(request_id,pool,run_id,actor,model,reserved_microusd,reserved_tokens,cost_basis) VALUES(?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    context.pool,
                    context.run_id,
                    actor,
                    model,
                    reserve,
                    request_tokens,
                    (
                        "pinned_price_reservation_v1"
                        if priced_model
                        else "unpriced_model_v1"
                    ),
                ),
            )
        return identifier

    def settle(self, identifier: str, usage: Any) -> None:
        if usage is None:
            self.fail(identifier)
            return

        def get(value, key):
            return (
                value.get(key) if isinstance(value, dict) else getattr(value, key, None)
            )

        prompt = get(usage, "prompt_tokens")
        completion = get(usage, "completion_tokens")
        if (
            type(prompt) is not int
            or type(completion) is not int
            or min(prompt, completion) < 0
        ):
            self.fail(identifier)
            return
        cached = get(get(usage, "prompt_tokens_details"), "cached_tokens")
        if type(cached) is not int or not 0 <= cached <= prompt:
            cached = None  # No measured discount: retain conservative input pricing.
        billed_cached = cached or 0
        # Standard GPT-5.4-mini rates verified 2026-09-13 at
        # https://developers.openai.com/api/docs/models/gpt-5.4-mini
        # Input/cached/output: $0.75/$0.075/$4.50 per million tokens.
        # Round upward to microdollars; reservations still assume no cache hit.
        charge = (
            (prompt - billed_cached) * 750
            + billed_cached * 75
            + completion * 4500
            + 999
        ) // 1000
        with self.connect() as db:
            reserved, model = db.execute(
                "SELECT reserved_microusd,model FROM requests WHERE request_id=?",
                (identifier,),
            ).fetchone()
            if str(model).removeprefix("openai/") != MODEL:
                db.execute(
                    "UPDATE requests SET charged_microusd=NULL,prompt_tokens=?,completion_tokens=?,status='settled_unpriced',cached_prompt_tokens=?,cost_basis='unpriced_model_v1' WHERE request_id=?",
                    (int(prompt), int(completion), cached, identifier),
                )
                return
            db.execute(
                "UPDATE requests SET charged_microusd=?,prompt_tokens=?,completion_tokens=?,status=?,cached_prompt_tokens=?,cost_basis=? WHERE request_id=?",
                (
                    charge,
                    int(prompt),
                    int(completion),
                    "settled" if charge <= reserved else "reservation_underestimated",
                    cached,
                    "reported_cache_v1"
                    if cached is not None
                    else "uncached_conservative_v1",
                    identifier,
                ),
            )

    def fail(self, identifier: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE requests SET status='usage_unknown' WHERE request_id=?",
                (identifier,),
            )

    def summary(self) -> dict[str, Any]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM requests ORDER BY created_utc,request_id"
                )
            ]
        unpriced = [r for r in rows if r.get("cost_basis") == "unpriced_model_v1"]
        return {
            "schema_version": "dcore_pilot_spend_v1",
            "ceiling_usd": None,
            "monetary_stop_enforced": False,
            "accounted_usd": sum(
                r["charged_microusd"]
                if r["charged_microusd"] is not None
                else r["reserved_microusd"]
                for r in rows
            )
            / 1e6,
            "cost_unknown_count": len(unpriced),
            "all_costs_known": not unpriced,
            "requests": rows,
        }


def current_request_actor() -> str:
    return _ACTOR.get()


@contextmanager
def actor_request_scope(actor: str):
    token = _ACTOR.set(actor)
    try:
        yield
    finally:
        _ACTOR.reset(token)


def require_pilot_request_scope() -> None:
    if _CONTEXT.get() is None:
        raise ValueError(
            "real engineering pilots must run from a manifest with a spending ledger"
        )


def current_request_usage() -> dict[str, Any] | None:
    context = _CONTEXT.get()
    if context is None:
        return None
    rows = [
        r
        for r in SpendingLedger(context.ledger).summary()["requests"]
        if r["run_id"] == context.run_id
    ]

    def purpose(row: dict[str, Any]) -> str:
        actor = str(row.get("actor", ""))
        prefix = actor.split(":", 1)[0]
        return (
            prefix
            if prefix
            in {
                "verifier",
                "diagnosis",
                "repair_selection",
                "reconsideration",
                "continuation",
            }
            else "controller"
            if actor
            else "unknown"
        )

    return {
        "accounting_basis": "provider_requests_v1",
        "provider_request_count": len(rows),
        "provider_prompt_tokens": sum(r["prompt_tokens"] or 0 for r in rows),
        "provider_completion_tokens": sum(r["completion_tokens"] or 0 for r in rows),
        "provider_usage_unknown_count": sum(
            r["charged_microusd"] is None for r in rows
        ),
        "provider_accounted_usd": sum(
            r["charged_microusd"]
            if r["charged_microusd"] is not None
            else r["reserved_microusd"]
            for r in rows
        )
        / 1e6,
        "provider_requests": rows,
        "provider_use_by_purpose": {
            label: sum(purpose(row) == label for row in rows)
            for label in sorted({purpose(row) for row in rows})
        },
    }


@contextmanager
def pilot_request_scope(row: dict[str, Any], run_dir: Path):
    if not row.get("engineering_llm_pilot"):
        yield
        return
    if not row.get("pilot_manifest_path"):
        raise ValueError("real engineering pilots require an explicit source manifest")
    ledger = row.get("pilot_budget_ledger")
    if not ledger:
        raise ValueError("manifest-backed engineering pilots require a spending ledger")
    actors = {"wetjune_2agent": 2, "wetjune_3agent": 3, "wetjune_4agent": 4}.get(
        row.get("team_id"), 2
    )
    native = row.get("execution") == "dcore"
    allocations = {}
    if native and row.get("team_spec_path"):
        from are.simulation.distributed.models import AgentTeamSpec

        team = AgentTeamSpec.model_validate_json(
            Path(row["team_spec_path"]).read_text()
        )
        # Scalar manifest overrides have the same precedence as load_team_spec.
        if not row.get("per_agent_call_budget"):
            allocations["actor_call_allocations"] = dict(team.per_agent_call_budget)
        if not row.get("per_agent_token_budget"):
            allocations["actor_token_allocations"] = dict(team.per_agent_token_budget)
    context = RequestContext(
        Path(ledger),
        row.get("pilot_budget_pool", "development"),
        str(run_dir.resolve()),
        int(row.get("team_call_budget") or row["max_model_calls"]),
        int(row["team_token_budget"]),
        (
            row.get("per_agent_call_budget")
            or max(
                1, int(row.get("team_call_budget") or row["max_model_calls"]) // actors
            )
        )
        if native
        else None,
        (
            row.get("per_agent_token_budget")
            or max(1, int(row["team_token_budget"]) // actors)
        )
        if native
        else None,
        int(row["max_output_tokens"]),
        int(row.get("max_prompt_tokens", 32768)),
        **allocations,
    )
    token = _CONTEXT.set(context)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def provider_completion(completion, **kwargs):
    from are.simulation.distributed.llm_budget import active_team_budget

    budget = active_team_budget()
    if budget is not None:
        return budget.provider_call(
            lambda **parameters: _pilot_provider_completion(completion, **parameters),
            **kwargs,
        )
    return _pilot_provider_completion(completion, **kwargs)


def _pilot_provider_completion(completion, **kwargs):
    context = _CONTEXT.get()
    if context is None:
        return completion(**kwargs)
    if estimate_tokens(kwargs.get("messages", [])) > context.max_prompt_tokens:
        raise RequestBudgetExceeded("prompt budget exceeded before provider request")
    # UTF-8 byte count bounds text tokens conservatively; extra allowance covers
    # message framing and adapter instructions. Discounts are never assumed.
    input_bound = (
        len(json.dumps(kwargs.get("messages", []), ensure_ascii=False).encode("utf-8"))
        + 4096
    )
    if any(
        r["status"] == "reservation_underestimated"
        for r in SpendingLedger(context.ledger).summary()["requests"]
    ):
        raise RequestBudgetExceeded("spending ledger needs a reservation audit")
    kwargs.pop("max_tokens", None)
    kwargs["max_completion_tokens"] = context.max_output_tokens
    kwargs["num_retries"] = 0
    kwargs["timeout"] = 60
    ledger = SpendingLedger(context.ledger)
    request = ledger.reserve(context, model=kwargs["model"], input_bound=input_bound)
    try:
        response = completion(**kwargs)
    except Exception:
        ledger.fail(request)
        raise
    ledger.settle(request, getattr(response, "usage", None))
    return response
