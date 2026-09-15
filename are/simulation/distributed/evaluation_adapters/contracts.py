"""Versioned comparison, witness, repair and continuation contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from are.simulation.distributed.models import FrozenModel, stable_digest


class DiagnosticPacket(FrozenModel):
    schema_version: Literal["diagnostic_packet_v1"] = "diagnostic_packet_v1"
    run_id: str
    scenario_id: str
    specification_digest: str
    public_task_contract: str
    public_task_contract_digest: str
    actors: tuple[str, ...]
    events: tuple[dict[str, Any], ...]
    local_contexts: tuple[dict[str, Any], ...]
    messages: tuple[dict[str, Any], ...]
    receipts: tuple[dict[str, Any], ...]
    decisions: tuple[dict[str, Any], ...]
    fact_versions: tuple[dict[str, Any], ...]
    requirements: tuple[dict[str, Any], ...]
    reference_transitions: tuple[dict[str, Any], ...] = ()
    existing_metrics: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] | None = None
    prefix_decision_id: str | None = None
    packet_digest: str = ""

    @model_validator(mode="after")
    def validate_packet(self) -> "DiagnosticPacket":
        if stable_digest(self.public_task_contract) != self.public_task_contract_digest:
            raise ValueError("public task contract digest mismatch")
        return self


class DiagnosticWitness(FrozenModel):
    schema_version: Literal["diagnostic_witness_v1"] = "diagnostic_witness_v1"
    witness_id: str
    decision_id: str
    obligation_id: str
    prerequisite_id: str
    actor_id: str
    mechanism: Literal[
        "missing_observation",
        "failed_delivery",
        "expired_evidence",
        "incorrect_scope",
        "context_omission",
        "failure_to_use_available_evidence",
        "native_execution_failure",
        "transition_order_failure",
        "unresolved_evidence",
    ]
    fact_key: str | None = None
    fact_version_ids: tuple[str, ...] = ()
    supporting_event_ids: tuple[str, ...] = ()
    root_support_group: str
    determination: Literal["supported", "not_supported", "unresolved"]
    explanation: str | None = None
    target_scope: tuple[int, int] | str | None = None
    decision_time: float | None = None
    deadline: float | None = None


class RepairPrimitive(FrozenModel):
    primitive: Literal[
        "acquire_observation",
        "redeliver_evidence",
        "refresh_observation",
        "route_evidence",
        "restore_context",
        "request_reconsideration",
    ]
    actor_id: str
    recipient_actor_id: str | None = None
    fact_key: str | None = None
    fact_version_id: str | None = None
    scope: tuple[int, int] | str | None = None
    native_action: str | None = None
    native_arguments: dict[str, Any] = Field(default_factory=dict)
    estimated_duration_seconds: float | None = Field(default=None, ge=0)
    estimated_native_cost: float | None = Field(default=None, ge=0)


class RepairCandidate(FrozenModel):
    schema_version: Literal["repair_candidate_v1"] = "repair_candidate_v1"
    candidate_id: str
    witness_id: str
    primitives: tuple[RepairPrimitive, ...]
    required_evidence_ids: tuple[str, ...] = ()
    total_native_cost: float = Field(default=0.0, ge=0)
    timing_slack_seconds: float | None = None
    feasibility: Literal["feasible", "infeasible", "unresolved"]
    rejection_reasons: tuple[str, ...] = ()
    priority_key: tuple[float, int, float, str]

    @model_validator(mode="after")
    def validate_bound(self) -> "RepairCandidate":
        if not 1 <= len(self.primitives) <= 2:
            raise ValueError("repair candidate must contain one or two primitives")
        return self


class ContinuationManifest(FrozenModel):
    schema_version: Literal["continuation_manifest_v1"] = "continuation_manifest_v1"
    source_run_id: str
    checkpoint_decision_id: str
    checkpoint_digest: str
    semantic_prefix_digest: str
    physical_state_digest: str
    actor_context_digests: dict[str, str]
    knowledge_digests: dict[str, str]
    pending_delivery_digest: str
    clock_digest: str
    repair_candidate_id: str | None = None
    world_seed: int
    scheduler_seed: int
    model_seed: int
    fault_seed: int
    remaining_call_budget: int
    remaining_token_budget: int | None = None
    controller_settings: dict[str, Any]
    scenario_horizon: float
    selection_locked: bool = True


class ComparatorResult(FrozenModel):
    schema_version: Literal["comparator_result_v1"] = "comparator_result_v1"
    method: str
    capability: Literal["score", "diagnosis", "repair", "live_policy"]
    status: Literal["ok", "unavailable", "error"]
    packet_digest: str
    scores: dict[str, float | None] = Field(default_factory=dict)
    witnesses: tuple[DiagnosticWitness, ...] = ()
    repairs: tuple[RepairCandidate, ...] = ()
    error: str | None = None
    source_revision: str | None = None
    provider_requests: int = 0
    provider_tokens: int | None = None
    provider_cost_usd: float | None = None
    prompt_digest: str | None = None
    model_settings: dict[str, Any] = Field(default_factory=dict)
    adapter_metadata: dict[str, Any] = Field(default_factory=dict)


def build_diagnostic_packet(
    run_dir: str | Path,
    *,
    public_task_contract: str | None = None,
    prefix_decision_id: str | None = None,
    include_outcome: bool = True,
) -> DiagnosticPacket:
    """Serialize one fair packet, optionally truncated before a decision."""

    root = Path(run_dir)
    trace_path = next(root.glob("trace.dcore_trace*.json"), None)
    process_path = next(root.glob("farm_process_spec*.json"), None)
    if trace_path is None or process_path is None:
        raise ValueError("run directory lacks a D-CORE trace or process specification")
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    process = json.loads(process_path.read_text(encoding="utf-8"))
    recorded_contract = trace.get("configuration", {}).get("public_task_contract")
    if public_task_contract is None:
        if not recorded_contract:
            raise ValueError("trace does not preserve its exact public task contract")
        public_task_contract = str(recorded_contract)
    elif recorded_contract and recorded_contract != public_task_contract:
        raise ValueError("supplied public task contract differs from the recorded run")
    metrics_path = next(root.glob("metrics.dcore_eval*.json"), None)
    metrics = (
        json.loads(metrics_path.read_text(encoding="utf-8"))
        if metrics_path is not None
        else {}
    )
    decisions = list(trace.get("decisions", ()))
    cutoff = None
    if prefix_decision_id:
        selected = next(
            (item for item in decisions if item.get("decision_id") == prefix_decision_id),
            None,
        )
        if selected is None:
            raise ValueError("prefix decision does not exist in trace")
        cutoff = float(selected["logical_time"])
        decisions = [item for item in decisions if float(item["logical_time"]) < cutoff]
    events = tuple(
        item
        for item in trace.get("events", ())
        if cutoff is None or float(item["logical_time"]) < cutoff
    )
    visible_fact_ids = {
        fact_id
        for decision in decisions
        for fact_id in decision.get("knowledge_snapshot", {}).get("item_ids", ())
    }
    facts = tuple(
        item
        for item in trace.get("fact_versions", ())
        if cutoff is None
        or item.get("authoritative") is not True
        and item.get("version_id") in visible_fact_ids
    )
    requirements = tuple(
        requirement
        for policy in process.get("information_policies", ())
        for requirement in policy.get("requirements", ())
    )
    raw = {
        "run_id": trace["run_id"],
        "scenario_id": trace.get("configuration", {}).get(
            "scenario_id", process.get("scenario_id", "unknown")
        ),
        "specification_digest": stable_digest(process),
        "public_task_contract": public_task_contract,
        "public_task_contract_digest": stable_digest(public_task_contract),
        "actors": tuple(trace.get("actors", ())),
        "events": events,
        "local_contexts": tuple(decisions),
        "messages": tuple(
            item
            for item in events
            if item.get("kind") in {"message_send", "message_receive"}
        ),
        "receipts": tuple(
            item
            for item in events
            if item.get("payload", {}).get("execution_receipt") is not None
            or item.get("action") == "dcore.tool_receipt"
        ),
        "decisions": tuple(decisions),
        "fact_versions": facts,
        "requirements": requirements,
        "reference_transitions": tuple(
            process.get("occurrence_net", {}).get("transitions", ())
        ),
        "existing_metrics": metrics,
        "outcome": trace.get("outcome") if include_outcome and cutoff is None else None,
        "prefix_decision_id": prefix_decision_id,
    }
    return DiagnosticPacket(**raw, packet_digest=stable_digest(raw))
