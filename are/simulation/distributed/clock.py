"""Vector-clock operations used by the D-CORE recorder."""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class ClockRelation(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    EQUAL = "equal"
    CONCURRENT = "concurrent"


def normalize(clock: Mapping[str, int], components: tuple[str, ...]) -> dict[str, int]:
    return {component: int(clock.get(component, 0)) for component in components}


def tick(clock: Mapping[str, int], actor_id: str) -> dict[str, int]:
    result = dict(clock)
    result[actor_id] = result.get(actor_id, 0) + 1
    return result


def merge(*clocks: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for clock in clocks:
        for actor_id, value in clock.items():
            result[actor_id] = max(result.get(actor_id, 0), int(value))
    return result


def compare(left: Mapping[str, int], right: Mapping[str, int]) -> ClockRelation:
    components = set(left) | set(right)
    left_le = all(
        left.get(component, 0) <= right.get(component, 0) for component in components
    )
    right_le = all(
        right.get(component, 0) <= left.get(component, 0) for component in components
    )
    if left_le and right_le:
        return ClockRelation.EQUAL
    if left_le:
        return ClockRelation.BEFORE
    if right_le:
        return ClockRelation.AFTER
    return ClockRelation.CONCURRENT


def happens_before(left: Mapping[str, int], right: Mapping[str, int]) -> bool:
    return compare(left, right) == ClockRelation.BEFORE
