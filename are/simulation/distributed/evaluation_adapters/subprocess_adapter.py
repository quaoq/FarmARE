"""Process-isolated comparator bridges with strict JSON input and output."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .contracts import ComparatorResult, DiagnosticPacket
from .registry import ComparatorAdapter


def _configuration() -> dict[str, Any]:
    path = os.environ.get("DCORE_COMPARATOR_ADAPTERS")
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "dcore_comparator_adapter_config_v1":
        raise ValueError("comparator adapter configuration has an unsupported schema")
    return dict(payload.get("adapters") or {})


def external_adapter(
    name: str,
    capability: str,
    *,
    expected_revision: str | None,
) -> ComparatorAdapter:
    """Create a bridge that is unavailable until an explicit command is pinned."""

    def run(packet: DiagnosticPacket) -> ComparatorResult:
        settings = _configuration().get(name)
        if not settings:
            return ComparatorResult(
                method=name,
                capability=capability,
                status="unavailable",
                packet_digest=packet.packet_digest,
                error="external_adapter_not_configured",
                source_revision=expected_revision,
            )
        command = settings.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise ValueError(f"adapter {name!r} requires a non-empty argv list")
        configured_revision = settings.get("source_revision")
        if expected_revision and configured_revision != expected_revision:
            raise ValueError(
                f"adapter {name!r} revision mismatch: expected {expected_revision}, "
                f"received {configured_revision}"
            )
        permitted = tuple(settings.get("pass_environment") or ())
        child_environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            **{key: os.environ[key] for key in permitted if key in os.environ},
        }
        envelope = {
            "schema_version": "dcore_comparator_request_v1",
            "method": name,
            "capability": capability,
            "packet": packet.model_dump(mode="json"),
        }
        with tempfile.TemporaryDirectory(prefix=f"dcore-{name}-") as directory:
            completed = subprocess.run(
                command,
                input=json.dumps(envelope),
                capture_output=True,
                text=True,
                cwd=directory,
                env=child_environment,
                timeout=float(settings.get("timeout_seconds", 600)),
                check=False,
            )
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-2000:]
            raise RuntimeError(f"adapter exited {completed.returncode}: {detail}")
        response = json.loads(completed.stdout)
        result = ComparatorResult.model_validate(response)
        if result.method != name or result.capability != capability:
            raise ValueError("adapter returned the wrong method or capability")
        if result.packet_digest != packet.packet_digest:
            raise ValueError("adapter returned a result for a different packet")
        return result

    return ComparatorAdapter(
        name=name,
        capability=capability,
        run=run,
        source_revision=expected_revision,
    )


__all__ = ["external_adapter"]
