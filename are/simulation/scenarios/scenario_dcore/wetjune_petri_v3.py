"""Hierarchical, expert-reviewable Wet-June Petri template.

The checked-in template is deliberately a draft.  It organizes the native
FarmARE oracle into semantic modules without claiming that engineering
thresholds or dependencies have received domain-expert approval.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.petri import (
    ArcSpec,
    DataGuardSpec,
    ExpansionRuleSpec,
    FactDefinitionSpec,
    FarmPetriTemplateSpec,
    GuardOperator,
    InformationPolicySpec,
    PetriModuleSpec,
    PetriNetSpec,
    PlaceSpec,
    PolicyResponse,
    PolicyRuleSpec,
    ScoringClass,
    TransitionKind,
    TransitionSpec,
    TransitionTemplateSpec,
    transition_dependencies,
)

WETJUNE_MODULES = (
    PetriModuleSpec(
        module_id="field_preparation", label="Field preparation", phase="field_prep"
    ),
    PetriModuleSpec(
        module_id="planting", label="Planting readiness and execution", phase="planting"
    ),
    PetriModuleSpec(
        module_id="establishment", label="Establishment monitoring", phase="emergence"
    ),
    PetriModuleSpec(module_id="r1_monitoring", label="R1 monitoring", phase="r1"),
    PetriModuleSpec(
        module_id="mid_diagnosis", label="Initial disease diagnosis", phase="midseason"
    ),
    PetriModuleSpec(
        module_id="mid_treatment",
        label="Initial treatment and response",
        phase="midseason",
    ),
    PetriModuleSpec(
        module_id="r5_recheck",
        label="R5 recurrence, recheck, and treatment",
        phase="r5",
    ),
    PetriModuleSpec(
        module_id="harvest_readiness", label="Harvest readiness", phase="harvest"
    ),
    PetriModuleSpec(
        module_id="harvest_execution", label="Harvest and unloading", phase="harvest"
    ),
    PetriModuleSpec(
        module_id="drying_storage", label="Drying and storage", phase="storage"
    ),
)


def _module_for(transition) -> str:
    if transition.phase == "field_prep":
        return "field_preparation"
    if transition.phase == "planting":
        return "planting"
    if transition.phase == "emergence":
        return "establishment"
    if transition.phase == "r1":
        return "r1_monitoring"
    if transition.phase == "midseason":
        if (
            transition.high_impact
            or "fungicide" in transition.transition_id
            and transition.kind
            in {
                TransitionKind.SEND,
                TransitionKind.RECEIVE,
            }
        ):
            return "mid_treatment"
        return "mid_diagnosis"
    if transition.phase == "r5":
        return "r5_recheck"
    if transition.phase == "harvest":
        if transition.kind in {TransitionKind.OBSERVE, TransitionKind.WAIT}:
            return "harvest_readiness"
        return "harvest_execution"
    return "drying_storage"


def _scoring_class(transition) -> ScoringClass:
    if transition.actor_id == "world":
        return ScoringClass.OPTIONAL_SAFE
    if transition.kind == TransitionKind.WAIT:
        return ScoringClass.BENIGN_LOOP
    if transition.kind in {
        TransitionKind.OBSERVE,
        TransitionKind.SEND,
        TransitionKind.RECEIVE,
        TransitionKind.VERIFY,
    }:
        return ScoringClass.REQUIRED_EVIDENCE
    if transition.harmful:
        return ScoringClass.HARMFUL
    return ScoringClass.REQUIRED_PROGRESS


def _fact_definitions() -> tuple[FactDefinitionSpec, ...]:
    def fact(
        key: str,
        value_type: str,
        truth: str,
        *actions: str,
        units: str | None = None,
        scope: str = "field",
        valid_for: float | None = None,
    ) -> FactDefinitionSpec:
        return FactDefinitionSpec(
            fact_key=key,
            value_type=value_type,
            units=units,
            scope_kind=scope,
            observation_actions=actions,
            truth_source=truth,
            engineering_valid_for=valid_for,
        )

    day = 86400.0
    return (
        fact(
            "planting:weather_suitable",
            "boolean",
            "WeatherApp",
            "WeatherApp__get_current_weather",
            "WeatherApp__get_forecast",
            valid_for=day,
        ),
        fact(
            "planting:soil_suitable",
            "boolean",
            "FarmWorldApp.soil",
            "SensorApp__read_soil_sensors",
            valid_for=2 * day,
        ),
        fact(
            "inventory:seed_sufficient",
            "boolean",
            "FarmWorldApp.inventory",
            "FarmWorldApp__get_inventory",
            scope="resource",
            valid_for=day,
        ),
        fact(
            "inventory:fertilizer_sufficient",
            "boolean",
            "FarmWorldApp.inventory",
            "FarmWorldApp__get_inventory",
            scope="resource",
            valid_for=day,
        ),
        fact(
            "inventory:fuel_sufficient",
            "boolean",
            "FarmWorldApp.inventory",
            "FarmWorldApp__get_inventory",
            scope="resource",
            valid_for=day,
        ),
        fact(
            "inventory:fungicide_sufficient",
            "boolean",
            "FarmWorldApp.inventory",
            "FarmWorldApp__get_inventory",
            scope="resource",
            valid_for=day,
        ),
        fact(
            "equipment:tractor_ready",
            "boolean",
            "TractorApp",
            "TractorApp__get_status",
            scope="equipment",
            valid_for=day,
        ),
        fact(
            "equipment:planter_ready",
            "boolean",
            "TractorApp",
            "TractorApp__get_status",
            scope="equipment",
            valid_for=day,
        ),
        fact(
            "equipment:sprayer_ready",
            "boolean",
            "TractorApp",
            "TractorApp__get_status",
            scope="equipment",
            valid_for=day,
        ),
        fact(
            "equipment:harvester_ready",
            "boolean",
            "TractorApp",
            "TractorApp__get_status",
            scope="equipment",
            valid_for=day,
        ),
        fact(
            "equipment:drone_ready",
            "boolean",
            "Mavic3M",
            "Mavic3M__check_status",
            scope="equipment",
            valid_for=day,
        ),
        fact(
            "equipment:robot_ready",
            "boolean",
            "Robot0",
            "Robot0__check_status",
            scope="equipment",
            valid_for=day,
        ),
        fact(
            "weather:spray_window_open",
            "boolean",
            "WeatherApp.is_sprayable",
            "WeatherApp__get_current_weather",
            "WeatherApp__get_forecast",
            valid_for=day,
        ),
        fact(
            "weather:harvest_window_open",
            "boolean",
            "WeatherApp",
            "WeatherApp__get_current_weather",
            valid_for=day,
        ),
        fact(
            "soil:trafficable",
            "boolean",
            "WeatherApp.is_trafficable",
            "SensorApp__read_soil_sensors",
            valid_for=2 * day,
        ),
        fact(
            "disease:confirmed",
            "boolean",
            "FarmWorldApp.ridges.disease_pressure",
            "Mavic3M__fly_survey",
            "Robot0__inspect_crop_health",
            scope="ridge_range",
            valid_for=3 * day,
        ),
        fact(
            "disease:severity",
            "number",
            "FarmWorldApp.ridges.disease_pressure",
            "Mavic3M__fly_survey",
            "Robot0__inspect_crop_health",
            scope="ridge_range",
            valid_for=3 * day,
        ),
        fact(
            "disease:affected_scope",
            "mapping",
            "FarmWorldApp.ridges",
            "Mavic3M__fly_survey",
            "Robot0__inspect_crop_health",
            scope="ridge_range",
            valid_for=3 * day,
        ),
        fact(
            "treatment:completed",
            "boolean",
            "FarmWorldApp.management",
            "FarmWorldApp__get_ridge_range_state",
            scope="ridge_range",
        ),
        fact(
            "treatment:response",
            "number",
            "FarmWorldApp.ridges.disease_pressure",
            "Mavic3M__fly_survey",
            "Robot0__inspect_crop_health",
            scope="ridge_range",
            valid_for=3 * day,
        ),
        fact(
            "crop:mature",
            "boolean",
            "FarmWorldApp.ridges.growth_stage",
            "FarmWorldApp__get_ridge_range_state",
            scope="ridge_range",
            valid_for=3 * day,
        ),
        fact(
            "crop:grain_moisture",
            "number",
            "FarmWorldApp.ridges.grain_moisture",
            "FarmWorldApp__get_ridge_range_state",
            units="percent",
            scope="ridge_range",
            valid_for=3 * day,
        ),
        fact(
            "equipment:dryer_ready",
            "boolean",
            "FarmWorldApp.inventory",
            "FarmWorldApp__get_inventory",
            scope="equipment",
            valid_for=day,
        ),
        fact(
            "storage:capacity_available",
            "boolean",
            "FarmWorldApp.inventory",
            "FarmWorldApp__get_inventory",
            scope="resource",
            valid_for=day,
        ),
    )


def _policy_rules() -> tuple[PolicyRuleSpec, ...]:
    return (
        PolicyRuleSpec(
            requirement_state="true",
            deadline_state="open",
            permitted_responses=(
                PolicyResponse.EXECUTE,
                PolicyResponse.REOBSERVE,
                PolicyResponse.DEFER,
            ),
            required_responses=(PolicyResponse.EXECUTE,),
        ),
        PolicyRuleSpec(
            requirement_state="true",
            deadline_state="closed",
            permitted_responses=(PolicyResponse.ABSTAIN,),
            required_responses=(PolicyResponse.ABSTAIN,),
        ),
        PolicyRuleSpec(
            requirement_state="false",
            deadline_state="open",
            permitted_responses=(
                PolicyResponse.REOBSERVE,
                PolicyResponse.DEFER,
                PolicyResponse.ABSTAIN,
            ),
        ),
        PolicyRuleSpec(
            requirement_state="false",
            deadline_state="closed",
            permitted_responses=(PolicyResponse.ABSTAIN,),
            required_responses=(PolicyResponse.ABSTAIN,),
        ),
        PolicyRuleSpec(
            requirement_state="unknown",
            deadline_state="open",
            channel_state="open",
            permitted_responses=(
                PolicyResponse.REOBSERVE,
                PolicyResponse.HANDOFF,
                PolicyResponse.DEFER,
            ),
        ),
        PolicyRuleSpec(
            requirement_state="unknown",
            deadline_state="open",
            channel_state="closed",
            permitted_responses=(PolicyResponse.REOBSERVE, PolicyResponse.ABSTAIN),
        ),
        PolicyRuleSpec(
            requirement_state="unknown",
            deadline_state="closed",
            permitted_responses=(PolicyResponse.ABSTAIN,),
            required_responses=(PolicyResponse.ABSTAIN,),
        ),
    )


def build_wetjune_template(base_net: PetriNetSpec) -> FarmPetriTemplateSpec:
    grouped: defaultdict[tuple[str, str, str, str, str], list[str]] = defaultdict(list)
    for transition in base_net.transitions:
        module = _module_for(transition)
        scoring = _scoring_class(transition)
        grouped[
            (
                module,
                transition.kind.value,
                transition.actor_id,
                transition.action,
                scoring.value,
            )
        ].append(transition.transition_id)
    templates = []
    for index, (key, source_ids) in enumerate(sorted(grouped.items())):
        module, kind, actor, action, scoring = key
        templates.append(
            TransitionTemplateSpec(
                template_id=f"wetjune-template-{index:03d}",
                module_id=module,
                label=f"{module}: {action}",
                kind=TransitionKind(kind),
                actor_id=actor,
                action_pattern=action,
                scoring_class=ScoringClass(scoring),
                expansion=ExpansionRuleSpec(
                    source_transition_ids=tuple(source_ids),
                    expected_count=len(source_ids),
                    kind="repeat" if len(source_ids) > 1 else "source_events",
                ),
            )
        )
    for phase, module in (
        ("midseason", "mid_treatment"),
        ("r5", "r5_recheck"),
        ("harvest", "harvest_readiness"),
    ):
        transition_id = f"policy:{phase}:abstain"
        templates.append(
            TransitionTemplateSpec(
                template_id=f"wetjune-template-abstain-{phase}",
                module_id=module,
                label=f"{phase}: safe abstention",
                kind=TransitionKind.ACT,
                actor_id="operations",
                action_pattern="farm.abstain",
                scoring_class=ScoringClass.OPTIONAL_SAFE,
                expansion=ExpansionRuleSpec(
                    source_transition_ids=(transition_id,),
                    expected_count=1,
                ),
                required=False,
            )
        )
    policies = (
        InformationPolicySpec(
            policy_id="wetjune-fungicide-policy",
            actor_id="operations",
            action_patterns=("TractorApp__apply_fungicide",),
            phases=("midseason", "r5"),
            requirement_fact_keys=(
                "weather:spray_window_open",
                "soil:trafficable",
                "disease:confirmed",
                "disease:affected_scope",
            ),
            requirements=(
                DataGuardSpec(
                    guard_id="policy:fungicide:spray",
                    fact_key="weather:spray_window_open",
                    source="knowledge",
                    expected=True,
                    required_evidence=True,
                ),
                DataGuardSpec(
                    guard_id="policy:fungicide:traffic",
                    fact_key="soil:trafficable",
                    source="knowledge",
                    expected=True,
                    required_evidence=True,
                ),
                DataGuardSpec(
                    guard_id="policy:fungicide:disease",
                    fact_key="disease:confirmed",
                    source="knowledge",
                    expected=True,
                    required_evidence=True,
                ),
                DataGuardSpec(
                    guard_id="policy:fungicide:scope",
                    fact_key="disease:affected_scope",
                    source="knowledge",
                    operator=GuardOperator.NE,
                    expected=None,
                    required_evidence=True,
                ),
            ),
            rules=_policy_rules(),
        ),
        InformationPolicySpec(
            policy_id="wetjune-harvest-policy",
            actor_id="operations",
            action_patterns=("TractorApp__harvest",),
            phases=("harvest",),
            requirement_fact_keys=(
                "crop:mature",
                "crop:grain_moisture",
                "weather:harvest_window_open",
                "soil:trafficable",
            ),
            requirements=(
                DataGuardSpec(
                    guard_id="policy:harvest:mature",
                    fact_key="crop:mature",
                    source="knowledge",
                    expected=True,
                    required_evidence=True,
                ),
                DataGuardSpec(
                    guard_id="policy:harvest:moisture",
                    fact_key="crop:grain_moisture",
                    source="knowledge",
                    operator=GuardOperator.LE,
                    expected=18.0,
                    required_evidence=True,
                ),
                DataGuardSpec(
                    guard_id="policy:harvest:weather",
                    fact_key="weather:harvest_window_open",
                    source="knowledge",
                    expected=True,
                    required_evidence=True,
                ),
                DataGuardSpec(
                    guard_id="policy:harvest:traffic",
                    fact_key="soil:trafficable",
                    source="knowledge",
                    expected=True,
                    required_evidence=True,
                ),
            ),
            rules=_policy_rules(),
        ),
    )
    return FarmPetriTemplateSpec(
        template_id="farm_wetjune_recheck:hierarchical:v1",
        scenario_id="farm_wetjune_recheck",
        actors=base_net.actors,
        modules=WETJUNE_MODULES,
        transition_templates=tuple(templates),
        fact_definitions=_fact_definitions(),
        information_policies=policies,
        metadata={
            "source_net_digest": stable_digest(base_net.model_dump(mode="json")),
            "review_policy": "two_independent_domain_specs_plus_adjudication_and_confirmation",
            "paper_eligible": False,
            "weights": "equal_module_engineering_defaults",
        },
    )


def expand_wetjune_template(
    template: FarmPetriTemplateSpec, base_net: PetriNetSpec
) -> PetriNetSpec:
    """Expand without consulting an agent trace or model output."""
    assignment = {
        source: item
        for item in template.transition_templates
        for source in item.expansion.source_transition_ids
    }
    missing = {item.transition_id for item in base_net.transitions} - set(assignment)
    if missing:
        raise ValueError(
            f"Wet-June template does not cover transitions: {sorted(missing)}"
        )
    module_template_weight: defaultdict[str, float] = defaultdict(float)
    for item in template.transition_templates:
        if item.required and item.scoring_class not in {
            ScoringClass.BENIGN_LOOP,
            ScoringClass.OPTIONAL_SAFE,
        }:
            module_template_weight[item.module_id] += item.within_module_weight
    budgets = {module.module_id: module.weight_budget for module in template.modules}
    transitions = []
    for transition in base_net.transitions:
        item = assignment[transition.transition_id]
        scored = item.scoring_class not in {
            ScoringClass.BENIGN_LOOP,
            ScoringClass.OPTIONAL_SAFE,
        }
        weight = (
            budgets[item.module_id]
            * item.within_module_weight
            / max(module_template_weight[item.module_id], 1e-9)
            / item.expansion.expected_count
        )
        guards = transition.guards
        if transition.action == "TractorApp__apply_fungicide":
            guards = (
                *guards,
                DataGuardSpec(
                    guard_id=f"guard:{transition.transition_id}:disease-scope:known",
                    fact_key="disease:affected_scope",
                    source="knowledge",
                    operator=GuardOperator.NE,
                    expected=None,
                    scope=transition.scope,
                    max_age=3 * 86400,
                    required_evidence=True,
                ),
                DataGuardSpec(
                    guard_id=f"guard:{transition.transition_id}:disease-scope:world",
                    fact_key="disease:affected_scope",
                    source="world",
                    operator=GuardOperator.NE,
                    expected=None,
                    scope=transition.scope,
                    max_age=3 * 86400,
                ),
            )
        transitions.append(
            transition.model_copy(
                update={
                    "module_id": item.module_id,
                    "template_id": item.template_id,
                    "scoring_class": item.scoring_class,
                    "required": transition.required and item.required and scored,
                    "weight": weight if scored else 1e-9,
                    "guards": guards,
                }
            )
        )
    for item in template.transition_templates:
        for source_id in item.expansion.source_transition_ids:
            if source_id in {
                transition.transition_id for transition in base_net.transitions
            }:
                continue
            transitions.append(
                TransitionSpec(
                    transition_id=source_id,
                    label=item.label,
                    actor_id=item.actor_id,
                    action=item.action_pattern,
                    kind=item.kind,
                    phase=next(
                        module.phase
                        for module in template.modules
                        if module.module_id == item.module_id
                    ),
                    required=False,
                    weight=1e-9,
                    module_id=item.module_id,
                    template_id=item.template_id,
                    scoring_class=item.scoring_class,
                )
            )
    review_path = Path(__file__).with_name("specs") / (
        "farm_wetjune_recheck.review_v3.json"
    )
    expanded = base_net.model_copy(
        update={
            "schema_version": "farm_petri_v3",
            "net_id": "farm_wetjune_recheck:petri:v3",
            "oracle_version": "farm_dcore_review_v3",
            "modules": template.modules,
            "expert_review_status": template.expert_review_status,
            "metadata": {
                **base_net.metadata,
                "hierarchical_template_id": template.template_id,
                "public_scenario_id": template.scenario_id,
                "hierarchical_template_digest": stable_digest(
                    template.model_dump(mode="json")
                ),
                "annotation_status": template.annotation_status,
                "paper_eligible": template.annotation_status == "frozen",
                "module_normalization": "fixed_budget_divided_across_scored_instances",
                "metric_annotation_status": (
                    "frozen" if template.annotation_status == "frozen" else "unfrozen"
                ),
                "branch_annotation_status": (
                    "frozen"
                    if template.annotation_status == "frozen"
                    and template.world_branches
                    else "unfrozen"
                ),
                "guard_annotation_status": (
                    "frozen" if template.annotation_status == "frozen" else "unfrozen"
                ),
                "review_manifest": str(review_path),
                "review_manifest_digest": stable_digest(
                    review_path.read_text(encoding="utf-8")
                ),
                "fact_definitions": [
                    item.model_dump(mode="json") for item in template.fact_definitions
                ],
                "information_policies": [
                    item.model_dump(mode="json")
                    for item in template.information_policies
                ],
            },
            "transitions": tuple(transitions),
        }
    )
    return _relax_observation_serialization(expanded)


def _relax_observation_serialization(net: PetriNetSpec) -> PetriNetSpec:
    """Fork/join serialized read-only runs without dropping their obligations."""

    transitions = {item.transition_id: item for item in net.transitions}
    observation_edges = {
        (left, right)
        for left, right in transition_dependencies(net)
        if transitions[left].kind == TransitionKind.OBSERVE
        and transitions[right].kind == TransitionKind.OBSERVE
        and transitions[left].actor_id == transitions[right].actor_id
        and transitions[left].module_id == transitions[right].module_id
    }
    neighbors: defaultdict[str, set[str]] = defaultdict(set)
    for left, right in observation_edges:
        neighbors[left].add(right)
        neighbors[right].add(left)
    components: list[tuple[str, ...]] = []
    unseen = set(neighbors)
    while unseen:
        root = min(unseen)
        stack = [root]
        component: set[str] = set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(neighbors[node] - component)
        unseen -= component
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    places = {item.place_id: item for item in net.places}
    arcs = list(net.arcs)
    relaxed: list[dict[str, object]] = []
    for index, component in enumerate(components):
        members = set(component)
        touching_places = {
            arc.target for arc in arcs if arc.source in members and arc.target in places
        } | {
            arc.source for arc in arcs if arc.target in members and arc.source in places
        }
        incoming_sources = sorted(
            {
                arc.source
                for arc in arcs
                if arc.target in touching_places
                and arc.source in transitions
                and arc.source not in members
            }
        )
        initially_marked = not incoming_sources and any(
            places[place_id].initially_marked for place_id in touching_places
        )
        outgoing_targets = sorted(
            {
                arc.target
                for arc in arcs
                if arc.source in touching_places
                and arc.target in transitions
                and arc.target not in members
            }
        )
        if not outgoing_targets:
            continue
        arcs = [
            arc
            for arc in arcs
            if arc.source not in members
            and arc.target not in members
            and arc.source not in touching_places
            and arc.target not in touching_places
        ]
        for place_id in touching_places:
            places.pop(place_id, None)
        prefix = f"parallel-observe-{index:02d}"
        for member_index, transition_id in enumerate(component):
            sources: tuple[str | None, ...] = (
                tuple(incoming_sources) if incoming_sources else (None,)
            )
            for source_index, source in enumerate(sources):
                input_id = f"{prefix}:in:{source_index:02d}:{member_index:02d}"
                places[input_id] = PlaceSpec(
                    place_id=input_id,
                    label="independent observation enabled",
                    initially_marked=initially_marked,
                )
                arcs.append(
                    ArcSpec(
                        arc_id=(
                            f"{prefix}:enable:{source_index:02d}:{member_index:02d}"
                        ),
                        source=input_id,
                        target=transition_id,
                    )
                )
                if source is None:
                    continue
                arcs.append(
                    ArcSpec(
                        arc_id=(
                            f"{prefix}:source:{source_index:02d}:{member_index:02d}"
                        ),
                        source=source,
                        target=input_id,
                    )
                )
            for target_index, target in enumerate(outgoing_targets):
                output_id = f"{prefix}:out:{member_index:02d}:{target_index:02d}"
                places[output_id] = PlaceSpec(
                    place_id=output_id,
                    label="independent observation complete",
                )
                arcs.append(
                    ArcSpec(
                        arc_id=(
                            f"{prefix}:complete:{member_index:02d}:{target_index:02d}"
                        ),
                        source=transition_id,
                        target=output_id,
                    )
                )
                arcs.append(
                    ArcSpec(
                        arc_id=(
                            f"{prefix}:target:{member_index:02d}:{target_index:02d}"
                        ),
                        source=output_id,
                        target=target,
                    )
                )
        relaxed.append(
            {
                "transition_ids": component,
                "incoming_sources": incoming_sources,
                "outgoing_targets": outgoing_targets,
            }
        )
    return net.model_copy(
        update={
            "places": tuple(sorted(places.values(), key=lambda item: item.place_id)),
            "arcs": tuple(sorted(arcs, key=lambda item: item.arc_id)),
            "metadata": {
                **net.metadata,
                "relaxed_observation_components": relaxed,
                "independent_observation_semantics": "petri_fork_join_v1",
            },
        }
    )
