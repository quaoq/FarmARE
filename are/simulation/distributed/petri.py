"""Executable Petri-net contracts for farm D-CORE evaluation.

The net is deliberately small and explicit: control flow is 1-safe, while
continuous farm quantities live in transition data guards.  This keeps the
causal semantics auditable without pretending that inventories or soil water
are Boolean tokens.
"""

from __future__ import annotations

from collections import defaultdict, deque
from enum import Enum
from typing import Any, Literal
from xml.etree.ElementTree import Element, SubElement, tostring

from pydantic import Field, model_validator

from are.simulation.distributed.models import FrozenModel


class PlaceKind(str, Enum):
    CONTROL = "control"
    WORLD = "world"
    EVIDENCE = "evidence"
    RESOURCE = "resource"
    KNOWLEDGE = "knowledge"


class TransitionKind(str, Enum):
    OBSERVE = "observe"
    SEND = "send"
    RECEIVE = "receive"
    WAIT = "wait"
    ACT = "act"
    VERIFY = "verify"
    FINISH = "finish"


class ScoringClass(str, Enum):
    REQUIRED_PROGRESS = "required_progress"
    REQUIRED_EVIDENCE = "required_evidence"
    OPTIONAL_SAFE = "optional_safe"
    BENIGN_LOOP = "benign_loop"
    HARMFUL = "harmful"


class PolicyResponse(str, Enum):
    EXECUTE = "execute"
    REOBSERVE = "reobserve"
    HANDOFF = "handoff"
    DEFER = "defer"
    ABSTAIN = "abstain"


class GuardOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GE = "ge"
    GT = "gt"
    LE = "le"
    LT = "lt"
    IN = "in"


class PlaceSpec(FrozenModel):
    place_id: str
    label: str = ""
    kind: PlaceKind = PlaceKind.CONTROL
    initially_marked: bool = False
    terminal: bool = False


class ArgumentConstraint(FrozenModel):
    name: str
    expected: Any
    tolerance: float | None = Field(default=None, ge=0)
    critical: bool = True
    weight: float = Field(default=1.0, gt=0)


class DataGuardSpec(FrozenModel):
    guard_id: str
    fact_key: str
    operator: GuardOperator = GuardOperator.EQ
    expected: Any = True
    source: Literal["world", "knowledge", "resource"] = "world"
    scope: tuple[int, int] | str | None = None
    max_age: float | None = Field(default=None, ge=0)
    required_evidence: bool = False
    branch_selector: bool = False


class TransitionSpec(FrozenModel):
    transition_id: str
    label: str
    actor_id: str
    action: str
    kind: TransitionKind = TransitionKind.ACT
    phase: str
    arguments: tuple[ArgumentConstraint, ...] = ()
    scope: tuple[int, int] | str | None = None
    scope_iou_threshold: float = Field(default=1.0, ge=0, le=1)
    window_start: float | None = None
    window_end: float | None = None
    guards: tuple[DataGuardSpec, ...] = ()
    required: bool = True
    harmful: bool = False
    high_impact: bool = False
    weight: float = Field(default=1.0, gt=0)
    module_id: str | None = None
    template_id: str | None = None
    scoring_class: ScoringClass = ScoringClass.REQUIRED_PROGRESS

    @model_validator(mode="after")
    def validate_window(self) -> "TransitionSpec":
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end < self.window_start
        ):
            raise ValueError("window_end must be >= window_start")
        if self.high_impact and not self.guards:
            raise ValueError(
                f"high-impact transition {self.transition_id!r} needs a data guard"
            )
        return self


class ArcSpec(FrozenModel):
    arc_id: str
    source: str
    target: str
    kind: Literal["normal"] = "normal"


class BranchAlternativeSpec(FrozenModel):
    alternative_id: str
    label: str
    transition_ids: tuple[str, ...]
    required_transition_ids: tuple[str, ...] = ()
    guards: tuple[DataGuardSpec, ...] = ()
    default: bool = False


