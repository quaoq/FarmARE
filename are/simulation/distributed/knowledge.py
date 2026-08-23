"""Append-only, actor-local knowledge stores."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from are.simulation.distributed.clock import ClockRelation, compare
from are.simulation.distributed.models import (
    CausalHandoff,
    Envelope,
    EpistemicStatus,
    KnowledgeItem,
    KnowledgeSnapshot,
    stable_digest,
)


class KnowledgeStore:
    def __init__(self, actor_id: str):
        self.actor_id = actor_id
        self._items: list[KnowledgeItem] = []
        self._by_id: dict[str, KnowledgeItem] = {}
        self._message_ids: set[str] = set()
        self._inbox_frontier: list[str] = []

    @property
    def items(self) -> tuple[KnowledgeItem, ...]:
        return tuple(self._items)

    @property
    def inbox_frontier(self) -> tuple[str, ...]:
        return tuple(self._inbox_frontier)

    def add(self, item: KnowledgeItem) -> bool:
        if item.item_id in self._by_id:
            return False
        self._items.append(item)
        self._by_id[item.item_id] = item
        return True

    def receive(self, envelope: Envelope, learned_at: float) -> list[KnowledgeItem]:
        if envelope.message_id in self._message_ids:
            return []
        self._message_ids.add(envelope.message_id)
        self._inbox_frontier.append(envelope.message_id)
        if not isinstance(envelope, CausalHandoff):
            return []
        added: list[KnowledgeItem] = []
        for index, claim in enumerate(envelope.claims):
            item = KnowledgeItem(
                item_id=f"{envelope.message_id}:claim:{index}",
                fact_key=claim.fact_key,
                value=claim.value,
                scope=claim.scope,
                status=EpistemicStatus.CLAIMED,
                source_actor=envelope.sender,
                evidence_ids=claim.evidence_ids,
                observed_at=claim.observed_at,
                learned_at=learned_at,
                valid_until=claim.valid_until,
                confidence=claim.confidence,
                causal_parents=claim.causal_parents,
                vector_clock=envelope.vector_clock,
                message_id=envelope.message_id,
            )
            if self.add(item):
                added.append(item)
        return added

    def latest(self, fact_key: str) -> KnowledgeItem | None:
        candidates = [item for item in self._items if item.fact_key == fact_key]
        if not candidates:
            return None

        def rank(item: KnowledgeItem) -> tuple[float, float, str]:
            return (item.observed_at, item.learned_at, item.item_id)

        latest = max(candidates, key=rank)
        for candidate in candidates:
            relation = compare(latest.vector_clock, candidate.vector_clock)
            if relation == ClockRelation.BEFORE:
                latest = candidate
        return latest

    def for_keys(self, fact_keys: Iterable[str]) -> tuple[KnowledgeItem, ...]:
        found = []
        for key in fact_keys:
            item = self.latest(key)
            if item is not None:
                found.append(item)
        return tuple(found)

    def snapshot(
        self, logical_time: float, vector_clock: dict[str, int]
    ) -> KnowledgeSnapshot:
        item_ids = tuple(item.item_id for item in self._items)
        digest = stable_digest(
            {
                "actor_id": self.actor_id,
                "logical_time": logical_time,
                "item_ids": item_ids,
                "clock": vector_clock,
                "inbox": self._inbox_frontier,
            }
        )
        return KnowledgeSnapshot(
            actor_id=self.actor_id,
            logical_time=logical_time,
            item_ids=item_ids,
            vector_clock=vector_clock,
            inbox_frontier=tuple(self._inbox_frontier),
            digest=digest,
        )


def synchronization_lags(
    stores: Iterable[KnowledgeStore],
) -> dict[str, dict[str, float | None]]:
    observations: defaultdict[str, list[KnowledgeItem]] = defaultdict(list)
    for store in stores:
        for item in store.items:
            observations[item.fact_key].append(item)
    result: dict[str, dict[str, float | None]] = {}
    for fact_key, items in observations.items():
        world_time = min(item.observed_at for item in items)
        result[fact_key] = {}
        for store in stores:
            local = [item for item in store.items if item.fact_key == fact_key]
            result[fact_key][store.actor_id] = (
                min(item.learned_at for item in local) - world_time if local else None
            )
    return result
