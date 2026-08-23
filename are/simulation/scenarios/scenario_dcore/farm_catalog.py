"""Farm-only D-CORE catalog backed by native FarmARE L3 scenarios."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.petri import (
    ArcSpec,
    ArgumentConstraint,
    DataGuardSpec,
    ExogenousBranchSpec,
    PetriNetSpec,
    PlaceKind,
    PlaceSpec,
    TransitionKind,
    TransitionSpec,
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_disease_then_drought_recovery_tradeoff import (
    ScenarioFullSeasonHBDiseaseThenDroughtRecoveryTradeoff,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence import (
    ScenarioFullSeasonHBThreeCultivarWetDiseaseDryHarvestSequence,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_wetjune_disease_recheck_after_fungicide import (
    ScenarioFullSeasonHBWetjuneDiseaseRecheckAfterFungicide,
)
from are.simulation.types import Action, OperationType, OracleEvent

INTELLIGENCE_ACTOR = "field_intelligence"
OPERATIONS_ACTOR = "operations"
ACTORS = (INTELLIGENCE_ACTOR, OPERATIONS_ACTOR)


@dataclass(frozen=True)
class FarmScenarioDescriptor:
    scenario_id: str
    native_scenario_id: str
    title: str
    scenario_class: type[Scenario]
    scientific_focus: tuple[str, ...]


FARM_SCENARIOS: dict[str, FarmScenarioDescriptor] = {
    "farm_wetjune_recheck": FarmScenarioDescriptor(
        scenario_id="farm_wetjune_recheck",
        native_scenario_id="scenario_full_season_hb_wetjune_disease_recheck_after_fungicide",
        title="Wet-June disease recheck",
        scenario_class=ScenarioFullSeasonHBWetjuneDiseaseRecheckAfterFungicide,
        scientific_focus=("freshness", "supersession", "reinspection"),
    ),
    "farm_disease_drought": FarmScenarioDescriptor(
        scenario_id="farm_disease_drought",
        native_scenario_id="scenario_full_season_hb_disease_then_drought_recovery_tradeoff",
        title="Disease-then-drought recovery tradeoff",
        scenario_class=ScenarioFullSeasonHBDiseaseThenDroughtRecoveryTradeoff,
        scientific_focus=("persistent_knowledge", "diagnostic_shift", "recovery"),
    ),
    "farm_three_cultivar": FarmScenarioDescriptor(
        scenario_id="farm_three_cultivar",
        native_scenario_id="scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence",
        title="Three-cultivar disease-water-harvest sequence",
        scenario_class=ScenarioFullSeasonHBThreeCultivarWetDiseaseDryHarvestSequence,
        scientific_focus=("concurrency", "spatial_scope", "harvest_order"),
    ),
}


def get_farm_descriptor(scenario_id: str) -> FarmScenarioDescriptor:
    aliases = {
        "farm_wetjune": "farm_wetjune_recheck",
        "farm_wetjune_fungicide_dcore": "farm_wetjune_recheck",
    }
    resolved = aliases.get(scenario_id, scenario_id)
    if resolved not in FARM_SCENARIOS:
        raise ValueError(
            f"unknown farm D-CORE scenario {scenario_id!r}; "
            f"choose one of {sorted(FARM_SCENARIOS)}"
        )
    return FARM_SCENARIOS[resolved]


def create_native_scenario(scenario_id: str, *, world_seed: int) -> Scenario:
    descriptor = get_farm_descriptor(scenario_id)
    scenario = descriptor.scenario_class(seed=world_seed)
    scenario.initialize()
    _freeze_exogenous_weather(scenario, world_seed)
    return scenario


def _freeze_exogenous_weather(scenario: Scenario, world_seed: int) -> None:
    """Precompute date-indexed weather independently of agent access order."""

    from are.simulation.apps.farm_world import FarmWorldApp
    from are.simulation.apps.farm_world.physics_orchestrator import (
        _generate_weather_day,
    )
    from are.simulation.physics import WeatherGenerator

    farm_world = scenario.get_typed_app(FarmWorldApp)
    physics = farm_world.physics
    profile = getattr(physics, "profile", None)
    if profile is None or scenario.start_time is None:
        return
    # Offset the registered profile seed so world_seed=0 preserves the
    # scenario's original forcing while additional seeds generate paired but
    # genuinely distinct worlds.
    effective_seed = int(profile.rng_seed) + int(world_seed)
    generator = WeatherGenerator(
        config=profile.to_weather_generator_config(), seed=effective_seed
    )
    start = datetime.fromtimestamp(
        scenario.start_time, tz=timezone.utc
    ).date() + timedelta(days=1)
    duration_days = max(
        1,
        int((scenario.duration or profile.duration_days * 86400.0) / 86400.0),
    )
    # Seven extra days cover every forecast exposed on the final season day.
    end = start + timedelta(days=duration_days + 6)
    current = start
    events = list(getattr(profile, "weather_events", ()) or ())
    while current <= end:
        _generate_weather_day(generator, current, events)
        current += timedelta(days=1)
    cache = getattr(generator, "_day_cache", {})
    weather_manifest = [asdict(cache[day]) for day in sorted(cache)]
    outbreak_manifest = [
        asdict(item) for item in (getattr(profile, "biotic_outbreaks", ()) or ())
    ]
    physics.weather_generator = generator
    physics.random_seed = effective_seed
    physics.dcore_exogenous_manifest = {  # type: ignore[attr-defined]
        "profile_name": profile.name,
        "world_seed": int(world_seed),
        "effective_weather_seed": effective_seed,
        "weather_days": weather_manifest,
        "biotic_outbreaks": outbreak_manifest,
    }


def phase_for_event(event_id: str, action: Action) -> str:
    value = event_id.lower()
    function = action.function_name.lower()
    # Do not classify from broad tokens in the scenario slug (the
    # three-cultivar scenario contains ``dry_harvest_sequence`` in every event
    # ID). Storage is a property of the concrete operation/checkpoint.
    if function in {
        "dry_grain",
        "store_grain",
    } or any(
        token in value.rsplit("sequence_", 1)[-1] for token in ("storage", "warehouse")
    ):
        return "storage"
    if "harvest" in value or function in {"harvest", "unload_grain"}:
        return "harvest"
    if "r5" in value:
        return "r5"
    if "mid" in value:
        return "midseason"
    if "r1" in value:
        return "r1"
    if "emergence" in value or "replant" in value:
        return "emergence"
    if "plant" in value:
        return "planting"
    return "field_prep"


def actor_for_action(action: Action) -> str:
    class_name = action.class_name
    function = action.function_name
    if class_name in {"WeatherApp", "SensorApp", "DroneApp", "RobotApp"}:
        return INTELLIGENCE_ACTOR
    if class_name == "FarmWorldApp" and function.startswith(("get_", "read_")):
        if function == "get_inventory":
            return OPERATIONS_ACTOR
        return INTELLIGENCE_ACTOR
    return OPERATIONS_ACTOR


def native_action_name(action: Action) -> str:
    if action.class_name in {"DroneApp", "RobotApp"} and action.app_name:
        return f"{action.app_name}__{action.function_name}"
    return f"{action.class_name}__{action.function_name}"


def is_high_impact(action: Action) -> bool:
    if action.operation_type != OperationType.WRITE:
        return False
    name = action.function_name.lower()
    harmless = {
        "charge",
        "attach_implement",
        "detach_implement",
        "load_seeds",
        "load_fertilizer",
        "load_fungicide",
        "load_pesticide",
    }
    return name not in harmless


def _argument_constraints(
    action: Action,
    event_id: str,
    numeric_tolerances: dict[str, Any],
) -> tuple[ArgumentConstraint, ...]:
    constraints: list[ArgumentConstraint] = []
    for name, expected in action.args.items():
        if name == "self":
            continue
        tolerance = None
        if isinstance(expected, float):
            annotation_key = f"{event_id}.{name}"
            tolerance = numeric_tolerances.get(annotation_key)
            if tolerance is None:
                # Engineering-only default. Paper runs are blocked by doctor
                # until the review manifest freezes this value.
                tolerance = max(abs(expected) * 0.05, 1e-9)
        constraints.append(
            ArgumentConstraint(
                name=name,
                expected=expected,
                tolerance=tolerance,
                critical=True,
            )
        )
    return tuple(constraints)


def _scope_from_args(args: dict[str, Any]) -> tuple[int, int] | None:
    pairs = (
        ("start_ridge", "end_ridge"),
        ("ridge_start", "ridge_end"),
    )
    for start_key, end_key in pairs:
        if start_key in args and end_key in args:
            try:
                return int(args[start_key]), int(args[end_key])
            except (TypeError, ValueError):
                return None
    if "ridge_id" in args:
        try:
            start = int(args["ridge_id"])
            count = int(args.get("ridge_count", 1))
            return start, start + count - 1
        except (TypeError, ValueError):
            return None
    return None


def _agronomic_guards(
    scenario_id: str,
    event_id: str,
    action: Action,
    scope: tuple[int, int] | None,
) -> tuple[DataGuardSpec, ...]:
    """Return predeclared information and truth guards for physical writes.

    The knowledge guard evaluates what Operations could justify; its world
    twin evaluates whether that belief was actually correct. Keeping the two
    explicit is essential to measuring the local/global gap.
    """

    function = action.function_name.lower()
    requirements: list[tuple[str, Any, float | None]] = []
    # Wet-June is the validated vertical slice. The remaining scenarios retain
    # generic evidence guards until their expert annotation manifests freeze
    # the agronomic thresholds; we do not invent thresholds from outcomes.
    if function == "apply_fungicide" and scenario_id == "farm_wetjune_recheck":
        requirements.extend(
            (
                # The native oracle explicitly obtains a multi-day forecast
                # before waiting three days for the treatment window.
                ("weather:spray_window_open", True, 4 * 86400),
                ("soil:trafficable", True, 2 * 86400),
                ("disease:confirmed", True, 3 * 86400),
            )
        )
    elif function == "harvest":
        requirements.append(("crop:mature", True, 3 * 86400))
    guards: list[DataGuardSpec] = []
    for fact_key, expected, max_age in requirements:
        suffix = fact_key.replace(":", "-")
        guards.extend(
            (
                DataGuardSpec(
                    guard_id=f"guard:{event_id}:{suffix}:known",
                    fact_key=fact_key,
                    source="knowledge",
                    expected=expected,
                    required_evidence=True,
                    scope=scope,
                    max_age=max_age,
                ),
                DataGuardSpec(
                    guard_id=f"guard:{event_id}:{suffix}:world",
                    fact_key=fact_key,
                    source="world",
                    expected=expected,
                    scope=scope,
                    max_age=max_age,
                ),
            )
        )
    return tuple(guards)


def compile_native_petri_net(
    scenario_id: str,
    *,
    world_seed: int = 0,
) -> PetriNetSpec:
    """Compile a reviewed L3 workflow into a distributed occurrence-oriented net.

    FarmARE's oracle is intentionally serialized.  This compiler preserves
    per-role program order and adds only evidence-to-write cross-role edges,
    exposing concurrency between independent reads and resource checks.
    """

    descriptor = get_farm_descriptor(scenario_id)
    review_path = Path(__file__).with_name("specs") / f"{scenario_id}.review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    numeric_tolerances = dict(review.get("numeric_tolerances", {}))
    transition_weights = dict(review.get("transition_weights", {}))
    time_windows = dict(review.get("time_windows", {}))
    guard_overrides = dict(review.get("guard_overrides", {}))
    expected_net_id = f"{scenario_id}:petri:v2"
    if review.get("petri_net_id") != expected_net_id:
        raise ValueError(
            f"review manifest {review_path} targets {review.get('petri_net_id')!r}, "
            f"expected {expected_net_id!r}"
        )
    scenario = create_native_scenario(scenario_id, world_seed=world_seed)
    transitions: list[TransitionSpec] = []
    source_events: list[tuple[str, Action]] = []
    for event in scenario.events:
        if not isinstance(event, OracleEvent):
            continue
        source = event.make_event(None)
        action = getattr(source, "action", None)
        if not isinstance(action, Action) or action.class_name == "AgentUserInterface":
            continue
        actor_id = actor_for_action(action)
        phase = phase_for_event(event.event_id, action)
        high_impact = actor_id == OPERATIONS_ACTOR and is_high_impact(action)
        transition_scope = _scope_from_args(action.args)
        evidence_phase = "harvest" if phase == "storage" else phase
        evidence_guards = (
            (
                DataGuardSpec(
                    guard_id=f"guard:{event.event_id}:evidence",
                    fact_key=f"phase_evidence:{evidence_phase}",
                    source="knowledge",
                    expected=True,
                    required_evidence=True,
                    scope=transition_scope,
                ),
            )
            if high_impact
            else ()
        )
        guards = evidence_guards + (
            _agronomic_guards(scenario_id, event.event_id, action, transition_scope)
            if high_impact
            else ()
        )
        if event.event_id in guard_overrides:
            guards = tuple(
                DataGuardSpec.model_validate(guard)
                for guard in guard_overrides[event.event_id]
            )
        kind = TransitionKind.ACT
        # SystemApp.advance_time is exposed as a read-like FarmARE action even
        # though its task semantics are temporal progression. Classify it
        # before the generic READ case so the executable reference represents
        # long-horizon waits explicitly rather than treating them as evidence.
        semantic_observation = action.operation_type == OperationType.READ or (
            actor_id == INTELLIGENCE_ACTOR
            and (
                action.function_name == "fly_survey"
                or action.function_name.startswith("inspect_")
            )
        )
        if action.function_name == "advance_time":
            kind = TransitionKind.WAIT
        elif semantic_observation:
            kind = TransitionKind.OBSERVE
        elif "harvest" in action.function_name:
            kind = TransitionKind.ACT
        transition = TransitionSpec(
            transition_id=event.event_id,
            label=native_action_name(action),
            actor_id=actor_id,
            action=native_action_name(action),
            kind=kind,
            phase=phase,
            arguments=_argument_constraints(action, event.event_id, numeric_tolerances),
            scope=transition_scope,
            window_start=(
                scenario.start_time
                + float(time_windows[event.event_id]["start_day"]) * 86400
                if event.event_id in time_windows
                and time_windows[event.event_id].get("start_day") is not None
                else None
            ),
            window_end=(
                scenario.start_time
                + float(time_windows[event.event_id]["end_day"]) * 86400
                if event.event_id in time_windows
                and time_windows[event.event_id].get("end_day") is not None
                else None
            ),
            guards=guards,
            high_impact=high_impact,
            weight=float(transition_weights.get(event.event_id, 1.0)),
        )
        transitions.append(transition)
        source_events.append((event.event_id, action))

    dependencies: set[tuple[str, str]] = set()
    communication_transitions: list[TransitionSpec] = []
    by_actor: defaultdict[str, list[str]] = defaultdict(list)
    phase_reads: defaultdict[str, list[tuple[int, str]]] = defaultdict(list)
    transition_index = {
        transition.transition_id: index for index, transition in enumerate(transitions)
    }
    for index, transition in enumerate(transitions):
        by_actor[transition.actor_id].append(transition.transition_id)
        if (
            transition.actor_id == INTELLIGENCE_ACTOR
            and transition.kind == TransitionKind.OBSERVE
        ):
            phase_reads[transition.phase].append((index, transition.transition_id))
    for actor_transitions in by_actor.values():
        dependencies.update(zip(actor_transitions, actor_transitions[1:]))
    for transition in transitions:
        if transition.actor_id == OPERATIONS_ACTOR and transition.high_impact:
            evidence_phase = transition.guards[0].fact_key.removeprefix(
                "phase_evidence:"
            )
            prior_reads = [
                event_id
                for index, event_id in phase_reads[evidence_phase]
                if index < transition_index[transition.transition_id]
            ]
            # The latest applicable observation is the direct evidence
            # prerequisite. Older reads are transitive history, not additional
            # constraints, and future reads must never authorize earlier work.
            if prior_reads:
                evidence_transition = prior_reads[-1]
                send_id = f"handoff_send:{transition.transition_id}"
                receive_id = f"handoff_receive:{transition.transition_id}"
                communication_transitions.extend(
                    (
                        TransitionSpec(
                            transition_id=send_id,
                            label=f"send evidence for {transition.label}",
                            actor_id=INTELLIGENCE_ACTOR,
                            action="farm.causal_handoff",
                            kind=TransitionKind.SEND,
                            phase=transition.phase,
                        ),
                        TransitionSpec(
                            transition_id=receive_id,
                            label=f"receive evidence for {transition.label}",
                            actor_id=OPERATIONS_ACTOR,
                            action="farm.causal_receive",
                            kind=TransitionKind.RECEIVE,
                            phase=transition.phase,
                        ),
                    )
                )
                dependencies.update(
                    {
                        (evidence_transition, send_id),
                        (send_id, receive_id),
                        (receive_id, transition.transition_id),
                    }
                )
    transitions.extend(communication_transitions)

    places: list[PlaceSpec] = []
    arcs: list[ArcSpec] = []
    first_by_actor = {
        actor_id: actor_transitions[0]
        for actor_id, actor_transitions in by_actor.items()
        if actor_transitions
    }
    last_by_actor = {
        actor_id: actor_transitions[-1]
        for actor_id, actor_transitions in by_actor.items()
        if actor_transitions
    }
    for actor_id, first in first_by_actor.items():
        place_id = f"start:{actor_id}"
        places.append(
            PlaceSpec(
                place_id=place_id,
                label=f"{actor_id} ready",
                initially_marked=True,
            )
        )
        arcs.append(
            ArcSpec(arc_id=f"arc:{place_id}:{first}", source=place_id, target=first)
        )
    for index, (source, target) in enumerate(sorted(dependencies)):
        place_id = f"dep:{index:04d}:{source}:{target}"
        kind = (
            PlaceKind.EVIDENCE
            if (
                next(t for t in transitions if t.transition_id == source).actor_id
                != next(t for t in transitions if t.transition_id == target).actor_id
            )
            else PlaceKind.CONTROL
        )
        places.append(PlaceSpec(place_id=place_id, kind=kind))
        arcs.extend(
            (
                ArcSpec(arc_id=f"arc:{place_id}:in", source=source, target=place_id),
                ArcSpec(arc_id=f"arc:{place_id}:out", source=place_id, target=target),
            )
        )
    finish_inputs: list[str] = []
    for actor_id, last in last_by_actor.items():
        place_id = f"finish-ready:{actor_id}"
        places.append(PlaceSpec(place_id=place_id, label=f"{actor_id} complete"))
        arcs.append(
            ArcSpec(arc_id=f"arc:{last}:{place_id}", source=last, target=place_id)
        )
        finish_inputs.append(place_id)
    finish = TransitionSpec(
        transition_id="season_complete",
        label="season complete",
        actor_id="world",
        action="farm.season_complete",
        kind=TransitionKind.FINISH,
        phase="storage",
    )
    transitions.append(finish)
    terminal = PlaceSpec(
        place_id="season-complete", label="season complete", terminal=True
    )
    places.append(terminal)
    for place_id in finish_inputs:
        arcs.append(
            ArcSpec(
                arc_id=f"arc:{place_id}:season_complete",
                source=place_id,
                target=finish.transition_id,
            )
        )
    arcs.append(
        ArcSpec(
            arc_id="arc:season_complete:terminal",
            source=finish.transition_id,
            target=terminal.place_id,
        )
    )

    return PetriNetSpec(
        schema_version="farm_petri_v2",
        net_id=expected_net_id,
        scenario_id=descriptor.native_scenario_id,
        actors=ACTORS,
        places=tuple(places),
        transitions=tuple(transitions),
        arcs=tuple(arcs),
        exogenous_branches=tuple(
            ExogenousBranchSpec.model_validate(branch)
            for branch in review.get("exogenous_branches", [])
        ),
        oracle_version=str(review.get("schema_version", "unreviewed")),
        expert_review_status=review.get("expert_review_status", "unreviewed"),
        metadata={
            "source": "native_farmare_l3_oracle",
            # Keep FarmARE's native identifier on the occurrence net while
            # binding it to the stable public benchmark identifier used by
            # review packets, manifests, matrices, and scenario construction.
            "public_scenario_id": descriptor.scenario_id,
            "world_seed": world_seed,
            "scientific_focus": descriptor.scientific_focus,
            "compiler": "distributed_role_order_plus_explicit_handoffs_v3",
            "source_event_digest": stable_digest(
                [event_id for event_id, _ in source_events]
            ),
            "expert_review_required": True,
            "metric_annotation_status": review.get(
                "metric_annotation_status", "unfrozen"
            ),
            "branch_annotation_status": review.get(
                "branch_annotation_status", "unfrozen"
            ),
            "guard_annotation_status": review.get(
                "guard_annotation_status", "unfrozen"
            ),
            "tolerance_source": (
                "expert_manifest"
                if review.get("metric_annotation_status") == "frozen"
                else "engineering_smoke_defaults"
            ),
            "review_manifest": str(review_path.relative_to(Path.cwd()))
            if review_path.is_relative_to(Path.cwd())
            else str(review_path),
            "review_manifest_digest": stable_digest(review),
            "removed_serialization_policy": (
                "preserve per-role order; retain only same-phase intelligence-to-"
                "high-impact-operation cross-role prerequisites"
            ),
        },
    )


def reference_action_map(scenario: Scenario) -> dict[str, OracleEvent]:
    return {
        event.event_id: event
        for event in scenario.events
        if isinstance(event, OracleEvent)
    }


def compile_paper_petri_net(scenario_id: str, *, world_seed: int = 0) -> PetriNetSpec:
    """Return the newest non-frozen paper specification for a farm scenario."""
    base = compile_native_petri_net(scenario_id, world_seed=world_seed)
    if get_farm_descriptor(scenario_id).scenario_id != "farm_wetjune_recheck":
        return base
    from are.simulation.scenarios.scenario_dcore.wetjune_petri_v3 import (
        build_wetjune_template,
        expand_wetjune_template,
    )

    template = build_wetjune_template(base)
    return expand_wetjune_template(template, base)