class ExogenousBranchSpec(FrozenModel):
    """A branch selected from frozen world facts, never observed actions."""

    branch_id: str
    alternatives: tuple[BranchAlternativeSpec, ...]

    @model_validator(mode="after")
    def validate_alternatives(self) -> "ExogenousBranchSpec":
        if len(self.alternatives) < 2:
            raise ValueError("an exogenous branch needs at least two alternatives")
        identifiers = [item.alternative_id for item in self.alternatives]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("branch alternative IDs must be unique")
        if sum(item.default for item in self.alternatives) > 1:
            raise ValueError("a branch can have at most one default alternative")
        return self


class WorldBranchSpec(ExogenousBranchSpec):
    commit_phase: str
    commitment_fact_keys: tuple[str, ...]


class FactDefinitionSpec(FrozenModel):
    fact_key: str
    value_type: Literal["boolean", "number", "string", "mapping"]
    units: str | None = None
    scope_kind: Literal["field", "ridge_range", "resource", "equipment"] = "field"
    observation_actions: tuple[str, ...] = ()
    truth_source: str
    engineering_valid_for: float | None = Field(default=None, ge=0)
    supersession: Literal["overlapping_scope", "global", "never"] = "overlapping_scope"
    paper_status: Literal["draft", "frozen"] = "draft"


class ExpansionRuleSpec(FrozenModel):
    kind: Literal["source_events", "ridge_batches", "repeat"] = "source_events"
    source_transition_ids: tuple[str, ...]
    expected_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_count(self) -> "ExpansionRuleSpec":
        if len(self.source_transition_ids) != self.expected_count:
            raise ValueError("expansion expected_count does not match source IDs")
        if len(set(self.source_transition_ids)) != len(self.source_transition_ids):
            raise ValueError("expansion source IDs must be unique")
        return self


class TransitionTemplateSpec(FrozenModel):
    template_id: str
    module_id: str
    label: str
    kind: TransitionKind
    actor_id: str
    action_pattern: str
    scoring_class: ScoringClass
    expansion: ExpansionRuleSpec
    within_module_weight: float = Field(default=1.0, gt=0)
    required: bool = True


class PetriModuleSpec(FrozenModel):
    module_id: str
    label: str
    phase: str
    weight_budget: float = Field(default=1.0, gt=0)
    required: bool = True


class ChoiceGroupSpec(FrozenModel):
    choice_id: str
    policy_id: str
    response_transition_ids: tuple[str, ...]
    minimum_selected: int = Field(default=1, ge=0)
    maximum_selected: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_choice(self) -> "ChoiceGroupSpec":
        if not self.response_transition_ids:
            raise ValueError("choice group needs response transitions")
        if self.minimum_selected > self.maximum_selected:
            raise ValueError("choice minimum cannot exceed maximum")
        return self


class PolicyRuleSpec(FrozenModel):
    requirement_state: Literal["true", "false", "unknown"]
    deadline_state: Literal["open", "closed"]
    channel_state: Literal["open", "closed", "any"] = "any"
    permitted_responses: tuple[PolicyResponse, ...]
    required_responses: tuple[PolicyResponse, ...] = ()

    @model_validator(mode="after")
    def validate_responses(self) -> "PolicyRuleSpec":
        if not self.permitted_responses:
            raise ValueError("policy rule needs a permitted response")
        if not set(self.required_responses) <= set(self.permitted_responses):
            raise ValueError("required policy responses must be permitted")
        return self


class InformationPolicySpec(FrozenModel):
    policy_id: str
    actor_id: str
    action_patterns: tuple[str, ...]
    phases: tuple[str, ...]
    requirement_fact_keys: tuple[str, ...]
    requirements: tuple[DataGuardSpec, ...] = ()
    deadline_world_time: float | None = None
    rules: tuple[PolicyRuleSpec, ...]

    @model_validator(mode="after")
    def validate_truth_table(self) -> "InformationPolicySpec":
        if self.requirements and {item.fact_key for item in self.requirements} != set(
            self.requirement_fact_keys
        ):
            raise ValueError("policy requirements and fact keys disagree")
        covered = {
            (rule.requirement_state, rule.deadline_state, rule.channel_state)
            for rule in self.rules
        }
        for requirement in ("true", "false", "unknown"):
            for deadline in ("open", "closed"):
                if not any(
                    (requirement, deadline, channel) in covered
                    for channel in ("open", "closed", "any")
                ):
                    raise ValueError(
                        f"policy {self.policy_id!r} has an incomplete truth table"
                    )
        return self


