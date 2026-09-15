"""Typed comparator registry with fair packet I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .contracts import ComparatorResult, DiagnosticPacket


@dataclass(frozen=True)
class ComparatorAdapter:
    name: str
    capability: str
    run: Callable[[DiagnosticPacket], ComparatorResult]
    source_revision: str | None = None


_ADAPTERS: dict[str, ComparatorAdapter] = {}


def register_adapter(adapter: ComparatorAdapter) -> None:
    if adapter.name in _ADAPTERS:
        raise ValueError(f"duplicate comparator adapter {adapter.name!r}")
    _ADAPTERS[adapter.name] = adapter


def available_adapters() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))


def run_adapters(
    packet: DiagnosticPacket, methods: Iterable[str]
) -> tuple[ComparatorResult, ...]:
    output = []
    for method in methods:
        adapter = _ADAPTERS.get(method)
        if adapter is None:
            output.append(
                ComparatorResult(
                    method=method,
                    capability="diagnosis",
                    status="unavailable",
                    packet_digest=packet.packet_digest,
                    error="adapter_not_installed",
                )
            )
            continue
        try:
            output.append(adapter.run(packet))
        except Exception as error:
            output.append(
                ComparatorResult(
                    method=method,
                    capability=adapter.capability,
                    status="error",
                    packet_digest=packet.packet_digest,
                    error=f"{type(error).__name__}: {error}",
                    source_revision=adapter.source_revision,
                )
            )
    return tuple(output)


__all__ = [
    "ComparatorAdapter",
    "available_adapters",
    "register_adapter",
    "run_adapters",
]
