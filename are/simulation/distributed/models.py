"""Versioned data contracts for deterministic distributed evaluation."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EventKind(str, Enum):
    DECISION = "decision"
    OBSERVATION = "observation"
    ACTION = "action"
    MESSAGE_SEND = "message_send"
    MESSAGE_RECEIVE = "message_receive"
    WORLD_EFFECT = "world_effect"
    GUARD = "guard"
    WATERMARK = "watermark"
    FINISH = "finish"
    BRANCH_COMMITMENT = "branch_commitment"
    POLICY_COMMITMENT = "policy_commitment"


class EpistemicStatus(str, Enum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    CLAIMED = "claimed"
    UNKNOWN = "unknown"


class RequirementVerdict(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class GuardVerdict(str, Enum):
    ALLOW = "allow"
    DEFER = "defer"
    BLOCK = "block"


class IntentKind(str, Enum):
    OBSERVE = "observe"
    ACT = "act"
    SEND = "send"
    WAIT = "wait"
    ABSTAIN = "abstain"
    FINISH = "finish"


class ActorSpec(FrozenModel):
    actor_id: str
    permitted_actions: tuple[str, ...] = ()
    tool_schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)
    observable_facts: tuple[str, ...] = ()
    role: str = ""


class CommunicationTopologySpec(FrozenModel):
    """Fixed communication graph for one distributed season."""

    topology_id: str
    kind: Literal["explicit", "fully_connected", "hierarchical", "shared_blackboard"]
    edges: tuple[tuple[str, str], ...] = ()
    center_actor: str | None = None


class AgentTeamSpec(FrozenModel):
    """Versioned, immutable team contract used by the runtime and evaluator."""

    schema_version: Literal["farm_team_v1"] = "farm_team_v1"
    team_id: str
    actors: tuple[ActorSpec, ...]
    topology: CommunicationTopologySpec
    activation_policy: Literal["round_robin", "seeded_permutation", "event_driven"] = (
        "round_robin"
    )
    activation_priorities: dict[str, int] = Field(default_factory=dict)
    time_authority: tuple[str, ...]
    fact_observers: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    team_call_budget: int | None = Field(default=None, gt=0)
    per_agent_call_budget: dict[str, int] = Field(default_factory=dict)
    team_token_budget: int | None = Field(default=None, gt=0)
    per_agent_token_budget: dict[str, int] = Field(default_factory=dict)
    petri_spec_digest: str | None = None
    role_refinement_digest: str | None = None
    expert_review_status: Literal[
        "unreviewed", "two_expert_draft", "adjudicated", "confirmed"
    ] = "unreviewed"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_team(self) -> "AgentTeamSpec":
        actor_ids = tuple(actor.actor_id for actor in self.actors)
        known = set(actor_ids)
        if len(actor_ids) < 2:
            raise ValueError("a distributed farm team needs at least two actors")
        if len(actor_ids) != len(known):
            raise ValueError("team actor IDs must be unique")
        if not self.time_authority or not set(self.time_authority) <= known:
            raise ValueError("time_authority must contain known actors")
        unknown_priority = set(self.activation_priorities) - known
        if unknown_priority:
            raise ValueError(
                f"activation priorities contain unknown actors: {sorted(unknown_priority)}"
            )
        for label, mapping in (
            ("per-agent call budget", self.per_agent_call_budget),
            ("per-agent token budget", self.per_agent_token_budget),
        ):
            unknown = set(mapping) - known
            if unknown:
                raise ValueError(f"{label} contains unknown actors: {sorted(unknown)}")
            if any(value <= 0 for value in mapping.values()):
                raise ValueError(f"{label} values must be positive")
        if (
            self.team_call_budget is not None
            and self.per_agent_call_budget
            and sum(self.per_agent_call_budget.values()) > self.team_call_budget
        ):
            raise ValueError("per-agent call budgets exceed the team call budget")
        if (
            self.team_token_budget is not None
            and self.per_agent_token_budget
            and sum(self.per_agent_token_budget.values()) > self.team_token_budget
        ):
            raise ValueError("per-agent token budgets exceed the team token budget")
        action_owners: dict[str, str] = {}
        for actor in self.actors:
            for action in actor.permitted_actions:
                previous = action_owners.get(action)
                if previous is not None:
                    raise ValueError(
                        f"action {action!r} has multiple owners: {previous!r}, {actor.actor_id!r}"
                    )
                action_owners[action] = actor.actor_id
        for fact_key, observers in self.fact_observers.items():
            unknown = set(observers) - known
            if unknown:
                raise ValueError(
                    f"fact {fact_key!r} has unknown observers: {sorted(unknown)}"
                )
        edges = self.topology.edges
        if len(edges) != len(set(edges)):
            raise ValueError("communication topology edges must be unique")
        if any(source == target for source, target in edges):
            raise ValueError("communication topology cannot contain self edges")
        unknown_edges = {
            actor for edge in edges for actor in edge if actor not in known
        }
        if unknown_edges:
            raise ValueError(
                f"communication topology contains unknown actors: {sorted(unknown_edges)}"
            )
        if self.topology.kind == "explicit" and not edges:
            raise ValueError("an explicit topology needs at least one edge")
        if self.topology.kind == "hierarchical":
            if self.topology.center_actor not in known:
                raise ValueError("a hierarchical topology needs a known center_actor")
        if self.expert_review_status == "confirmed":
            digest = self.metadata.get("confirmed_team_review_digest")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(
                    "confirmed team requires a SHA-256 independent-review digest"
                )
        return self


class RoleRefinementSpec(FrozenModel):
    """Reviewed mapping from the primary two-role oracle to a larger team."""

    schema_version: Literal["farm_role_refinement_v1"] = "farm_role_refinement_v1"
    refinement_id: str
    scenario_id: str
    source_team_id: str
    target_team_id: str
    role_mapping: dict[str, tuple[str, ...]]
    transition_owner_overrides: dict[str, str] = Field(default_factory=dict)
    communication_paths: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    preserved_dependency_ids: tuple[str, ...] = ()
    added_synchronization_ids: tuple[str, ...] = ()
    module_weight_budgets: dict[str, float] = Field(default_factory=dict)
    intended_concurrency: tuple[tuple[str, str], ...] = ()
    reviewer_rationale: str = ""
    confirmation_digest: str | None = None
    expert_review_status: Literal[
        "unreviewed", "two_expert_draft", "adjudicated", "confirmed"
    ] = "unreviewed"

    @model_validator(mode="after")
    def validate_refinement(self) -> "RoleRefinementSpec":
        if self.source_team_id == self.target_team_id:
            raise ValueError("role refinement source and target teams must differ")
        if any(len(path) < 2 for path in self.communication_paths.values()):
            raise ValueError("refinement communication paths need at least two actors")
        if any(weight <= 0 for weight in self.module_weight_budgets.values()):
            raise ValueError("refinement module budgets must be positive")
        if self.expert_review_status == "confirmed":
            if (
                not isinstance(self.confirmation_digest, str)
                or len(self.confirmation_digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in self.confirmation_digest
                )
            ):
                raise ValueError(
                    "confirmed role refinement needs a SHA-256 confirmation digest"
                )
        return self


class ReferenceEventSpec(FrozenModel):
    event_id: str
    actor_id: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    scope: tuple[int, int] | str | None = None
    required: bool = True
    harmful: bool = False
    weight: float = Field(default=1.0, gt=0)
    window_start: float | None = None
    window_end: float | None = None
    required_facts: tuple[str, ...] = ()
    branch_condition: dict[str, Any] = Field(default_factory=dict)
    expected_evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_window(self) -> "ReferenceEventSpec":
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end < self.window_start
        ):
            raise ValueError("window_end must be >= window_start")
        return self


class CausalEdgeSpec(FrozenModel):
    source: str
    target: str
    weight: float = Field(default=1.0, gt=0)
    reason: str = "task"


class ConflictSetSpec(FrozenModel):
    event_ids: tuple[str, ...]
    weight: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def validate_size(self) -> "ConflictSetSpec":
        if len(set(self.event_ids)) < 2:
            raise ValueError("a conflict set needs at least two distinct events")
        return self


class FactRequirement(FrozenModel):
    requirement_id: str
    action: str
    actor_id: str
    fact_key: str
    expected_value: Any = True
    operator: Literal["eq", "ne", "ge", "gt", "le", "lt", "in"] = "eq"
    scope: tuple[int, int] | str | None = None
    max_age: float | None = Field(default=None, ge=0)
    deadline: float | None = None
    require_evidence: bool = True


class DistributedTaskSpec(FrozenModel):
    task_id: str
    actors: tuple[ActorSpec, ...]
    events: tuple[ReferenceEventSpec, ...]
    causal_edges: tuple[CausalEdgeSpec, ...] = ()
    conflict_sets: tuple[ConflictSetSpec, ...] = ()
    fact_requirements: tuple[FactRequirement, ...] = ()
    observability: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    ownership: dict[str, str] = Field(default_factory=dict)
    time_windows: dict[str, tuple[float, float]] = Field(default_factory=dict)
    outcome_adapter: str | None = None

    @model_validator(mode="after")
    def validate_graph(self) -> "DistributedTaskSpec":
        actor_ids = [actor.actor_id for actor in self.actors]
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("actor IDs must be unique")
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("reference event IDs must be unique")
        known_events = set(event_ids)
        known_actors = set(actor_ids)
        for event in self.events:
            if event.actor_id not in known_actors:
                raise ValueError(
                    f"unknown actor {event.actor_id!r} in {event.event_id}"
                )

        edges = {(edge.source, edge.target) for edge in self.causal_edges}
        if len(edges) != len(self.causal_edges):
            raise ValueError("causal edges must be unique")
        for source, target in edges:
            if source not in known_events or target not in known_events:
                raise ValueError(
                    f"causal edge references an unknown event: {source}->{target}"
                )
            if source == target:
                raise ValueError("causal self-edges are invalid")

        adjacency = {event_id: set() for event_id in event_ids}
        for source, target in edges:
            adjacency[source].add(target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("reference causal graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for child in adjacency[node]:
                visit(child)
            visiting.remove(node)
            visited.add(node)

        for event_id in event_ids:
            visit(event_id)

        def path_exists(source: str, target: str, skipped: tuple[str, str]) -> bool:
            stack = [source]
            seen = {source}
            while stack:
                node = stack.pop()
                for child in adjacency[node]:
                    if (node, child) == skipped:
                        continue
                    if child == target:
                        return True
                    if child not in seen:
                        seen.add(child)
                        stack.append(child)
            return False

        for edge in edges:
            if path_exists(edge[0], edge[1], edge):
                raise ValueError(
                    f"causal edge {edge[0]}->{edge[1]} is transitively redundant"
                )

        by_id = {event.event_id: event for event in self.events}
        for conflict in self.conflict_sets:
            if not set(conflict.event_ids) <= known_events:
                raise ValueError("conflict set references an unknown event")
            if all(by_id[event_id].required for event_id in conflict.event_ids):
                raise ValueError("mutually exclusive events cannot all be required")
        for requirement in self.fact_requirements:
            if requirement.actor_id not in known_actors:
                raise ValueError("fact requirement references an unknown actor")
        for fact_key, observers in self.observability.items():
            unknown = set(observers) - known_actors
            if unknown:
                raise ValueError(
                    f"observability for {fact_key!r} contains unknown actors: "
                    f"{sorted(unknown)}"
                )
        for action, owner in self.ownership.items():
            if owner not in known_actors:
                raise ValueError(
                    f"ownership for {action!r} references unknown actor {owner!r}"
                )
            actor = next(actor for actor in self.actors if actor.actor_id == owner)
            if action not in actor.permitted_actions:
                raise ValueError(
                    f"ownership for {action!r} conflicts with actor permissions"
                )
        for event_id, window in self.time_windows.items():
            if event_id not in known_events:
                raise ValueError(f"time window references unknown event {event_id!r}")
            if len(window) != 2 or window[1] < window[0]:
                raise ValueError(f"invalid time window for event {event_id!r}")
        return self


class KnowledgeItem(FrozenModel):
    item_id: str
    fact_key: str
    value: Any = None
    scope: tuple[int, int] | str | None = None
    status: EpistemicStatus
    source_actor: str
    evidence_ids: tuple[str, ...] = ()
    observed_at: float
    learned_at: float
    valid_until: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    causal_parents: tuple[str, ...] = ()
    vector_clock: dict[str, int] = Field(default_factory=dict)
    message_id: str | None = None


class KnowledgeSnapshot(FrozenModel):
    actor_id: str
    logical_time: float
    item_ids: tuple[str, ...]
    vector_clock: dict[str, int]
    inbox_frontier: tuple[str, ...] = ()
    digest: str


class Claim(FrozenModel):
    fact_key: str
    value: Any
    fact_version_id: str | None = None
    scope: tuple[int, int] | str | None = None
    status: EpistemicStatus = EpistemicStatus.CLAIMED
    confidence: float | None = Field(default=None, ge=0, le=1)
    observed_at: float
    valid_until: float | None = None
    evidence_ids: tuple[str, ...] = ()
    causal_parents: tuple[str, ...] = ()


class FreeTextEnvelope(FrozenModel):
    envelope_type: Literal["free_text"] = "free_text"
    message_id: str = ""
    sender: str
    recipient: str
    text: str
    vector_clock: dict[str, int] = Field(default_factory=dict)
    send_time: float = 0.0


class CausalHandoff(FrozenModel):
    envelope_type: Literal["causal"] = "causal"
    message_id: str = ""
    sender: str
    recipient: str
    text: str = ""
    claims: tuple[Claim, ...] = ()
    unresolved: tuple[str, ...] = ()
    vector_clock: dict[str, int] = Field(default_factory=dict)
    send_time: float = 0.0


Envelope = FreeTextEnvelope | CausalHandoff


class AgentIntent(FrozenModel):
    kind: IntentKind
    action: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    fact_key: str | None = None
    fact_value: Any = None
    scope: tuple[int, int] | str | None = None
    valid_for: float | None = None
    recipient: str | None = None
    recipients: tuple[str, ...] = ()
    text: str = ""
    claim_fact_keys: tuple[str, ...] = ()
    unresolved_requirements: tuple[str, ...] = ()
    wait: float = Field(default=0.0, ge=0)
    causal_parents: tuple[str, ...] = ()
    llm_input_log_id: str | None = None


class LocalView(FrozenModel):
    actor: ActorSpec
    logical_time: float
    world_time: float
    knowledge: tuple[KnowledgeItem, ...]
    inbox: tuple[Envelope, ...]
    vector_clock: dict[str, int]
    unresolved: tuple[str, ...] = ()
    previous_result: Any = None


class GuardResult(FrozenModel):
    verdict: GuardVerdict
    reasons: tuple[str, ...] = ()
    supporting_item_ids: tuple[str, ...] = ()
    requirement_verdicts: dict[str, RequirementVerdict] = Field(default_factory=dict)


class TraceEvent(FrozenModel):
    event_id: str
    kind: EventKind
    actor_id: str
    local_sequence: int
    logical_time: float
    world_time: float
    vector_clock: dict[str, int]
    action: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    causal_parents: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    message_id: str | None = None
    decision_context_id: str | None = None
    farmare_event_id: str | None = None
    status: str = "ok"
    payload: dict[str, Any] = Field(default_factory=dict)
    season_phase: str | None = None
    fact_version: str | None = None


class DecisionRecord(FrozenModel):
    decision_id: str
    actor_id: str
    logical_time: float
    knowledge_snapshot: KnowledgeSnapshot
    proposed_intent: AgentIntent
    guard: GuardResult | None = None
    prompt_digest: str | None = None
    prompt_item_ids: tuple[str, ...] = ()
    prompt_message_ids: tuple[str, ...] = ()
    llm_input_log_id: str | None = None
    season_phase: str | None = None
    response_id: str | None = None
    model_name: str | None = None
    model_provider: str | None = None
    system_fingerprint: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    completion_duration: float | None = None
    retry_count: int = 0
    policy_commitment_id: str | None = None


class WorldBranchCommitmentRecord(FrozenModel):
    commitment_id: str
    branch_id: str
    alternative_id: str
    logical_time: float
    world_time: float
    evidence_fact_version_ids: tuple[str, ...]
    world_context_digest: str
    specification_digest: str


class PolicyCommitmentRecord(FrozenModel):
    commitment_id: str
    policy_id: str
    actor_id: str
    decision_id: str
    logical_time: float
    world_time: float
    knowledge_snapshot_digest: str
    requirement_verdicts: dict[str, RequirementVerdict]
    deadline_state: Literal["open", "closed"]
    channel_state: Literal["open", "closed"]
    permitted_responses: tuple[
        Literal["execute", "reobserve", "handoff", "defer", "abstain"], ...
    ]
    required_responses: tuple[
        Literal["execute", "reobserve", "handoff", "defer", "abstain"], ...
    ] = ()
    specification_digest: str
    supporting_item_ids: tuple[str, ...] = ()
    season_phase: str | None = None
    # v5 audit fields.  They are deliberately redundant with the frozen
    # specification: the evaluator recomputes them and rejects disagreement.
    matched_rule_id: str | None = None
    recomputation_basis_digest: str | None = None


class FaultManifestationRecord(FrozenModel):
    """Observed manifestation of one predeclared communication treatment."""

    fault_id: str
    mode: Literal[
        "none",
        "delay_within_validity",
        "delay_past_validity",
        "delay_past_deadline",
        "drop",
        "duplicate",
        "reorder",
        "mixed",
    ]
    target_ids: tuple[str, ...] = ()
    manifested: bool
    evidence_event_ids: tuple[str, ...] = ()
    details: dict[str, Any] = Field(default_factory=dict)


class FactVersionRecord(FrozenModel):
    """Authoritative or observed version of a farm fact.

    ``visible_to`` is an information-flow statement, not an access-control
    shortcut.  World-only records are retained for evaluation but cannot be
    placed in a controller context unless a corresponding observation or
    delivered claim exists.
    """

    version_id: str
    fact_key: str
    value: Any
    status: EpistemicStatus | None = None
    scope: tuple[int, int] | str | None = None
    source_event_id: str
    origin_version_id: str | None = None
    supersedes_version_ids: tuple[str, ...] = ()
    farmare_event_id: str | None = None
    world_time: float
    learned_time: float | None = None
    valid_until: float | None = None
    evidence_ids: tuple[str, ...] = ()
    visible_to: tuple[str, ...] = ()
    season_phase: str | None = None
    authoritative: bool = False


class DistributedTrace(FrozenModel):
    schema_version: Literal[
        "dcore_trace_v1",
        "dcore_trace_v2",
        "dcore_trace_v3",
        "dcore_trace_v4",
        "dcore_trace_v5",
    ] = "dcore_trace_v3"
    metric_version: str = "dcore_eval_v3"
    run_id: str
    task_id: str
    actors: tuple[str, ...]
    events: tuple[TraceEvent, ...]
    knowledge_snapshots: tuple[KnowledgeSnapshot, ...] = ()
    decisions: tuple[DecisionRecord, ...] = ()
    fact_versions: tuple[FactVersionRecord, ...] = ()
    world_branch_commitments: tuple[WorldBranchCommitmentRecord, ...] = ()
    policy_commitments: tuple[PolicyCommitmentRecord, ...] = ()
    configuration: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] = Field(default_factory=dict)
    source_trace: str | None = None
    team_id: str | None = None
    team_spec_digest: str | None = None
    role_refinement_digest: str | None = None
    fault_manifestations: tuple[FaultManifestationRecord, ...] = ()


class DistributedRunnerConfig(BaseModel):
    # Generic v1 semantic fixtures keep their historical default. The public
    # CLI and FarmDistributedRunConfig below are deliberately farm-only.
    scenario_id: str = "transaction_revocation"
    controller_mode: Literal["scripted", "mock_llm", "llm", "replay"] = "scripted"
    visibility_mode: Literal["local", "shared_blackboard"] = "local"
    handoff_mode: Literal["free_text", "causal"] = "causal"
    enforcement_mode: Literal["off", "audit", "enforce"] = "enforce"
    scheduler_seed: int = 0
    world_seed: int = 0
    model_seed: int = 0
    fault_seed: int = 0
    interleaving_mode: Literal["deterministic", "enumerate"] = "deterministic"
    fault: Literal[
        "none",
        "delay",
        "delay_within_validity",
        "delay_past_validity",
        "delay_past_deadline",
        "drop",
        "duplicate",
        "reorder",
        "mixed",
    ] = "none"
    delay: float = Field(default=0.0, ge=0)
    fault_target_ids: tuple[str, ...] = ()
    fault_valid_until_world_time: float | None = None
    fault_delivery_world_time: float | None = None
    # Resolved from the frozen v5 treatment/policy.  This is recorded in the
    # run manifest and lets the transport prove a past-deadline treatment
    # manifested instead of using evidence expiry as a proxy.
    fault_deadline_world_time: float | None = None
    max_logical_steps: int = Field(default=2000, gt=0)
    max_deferrals: int = Field(default=3, ge=0)
    output_dir: str | None = None
    replay_trace: str | None = None
    petri_spec_path: str | None = None
    scientific_gate_manifest: str | None = None
    paper_mode: bool = False
    bounded_llm_smoke: bool = False
    team_spec_path: str | None = None
    role_refinement_path: str | None = None
    team_id: Literal["wetjune_2agent", "wetjune_3agent", "wetjune_4agent"] = (
        "wetjune_2agent"
    )
    activation_policy: (
        Literal["round_robin", "seeded_permutation", "event_driven"] | None
    ) = None
    communication_topology: (
        Literal["explicit", "fully_connected", "hierarchical", "shared_blackboard"]
        | None
    ) = None
    team_call_budget: int | None = Field(default=None, gt=0)
    per_agent_call_budget: int | None = Field(default=None, gt=0)
    team_token_budget: int | None = Field(default=None, gt=0)
    per_agent_token_budget: int | None = Field(default=None, gt=0)
    max_messages: int = Field(default=10000, gt=0)
    model_by_actor: dict[str, str] = Field(default_factory=dict)
    provider_by_actor: dict[str, str] = Field(default_factory=dict)
    endpoint_by_actor: dict[str, str] = Field(default_factory=dict)
    agent_family_by_actor: dict[str, str] = Field(default_factory=dict)
    history_window_by_actor: dict[str, int] = Field(default_factory=dict)
    temperature_by_actor: dict[str, float] = Field(default_factory=dict)
    max_model_calls: int = Field(default=700, gt=0)
    max_output_tokens: int = Field(default=1024, gt=0)
    condition_id: str = "causal_enforced"
    controller_profile_id: str = "default"
    repeat_index: int = 0
    resume: bool = True
    # v4 remains the compatibility default for engineering fixtures.  Paper
    # manifests must opt into v5 and the paper-mode gate enforces it.
    scientific_contract: Literal["v4", "v5"] = "v4"

    @model_validator(mode="after")
    def validate_modes(self) -> "DistributedRunnerConfig":
        if self.handoff_mode == "free_text" and self.enforcement_mode == "enforce":
            raise ValueError("free-text handoffs cannot use enforcement mode")
        if self.controller_mode == "replay" and not self.replay_trace:
            raise ValueError("replay mode requires replay_trace")
        if self.paper_mode and not self.petri_spec_path:
            raise ValueError("paper mode requires a frozen petri_spec_path")
        if self.controller_mode == "llm" and not self.paper_mode:
            raise ValueError(
                "real-LLM runs require paper_mode and a frozen specification"
            )
        if self.controller_mode == "llm" and not self.scientific_gate_manifest:
            raise ValueError("real-LLM runs require a scientific_gate_manifest")
        if self.bounded_llm_smoke:
            if (
                self.controller_mode != "llm"
                or not self.paper_mode
                or self.scenario_id != "farm_wetjune_recheck"
            ):
                raise ValueError(
                    "bounded LLM smoke is only valid for a paper-mode Wet-June LLM run"
                )
            if self.max_model_calls > 12:
                raise ValueError("bounded LLM smoke permits at most 12 model calls")
        if self.team_call_budget and self.team_call_budget > self.max_model_calls:
            raise ValueError("team_call_budget cannot exceed max_model_calls")
        return self


class FarmDistributedRunConfig(DistributedRunnerConfig):
    scenario_id: Literal[
        "farm_wetjune_recheck", "farm_disease_drought", "farm_three_cultivar"
    ] = "farm_wetjune_recheck"


class DistributedRunResult(BaseModel):
    trace: DistributedTrace
    metrics: dict[str, Any]
    attribution: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


def stable_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