class FarmPetriTemplateSpec(FrozenModel):
    schema_version: Literal["farm_petri_template_v1"] = "farm_petri_template_v1"
    template_id: str
    scenario_id: str
    actors: tuple[str, ...]
    modules: tuple[PetriModuleSpec, ...]
    transition_templates: tuple[TransitionTemplateSpec, ...]
    fact_definitions: tuple[FactDefinitionSpec, ...]
    world_branches: tuple[WorldBranchSpec, ...] = ()
    information_policies: tuple[InformationPolicySpec, ...] = ()
    choice_groups: tuple[ChoiceGroupSpec, ...] = ()
    expert_review_status: Literal[
        "unreviewed", "two_expert_draft", "adjudicated", "confirmed"
    ] = "unreviewed"
    annotation_status: Literal["draft", "frozen"] = "draft"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_template(self) -> "FarmPetriTemplateSpec":
        module_ids = [module.module_id for module in self.modules]
        template_ids = [item.template_id for item in self.transition_templates]
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("Petri module IDs must be unique")
        if len(template_ids) != len(set(template_ids)):
            raise ValueError("transition template IDs must be unique")
        unknown_modules = {item.module_id for item in self.transition_templates} - set(
            module_ids
        )
        if unknown_modules:
            raise ValueError(f"unknown transition-template modules: {unknown_modules}")
        expanded = [
            source
            for item in self.transition_templates
            for source in item.expansion.source_transition_ids
        ]
        if len(expanded) != len(set(expanded)):
            raise ValueError("source transition belongs to multiple templates")
        policy_ids = {policy.policy_id for policy in self.information_policies}
        if any(choice.policy_id not in policy_ids for choice in self.choice_groups):
            raise ValueError("choice group references an unknown policy")
        if (
            self.annotation_status == "frozen"
            and self.expert_review_status != "confirmed"
        ):
            raise ValueError("only a confirmed template may be frozen")
        return self


