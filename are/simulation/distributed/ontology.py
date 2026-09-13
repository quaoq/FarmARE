"""Minimal public coordination vocabulary, derived solely from tool grants.

No world state, oracle workflow, private observations, or agronomic thresholds
belong in capability cards. They describe permission, never current readiness.
"""

from __future__ import annotations

from typing import Any

from are.simulation.distributed.models import AgentTeamSpec, stable_digest
from are.simulation.distributed.teams import recipients_for, shortest_path, team_digest

COORDINATION_CONTRACT = (
    "Capability cards list permissions, not current state or successful execution. "
    "Route a task to its tool owner via a permitted next hop. If you cannot do a "
    "requested task, report the refusal and owner; never substitute a different "
    "operation. Preserve explicit inclusive ridge ranges, units, observation time, "
    "validity and evidence IDs when handing off information. A sensor ID is not a "
    "ridge ID. A proposal or message is not an execution receipt. After a rejected "
    "operation, inspect the error, obtain fresh evidence or resources, and replan, "
    "reroute, defer or abstain. Do not claim success without an accepted receipt."
    " Write receipts are local facts named tool_receipt:<tool name>; send their "
    "fact keys to communicate verifiable operation outcomes. Receipts describe "
    "past execution, not current evidence validity or agronomic success."
)


def capability_cards(team: AgentTeamSpec, viewer: str) -> dict[str, Any]:
    """Generate fixed, topology-aware cards without consulting the simulator."""
    if viewer not in {actor.actor_id for actor in team.actors}:
        raise ValueError(f"unknown capability-card viewer {viewer!r}")
    cards = []
    for actor in sorted(team.actors, key=lambda item: item.actor_id):
        try:
            route = shortest_path(team, viewer, actor.actor_id)
        except ValueError:
            route = ()
        cards.append(
            {
                "actor_id": actor.actor_id,
                "role": actor.role,
                "can_use": sorted(actor.permitted_actions),
                "observes": sorted(actor.observable_facts),
                "can_advance_time": actor.actor_id in team.time_authority,
                "message_recipients": sorted(recipients_for(team, actor.actor_id)),
                "route_from_viewer": list(route),
                "next_hop": route[1] if len(route) > 1 else None,
                "tools_not_listed_are_forbidden": True,
            }
        )
    payload = {
        "schema_version": "farm_coordination_ontology_v1",
        "team_digest": team_digest(team),
        "viewer": viewer,
        "scope_convention": "zero-based inclusive ridge ranges; sensor IDs are opaque",
        "entities": ["agent", "tool", "region", "observation", "action", "fact"],
        "relations": [
            "can_use",
            "observes",
            "controls",
            "applies_to",
            "requires_evidence",
        ],
        "fact_fields": [
            "scope",
            "observed_at",
            "source_actor",
            "valid_until",
            "confidence",
            "item_id",
            "evidence_ids",
        ],
        "cards": cards,
    }
    return {**payload, "digest": stable_digest(payload)}
