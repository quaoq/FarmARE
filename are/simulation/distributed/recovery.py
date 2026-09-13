"""Recorded native failure/retry episodes, separate from policy recovery.

Successful retry means native acceptance of the same actor/tool/region. It is
not proof of agronomic success, causal yield recovery, or a correct policy.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from are.simulation.distributed.farm_adapter import scope_from_args
from are.simulation.distributed.models import EventKind, TraceEvent, stable_digest


def native_retry_profile(events: Iterable[TraceEvent]) -> dict[str, Any]:
    pending: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    episodes = []
    for event in events:
        if event.kind != EventKind.ACTION or not event.payload.get("write"):
            continue
        scope = scope_from_args(event.args) or event.payload.get("scope")
        key = (event.actor_id, event.action, stable_digest(scope))
        if event.status == "error":
            episode = pending.setdefault(
                key,
                {
                    "actor_id": event.actor_id,
                    "action": event.action,
                    "scope": scope,
                    "first_failure_world_time": event.world_time,
                    "failed_event_ids": [],
                    "initial_arguments": event.args,
                },
            )
            episode["failed_event_ids"].append(event.event_id)
        elif event.status == "ok" and event.farmare_event_id and key in pending:
            episode = pending.pop(key)
            episodes.append(
                {
                    **episode,
                    "accepted_retry_event_id": event.event_id,
                    "accepted_retry_farmare_event_id": event.farmare_event_id,
                    "latency_seconds": event.world_time
                    - episode["first_failure_world_time"],
                    "arguments_changed": episode["initial_arguments"] != event.args,
                }
            )
    episodes.extend(
        {
            **episode,
            "accepted_retry_event_id": None,
            "accepted_retry_farmare_event_id": None,
            "latency_seconds": None,
            "arguments_changed": None,
        }
        for episode in pending.values()
    )
    episodes.sort(
        key=lambda item: (
            item["first_failure_world_time"],
            item["actor_id"],
            item["failed_event_ids"][0],
        )
    )
    return {
        "schema_version": "farm_native_retry_profile_v1",
        "failure_count": sum(len(item["failed_event_ids"]) for item in episodes),
        "episode_count": len(episodes),
        "accepted_retry_count": sum(
            item["accepted_retry_event_id"] is not None for item in episodes
        ),
        "unresolved_episode_count": sum(
            item["accepted_retry_event_id"] is None for item in episodes
        ),
        "episodes": episodes,
        "interpretation": "Recorded native acceptance after failure; not causal yield recovery or policy conformance.",
    }
