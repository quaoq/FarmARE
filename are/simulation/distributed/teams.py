"""Declarative team, topology, and role-refinement support for Farm D-CORE."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable

from are.simulation.distributed.models import (
    ActorSpec,
    AgentTeamSpec,
    CommunicationTopologySpec,
    DistributedRunnerConfig,
    RoleRefinementSpec,
    stable_digest,
)
from are.simulation.distributed.petri import (
    ArcSpec,
    PetriNetSpec,
    PlaceSpec,
    TransitionKind,
    TransitionSpec,
)
from are.simulation.tool_utils import AppTool

FIELD_INTELLIGENCE = "field_intelligence"
OPERATIONS = "operations"
SCOUTING = "scouting"
AGRONOMY = "agronomy"
RESOURCE_MANAGEMENT = "resource_management"

PRIMARY_TEAM_ID = "wetjune_2agent"
THREE_AGENT_TEAM_ID = "wetjune_3agent"
FOUR_AGENT_TEAM_ID = "wetjune_4agent"

RESOURCE_FUNCTIONS = {
    "get_inventory",
    "get_status",
    "attach_implement",
    "detach_implement",
    "load_seeds",
    "load_fertilizer",
    "load_fungicide",
    "load_pesticide",
    "refill_pesticide_tank",
    "refuel",
}


def is_intelligence_tool(tool: AppTool) -> bool:
    if tool.class_name in {"WeatherApp", "SensorApp", "DroneApp", "RobotApp"}:
        return True
    return bool(
        tool.class_name == "FarmWorldApp"
        and tool.func_name != "get_inventory"
        and tool.func_name
        and tool.func_name.startswith(("get_", "read_"))
    )


def owner_for_team_tool(tool: AppTool, team_id: str) -> str:
    """Resolve one exclusive owner while preserving aggregate farm capability."""

    if team_id == PRIMARY_TEAM_ID:
        return FIELD_INTELLIGENCE if is_intelligence_tool(tool) else OPERATIONS
    if team_id == THREE_AGENT_TEAM_ID:
        return SCOUTING if is_intelligence_tool(tool) else OPERATIONS
    if team_id == FOUR_AGENT_TEAM_ID:
        if is_intelligence_tool(tool):
            # Charging is an equipment-readiness responsibility in this
            # decomposition; all sensor results remain scouting-only.
            if tool.func_name == "charge":
                return RESOURCE_MANAGEMENT
            return SCOUTING
        if tool.func_name in RESOURCE_FUNCTIONS:
            return RESOURCE_MANAGEMENT
        return OPERATIONS
    raise ValueError(f"unknown built-in team {team_id!r}")


def _roles(team_id: str) -> tuple[tuple[str, str], ...]:
    if team_id == PRIMARY_TEAM_ID:
        return (
            (
                FIELD_INTELLIGENCE,
                "Field Intelligence Agent: observe, diagnose, and communicate evidence",
            ),
            (
                OPERATIONS,
                "Operations Agent: manage equipment, time, interventions, harvest, and storage",
            ),
        )
    if team_id == THREE_AGENT_TEAM_ID:
        return (
            (SCOUTING, "Sensing and Scouting Agent: acquire scoped farm evidence"),
            (
                AGRONOMY,
                "Agronomic Diagnosis Agent: interpret evidence and issue causal recommendations",
            ),
            (
                OPERATIONS,
                "Farm Operations Agent: execute physical work and advance the season",
            ),
        )
    if team_id == FOUR_AGENT_TEAM_ID:
        return (
            (SCOUTING, "Sensing and Scouting Agent: acquire scoped farm evidence"),
            (
                AGRONOMY,
                "Agronomic Diagnosis Agent: interpret evidence and issue causal recommendations",
            ),
            (
                RESOURCE_MANAGEMENT,
                "Equipment and Resource Agent: verify and prepare inventory and machinery",
            ),
            (
                OPERATIONS,
                "Field Operations Agent: execute agronomic work and advance the season",
            ),
        )
    raise ValueError(f"unknown built-in team {team_id!r}")


def _topology(team_id: str) -> CommunicationTopologySpec:
    if team_id == PRIMARY_TEAM_ID:
        return CommunicationTopologySpec(
            topology_id="two-agent-direct",
            kind="fully_connected",
        )
    if team_id == THREE_AGENT_TEAM_ID:
        edges = (
            (SCOUTING, AGRONOMY),
            (AGRONOMY, SCOUTING),
            (AGRONOMY, OPERATIONS),
            (OPERATIONS, AGRONOMY),
        )
        return CommunicationTopologySpec(
            topology_id="scout-agronomy-operations",
            kind="hierarchical",
            edges=edges,
            center_actor=AGRONOMY,
        )
    edges = (
        (SCOUTING, AGRONOMY),
        (AGRONOMY, SCOUTING),
        (AGRONOMY, OPERATIONS),
        (OPERATIONS, AGRONOMY),
        (RESOURCE_MANAGEMENT, OPERATIONS),
        (OPERATIONS, RESOURCE_MANAGEMENT),
    )
    return CommunicationTopologySpec(
        topology_id="scout-agronomy-plus-resource-operations",
        kind="explicit",
        edges=edges,
    )


def build_builtin_team(team_id: str, tools: Iterable[AppTool]) -> AgentTeamSpec:
    """Resolve a built-in role decomposition against native FarmARE tools."""

    grants: defaultdict[str, list[str]] = defaultdict(list)
    schemas: defaultdict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for tool in tools:
        if tool.name.startswith("AgentUserInterface__"):
            continue
        owner = owner_for_team_tool(tool, team_id)
        grants[owner].append(tool.name)
        schemas[owner][tool.name] = {}
    actors = tuple(
        ActorSpec(
            actor_id=actor_id,
            role=role,
            permitted_actions=tuple(sorted(grants[actor_id])),
            tool_schemas=schemas[actor_id],
        )
        for actor_id, role in _roles(team_id)
    )
    return AgentTeamSpec(
        team_id=team_id,
        actors=actors,
        topology=_topology(team_id),
        activation_policy="round_robin",
        activation_priorities={
            actor.actor_id: index for index, actor in enumerate(actors)
        },
        time_authority=(OPERATIONS,),
        expert_review_status="unreviewed",
        metadata={
            "primary_condition": team_id == PRIMARY_TEAM_ID,
            "role_decomposition": "built_in_predeclared",
        },
    )


def _edges_for_kind(
    kind: str, actor_ids: tuple[str, ...], current: CommunicationTopologySpec
) -> tuple[tuple[str, str], ...]:
    if kind in {"fully_connected", "shared_blackboard"}:
        return tuple(
            (source, target)
            for source in actor_ids
            for target in actor_ids
            if source != target
        )
    if kind == current.kind:
        return current.edges
    raise ValueError(f"topology override {kind!r} needs an explicit team specification")


def load_team_spec(
    config: DistributedRunnerConfig, tools: Iterable[AppTool]
) -> AgentTeamSpec:
    if config.team_spec_path:
        team = AgentTeamSpec.model_validate_json(
            Path(config.team_spec_path).read_text(encoding="utf-8")
        )
    else:
        team = build_builtin_team(config.team_id, tools)
    actor_ids = tuple(actor.actor_id for actor in team.actors)
    updates: dict[str, object] = {}
    if config.activation_policy:
        updates["activation_policy"] = config.activation_policy
    if config.communication_topology:
        updates["topology"] = team.topology.model_copy(
            update={
                "kind": config.communication_topology,
                "edges": _edges_for_kind(
                    config.communication_topology, actor_ids, team.topology
                ),
                "topology_id": f"override:{config.communication_topology}",
            }
        )
    if config.team_call_budget:
        updates["team_call_budget"] = config.team_call_budget
    if config.per_agent_call_budget:
        updates["per_agent_call_budget"] = {
            actor: config.per_agent_call_budget for actor in actor_ids
        }
    if config.team_token_budget:
        updates["team_token_budget"] = config.team_token_budget
    if config.per_agent_token_budget:
        updates["per_agent_token_budget"] = {
            actor: config.per_agent_token_budget for actor in actor_ids
        }
    return (
        AgentTeamSpec.model_validate({**team.model_dump(mode="python"), **updates})
        if updates
        else team
    )


def topology_edges(team: AgentTeamSpec) -> tuple[tuple[str, str], ...]:
    actors = tuple(actor.actor_id for actor in team.actors)
    if team.topology.kind in {"fully_connected", "shared_blackboard"}:
        return _edges_for_kind(team.topology.kind, actors, team.topology)
    return team.topology.edges


def can_send(team: AgentTeamSpec, sender: str, recipient: str) -> bool:
    return (sender, recipient) in set(topology_edges(team))


def recipients_for(team: AgentTeamSpec, sender: str) -> tuple[str, ...]:
    return tuple(target for source, target in topology_edges(team) if source == sender)


def shortest_path(team: AgentTeamSpec, sender: str, recipient: str) -> tuple[str, ...]:
    """Return the deterministic shortest path in the declared directed graph."""

    if sender == recipient:
        return (sender,)
    adjacency: defaultdict[str, list[str]] = defaultdict(list)
    for source, target in topology_edges(team):
        adjacency[source].append(target)
    for targets in adjacency.values():
        targets.sort()
    queue = deque([(sender, (sender,))])
    seen = {sender}
    while queue:
        node, path = queue.popleft()
        for target in adjacency[node]:
            if target == recipient:
                return (*path, target)
            if target not in seen:
                seen.add(target)
                queue.append((target, (*path, target)))
    raise ValueError(f"no communication path from {sender!r} to {recipient!r}")


def team_digest(team: AgentTeamSpec) -> str:
    return stable_digest(team.model_dump(mode="json"))


def built_in_role_refinement(
    team: AgentTeamSpec, scenario_id: str
) -> RoleRefinementSpec | None:
    if team.team_id == PRIMARY_TEAM_ID:
        return None
    if team.team_id not in {THREE_AGENT_TEAM_ID, FOUR_AGENT_TEAM_ID}:
        return None
    routes = {
        "agronomic_evidence": shortest_path(team, SCOUTING, OPERATIONS),
    }
    if team.team_id == FOUR_AGENT_TEAM_ID:
        routes["resource_readiness"] = shortest_path(
            team, RESOURCE_MANAGEMENT, OPERATIONS
        )
    return RoleRefinementSpec(
        refinement_id=f"{scenario_id}:{team.team_id}:draft-v1",
        scenario_id=scenario_id,
        source_team_id=PRIMARY_TEAM_ID,
        target_team_id=team.team_id,
        role_mapping={
            FIELD_INTELLIGENCE: (
                (SCOUTING, AGRONOMY)
                if team.team_id in {THREE_AGENT_TEAM_ID, FOUR_AGENT_TEAM_ID}
                else (FIELD_INTELLIGENCE,)
            ),
            OPERATIONS: (
                (RESOURCE_MANAGEMENT, OPERATIONS)
                if team.team_id == FOUR_AGENT_TEAM_ID
                else (OPERATIONS,)
            ),
        },
        communication_paths=routes,
        reviewer_rationale=(
            "Engineering draft for scalability validation; not eligible for paper tables."
        ),
        expert_review_status="unreviewed",
    )


def load_role_refinement(
    config: DistributedRunnerConfig,
    team: AgentTeamSpec,
    scenario_id: str,
) -> RoleRefinementSpec | None:
    """Load an attested role refinement or the built-in engineering draft.

    A custom file is the only path by which a non-primary decomposition can
    become paper eligible.  The runtime verifies its declared endpoints and
    later binds its digest to both the team and the expanded Petri net.
    """

    if config.role_refinement_path:
        refinement = RoleRefinementSpec.model_validate_json(
            Path(config.role_refinement_path).read_text(encoding="utf-8")
        )
        if refinement.scenario_id != scenario_id:
            raise ValueError("role refinement scenario does not match the run")
        if refinement.target_team_id != team.team_id:
            raise ValueError("role refinement target team does not match the run")
        if refinement.source_team_id != PRIMARY_TEAM_ID:
            raise ValueError("role refinement must refine the primary Wet-June team")
        return refinement
    return built_in_role_refinement(team, scenario_id)


def refine_petri_for_team(
    net: PetriNetSpec,
    team: AgentTeamSpec,
    refinement: RoleRefinementSpec | None,
) -> PetriNetSpec:
    """Deterministically refine ownership and handoff paths for a fixed team.

    This is an engineering transformation, not an expert-review shortcut. A
    non-primary result is explicitly ineligible for paper mode until the
    resulting refinement digest is independently confirmed.
    """

    if team.team_id == PRIMARY_TEAM_ID:
        return net
    actor_ids = tuple(actor.actor_id for actor in team.actors)
    actor_set = set(actor_ids)
    owners = {
        action: actor.actor_id
        for actor in team.actors
        for action in actor.permitted_actions
    }
    overrides = refinement.transition_owner_overrides if refinement else {}
    unknown_transition_overrides = set(overrides) - {
        transition.transition_id for transition in net.transitions
    }
    if unknown_transition_overrides:
        raise ValueError(
            "role refinement overrides unknown transitions: "
            f"{sorted(unknown_transition_overrides)}"
        )
    unknown_override_actors = set(overrides.values()) - actor_set
    if unknown_override_actors:
        raise ValueError(
            "role refinement assigns transitions to unknown actors: "
            f"{sorted(unknown_override_actors)}"
        )
    declared_paths = refinement.communication_paths if refinement else {}
    agronomic_path = declared_paths.get("agronomic_evidence")
    mapped_evidence_roles = (
        refinement.role_mapping.get(FIELD_INTELLIGENCE, ()) if refinement else ()
    )
    evidence_actor = (
        agronomic_path[0]
        if agronomic_path
        else (mapped_evidence_roles[0] if mapped_evidence_roles else SCOUTING)
    )
    if evidence_actor not in actor_set:
        raise ValueError("role refinement has no valid agronomic evidence source")
    remapped: dict[str, TransitionSpec] = {}
    for transition in net.transitions:
        actor = overrides.get(transition.transition_id, transition.actor_id)
        if transition.transition_id in overrides:
            pass
        elif transition.action in owners:
            actor = owners[transition.action]
        elif actor == FIELD_INTELLIGENCE:
            actor = evidence_actor
        remapped[transition.transition_id] = transition.model_copy(
            update={"actor_id": actor}
        )

    places = list(net.places)
    arcs = list(net.arcs)
    additions: list[TransitionSpec] = []
    communication_targets: list[tuple[str, str, str]] = []
    for send_id, send in tuple(remapped.items()):
        prefix = "handoff_send:"
        if not send_id.startswith(prefix):
            continue
        target_id = send_id.removeprefix(prefix)
        receive_id = f"handoff_receive:{target_id}"
        if receive_id not in remapped or target_id not in remapped:
            continue
        target_actor = remapped[target_id].actor_id
        path = (
            agronomic_path
            if agronomic_path
            and agronomic_path[0] == evidence_actor
            and agronomic_path[-1] == target_actor
            else shortest_path(team, evidence_actor, target_actor)
        )
        remapped[send_id] = send.model_copy(update={"actor_id": path[0]})
        remapped[receive_id] = remapped[receive_id].model_copy(
            update={"actor_id": path[-1]}
        )
        communication_targets.append((send_id, receive_id, target_id))
        if len(path) <= 2:
            continue
        incoming = next(
            arc
            for arc in arcs
            if arc.target == receive_id
            and any(
                parent.source == send_id and parent.target == arc.source
                for parent in arcs
            )
        )
        arcs.remove(incoming)
        previous_place = incoming.source
        total_weight = remapped[send_id].weight + remapped[receive_id].weight
        hop_weight = total_weight / (2 * (len(path) - 1))
        remapped[send_id] = remapped[send_id].model_copy(update={"weight": hop_weight})
        remapped[receive_id] = remapped[receive_id].model_copy(
            update={"weight": hop_weight}
        )
        for hop in range(1, len(path) - 1):
            receiver = path[hop]
            receive_hop_id = f"{receive_id}:hop{hop}"
            send_hop_id = f"handoff_forward_send:{target_id}:hop{hop}"
            receive_hop = remapped[receive_id].model_copy(
                update={
                    "transition_id": receive_hop_id,
                    "actor_id": receiver,
                    "weight": hop_weight,
                    "template_id": f"team:{team.team_id}:receive:hop{hop}",
                }
            )
            send_hop = remapped[send_id].model_copy(
                update={
                    "transition_id": send_hop_id,
                    "actor_id": receiver,
                    "weight": hop_weight,
                    "template_id": f"team:{team.team_id}:send:hop{hop}",
                }
            )
            additions.extend((receive_hop, send_hop))
            between = PlaceSpec(
                place_id=f"team:{team.team_id}:{target_id}:hop{hop}:received",
                label=f"{receiver} received evidence for {target_id}",
            )
            forwarded = PlaceSpec(
                place_id=f"team:{team.team_id}:{target_id}:hop{hop}:forwarded",
                label=f"{receiver} forwarded evidence for {target_id}",
            )
            places.extend((between, forwarded))
            arcs.extend(
                (
                    ArcSpec(
                        arc_id=f"arc:{previous_place}:{receive_hop_id}",
                        source=previous_place,
                        target=receive_hop_id,
                    ),
                    ArcSpec(
                        arc_id=f"arc:{receive_hop_id}:{between.place_id}",
                        source=receive_hop_id,
                        target=between.place_id,
                    ),
                    ArcSpec(
                        arc_id=f"arc:{between.place_id}:{send_hop_id}",
                        source=between.place_id,
                        target=send_hop_id,
                    ),
                    ArcSpec(
                        arc_id=f"arc:{send_hop_id}:{forwarded.place_id}",
                        source=send_hop_id,
                        target=forwarded.place_id,
                    ),
                )
            )
            previous_place = forwarded.place_id
        arcs.append(
            ArcSpec(
                arc_id=f"arc:{previous_place}:{receive_id}",
                source=previous_place,
                target=receive_id,
            )
        )

    # The four-role decomposition adds an independently visible resource
    # readiness path before high-impact field operations. Its initial token is
    # an explicit synchronization opportunity, not fabricated evidence.
    resource_path = declared_paths.get("resource_readiness")
    if resource_path:
        resource_actor = resource_path[0]
        resource_target = resource_path[-1]
        for _send_id, _receive_id, target_id in communication_targets:
            target = remapped[target_id]
            if not target.high_impact or target.actor_id != resource_target:
                continue
            send_id = f"resource_handoff_send:{target_id}"
            receive_id = f"resource_handoff_receive:{target_id}"
            start = PlaceSpec(
                place_id=f"resource_handoff_ready:{target_id}",
                label=f"resource readiness opportunity for {target_id}",
                initially_marked=True,
            )
            sent = PlaceSpec(
                place_id=f"resource_handoff_sent:{target_id}",
                label=f"resource readiness sent for {target_id}",
            )
            received = PlaceSpec(
                place_id=f"resource_handoff_received:{target_id}",
                label=f"resource readiness received for {target_id}",
            )
            module_id = target.module_id
            additions.extend(
                (
                    TransitionSpec(
                        transition_id=send_id,
                        label=f"resource readiness handoff for {target.label}",
                        actor_id=resource_actor,
                        action="farm.resource_handoff",
                        kind=TransitionKind.SEND,
                        phase=target.phase,
                        required=True,
                        weight=target.weight / 8,
                        module_id=module_id,
                        template_id=f"team:{team.team_id}:resource-send",
                        # Resource evidence is charged once through its
                        # semantic CC obligation, not as extra EF mass.
                        scoring_class="optional_safe",
                    ),
                    TransitionSpec(
                        transition_id=receive_id,
                        label=f"receive resource readiness for {target.label}",
                        actor_id=resource_target,
                        action="farm.resource_receive",
                        kind=TransitionKind.RECEIVE,
                        phase=target.phase,
                        required=True,
                        weight=target.weight / 8,
                        module_id=module_id,
                        template_id=f"team:{team.team_id}:resource-receive",
                        scoring_class="optional_safe",
                    ),
                )
            )
            places.extend((start, sent, received))
            arcs.extend(
                (
                    ArcSpec(
                        arc_id=f"arc:{start.place_id}:{send_id}",
                        source=start.place_id,
                        target=send_id,
                    ),
                    ArcSpec(
                        arc_id=f"arc:{send_id}:{sent.place_id}",
                        source=send_id,
                        target=sent.place_id,
                    ),
                    ArcSpec(
                        arc_id=f"arc:{sent.place_id}:{receive_id}",
                        source=sent.place_id,
                        target=receive_id,
                    ),
                    ArcSpec(
                        arc_id=f"arc:{receive_id}:{received.place_id}",
                        source=receive_id,
                        target=received.place_id,
                    ),
                    ArcSpec(
                        arc_id=f"arc:{received.place_id}:{target_id}",
                        source=received.place_id,
                        target=target_id,
                    ),
                )
            )

    refinement_digest = (
        stable_digest(refinement.model_dump(mode="json")) if refinement else None
    )
    metadata = {
        **net.metadata,
        "base_petri_spec_digest": stable_digest(net.model_dump(mode="json")),
        "team_id": team.team_id,
        "team_spec_digest": team_digest(team),
        "role_refinement_digest": refinement_digest,
        "role_refinement_status": (
            refinement.expert_review_status if refinement else "not_applicable"
        ),
        "reviewed_communication_paths": {
            key: list(path) for key, path in declared_paths.items()
        },
        "paper_eligible": bool(
            net.metadata.get("paper_eligible")
            and refinement
            and refinement.expert_review_status == "confirmed"
        ),
    }
    policies = []
    for raw in metadata.get("information_policies", []):
        policy = dict(raw)
        patterns = set(policy.get("action_patterns", []))
        candidate_owners = {
            owner for action, owner in owners.items() if action in patterns
        }
        if len(candidate_owners) == 1:
            policy["actor_id"] = next(iter(candidate_owners))
        policies.append(policy)
    metadata["information_policies"] = policies
    return net.model_copy(
        update={
            "net_id": f"{net.net_id}:team:{team.team_id}",
            "actors": actor_ids,
            "transitions": tuple((*remapped.values(), *additions)),
            "places": tuple(places),
            "arcs": tuple(arcs),
            "metadata": metadata,
        }
    )


def refine_process_for_team(process, team: AgentTeamSpec, refinement: RoleRefinementSpec):
    """Derive a reviewed n-agent v5 process from reviewed base and role mapping.

    The transformation is deterministic. Agronomic definitions and module
    budgets are inherited unchanged; only ownership, explicit communication
    paths, and their categorical acceptance/obligation records are refined.
    """

    from are.simulation.distributed.petri import transition_dependencies
    from are.simulation.distributed.scientific_v5 import (
        CausalObligationGroupSpec,
        CausalPathSpec,
        FarmProcessSpecV5,
        TransitionAcceptanceSpec,
    )

    if process.scenario_id != refinement.scenario_id:
        raise ValueError("process and role-refinement scenarios disagree")
    if team.team_id != refinement.target_team_id:
        raise ValueError("team and role-refinement targets disagree")
    paper_eligible = bool(
        process.annotation_status == "frozen"
        and process.expert_review_status == "confirmed"
        and team.expert_review_status == "confirmed"
        and refinement.expert_review_status == "confirmed"
    )

    net = refine_petri_for_team(process.occurrence_net, team, refinement)
    transitions = {item.transition_id: item for item in net.transitions}
    dependencies = transition_dependencies(net)
    graph: dict[str, list[str]] = defaultdict(list)
    for source, target in sorted(dependencies):
        graph[source].append(target)

    def expanded_edge(source: str, target: str) -> tuple[tuple[str, str], ...]:
        if (source, target) in dependencies:
            return ((source, target),)
        queue = deque([(source, ())])
        seen = {source}
        while queue:
            current, edges = queue.popleft()
            for successor in graph.get(current, ()):
                next_edges = (*edges, (current, successor))
                if successor == target:
                    return next_edges
                if successor not in seen:
                    seen.add(successor)
                    queue.append((successor, next_edges))
        raise ValueError(
            f"role refinement removed normative path {source!r} -> {target!r}"
        )

    acceptance = {item.transition_id: item for item in process.acceptance}
    for transition in net.transitions:
        if (
            transition.required
            and transition.actor_id != "world"
            and transition.transition_id not in acceptance
        ):
            acceptance[transition.transition_id] = TransitionAcceptanceSpec(
                transition_id=transition.transition_id
            )

    action_owners = {
        action: actor.actor_id
        for actor in team.actors
        for action in actor.permitted_actions
    }
    policies = []
    for policy in process.information_policies:
        owners = {
            action_owners[action]
            for action in policy.action_patterns
            if action in action_owners
        }
        actor_id = next(iter(owners)) if len(owners) == 1 else policy.actor_id
        if actor_id not in net.actors:
            mapped = refinement.role_mapping.get(policy.actor_id, ())
            if not mapped:
                raise ValueError(f"no reviewed actor mapping for policy {policy.policy_id!r}")
            actor_id = mapped[-1]
        policies.append(policy.model_copy(update={"actor_id": actor_id}))

    agronomic_path = refinement.communication_paths.get("agronomic_evidence")
    obligations = []
    for obligation in process.causal_obligations:
        target_actors = {
            transitions[target].actor_id for target in obligation.target_transition_ids
        }
        target_actor = next(iter(target_actors)) if len(target_actors) == 1 else None
        alternatives = []
        for alternative in obligation.alternatives:
            edges = tuple(
                expanded
                for source, target in alternative.transition_edges
                for expanded in expanded_edge(source, target)
            )
            actor_path = alternative.required_actor_path
            if actor_path and agronomic_path and target_actor == agronomic_path[-1]:
                actor_path = agronomic_path
            elif actor_path:
                remapped = []
                for actor_id in actor_path:
                    candidates = refinement.role_mapping.get(actor_id, (actor_id,))
                    remapped.append(candidates[-1])
                actor_path = tuple(dict.fromkeys(remapped))
            alternatives.append(
                alternative.model_copy(
                    update={
                        "transition_edges": tuple(dict.fromkeys(edges)),
                        "required_actor_path": actor_path,
                    }
                )
            )
        obligations.append(obligation.model_copy(update={"alternatives": tuple(alternatives)}))

    for send_id, send in transitions.items():
        prefix = "resource_handoff_send:"
        if not send_id.startswith(prefix):
            continue
        target_id = send_id.removeprefix(prefix)
        receive_id = f"resource_handoff_receive:{target_id}"
        if receive_id not in transitions or target_id not in transitions:
            raise ValueError("reviewed resource handoff has an incomplete transition path")
        obligations.append(
            CausalObligationGroupSpec(
                obligation_id=f"team:{team.team_id}:resource-readiness:{target_id}",
                label=f"resource readiness reaches {target_id}",
                module_id=str(transitions[target_id].module_id),
                target_transition_ids=(target_id,),
                alternatives=(
                    CausalPathSpec(
                        path_id=f"team:{team.team_id}:resource-path:{target_id}",
                        transition_edges=((send_id, receive_id), (receive_id, target_id)),
                        required_actor_path=refinement.communication_paths[
                            "resource_readiness"
                        ],
                    ),
                ),
            )
        )

    negative = []
    for obligation in process.negative_action_obligations:
        negative.append(
            obligation.model_copy(
                update={
                    "actor_id": action_owners.get(obligation.action, obligation.actor_id)
                }
            )
        )
    refinement_digest = stable_digest(refinement.model_dump(mode="json"))
    derived_digest = stable_digest(
        {
            "base_process_digest": process.digest,
            "team_digest": team_digest(team),
            "role_refinement_digest": refinement_digest,
        }
    )
    return FarmProcessSpecV5(
        **{
            **process.model_dump(mode="python"),
            "process_id": f"{process.process_id}:team:{team.team_id}",
            "occurrence_net": net,
            "information_policies": tuple(policies),
            "acceptance": tuple(acceptance[key] for key in sorted(acceptance)),
            "causal_obligations": tuple(obligations),
            "negative_action_obligations": tuple(negative),
            "expert_review_status": "confirmed" if paper_eligible else "unreviewed",
            "annotation_status": "frozen" if paper_eligible else "draft",
            "review_digest": derived_digest if paper_eligible else None,
            "metadata": {
                **process.metadata,
                "base_process_digest": process.digest,
                "team_spec_digest": team_digest(team),
                "role_refinement_digest": refinement_digest,
                "role_refinement_review_status": refinement.expert_review_status,
                "derived_by": "deterministic_role_refinement_v1",
                "engineering_defaults": not paper_eligible,
            },
        }
    )


def write_builtin_team(team: AgentTeamSpec, path: Path) -> None:
    path.write_text(
        json.dumps(team.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