class PetriNetSpec(FrozenModel):
    schema_version: Literal["farm_petri_v1", "farm_petri_v2", "farm_petri_v3"] = (
        "farm_petri_v2"
    )
    net_id: str
    scenario_id: str
    actors: tuple[str, ...]
    places: tuple[PlaceSpec, ...]
    transitions: tuple[TransitionSpec, ...]
    arcs: tuple[ArcSpec, ...]
    exogenous_branches: tuple[WorldBranchSpec | ExogenousBranchSpec, ...] = ()
    oracle_version: str = "unreviewed"
    expert_review_status: Literal[
        "unreviewed", "two_expert_draft", "adjudicated", "confirmed"
    ] = "unreviewed"
    metadata: dict[str, Any] = Field(default_factory=dict)
    modules: tuple[PetriModuleSpec, ...] = ()
    choice_groups: tuple[ChoiceGroupSpec, ...] = ()

    @model_validator(mode="after")
    def validate_structure(self) -> "PetriNetSpec":
        place_ids = [place.place_id for place in self.places]
        transition_ids = [transition.transition_id for transition in self.transitions]
        node_ids = set(place_ids) | set(transition_ids)
        if len(place_ids) != len(set(place_ids)):
            raise ValueError("Petri place IDs must be unique")
        if len(transition_ids) != len(set(transition_ids)):
            raise ValueError("Petri transition IDs must be unique")
        if set(place_ids) & set(transition_ids):
            raise ValueError("place and transition IDs must be disjoint")
        if len({arc.arc_id for arc in self.arcs}) != len(self.arcs):
            raise ValueError("Petri arc IDs must be unique")
        places = set(place_ids)
        transitions = set(transition_ids)
        actor_ids = set(self.actors)
        module_ids = {module.module_id for module in self.modules}
        for transition in self.transitions:
            if transition.actor_id not in actor_ids and transition.actor_id != "world":
                raise ValueError(
                    f"unknown actor {transition.actor_id!r} on {transition.transition_id}"
                )
            if transition.module_id and transition.module_id not in module_ids:
                raise ValueError(
                    f"unknown module {transition.module_id!r} on {transition.transition_id}"
                )
        branch_ids = [branch.branch_id for branch in self.exogenous_branches]
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("Petri branch IDs must be unique")
        branch_transition_owner: dict[str, str] = {}
        for branch in self.exogenous_branches:
            for alternative in branch.alternatives:
                if not alternative.transition_ids:
                    raise ValueError("branch alternatives cannot be empty")
                unknown = set(alternative.transition_ids) - transitions
                if unknown:
                    raise ValueError(
                        f"branch {branch.branch_id!r} references unknown transitions: "
                        f"{sorted(unknown)}"
                    )
                if not set(alternative.required_transition_ids) <= set(
                    alternative.transition_ids
                ):
                    raise ValueError(
                        "conditionally required transitions must belong to their alternative"
                    )
                for transition_id in alternative.transition_ids:
                    previous = branch_transition_owner.get(transition_id)
                    if previous and previous != branch.branch_id:
                        raise ValueError(
                            f"transition {transition_id!r} belongs to multiple branches"
                        )
                    branch_transition_owner[transition_id] = branch.branch_id
                    if next(
                        item
                        for item in self.transitions
                        if item.transition_id == transition_id
                    ).required:
                        raise ValueError(
                            "branch transitions must use required=False; occurrence "
                            "commitment supplies conditional requiredness"
                        )
                if not alternative.default and not alternative.guards:
                    raise ValueError(
                        "non-default branch alternatives need exogenous guards"
                    )
                if any(
                    guard.source != "world" or not guard.branch_selector
                    for guard in alternative.guards
                ):
                    raise ValueError(
                        "branch selection guards must be world-sourced branch selectors"
                    )
        choice_ids = [choice.choice_id for choice in self.choice_groups]
        if len(choice_ids) != len(set(choice_ids)):
            raise ValueError("Petri choice-group IDs must be unique")
        for choice in self.choice_groups:
            unknown = set(choice.response_transition_ids) - transitions
            if unknown:
                raise ValueError(
                    f"choice {choice.choice_id!r} references unknown transitions: "
                    f"{sorted(unknown)}"
                )
            if choice.maximum_selected > len(choice.response_transition_ids):
                raise ValueError("choice maximum exceeds its response count")
        seen_edges: set[tuple[str, str]] = set()
        for arc in self.arcs:
            if arc.source not in node_ids or arc.target not in node_ids:
                raise ValueError(f"arc {arc.arc_id!r} references an unknown node")
            if not (
                (arc.source in places and arc.target in transitions)
                or (arc.source in transitions and arc.target in places)
            ):
                raise ValueError("Petri arcs must connect a place and a transition")
            edge = (arc.source, arc.target)
            if edge in seen_edges:
                raise ValueError(f"duplicate Petri arc {edge}")
            seen_edges.add(edge)
        return self


class BranchCommitment(FrozenModel):
    branch_id: str
    alternative_id: str
    evidence_fact_keys: tuple[str, ...]
    world_context_digest: str


class OccurrenceNet(FrozenModel):
    net_id: str
    world_fingerprint: str
    applicable_transition_ids: tuple[str, ...]
    direct_dependencies: tuple[tuple[str, str], ...]
    concurrent_pairs: tuple[tuple[str, str], ...]
    excluded_by_guard: tuple[str, ...] = ()
    branch_commitments: tuple[BranchCommitment, ...] = ()
    conditionally_required_transition_ids: tuple[str, ...] = ()


