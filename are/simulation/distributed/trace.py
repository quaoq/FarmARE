"""Causal trace recording and validation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from are.simulation.distributed.clock import happens_before, merge, tick
from are.simulation.distributed.models import (
    DecisionRecord,
    DistributedTrace,
    EventKind,
    FactVersionRecord,
    FaultManifestationRecord,
    KnowledgeSnapshot,
    PolicyCommitmentRecord,
    TraceEvent,
    WorldBranchCommitmentRecord,
)


class CausalTraceRecorder:
    def __init__(
        self,
        run_id: str,
        task_id: str,
        actor_ids: tuple[str, ...],
        *,
        schema_version: str = "dcore_trace_v3",
        metric_version: str = "dcore_eval_v3",
        team_id: str | None = None,
        team_spec_digest: str | None = None,
        role_refinement_digest: str | None = None,
    ):
        self.run_id = run_id
        self.task_id = task_id
        self.actor_ids = actor_ids
        self.schema_version = schema_version
        self.metric_version = metric_version
        self.team_id = team_id
        self.team_spec_digest = team_spec_digest
        self.role_refinement_digest = role_refinement_digest
        components = (*actor_ids, "world")
        self._clocks = {
            actor: {component: 0 for component in components} for actor in components
        }
        self._sequences: defaultdict[str, int] = defaultdict(int)
        self._last_event: dict[str, str] = {}
        self._event_by_id: dict[str, TraceEvent] = {}
        self.events: list[TraceEvent] = []
        self.snapshots: list[KnowledgeSnapshot] = []
        self.decisions: list[DecisionRecord] = []
        self.fact_versions: list[FactVersionRecord] = []
        self.world_branch_commitments: list[WorldBranchCommitmentRecord] = []
        self.policy_commitments: list[PolicyCommitmentRecord] = []
        self.fault_manifestations: list[FaultManifestationRecord] = []

    def clock(self, actor_id: str) -> dict[str, int]:
        return dict(self._clocks[actor_id])

    def record(
        self,
        kind: EventKind,
        actor_id: str,
        logical_time: float,
        world_time: float | None = None,
        *,
        received_clock: dict[str, int] | None = None,
        action: str | None = None,
        args: dict[str, Any] | None = None,
        causal_parents: tuple[str, ...] = (),
        evidence_ids: tuple[str, ...] = (),
        message_id: str | None = None,
        decision_context_id: str | None = None,
        farmare_event_id: str | None = None,
        status: str = "ok",
        payload: dict[str, Any] | None = None,
        season_phase: str | None = None,
        fact_version: str | None = None,
    ) -> TraceEvent:
        if actor_id not in self._clocks:
            raise ValueError(f"unknown trace actor {actor_id!r}")
        parents = list(causal_parents)
        previous = self._last_event.get(actor_id)
        if previous and previous not in parents:
            parents.insert(0, previous)
        clock = self._clocks[actor_id]
        if received_clock is not None:
            clock = merge(clock, received_clock)
        known_parent_clocks = [
            self._event_by_id[parent].vector_clock
            for parent in parents
            if parent in self._event_by_id
        ]
        if known_parent_clocks:
            clock = merge(clock, *known_parent_clocks)
        clock = tick(clock, actor_id)
        self._clocks[actor_id] = clock
        self._sequences[actor_id] += 1
        event = TraceEvent(
            event_id=f"{self.run_id}:e{len(self.events):06d}",
            kind=kind,
            actor_id=actor_id,
            local_sequence=self._sequences[actor_id],
            logical_time=logical_time,
            world_time=logical_time if world_time is None else world_time,
            vector_clock=clock,
            action=action,
            args=args or {},
            causal_parents=tuple(dict.fromkeys(parents)),
            evidence_ids=evidence_ids,
            message_id=message_id,
            decision_context_id=decision_context_id,
            farmare_event_id=farmare_event_id,
            status=status,
            payload=payload or {},
            season_phase=season_phase,
            fact_version=fact_version,
        )
        self.events.append(event)
        self._event_by_id[event.event_id] = event
        self._last_event[actor_id] = event.event_id
        return event

    def add_snapshot(self, snapshot: KnowledgeSnapshot) -> None:
        self.snapshots.append(snapshot)

    def add_decision(self, decision: DecisionRecord) -> None:
        self.decisions.append(decision)

    def add_fact_version(self, fact: FactVersionRecord) -> None:
        if any(item.version_id == fact.version_id for item in self.fact_versions):
            raise ValueError(f"duplicate fact version {fact.version_id!r}")
        if not fact.supersedes_version_ids:
            source_actor = self._event_by_id[fact.source_event_id].actor_id

            def overlaps(left: object, right: object) -> bool:
                if left is None or right is None:
                    return True
                if isinstance(left, tuple) and isinstance(right, tuple):
                    return left[0] <= right[1] and right[0] <= left[1]
                return left == right

            superseded = tuple(
                item.version_id
                for item in self.fact_versions
                if item.fact_key == fact.fact_key
                and item.authoritative == fact.authoritative
                and item.visible_to == fact.visible_to
                and self._event_by_id[item.source_event_id].actor_id == source_actor
                and item.world_time <= fact.world_time
                and overlaps(item.scope, fact.scope)
            )
            if superseded:
                fact = fact.model_copy(update={"supersedes_version_ids": superseded})
        self.fact_versions.append(fact)

    def add_world_branch_commitment(
        self, commitment: WorldBranchCommitmentRecord
    ) -> None:
        if any(
            item.commitment_id == commitment.commitment_id
            for item in self.world_branch_commitments
        ):
            raise ValueError(f"duplicate world commitment {commitment.commitment_id!r}")
        self.world_branch_commitments.append(commitment)

    def add_policy_commitment(self, commitment: PolicyCommitmentRecord) -> None:
        if any(
            item.commitment_id == commitment.commitment_id
            for item in self.policy_commitments
        ):
            raise ValueError(
                f"duplicate policy commitment {commitment.commitment_id!r}"
            )
        self.policy_commitments.append(commitment)

    def add_fault_manifestation(self, record: FaultManifestationRecord) -> None:
        if any(item.fault_id == record.fault_id for item in self.fault_manifestations):
            raise ValueError(f"duplicate fault manifestation {record.fault_id!r}")
        self.fault_manifestations.append(record)

    def build(
        self,
        configuration: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
        source_trace: str | None = None,
    ) -> DistributedTrace:
        trace = DistributedTrace(
            schema_version=self.schema_version,
            metric_version=self.metric_version,
            run_id=self.run_id,
            task_id=self.task_id,
            actors=self.actor_ids,
            events=tuple(self.events),
            knowledge_snapshots=tuple(self.snapshots),
            decisions=tuple(self.decisions),
            fact_versions=tuple(self.fact_versions),
            world_branch_commitments=tuple(self.world_branch_commitments),
            policy_commitments=tuple(self.policy_commitments),
            configuration=configuration or {},
            outcome=outcome or {},
            source_trace=source_trace,
            team_id=self.team_id,
            team_spec_digest=self.team_spec_digest,
            role_refinement_digest=self.role_refinement_digest,
            fault_manifestations=tuple(self.fault_manifestations),
        )
        validate_trace(trace)
        return trace


def validate_trace(trace: DistributedTrace) -> None:
    by_id: dict[str, TraceEvent] = {}
    last_sequence: defaultdict[str, int] = defaultdict(int)
    last_clock: dict[str, dict[str, int]] = {}
    sends: dict[str, TraceEvent] = {}
    receives: list[TraceEvent] = []
    valid_clock_components = {*trace.actors, "world"}
    for event in trace.events:
        if event.event_id in by_id:
            raise ValueError(f"duplicate trace event ID {event.event_id}")
        if event.actor_id not in (*trace.actors, "world"):
            raise ValueError(f"unknown actor in trace: {event.actor_id}")
        if set(event.vector_clock) != valid_clock_components:
            raise ValueError(
                f"event {event.event_id} has malformed vector-clock components"
            )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in event.vector_clock.values()
        ):
            raise ValueError(f"event {event.event_id} has a malformed vector clock")
        if event.local_sequence != last_sequence[event.actor_id] + 1:
            raise ValueError(f"non-contiguous local sequence for {event.actor_id}")
        last_sequence[event.actor_id] = event.local_sequence
        previous_clock = last_clock.get(event.actor_id)
        if previous_clock is not None and not happens_before(
            previous_clock, event.vector_clock
        ):
            raise ValueError(f"clock regression for {event.actor_id}")
        last_clock[event.actor_id] = event.vector_clock
        for parent in event.causal_parents:
            if parent not in by_id:
                raise ValueError(
                    f"event {event.event_id} has missing or forward parent {parent}"
                )
            if not happens_before(by_id[parent].vector_clock, event.vector_clock):
                raise ValueError(
                    f"event {event.event_id} clock does not dominate parent {parent}"
                )
        if event.kind == EventKind.MESSAGE_SEND:
            if not event.message_id:
                raise ValueError("message send has no message ID")
            if event.message_id in sends:
                raise ValueError(f"duplicate message send {event.message_id}")
            sends[event.message_id] = event
        if event.kind == EventKind.MESSAGE_RECEIVE:
            receives.append(event)
        by_id[event.event_id] = event
    for receive in receives:
        if not receive.message_id or receive.message_id not in sends:
            raise ValueError("message receive has no corresponding send")
        send = sends[receive.message_id]
        if not happens_before(send.vector_clock, receive.vector_clock):
            raise ValueError("receive vector clock does not dominate send")

    fact_ids = [fact.version_id for fact in trace.fact_versions]
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("duplicate fact-version ID in trace")
    known_fact_ids = set(fact_ids)
    for fact in trace.fact_versions:
        if fact.source_event_id not in by_id:
            raise ValueError(
                f"fact version {fact.version_id!r} has an unknown source event"
            )
        if not set(fact.evidence_ids) <= set(by_id):
            raise ValueError(f"fact version {fact.version_id!r} has unknown evidence")
        if fact.origin_version_id and fact.origin_version_id not in known_fact_ids:
            raise ValueError(
                f"fact version {fact.version_id!r} has an unknown origin version"
            )
        if not set(fact.supersedes_version_ids) <= known_fact_ids:
            raise ValueError(
                f"fact version {fact.version_id!r} supersedes an unknown version"
            )
        for prior_id in fact.supersedes_version_ids:
            prior = next(
                item for item in trace.fact_versions if item.version_id == prior_id
            )
            if prior.fact_key != fact.fact_key or prior.world_time > fact.world_time:
                raise ValueError(
                    f"fact version {fact.version_id!r} has invalid supersession"
                )
        if not set(fact.visible_to) <= set(trace.actors):
            raise ValueError(
                f"fact version {fact.version_id!r} is visible to an unknown actor"
            )

    decision_ids = [decision.decision_id for decision in trace.decisions]
    if len(decision_ids) != len(set(decision_ids)):
        raise ValueError("duplicate decision ID in trace")
    snapshots = {
        (snapshot.actor_id, snapshot.logical_time, snapshot.digest)
        for snapshot in trace.knowledge_snapshots
    }
    for decision in trace.decisions:
        event = by_id.get(decision.decision_id)
        if event is None or event.kind != EventKind.DECISION:
            raise ValueError(
                f"decision {decision.decision_id!r} has no decision trace event"
            )
        if (
            decision.knowledge_snapshot.actor_id,
            decision.knowledge_snapshot.logical_time,
            decision.knowledge_snapshot.digest,
        ) not in snapshots:
            raise ValueError(
                f"decision {decision.decision_id!r} references an unknown snapshot"
            )
        if decision.knowledge_snapshot.actor_id != decision.actor_id:
            raise ValueError("decision and knowledge-snapshot actors disagree")
        if not set(decision.prompt_item_ids) <= set(
            decision.knowledge_snapshot.item_ids
        ):
            raise ValueError("decision prompt contains unavailable knowledge")
        if not set(decision.prompt_message_ids) <= set(
            decision.knowledge_snapshot.inbox_frontier
        ):
            raise ValueError("decision prompt contains an undelivered message")
    commitment_ids = [item.commitment_id for item in trace.policy_commitments]
    if len(commitment_ids) != len(set(commitment_ids)):
        raise ValueError("duplicate policy commitment ID in trace")
    decisions_by_id = {decision.decision_id: decision for decision in trace.decisions}
    for commitment in trace.policy_commitments:
        decision = decisions_by_id.get(commitment.decision_id)
        event = by_id.get(commitment.commitment_id)
        if decision is None:
            raise ValueError("policy commitment references an unknown decision")
        if event is None or event.kind != EventKind.POLICY_COMMITMENT:
            raise ValueError("policy commitment has no commitment trace event")
        if commitment.actor_id != decision.actor_id:
            raise ValueError("policy commitment and decision actors disagree")
        if commitment.knowledge_snapshot_digest != decision.knowledge_snapshot.digest:
            raise ValueError("policy commitment uses a different knowledge snapshot")
        if event.logical_time > decision.logical_time:
            raise ValueError("policy commitment was recorded after its decision")
        if not happens_before(
            event.vector_clock, by_id[decision.decision_id].vector_clock
        ):
            raise ValueError("policy commitment does not happen-before its decision")
        if not set(commitment.supporting_item_ids) <= set(
            decision.knowledge_snapshot.item_ids
        ):
            raise ValueError("policy commitment uses unavailable knowledge")
    branch_ids = [item.commitment_id for item in trace.world_branch_commitments]
    if len(branch_ids) != len(set(branch_ids)):
        raise ValueError("duplicate world-branch commitment ID in trace")
    for commitment in trace.world_branch_commitments:
        event = by_id.get(commitment.commitment_id)
        if event is None or event.kind != EventKind.BRANCH_COMMITMENT:
            raise ValueError("world-branch commitment has no commitment trace event")
        if not set(commitment.evidence_fact_version_ids) <= known_fact_ids:
            raise ValueError("world-branch commitment has unknown evidence")
        if any(
            next(
                fact.world_time
                for fact in trace.fact_versions
                if fact.version_id == version_id
            )
            > commitment.world_time
            for version_id in commitment.evidence_fact_version_ids
        ):
            raise ValueError("world-branch commitment uses future evidence")
    fault_ids = [item.fault_id for item in trace.fault_manifestations]
    if len(fault_ids) != len(set(fault_ids)):
        raise ValueError("duplicate fault-manifestation ID")
    for item in trace.fault_manifestations:
        if not set(item.evidence_event_ids) <= set(by_id):
            raise ValueError("fault manifestation references an unknown event")
        if trace.schema_version == "dcore_trace_v5" and item.mode != "none":
            if item.manifested and not item.evidence_event_ids:
                raise ValueError("manifested v5 fault has no trace evidence")
    for event in trace.events:
        if (
            event.kind == EventKind.ACTION
            and event.decision_context_id
            and event.decision_context_id not in set(decision_ids)
        ):
            raise ValueError(
                f"action {event.event_id!r} references an unknown decision"
            )

    adjacency: dict[str, list[str]] = {event_id: [] for event_id in by_id}
    for event in trace.events:
        for parent in event.causal_parents:
            adjacency[parent].append(event.event_id)
    # Kahn's algorithm avoids Python recursion limits on full-season,
    # multi-agent traces while retaining exact cycle detection.
    indegree = {event_id: 0 for event_id in adjacency}
    for children in adjacency.values():
        for child in children:
            indegree[child] += 1
    frontier = sorted(event_id for event_id, degree in indegree.items() if degree == 0)
    visited_count = 0
    while frontier:
        node = frontier.pop(0)
        visited_count += 1
        for child in adjacency[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                frontier.append(child)
        frontier.sort()
    if visited_count != len(adjacency):
        raise ValueError("observed causal graph contains a cycle")
