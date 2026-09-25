"""Append-only, actor-local knowledge stores."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from are.simulation.distributed.models import (
    CausalHandoff,
    Envelope,
    EpistemicStatus,
    KnowledgeItem,
    KnowledgeSnapshot,
    stable_digest,
)


def knowledge_frontier(items: Iterable[KnowledgeItem]) -> tuple[KnowledgeItem, ...]:
    """Latest observation per exact scope; receive clocks do not imply freshness.

    Overlapping but unequal scopes are retained for downstream scope checks.
    History remains append-only so evaluators can reconstruct old decisions.
    """
    frontier: dict[tuple[str, str], tuple[int, KnowledgeItem]] = {}
    for index, item in enumerate(items):
        key = (item.fact_key, repr(item.scope))
        previous = frontier.get(key)
        rank = (item.observed_at, item.learned_at, index)
        if previous is None or rank > (
            previous[1].observed_at,
            previous[1].learned_at,
            previous[0],
        ):
            frontier[key] = (index, item)
    return tuple(
        item
        for _, item in sorted(
            frontier.values(),
            key=lambda entry: (
                entry[1].observed_at,
                entry[1].learned_at,
                entry[0],
            ),
        )
    )


def normalize_scope(value):
    """Normalize a serialized ridge range while preserving resource scopes."""

    if isinstance(value, list) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    return value


def scope_covers(
    actual: tuple[int, int] | str | None, required: tuple[int, int] | str | None
) -> bool:
    """Scope is explicit; an unknown scope never establishes regional coverage."""
    actual, required = normalize_scope(actual), normalize_scope(required)
    if required is None:
        return True
    if actual is None:
        return False
    if isinstance(actual, tuple) and isinstance(required, tuple):
        return actual[0] <= required[0] and actual[1] >= required[1]
    return actual == required


def scope_satisfies(
    actual: tuple[int, int] | str | None,
    required: tuple[int, int] | str | None,
    match: str = "covers",
) -> bool:
    """Apply one authored scope rule across runtime, diagnosis and evaluation."""

    actual, required = normalize_scope(actual), normalize_scope(required)
    if required is None:
        return True
    if match == "exact":
        return actual == required
    if match != "covers":
        raise ValueError(f"unknown scope matching rule {match!r}")
    return scope_covers(actual, required)


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

    def latest(
        self,
        fact_key: str,
        *,
        scope: tuple[int, int] | str | None = None,
        scope_match: str = "covers",
        at: float | None = None,
    ) -> KnowledgeItem | None:
        candidates = [
            (index, item)
            for index, item in enumerate(self._items)
            if item.fact_key == fact_key
            and scope_satisfies(item.scope, scope, scope_match)
            and (at is None or (item.observed_at <= at and item.learned_at <= at))
        ]
        if not candidates:
            return None

        return max(
            candidates,
            key=lambda entry: (
                entry[1].observed_at,
                entry[1].learned_at,
                entry[0],
            ),
        )[1]

    def for_keys(self, fact_keys: Iterable[str]) -> tuple[KnowledgeItem, ...]:
        keys = set(fact_keys)
        return knowledge_frontier(item for item in self._items if item.fact_key in keys)

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