def _guard_holds(guard: DataGuardSpec, context: dict[str, Any]) -> bool:
    if guard.fact_key not in context:
        return not guard.branch_selector
    actual = context[guard.fact_key]
    expected = guard.expected
    operations = {
        GuardOperator.EQ: lambda: actual == expected,
        GuardOperator.NE: lambda: actual != expected,
        GuardOperator.GE: lambda: actual >= expected,
        GuardOperator.GT: lambda: actual > expected,
        GuardOperator.LE: lambda: actual <= expected,
        GuardOperator.LT: lambda: actual < expected,
        GuardOperator.IN: lambda: actual in expected,
    }
    try:
        return bool(operations[guard.operator]())
    except (TypeError, ValueError):
        return False


def transition_dependencies(net: PetriNetSpec) -> set[tuple[str, str]]:
    """Return direct transition dependencies induced by intermediate places."""
    incoming: defaultdict[str, set[str]] = defaultdict(set)
    outgoing: defaultdict[str, set[str]] = defaultdict(set)
    transition_ids = {transition.transition_id for transition in net.transitions}
    place_ids = {place.place_id for place in net.places}
    for arc in net.arcs:
        if arc.source in transition_ids and arc.target in place_ids:
            incoming[arc.target].add(arc.source)
        elif arc.source in place_ids and arc.target in transition_ids:
            outgoing[arc.source].add(arc.target)
    return {
        (source, target)
        for place_id in place_ids
        for source in incoming[place_id]
        for target in outgoing[place_id]
    }


def unfold_petri_net(
    net: PetriNetSpec,
    *,
    world_context: dict[str, Any] | None = None,
    world_fingerprint: str = "unspecified",
    committed_branches: dict[str, str] | None = None,
) -> OccurrenceNet:
    context = world_context or {}
    requested = committed_branches or {}
    commitments: list[BranchCommitment] = []
    selected_transition_ids: set[str] = set()
    conditionally_required: set[str] = set()
    all_branch_transition_ids: set[str] = set()
    for branch in net.exogenous_branches:
        all_branch_transition_ids.update(
            transition_id
            for alternative in branch.alternatives
            for transition_id in alternative.transition_ids
        )
        chosen = None
        if branch.branch_id in requested:
            chosen = next(
                (
                    item
                    for item in branch.alternatives
                    if item.alternative_id == requested[branch.branch_id]
                ),
                None,
            )
            if chosen is None:
                raise ValueError(
                    f"unknown commitment {requested[branch.branch_id]!r} for "
                    f"branch {branch.branch_id!r}"
                )
            if chosen.guards and not all(
                _guard_holds(guard, context) for guard in chosen.guards
            ):
                raise ValueError(
                    f"committed branch {branch.branch_id!r} contradicts world facts"
                )
        else:
            matching = [
                alternative
                for alternative in branch.alternatives
                if alternative.guards
                and all(
                    guard.fact_key in context and _guard_holds(guard, context)
                    for guard in alternative.guards
                )
            ]
            if len(matching) > 1:
                raise ValueError(f"ambiguous exogenous branch {branch.branch_id!r}")
            chosen = (
                matching[0]
                if matching
                else next((item for item in branch.alternatives if item.default), None)
            )
            if chosen is None:
                raise ValueError(
                    f"world facts do not commit branch {branch.branch_id!r}"
                )
        selected_transition_ids.update(chosen.transition_ids)
        conditionally_required.update(
            chosen.required_transition_ids or chosen.transition_ids
        )
        commitments.append(
            BranchCommitment(
                branch_id=branch.branch_id,
                alternative_id=chosen.alternative_id,
                evidence_fact_keys=tuple(
                    dict.fromkeys(guard.fact_key for guard in chosen.guards)
                ),
                world_context_digest=_stable_context_digest(context),
            )
        )
    applicable = {
        transition.transition_id
        for transition in net.transitions
        if all(_guard_holds(guard, context) for guard in transition.guards)
        and (
            transition.transition_id not in all_branch_transition_ids
            or transition.transition_id in selected_transition_ids
        )
    }
    excluded = {transition.transition_id for transition in net.transitions} - applicable
    dependencies = {
        edge
        for edge in transition_dependencies(net)
        if edge[0] in applicable and edge[1] in applicable
    }
    children: defaultdict[str, set[str]] = defaultdict(set)
    for source, target in dependencies:
        children[source].add(target)

    reachability: dict[str, set[str]] = {}
    for source in applicable:
        reached: set[str] = set()
        queue = deque(children[source])
        while queue:
            node = queue.popleft()
            if node in reached:
                continue
            reached.add(node)
            queue.extend(children[node])
        reachability[source] = reached
    ordered = sorted(applicable)
    concurrent = tuple(
        (left, right)
        for index, left in enumerate(ordered)
        for right in ordered[index + 1 :]
        if right not in reachability[left] and left not in reachability[right]
    )
    return OccurrenceNet(
        net_id=net.net_id,
        world_fingerprint=world_fingerprint,
        applicable_transition_ids=tuple(ordered),
        direct_dependencies=tuple(sorted(dependencies)),
        concurrent_pairs=concurrent,
        excluded_by_guard=tuple(sorted(excluded)),
        branch_commitments=tuple(commitments),
        conditionally_required_transition_ids=tuple(sorted(conditionally_required)),
    )


