"""Crash-resilient append-only evidence for distributed seasons."""

from __future__ import annotations

import base64
import gzip
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

_PROVIDER_JOURNAL: ContextVar[Callable[[str, dict[str, Any]], None] | None] = (
    ContextVar("dcore_provider_journal", default=None)
)

_CHECKPOINT_ENCODING = "gzip+base64+json-v1"


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


def _compact_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    """Compress cumulative prompt history without weakening journal evidence."""

    checkpoint = payload.get("checkpoint")
    if not isinstance(checkpoint, dict) or "prompt_history" not in checkpoint:
        return payload
    prompt_history = checkpoint.get("prompt_history")
    raw = json.dumps(
        prompt_history, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    compact = dict(checkpoint)
    compact.pop("prompt_history", None)
    compact.update(
        {
            "prompt_history_encoding": _CHECKPOINT_ENCODING,
            "prompt_history_compressed": base64.b64encode(compressed).decode("ascii"),
            "prompt_history_uncompressed_bytes": len(raw),
            "prompt_history_content_digest": stable_digest(prompt_history),
        }
    )
    return {**payload, "checkpoint": compact}


def _hydrate_checkpoint(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload", {})
    checkpoint = payload.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return record
    encoding = checkpoint.get("prompt_history_encoding")
    if encoding is None:
        return record
    if encoding != _CHECKPOINT_ENCODING:
        raise ValueError("journal checkpoint uses an unknown prompt-history encoding")
    try:
        raw = gzip.decompress(
            base64.b64decode(checkpoint["prompt_history_compressed"], validate=True)
        )
        prompt_history = json.loads(raw)
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as error:
        raise ValueError("journal checkpoint prompt history is corrupt") from error
    if len(raw) != checkpoint.get("prompt_history_uncompressed_bytes"):
        raise ValueError("journal checkpoint prompt-history length mismatch")
    if stable_digest(prompt_history) != checkpoint.get("prompt_history_content_digest"):
        raise ValueError("journal checkpoint prompt-history digest mismatch")
    hydrated = dict(checkpoint)
    for key in (
        "prompt_history_encoding",
        "prompt_history_compressed",
        "prompt_history_uncompressed_bytes",
        "prompt_history_content_digest",
    ):
        hydrated.pop(key, None)
    hydrated["prompt_history"] = prompt_history
    return {**record, "payload": {**payload, "checkpoint": hydrated}}


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
        if kind == "parsed_proposal":
            clean = _compact_checkpoint(clean)
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


def iter_journal(
    path: str | Path, *, hydrate_checkpoints: bool = True
) -> Iterator[dict[str, Any]]:
    """Load and validate a complete journal prefix."""

    expected = 0
    with Path(path).open("r", encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                if any(remaining.strip() for remaining in source):
                    raise ValueError(
                        "journal contains corruption before its final record"
                    ) from error
                break
            try:
                digest = record.pop("record_digest")
            except KeyError as error:
                raise ValueError("journal record lacks its digest") from error
            if record.get("sequence") != expected:
                raise ValueError("journal sequence is not contiguous")
            if stable_digest(record) != digest:
                raise ValueError("journal record digest mismatch")
            record["record_digest"] = digest
            yield _hydrate_checkpoint(record) if hydrate_checkpoints else record
            expected += 1


def load_journal(
    path: str | Path, *, hydrate_checkpoints: bool = True
) -> list[dict[str, Any]]:
    return list(iter_journal(path, hydrate_checkpoints=hydrate_checkpoints))


def proposal_checkpoint(path: str | Path, intent_id: str) -> dict[str, Any]:
    """Return one verified checkpoint without hydrating every proposal."""

    for record in iter_journal(path, hydrate_checkpoints=False):
        payload = record.get("payload", {})
        if (
            record.get("kind") == "parsed_proposal"
            and payload.get("intent_id") == intent_id
        ):
            return dict(_hydrate_checkpoint(record)["payload"].get("checkpoint") or {})
    return {}


def uncertain_native_writes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return intents that have no request-bound native receipt."""

    pending: dict[str, dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload", {})
        intent_id = payload.get("intent_id")
        if (
            record.get("kind") in {"native_write_intent", "repair_native_intent"}
            and intent_id
        ):
            pending[intent_id] = payload
        elif (
            record.get("kind") in {"native_write_receipt", "repair_native_receipt"}
            and intent_id
        ):
            pending.pop(intent_id, None)
    return [
        {**payload, "status": "uncertain_native_write"} for payload in pending.values()
    ]


def uncertain_provider_requests(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return provider requests dispatched without a recorded response."""

    pending: dict[str, dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload", {})
        request_id = payload.get("provider_request_id")
        if record.get("kind") == "provider_request_intent" and request_id:
            pending[str(request_id)] = payload
        elif (
            record.get("kind")
            in {
                "provider_response_receipt",
                "provider_error_receipt",
            }
            and request_id
        ):
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
    records = load_journal(journal_path, hydrate_checkpoints=False)
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
    elif any(
        kind in kinds for kind in ("model_request", "model_response", "model_exchange")
    ):
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
    "iter_journal",
    "load_journal",
    "journal_provider_event",
    "provider_journal",
    "proposal_checkpoint",
    "uncertain_native_writes",
    "uncertain_provider_requests",
]
