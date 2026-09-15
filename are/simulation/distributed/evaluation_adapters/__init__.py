"""Common evidence and typed outputs for D-CORE comparison methods."""

from .builtin import register_builtin_adapters
from .contracts import (
    ComparatorResult,
    ContinuationManifest,
    DiagnosticPacket,
    DiagnosticWitness,
    RepairCandidate,
    RepairPrimitive,
    build_diagnostic_packet,
)
from .registry import ComparatorAdapter, available_adapters, run_adapters

register_builtin_adapters()

__all__ = [
    "ComparatorAdapter",
    "ComparatorResult",
    "ContinuationManifest",
    "DiagnosticPacket",
    "DiagnosticWitness",
    "RepairCandidate",
    "RepairPrimitive",
    "available_adapters",
    "build_diagnostic_packet",
    "run_adapters",
]