def _stable_context_digest(context: dict[str, Any]) -> str:
    from are.simulation.distributed.models import stable_digest

    return stable_digest(context)


def validate_petri_net(
    net: PetriNetSpec, *, max_markings: int = 100_000
) -> dict[str, Any]:
    """Explore structural markings and reject unsafe/dead reference nets."""
    places = {place.place_id: place for place in net.places}
    transitions = {
        transition.transition_id: transition for transition in net.transitions
    }
    inputs: defaultdict[str, set[str]] = defaultdict(set)
    outputs: defaultdict[str, set[str]] = defaultdict(set)
    for arc in net.arcs:
        if arc.source in places:
            inputs[arc.target].add(arc.source)
        else:
            outputs[arc.source].add(arc.target)

    # Compiled seasonal references are occurrence nets: acyclic and every
    # place has at most one producer. For these nets, 1-safety and reachability
    # can be proved structurally without enumerating an exponential number of
    # harmless interleavings.
    node_children: defaultdict[str, set[str]] = defaultdict(set)
    indegree = {node: 0 for node in (*places, *transitions)}
    producers: defaultdict[str, set[str]] = defaultdict(set)
    consumers: defaultdict[str, set[str]] = defaultdict(set)
    for arc in net.arcs:
        node_children[arc.source].add(arc.target)
        indegree[arc.target] += 1
        if arc.target in places and arc.source in transitions:
            producers[arc.target].add(arc.source)
        if arc.source in places and arc.target in transitions:
            consumers[arc.source].add(arc.target)
    topo_queue = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    topological: list[str] = []
    while topo_queue:
        node = topo_queue.popleft()
        topological.append(node)
        for child in sorted(node_children[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                topo_queue.append(child)
    # The fast proof is deliberately limited to an acyclic marked graph. Each
    # place has at most one producer and consumer, every transition consumes a
    # token, and an initially marked place is never produced again. Under
    # these conditions every transition can fire at most once and no two
    # branches compete for a token. More general nets use marking exploration.
    occurrence_net = (
        len(topological) == len(indegree)
        and all(
            len(source_transitions) <= 1 for source_transitions in producers.values()
        )
        and all(
            len(target_transitions) <= 1 for target_transitions in consumers.values()
        )
        # Optional response instances (for example explicit abstention) may be
        # source events with an empty preset. Occurrence-net event identities
        # are single-use, so this does not grant repeatable behavior.
        and all(
            inputs[transition_id] or not transitions[transition_id].required
            for transition_id in transitions
        )
        and all(
            not place.initially_marked or not producers[place.place_id]
            for place in net.places
        )
    )
    if occurrence_net:
        reachable_places = {
            place.place_id for place in net.places if place.initially_marked
        }
        reachable_transitions: set[str] = set()
        for node in topological:
            if node not in transitions or not inputs[node] <= reachable_places:
                continue
            reachable_transitions.add(node)
            reachable_places.update(outputs[node])
        dead = sorted(set(transitions) - reachable_transitions)
        required_dead = sorted(
            transition_id
            for transition_id in dead
            if transitions[transition_id].required
        )
        if required_dead:
            raise ValueError(
                f"required Petri transitions are unreachable: {required_dead}"
            )
        terminal_seen = any(
            place.terminal and place.place_id in reachable_places
            for place in net.places
        )
        if not terminal_seen:
            raise ValueError("Petri net has no reachable terminal marking")
        return {
            "net_id": net.net_id,
            "places": len(places),
            "transitions": len(transitions),
            "arcs": len(net.arcs),
            "reachable_markings": None,
            "reachable_transitions": len(reachable_transitions),
            "dead_optional_transitions": dead,
            "exogenous_branches": len(net.exogenous_branches),
            "terminal_reachable": True,
            "one_safe": True,
            "validation_mode": "symbolic_occurrence_net",
        }

    initial = frozenset(
        place.place_id for place in net.places if place.initially_marked
    )
    queue = deque([initial])
    seen = {initial}
    fired: set[str] = set()
    terminal_seen = False
    while queue:
        marking = queue.popleft()
        if any(places[place_id].terminal for place_id in marking):
            terminal_seen = True
        for transition_id in sorted(transitions):
            required = inputs[transition_id]
            if not required <= marking:
                continue
            produced = outputs[transition_id]
            remainder = set(marking) - required
            if remainder & produced:
                raise ValueError(
                    f"transition {transition_id!r} violates 1-safety at {sorted(remainder & produced)}"
                )
            next_marking = frozenset(remainder | produced)
            fired.add(transition_id)
            if next_marking not in seen:
                seen.add(next_marking)
                queue.append(next_marking)
                if len(seen) > max_markings:
                    raise ValueError("Petri reachability exceeds validation bound")
    dead = sorted(set(transitions) - fired)
    required_dead = sorted(
        transition_id for transition_id in dead if transitions[transition_id].required
    )
    if required_dead:
        raise ValueError(f"required Petri transitions are unreachable: {required_dead}")
    if not terminal_seen:
        raise ValueError("Petri net has no reachable terminal marking")
    return {
        "net_id": net.net_id,
        "places": len(places),
        "transitions": len(transitions),
        "arcs": len(net.arcs),
        "reachable_markings": len(seen),
        "dead_optional_transitions": dead,
        "exogenous_branches": len(net.exogenous_branches),
        "terminal_reachable": terminal_seen,
        "one_safe": True,
        "validation_mode": "exhaustive_marking",
    }


def petri_to_dot(net: PetriNetSpec) -> str:
    lines = ["digraph farm_dcore {", "  rankdir=LR;"]
    for place in net.places:
        shape = "doublecircle" if place.terminal else "circle"
        label = place.label or place.place_id
        lines.append(f'  "{place.place_id}" [shape={shape}, label="{label}"];')
    for transition in net.transitions:
        label = f"{transition.label}\\n[{transition.actor_id}|{transition.phase}]"
        lines.append(f'  "{transition.transition_id}" [shape=box, label="{label}"];')
    for arc in net.arcs:
        lines.append(f'  "{arc.source}" -> "{arc.target}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def petri_to_pnml(net: PetriNetSpec) -> str:
    pnml = Element("pnml")
    net_element = SubElement(pnml, "net", id=net.net_id, type="farm_dcore")
    page = SubElement(net_element, "page", id="page-1")
    for place in net.places:
        node = SubElement(page, "place", id=place.place_id)
        name = SubElement(node, "name")
        SubElement(name, "text").text = place.label or place.place_id
        if place.initially_marked:
            marking = SubElement(node, "initialMarking")
            SubElement(marking, "text").text = "1"
    for transition in net.transitions:
        node = SubElement(page, "transition", id=transition.transition_id)
        name = SubElement(node, "name")
        SubElement(name, "text").text = transition.label
    for arc in net.arcs:
        SubElement(page, "arc", id=arc.arc_id, source=arc.source, target=arc.target)
    return tostring(pnml, encoding="unicode")
