"""Deterministic logical-time scheduling and bounded interleaving enumeration."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable


class SchedulerPriority(IntEnum):
    WORLD_EFFECT = 10
    MESSAGE_DELIVERY = 20
    TOOL_COMPLETION = 30
    ACTOR_ACTIVATION = 40
    DEADLINE = 50
    WATERMARK = 60
    TERMINATION = 70


@dataclass(order=True, frozen=True)
class SchedulerItem:
    logical_time: float
    priority: int
    actor_id: str
    insertion_sequence: int
    kind: str = field(compare=False)
    payload: dict[str, Any] = field(default_factory=dict, compare=False)


class DeterministicScheduler:
    def __init__(self) -> None:
        self._queue: list[SchedulerItem] = []
        self._sequence = 0
        self.logical_time = 0.0

    def schedule(
        self,
        logical_time: float,
        priority: SchedulerPriority | int,
        actor_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
    ) -> SchedulerItem:
        if logical_time < self.logical_time:
            raise ValueError("cannot schedule an event in the logical past")
        self._sequence += 1
        item = SchedulerItem(
            logical_time=float(logical_time),
            priority=int(priority),
            actor_id=actor_id,
            insertion_sequence=self._sequence,
            kind=kind,
            payload=payload or {},
        )
        heapq.heappush(self._queue, item)
        return item

    def pop(self) -> SchedulerItem:
        if not self._queue:
            raise IndexError("scheduler is empty")
        item = heapq.heappop(self._queue)
        self.logical_time = item.logical_time
        return item

    def peek(self) -> SchedulerItem | None:
        return self._queue[0] if self._queue else None

    def __bool__(self) -> bool:
        return bool(self._queue)

    def snapshot(self) -> tuple[SchedulerItem, ...]:
        return tuple(sorted(self._queue))


def enumerate_interleavings(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
    *,
    max_schedules: int = 10_000,
    independence_keys: dict[str, str] | None = None,
) -> list[tuple[str, ...]]:
    """Enumerate bounded topological schedules with safe symmetry reduction.

    Nodes that are simultaneously enabled and share an independence key are
    explored in lexical order only. Callers should use a common key solely for
    operations known to commute.
    """

    node_set = set(nodes)
    successors = {node: set() for node in node_set}
    indegree = {node: 0 for node in node_set}
    for source, target in edges:
        if source not in node_set or target not in node_set:
            raise ValueError("interleaving edge references an unknown node")
        if target not in successors[source]:
            successors[source].add(target)
            indegree[target] += 1
    schedules: list[tuple[str, ...]] = []

    def explore(prefix: list[str], current_indegree: dict[str, int]) -> None:
        if len(schedules) >= max_schedules:
            return
        if len(prefix) == len(node_set):
            schedules.append(tuple(prefix))
            return
        enabled = sorted(
            node
            for node in node_set
            if node not in prefix and current_indegree[node] == 0
        )
        if not enabled:
            raise ValueError("interleaving graph contains a cycle")
        representatives: list[str] = []
        seen_keys: set[str] = set()
        for node in enabled:
            key = (independence_keys or {}).get(node)
            if key is not None and key in seen_keys:
                continue
            if key is not None:
                seen_keys.add(key)
            representatives.append(node)
        for node in representatives:
            next_indegree = dict(current_indegree)
            for child in successors[node]:
                next_indegree[child] -= 1
            explore([*prefix, node], next_indegree)

    explore([], indegree)
    return schedules
