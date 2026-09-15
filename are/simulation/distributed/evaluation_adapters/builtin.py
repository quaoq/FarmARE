"""Same-packet local comparison methods used in every installation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from are.simulation.distributed.models import stable_digest

from .contracts import (
    ComparatorResult,
    DiagnosticPacket,
    DiagnosticWitness,
    RepairCandidate,
    RepairPrimitive,
)
from .registry import ComparatorAdapter, register_adapter
from .subprocess_adapter import external_adapter


def _score_result(
    packet: DiagnosticPacket, method: str, scores: dict[str, float | None]
) -> ComparatorResult:
    return ComparatorResult(
        method=method,
        capability="score",
        status="ok",
        packet_digest=packet.packet_digest,
        scores=scores,
        source_revision="farmare_local_adapter_v1",
    )


def _core(packet: DiagnosticPacket) -> ComparatorResult:
    metrics = packet.existing_metrics
    merged = metrics.get("merged_pc_ktc", {})
    return _score_result(
        packet,
        "core",
        {
            "combined": merged.get("combined"),
            "path_correctness": metrics.get("core_path_correctness"),
        },
    )


def _fairy(packet: DiagnosticPacket) -> ComparatorResult:
    metrics = packet.existing_metrics
    return _score_result(
        packet,
        "fairy_temporal",
        {
            "event_fidelity": metrics.get("event_fidelity"),
            "timing_fidelity": metrics.get("timing_fidelity"),
        },
    )


def _information_enriched_core(packet: DiagnosticPacket) -> ComparatorResult:
    metrics = packet.existing_metrics
    merged = metrics.get("merged_pc_ktc", {})
    policy = metrics.get("information_policy_conformance", {})
    return _score_result(
        packet,
        "information_enriched_core",
        {
            "path_correctness": metrics.get("core_path_correctness"),
            "combined": merged.get("combined"),
            "information_conformance": policy.get("conformance"),
        },
    )


def _marble(packet: DiagnosticPacket) -> ComparatorResult:
    observed = {
        (event.get("actor_id"), event.get("action"))
        for event in packet.events
        if event.get("kind") == "action" and event.get("status") == "ok"
    }
    required = [
        item
        for item in packet.reference_transitions
        if item.get("required")
        and item.get("actor_id") != "world"
        and item.get("kind") not in {"send", "receive"}
    ]
    completed = sum(
        (item.get("actor_id"), item.get("action")) in observed for item in required
    )
    return _score_result(
        packet,
        "marble_style_agricultural_milestones",
        {"milestone_completion": completed / len(required) if required else None},
    )


def _mechanism(value: str | None) -> str:
    return {
        "missing_observation": "missing_observation",
        "observation_gap": "missing_observation",
        "failed_delivery": "failed_delivery",
        "transit_gap": "failed_delivery",
        "expired_evidence": "expired_evidence",
        "stale_information": "expired_evidence",
        "incorrect_scope": "incorrect_scope",
        "context_omission": "context_omission",
        "uptake_error": "context_omission",
        "reasoning_error": "failure_to_use_available_evidence",
        "failure_to_use_available_evidence": "failure_to_use_available_evidence",
        "native_execution_failure": "native_execution_failure",
        "transition_order_failure": "transition_order_failure",
    }.get(value, "unresolved_evidence")


def _dcore(packet: DiagnosticPacket) -> ComparatorResult:
    witnesses = []
    rows = packet.existing_metrics.get("provenance_failure_localization", ())
    event_by_id = {item.get("event_id"): item for item in packet.events}
    for row in rows:
        event = event_by_id.get(row.get("target_event_id"), {})
        chain = row.get("exact_fact_version_chain", ())
        witnesses.append(
            DiagnosticWitness(
                witness_id=row.get("witness_id")
                or stable_digest(row)[:24],
                decision_id=event.get("decision_context_id")
                or row.get("target_event_id", "unknown"),
                obligation_id=row.get("obligation_id", "unknown"),
                prerequisite_id=row.get("prerequisite_id", "legacy"),
                actor_id=event.get("actor_id", "unknown"),
                mechanism=_mechanism(row.get("primary")),
                fact_key=row.get("fact_key"),
                fact_version_ids=tuple(
                    item["fact_version_id"]
                    for item in chain
                    if item.get("fact_version_id")
                ),
                supporting_event_ids=tuple(
                    item["source_event_id"]
                    for item in chain
                    if item.get("source_event_id")
                ),
                root_support_group=row.get("root_support_group")
                or row.get("obligation_id", "unknown"),
                determination=row.get("determination", "supported"),
                decision_time=event.get("world_time"),
            )
        )
    return ComparatorResult(
        method="dcore",
        capability="diagnosis",
        status="ok",
        packet_digest=packet.packet_digest,
        witnesses=tuple(witnesses),
        source_revision="dcore_eval_v5",
    )


def _full_information_checker(packet: DiagnosticPacket) -> ComparatorResult:
    facts = {item.get("version_id"): item for item in packet.fact_versions}
    requirements_by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for transition in packet.reference_transitions:
        for guard in transition.get("guards", ()):
            requirements_by_action[transition.get("action")].append(guard)
    witnesses = []
    for decision in packet.decisions:
        action = decision.get("proposed_intent", {}).get("action")
        snapshot_ids = set(
            decision.get("knowledge_snapshot", {}).get("item_ids", ())
        )
        local = [facts[item] for item in snapshot_ids if item in facts]
        for guard in requirements_by_action.get(action, ()):
            matching = [item for item in local if item.get("fact_key") == guard.get("fact_key")]
            if matching:
                continue
            witness_id = stable_digest(
                [decision.get("decision_id"), guard.get("guard_id")]
            )[:24]
            witnesses.append(
                DiagnosticWitness(
                    witness_id=witness_id,
                    decision_id=decision.get("decision_id", "unknown"),
                    obligation_id=f"checker:{action}",
                    prerequisite_id=guard.get("guard_id", "unknown"),
                    actor_id=decision.get("actor_id", "unknown"),
                    mechanism="missing_observation",
                    fact_key=guard.get("fact_key"),
                    root_support_group=f"checker:{action}",
                    determination="supported",
                    target_scope=guard.get("scope"),
                    decision_time=decision.get("logical_time"),
                )
            )
    return ComparatorResult(
        method="full_information_checker",
        capability="diagnosis",
        status="ok",
        packet_digest=packet.packet_digest,
        witnesses=tuple(witnesses),
        source_revision="independent_join_checker_v1",
    )


def _dcfa_style(packet: DiagnosticPacket) -> ComparatorResult:
    """Independent dual-view attribution; no DCFA repository code is used."""

    local = _full_information_checker(packet)
    event_by_id = {item.get("event_id"): item for item in packet.events}
    incoming: dict[str, set[str]] = defaultdict(set)
    for event in packet.events:
        for parent in event.get("causal_parents", ()):
            incoming[str(event.get("event_id"))].add(str(parent))
    witnesses = []
    for witness in local.witnesses:
        event = event_by_id.get(witness.decision_id, {})
        supporting = tuple(sorted(incoming.get(witness.decision_id, ())))
        witnesses.append(
            witness.model_copy(
                update={
                    "witness_id": stable_digest(
                        ["dcfa_style", witness.witness_id, supporting]
                    )[:24],
                    "supporting_event_ids": supporting,
                    "decision_time": event.get("world_time", witness.decision_time),
                }
            )
        )
    return ComparatorResult(
        method="dcfa_style_reimplementation",
        capability="diagnosis",
        status="ok",
        packet_digest=packet.packet_digest,
        witnesses=tuple(witnesses),
        source_revision="independent_dual_view_v1_no_dcfa_code",
    )


def _generic_reconsideration(packet: DiagnosticPacket) -> ComparatorResult:
    repairs = []
    for witness in _dcore(packet).witnesses:
        primitive = RepairPrimitive(
            primitive="request_reconsideration",
            actor_id=witness.actor_id,
            fact_key=witness.fact_key,
            scope=witness.target_scope,
        )
        candidate_id = stable_digest(["generic_reconsideration", witness.witness_id])[
            :24
        ]
        repairs.append(
            RepairCandidate(
                candidate_id=candidate_id,
                witness_id=witness.witness_id,
                primitives=(primitive,),
                required_evidence_ids=witness.fact_version_ids,
                feasibility="unresolved",
                priority_key=(
                    witness.decision_time or float("inf"),
                    1,
                    0.0,
                    candidate_id,
                ),
            )
        )
    return ComparatorResult(
        method="generic_reconsideration",
        capability="repair",
        status="ok",
        packet_digest=packet.packet_digest,
        repairs=tuple(repairs),
        source_revision="generic_reconsideration_v1",
    )


def _bounded_repairs(packet: DiagnosticPacket, method: str) -> ComparatorResult:
    from are.simulation.distributed.repair_study import enumerate_repairs

    repairs = tuple(
        candidate
        for witness in _dcore(packet).witnesses
        for candidate in enumerate_repairs(witness)
    )
    return ComparatorResult(
        method=method,
        capability="repair",
        status="ok",
        packet_digest=packet.packet_digest,
        repairs=repairs,
        source_revision="dcore_repair_catalogue_v1",
    )


def _fixed_protocol_repairs(packet: DiagnosticPacket) -> ComparatorResult:
    return _bounded_repairs(packet, "fixed_protocol_rules")


def _dcore_repairs(packet: DiagnosticPacket) -> ComparatorResult:
    return _bounded_repairs(packet, "dcore_bounded_repair")


def _dover_adaptation(packet: DiagnosticPacket) -> ComparatorResult:
    """Outcome-oriented targeted interventions at FarmARE continuation boundaries.

    DoVer edits messages or plans and validates the intervention by continuing
    execution. FarmARE is asynchronous, so this adaptation uses the first
    full-information-checker hypothesis and proposes a targeted reconsideration
    at the common, digest-verified continuation boundary. The continuation
    executor, rather than this selector, measures whether the intervention
    changes the native outcome.
    """

    repairs = []
    for witness in _full_information_checker(packet).witnesses:
        primitive = RepairPrimitive(
            primitive="request_reconsideration",
            actor_id=witness.actor_id,
            fact_key=witness.fact_key,
            scope=witness.target_scope,
        )
        candidate_id = stable_digest(
            ["dover_adaptation", witness.decision_id, witness.prerequisite_id]
        )[:24]
        repairs.append(
            RepairCandidate(
                candidate_id=candidate_id,
                witness_id=witness.witness_id,
                primitives=(primitive,),
                required_evidence_ids=(),
                feasibility="feasible",
                priority_key=(
                    witness.decision_time or float("inf"),
                    1,
                    0.0,
                    candidate_id,
                ),
            )
        )
    return ComparatorResult(
        method="dover_adaptation",
        capability="repair",
        status="ok",
        packet_digest=packet.packet_digest,
        repairs=tuple(repairs),
        source_revision="dover_publication_async_boundary_adaptation_v1",
        adapter_metadata={
            "adaptation": "targeted_reconsideration_at_common_continuation_boundary",
            "hypothesis_source": "independent_full_information_checker",
            "outcome_validation": "matched_native_continuation",
        },
    )


def register_builtin_adapters() -> None:
    for name, capability, function in (
        ("core", "score", _core),
        ("fairy_temporal", "score", _fairy),
        ("information_enriched_core", "score", _information_enriched_core),
        ("marble_style_agricultural_milestones", "score", _marble),
        ("dcore", "diagnosis", _dcore),
        ("full_information_checker", "diagnosis", _full_information_checker),
        ("dcfa_style_reimplementation", "diagnosis", _dcfa_style),
        ("generic_reconsideration", "repair", _generic_reconsideration),
        ("fixed_protocol_rules", "repair", _fixed_protocol_repairs),
        ("dover_adaptation", "repair", _dover_adaptation),
        ("dcore_bounded_repair", "repair", _dcore_repairs),
    ):
        register_adapter(ComparatorAdapter(name, capability, function))
    # These bridges run an exact, pinned external checkout only after the
    # professor supplies its isolated command configuration. Until then the
    # typed output is explicitly unavailable rather than silently simulated.
    for adapter in (
        external_adapter(
            "who_when_all_at_once",
            "diagnosis",
            expected_revision="f4d2b6da464a826580e59b3a0eae15ea2d642d7c",
        ),
        external_adapter(
            "agentrx",
            "diagnosis",
            expected_revision="7a18c79708e7671be15124460f4f7296107c2a55",
        ),
        external_adapter(
            "agentrx_reviewed_constraints",
            "diagnosis",
            expected_revision="7a18c79708e7671be15124460f4f7296107c2a55",
        ),
    ):
        register_adapter(adapter)


__all__ = ["register_builtin_adapters"]
