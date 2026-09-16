"""Crash-resilient append-only evidence for distributed seasons."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator

from are.simulation.distributed.models import stable_digest

_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "provider_header",
    "secret",
    "token_header",
)

_PROVIDER_JOURNAL: ContextVar[
    Callable[[str, dict[str, Any]], None] | None
] = ContextVar("dcore_provider_journal", default=None)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(marker in str(key).lower() for marker in _SECRET_MARKERS)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class DurableRunJournal:
    """Append JSON records and fsync before the next external side effect."""

    schema_version = "dcore_progress_journal_v1"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as source:
                self.sequence = sum(1 for line in source if line.strip())

    def append(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean = _redact(payload)
        record = {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "kind": kind,
            "payload": clean,
        }
        record["record_digest"] = stable_digest(record)
        encoded = json.dumps(record, sort_keys=True, default=str) + "\n"
        with self.path.open("a", encoding="utf-8") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        self.sequence += 1
        return record


def load_journal(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate a complete journal prefix."""

    records: list[dict[str, Any]] = []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    populated = [index for index, line in enumerate(lines) if line.strip()]
    last = populated[-1] if populated else -1
    expected = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if index == last:
                break
            raise ValueError("journal contains corruption before its final record")
        try:
            digest = record.pop("record_digest")
        except KeyError as error:
            raise ValueError("journal record lacks its digest") from error
        if record.get("sequence") != expected:
            raise ValueError("journal sequence is not contiguous")
        if stable_digest(record) != digest:
            raise ValueError("journal record digest mismatch")
        record["record_digest"] = digest
        records.append(record)
        expected += 1
    return records


def uncertain_native_writes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return intents that have no request-bound native receipt."""

    pending: dict[str, dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload", {})
        intent_id = payload.get("intent_id")
        if record.get("kind") == "native_write_intent" and intent_id:
            pending[intent_id] = payload
        elif record.get("kind") == "native_write_receipt" and intent_id:
            pending.pop(intent_id, None)
    return [
        {**payload, "status": "uncertain_native_write"}
        for payload in pending.values()
    ]


def uncertain_provider_requests(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return provider requests dispatched without a recorded response."""

    pending: dict[str, dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload", {})
        request_id = payload.get("provider_request_id")
        if record.get("kind") == "provider_request_intent" and request_id:
            pending[str(request_id)] = payload
        elif record.get("kind") in {
            "provider_response_receipt",
            "provider_error_receipt",
        } and request_id:
            pending.pop(str(request_id), None)
    return [
        {**payload, "status": "uncertain_provider_request"}
        for payload in pending.values()
    ]


@contextmanager
def provider_journal(
    append: Callable[[str, dict[str, Any]], None] | None,
) -> Iterator[None]:
    token = _PROVIDER_JOURNAL.set(append)
    try:
        yield
    finally:
        _PROVIDER_JOURNAL.reset(token)


def journal_provider_event(kind: str, payload: dict[str, Any]) -> None:
    append = _PROVIDER_JOURNAL.get()
    if append is not None:
        append(kind, payload)


def interruption_status(path: str | Path) -> dict[str, Any]:
    """Classify an interrupted journal without guessing whether a write landed."""

    journal_path = Path(path)
    if not journal_path.is_file():
        return {
            "schema_version": "dcore_recovery_status_v1",
            "status": "unknown_legacy_interruption",
            "safe_to_replay_in_place": False,
            "uncertain_native_writes": [],
            "uncertain_provider_requests": [],
            "journal_records": 0,
        }
    records = load_journal(journal_path)
    uncertain = uncertain_native_writes(records)
    uncertain_requests = uncertain_provider_requests(records)
    kinds = [record.get("kind") for record in records]
    if uncertain:
        status = "uncertain_native_write"
    elif uncertain_requests:
        status = "uncertain_provider_request"
    elif "native_write_receipt" in kinds:
        status = "interrupted_after_native_receipt"
    elif "native_write_intent" in kinds:
        # Every intent was paired with a receipt, so this branch is defensive.
        status = "interrupted_after_native_receipt"
    elif any(kind in kinds for kind in ("model_request", "model_response", "model_exchange")):
        status = "interrupted_before_native_write"
    else:
        status = "interrupted_before_model_request"
    return {
        "schema_version": "dcore_recovery_status_v1",
        "status": status,
        # Reissuing provider calls or accepted writes under the same run identity
        # would alter treatment and cost. Resume occurs through prefix replay or a
        # separately identified attempt, never by silently restarting this row.
        "safe_to_replay_in_place": status == "interrupted_before_model_request",
        "uncertain_native_writes": uncertain,
        "uncertain_provider_requests": uncertain_requests,
        "journal_records": len(records),
        "last_record_kind": kinds[-1] if kinds else None,
    }


__all__ = [
    "DurableRunJournal",
    "interruption_status",
    "load_journal",
    "journal_provider_event",
    "provider_journal",
    "uncertain_native_writes",
    "uncertain_provider_requests",
]
