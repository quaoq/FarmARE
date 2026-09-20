"""Author-defined farm contracts; genuine professor approval is a separate gate.

Reuse native occurrence identities and tool semantics, but author the scientific
facts, scoring, policies, chronology, negative obligations and fault assignments.
This module does not use model outputs to generate or choose a specification.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.petri import (
    BranchAlternativeSpec,
    DataGuardSpec,
    FactDefinitionSpec,
    GuardOperator,
    PetriModuleSpec,
    PolicyResponse,
    ScoringClass,
    WorldBranchSpec,
)
from are.simulation.distributed.scientific_v5 import (
    CausalObligationGroupSpec,
    CausalPathSpec,
    CausalPrerequisiteSpec,
    CommunicationFaultTreatmentSpec,
    FactVectorPolicyRuleSpec,
    FactVerdictPatternSpec,
    FarmProcessSpecV5,
    InformationPolicySpecV5,
    NegativeActionObligationSpec,
    PhaseWindowSpec,
    TransitionAcceptanceSpec,
)
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    FARM_SCENARIOS,
    compile_native_petri_net,
)

DAY = 86400.0
HIGH_IMPACT = {
    "TractorApp__plant_seeds",
    "TractorApp__apply_fungicide",
    "FieldOpsApp__irrigate",
    "TractorApp__harvest",
}
REQUIRED_WRITES = HIGH_IMPACT | {
    "TractorApp__level",
    "TractorApp__base_fertilize",
    "TractorApp__form_ridges",
    "FarmWorldApp__dry_grain",
    "FarmWorldApp__store_grain",
}


def _time(date: str) -> float:
    return datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()


def _windows(scenario: str) -> tuple[PhaseWindowSpec, ...]:
    # Calendar windows are author protocol choices, not inferred crop stages.
    # Harvest and storage share a zone's window, permitting native interleaving.
    starts = [
        ("establishment", "2026-05-04"),
        ("monitoring", "2026-05-20"),
        ("disease", "2026-07-01"),
        ("reproduction", "2026-07-24"),
    ]
    starts += (
        [
            ("harvest_a", "2026-08-20"),
            ("harvest_b", "2026-09-04"),
            ("harvest_c", "2026-09-17"),
        ]
        if scenario == "farm_three_cultivar"
        else [("harvest", "2026-09-01")]
    )
    ends = [date for _, date in starts[1:]] + ["2026-11-02"]
    return tuple(
        PhaseWindowSpec(
            phase=phase, start_world_time=_time(start), end_world_time=_time(end)
        )
        for (phase, start), end in zip(starts, ends, strict=True)
    )


def _phase(transition, scenario: str) -> str:
    if transition.phase in {"field_prep", "planting"}:
        return "establishment"
    if transition.phase in {"emergence", "r1"}:
        return "monitoring"
    if transition.phase == "midseason":
        return "disease"
    if transition.phase == "r5":
        return "reproduction"
    if scenario == "farm_three_cultivar":
        for zone in "abc":
            if f"zone_{zone}_" in transition.transition_id:
                return f"harvest_{zone}"
        return "harvest_c"  # terminal season bookkeeping
    return "harvest"


def _rules(requirements) -> tuple[FactVectorPolicyRuleSpec, ...]:
    keys = [g.fact_key for g in requirements]

    def rule(name, pattern, deadline, channel, allowed):
        return FactVectorPolicyRuleSpec(
            rule_id=name,
            fact_pattern=tuple(
                FactVerdictPatternSpec(fact_key=k, verdict=v)
                for k, v in zip(keys, pattern, strict=True)
            ),
            deadline_state=deadline,
            channel_state=channel,
            permitted_responses=allowed,
        )

    rules = [
        rule(
            "deadline_closed",
            ["any"] * len(keys),
            "closed",
            "any",
            (PolicyResponse.ABSTAIN, PolicyResponse.HANDOFF),
        ),
        rule(
            "all_prerequisites_supported",
            ["true"] * len(keys),
            "open",
            "any",
            (PolicyResponse.EXECUTE, PolicyResponse.DEFER, PolicyResponse.HANDOFF),
        ),
    ]
    # First non-true cell gives a compact, exhaustive, disjoint ternary table.
    # A closed channel cannot require an impossible outgoing handoff.
    for index in range(len(keys)):
        for verdict in ("false", "unknown"):
            pattern = ["true"] * index + [verdict] + ["any"] * (len(keys) - index - 1)
            for channel in ("open", "closed"):
                allowed = (
                    PolicyResponse.REOBSERVE,
                    PolicyResponse.DEFER,
                    PolicyResponse.ABSTAIN,
                )
                if channel == "open":
                    allowed += (PolicyResponse.HANDOFF,)
                rules.append(
                    rule(
                        f"first_{index}_{verdict}_{channel}",
                        pattern,
                        "open",
                        channel,
                        allowed,
                    )
                )
    return tuple(rules)


def _facts() -> tuple[FactDefinitionSpec, ...]:
    ridge_read = ("FarmWorldApp__get_ridge_range_state",)
    soil_reads = ("SensorApp__read_soil_sensor", "SensorApp__read_soil_sensors")
    definitions = (
        (
            "planting:soil_suitable",
            "boolean",
            None,
            soil_reads,
            "0.20 <= mean(FarmWorldApp.ridges[scope].soil_vwc) <= 0.35",
            DAY,
        ),
        (
            "soil:trafficable",
            "boolean",
            None,
            soil_reads,
            "WeatherApp.is_trafficable: native field-average VWC below 0.40",
            DAY,
        ),
        (
            "disease:confirmed",
            "boolean",
            None,
            ridge_read + ("Robot0__inspect_crop_health",),
            "any(FarmWorldApp.ridges[scope].disease_pressure >= 0.20)",
            3 * DAY,
        ),
        (
            "weather:spray_window_open",
            "boolean",
            None,
            ("WeatherApp__get_current_weather",),
            "WeatherApp.is_sprayable: rain == 0 mm and wind < 8 m/s",
            DAY,
        ),
        (
            "weather:harvest_window_open",
            "boolean",
            None,
            ("WeatherApp__get_current_weather",),
            "WeatherApp.rainfall_mm == 0",
            DAY,
        ),
        (
            "drought:surface_dry",
            "boolean",
            None,
            soil_reads + ridge_read,
            "mean(FarmWorldApp.ridges[scope].soil_vwc) < 0.20; surface proxy",
            DAY,
        ),
        (
            "drought:root_stressed",
            "boolean",
            None,
            (),
            "fraction(physics.soil.states[scope].root_vwc < 0.18) >= 0.50",
            DAY,
        ),
        (
            "crop:mature",
            "boolean",
            None,
            ridge_read,
            "all requested native ridges are R8 or already harvested",
            DAY,
        ),
        (
            "crop:grain_moisture",
            "number",
            "percent",
            ridge_read,
            "max(FarmWorldApp.ridges[scope].grain_moisture_pct)",
            DAY,
        ),
    )
    return tuple(
        FactDefinitionSpec(
            fact_key=key,
            value_type=kind,
            units=units,
            scope_kind="ridge_range",
            observation_actions=actions,
            truth_source=source,
            engineering_valid_for=validity,
            paper_status="frozen",
        )
        for key, kind, units, actions, source, validity in definitions
    )


def author_process(
    scenario: str,
    *,
    scenario_revision: str | None = None,
    calibration_candidate: bool = False,
    reference_harvest_calendar: bool = False,
    reference_harvest_opening: bool = False,
) -> FarmProcessSpecV5:
    if scenario not in FARM_SCENARIOS:
        raise ValueError("unknown authored scenario")
    if reference_harvest_opening and (
        not reference_harvest_calendar or scenario != "farm_disease_drought"
    ):
        raise ValueError(
            "harvest-opening reference requires the bounded drought calendar"
        )
    reference_policy = (
        "authored_opening_harvest_v6"
        if reference_harvest_opening
        else "authored_harvest_calendar_v5"
    )
    native = compile_native_petri_net(
        scenario,
        world_seed=0,
        scenario_revision=scenario_revision,
        calibration_candidate=calibration_candidate,
    )
    scenario_horizon = float(native.metadata["scenario_horizon"])
    windows = _windows(scenario)
    by_phase = {w.phase: w for w in windows}
    disease_scope = (21, 42) if scenario == "farm_three_cultivar" else (20, 43)
    irrigation_scope = (43, 63) if scenario == "farm_three_cultivar" else (20, 43)

    def guard(
        phase, key, scope, expected=True, operator=GuardOperator.EQ, world_key=None
    ):
        return DataGuardSpec(
            guard_id=f"author:{phase}:{key}",
            fact_key=key,
            source="knowledge",
            expected=expected,
            operator=operator,
            scope=scope,
            # An existential positive or a regional mean cannot establish the
            # same predicate on a smaller region merely by covering it.
            scope_match="exact"
            if key in {"disease:confirmed", "drought:surface_dry"}
            or (key == "crop:mature" and expected is False)
            else "covers",
            max_age=DAY,
            required_evidence=True,
            world_fact_key=world_key,
        )

    requirements = {
        "establishment": (guard("establishment", "planting:soil_suitable", (0, 63)),),
        "disease": tuple(
            guard("disease", key, disease_scope)
            for key in (
                "disease:confirmed",
                "weather:spray_window_open",
                "soil:trafficable",
            )
        ),
    }
    if scenario == "farm_wetjune_recheck":
        requirements["reproduction"] = tuple(
            guard("reproduction", key, disease_scope)
            for key in (
                "disease:confirmed",
                "weather:spray_window_open",
                "soil:trafficable",
            )
        )
    else:
        requirements["reproduction"] = (
            guard(
                "reproduction",
                "drought:surface_dry",
                irrigation_scope,
                world_key="drought:root_stressed",
            ),
            guard("reproduction", "crop:mature", irrigation_scope, False),
        )
    for phase, scope in (
        (("harvest_a", (0, 20)), ("harvest_b", (21, 42)), ("harvest_c", (43, 63)))
        if scenario == "farm_three_cultivar"
        else (("harvest", (0, 63)),)
    ):
        requirements[phase] = (
            guard(phase, "crop:mature", scope),
            guard(phase, "crop:grain_moisture", scope, 18.0, GuardOperator.LE),
            guard(phase, "weather:harvest_window_open", scope),
            guard(phase, "soil:trafficable", scope),
        )

    transitions = []
    for source in native.transitions:
        phase = _phase(source, scenario)
        high = source.action in HIGH_IMPACT
        guards = (
            tuple(
                g.model_copy(
                    update={
                        "guard_id": f"{g.guard_id}:{source.transition_id}",
                        # Planting and harvest evidence must cover the exact
                        # native batch. Requiring one zone-wide fact for every
                        # four-ridge pass makes fresh batch observations
                        # unusable and can deadlock a legal operation. Other
                        # decisions retain their prespecified management-region
                        # scope.
                        "scope": (
                            source.scope
                            if (
                                phase == "establishment"
                                and source.action == "TractorApp__plant_seeds"
                                and g.fact_key == "planting:soil_suitable"
                            )
                            or (
                                phase.startswith("harvest")
                                and source.action == "TractorApp__harvest"
                            )
                            else g.scope
                        ),
                    }
                )
                for g in requirements.get(phase, ())
            )
            if high
            else ()
        )
        required = source.action in REQUIRED_WRITES
        transitions.append(
            source.model_copy(
                update={
                    "phase": phase,
                    "module_id": phase,
                    "high_impact": high,
                    "guards": guards,
                    "required": required,
                    "scoring_class": ScoringClass.REQUIRED_PROGRESS
                    if required
                    else ScoringClass.OPTIONAL_SAFE,
                    "arguments": tuple(
                        a.model_copy(update={"tolerance": 0.0})
                        if isinstance(a.expected, float)
                        else a
                        for a in source.arguments
                    ),
                }
            )
        )
    counts = Counter(t.phase for t in transitions if t.required)
    transitions = tuple(
        t.model_copy(update={"weight": 1.0 / counts[t.phase]})
        if t.required
        else t.model_copy(update={"weight": 1.0})
        for t in transitions
    )
    modules = tuple(
        PetriModuleSpec(
            module_id=w.phase,
            label=w.phase.replace("_", " "),
            phase=w.phase,
            weight_budget=1.0,
        )
        for w in windows
    )
    policies = tuple(
        InformationPolicySpecV5(
            policy_id=f"{scenario}:{phase}",
            actor_id="operations",
            phases=(phase,),
            action_patterns=tuple(
                sorted(
                    {
                        t.action
                        for t in transitions
                        if t.phase == phase and t.high_impact
                    }
                )
            ),
            requirements=guards,
            deadline_world_time=min(by_phase[phase].end_world_time, scenario_horizon),
            rules=_rules(guards),
        )
        for phase, guards in requirements.items()
    )

    # Partition exogenous entry conditions. Both branches retain the same
    # eventual task; a closed window requires deferral/reobservation under the
    # policy. This is an availability branch, not a yield-causation model.
    treatments = tuple(
        t.transition_id for t in transitions if t.phase == "disease" and t.high_impact
    )
    branch = WorldBranchSpec(
        branch_id="disease_window_at_entry",
        commit_phase="disease",
        commitment_fact_keys=("weather:spray_window_open",),
        alternatives=(
            BranchAlternativeSpec(
                alternative_id="open",
                label="Spray window open at entry",
                transition_ids=treatments,
                required_transition_ids=treatments,
                guards=(
                    DataGuardSpec(
                        guard_id="author:entry:spray_open",
                        fact_key="weather:spray_window_open",
                        source="world",
                        expected=True,
                        branch_selector=True,
                    ),
                ),
            ),
            BranchAlternativeSpec(
                alternative_id="closed",
                label="Wait for a qualifying spray window",
                transition_ids=treatments,
                required_transition_ids=treatments,
                default=True,
            ),
        ),
    )
    facts = _facts()
    transitions = tuple(
        t.model_copy(update={"required": False}) if t.transition_id in treatments else t
        for t in transitions
    )
    choices = {
        **(
            {"reference_harvest_policy": reference_policy}
            if reference_harvest_calendar
            else {}
        ),
        **(
            {
                "reference_harvest_retries": [
                    "rain",
                    "immaturity",
                    "grain_moisture",
                    "soil_trafficability",
                ]
            }
            if reference_harvest_opening
            else {}
        ),
        "version": "author_domain_v1",
        "authorship": "author_defined",
        "phase_windows": [w.model_dump(mode="json") for w in windows],
        "information_policies": [p.model_dump(mode="json") for p in policies],
        "fact_definitions": [f.model_dump(mode="json") for f in facts],
        "high_impact_actions": sorted(HIGH_IMPACT),
        "required_writes": sorted(REQUIRED_WRITES),
        "numeric_acceptance": "exact native reference dose; distinct doses require a frozen sensitivity",
        "module_weight": "one per phase; equal required-write shares; empty phases unavailable",
        "unscored_helpers": "reads, clock steps, refills and configuration are not crop-work completion credit",
        "branch_meaning": "entry availability; eventual treatment obligations unchanged; no physical causal claim",
        "soil_proxy_assumption": "surface mean <0.20 is an imperfect proxy for >=50% root VWC below0.18; disagreement remains measurable",
        "native_acceptance": "request-bound accepted native receipt; full native resource/equipment/temperature checks remain active",
        "management_region_assumption": "Disease anywhere in the declared management region permits its prescribed patch treatment; this does not assert disease on every ridge. Evidence scope is fixed before choice; each application batch retains exact native scope acceptance.",
        "source_mechanics": [
            "are/simulation/apps/farm_world/tractor_app.py",
            "are/simulation/apps/farm_world/field_ops_app.py",
            "are/simulation/apps/farm_world/farm_world_app.py",
            "are/simulation/distributed/farm_adapter.py",
        ],
        "professor_approved": False,
        "empirical_validation_pending": True,
    }
    net = native.model_copy(
        update={
            "net_id": f"{scenario}:author_domain_v1",
            "transitions": transitions,
            "modules": modules,
            "exogenous_branches": (branch,),
            "expert_review_status": "author_defined",
            "metadata": {
                **native.metadata,
                "fact_definitions": choices["fact_definitions"],
                "information_policies": [],
                "paper_eligible": False,
                "authored_choices_digest": stable_digest(choices),
                **(
                    {
                        "reference_harvest_openings": {
                            w.phase: w.start_world_time
                            for w in windows
                            if w.phase == "harvest"
                        }
                    }
                    if reference_harvest_opening
                    else {}
                ),
                **(
                    {
                        "reference_harvest_deadlines": {
                            w.phase: min(w.end_world_time, scenario_horizon)
                            for w in windows
                            if w.phase.startswith("harvest")
                        },
                        "reference_harvest_deadline_rule": (
                            "min(authored_phase_end,native_scenario_horizon)"
                        ),
                    }
                    if reference_harvest_calendar
                    else {}
                ),
                **(
                    {"reference_harvest_policy": reference_policy}
                    if reference_harvest_calendar
                    else {}
                ),
                **(
                    {"reference_harvest_retries": choices["reference_harvest_retries"]}
                    if reference_harvest_opening
                    else {}
                ),
            },
        }
    )
    causal = tuple(
        CausalObligationGroupSpec(
            obligation_id=f"support:{t.transition_id}",
            label="Scoped fresh evidence supports this requested operation",
            module_id=t.module_id,
            target_transition_ids=(t.transition_id,),
            weight=t.weight,
            alternatives=(
                CausalPathSpec(
                    path_id=f"evidence:{t.transition_id}",
                    guard_ids=tuple(g.guard_id for g in t.guards),
                    required_actor_path=("field_intelligence", "operations"),
                ),
            ),
            prerequisites=tuple(
                CausalPrerequisiteSpec(
                    prerequisite_id=g.guard_id,
                    guard_id=g.guard_id,
                    fact_key=g.fact_key,
                    actor_id=t.actor_id,
                    scope=g.scope,
                    max_age=g.max_age,
                )
                for g in t.guards
            ),
        )
        for t in transitions
        if t.high_impact
    )
    negatives = tuple(
        NegativeActionObligationSpec(
            obligation_id=f"unsafe_spray:{phase}",
            classification="prohibited",
            actor_id="operations",
            action="TractorApp__apply_fungicide",
            phases=(phase,),
            module_id=phase,
            weight=1.0,
            world_guards=(
                DataGuardSpec(
                    guard_id=f"negative:{phase}:rain",
                    fact_key="weather:spray_window_open",
                    source="world",
                    expected=False,
                ),
            ),
        )
        for phase in requirements
        if phase in {"disease", "reproduction"}
    )
    target = "selector:reproduction:field_intelligence:operations:1"
    second = "selector:reproduction:field_intelligence:operations:2"
    third = "selector:reproduction:field_intelligence:operations:3"
    deadline = by_phase["reproduction"].end_world_time
    faults = tuple(
        CommunicationFaultTreatmentSpec(
            fault_id=f"author:{mode}",
            mode=mode,
            target_ids=()
            if mode == "reliable"
            else (
                (target, second, third)
                if mode == "mixed"
                else (target, second)
                if mode == "reorder"
                else (target,)
            ),
            target_fact_keys=tuple(g.fact_key for g in requirements["reproduction"]),
            # Delay from the actual send. Compare arrival to the selected
            # evidence's real observation-time expiry, never an invented TTL.
            delivery_delay_seconds=DAY / 2
            if mode == "delay_within_validity"
            else 2 * DAY
            if mode == "delay_past_validity"
            else None,
            delivery_world_time=(
                deadline + DAY if mode == "delay_past_deadline" else None
            ),
            deadline_id=f"{scenario}:reproduction"
            if mode == "delay_past_deadline"
            else None,
        )
        for mode in (
            "reliable",
            "delay_within_validity",
            "delay_past_validity",
            "delay_past_deadline",
            "drop",
            "duplicate",
            "reorder",
            "mixed",
        )
    )
    return FarmProcessSpecV5(
        process_id=f"{scenario}:author_domain_v1",
        scenario_id=scenario,
        occurrence_net=net,
        information_policies=policies,
        phase_windows=windows,
        acceptance=tuple(
            TransitionAcceptanceSpec(
                transition_id=t.transition_id,
                arguments=t.arguments,
                scope_iou_threshold=1.0,
                accepted_statuses=("ok", "dropped")
                if t.kind.value == "send"
                else ("ok",),
            )
            for t in transitions
            if t.actor_id != "world"
        ),
        causal_obligations=causal,
        negative_action_obligations=negatives,
        fault_treatments=faults,
        expert_review_status="author_defined",
        annotation_status="frozen",
        review_digest=stable_digest(choices),
        metadata={
            "scenario_horizon": scenario_horizon,
            **(
                {
                    "native_scenario": {
                        "scenario_revision": scenario_revision,
                        "calibration_candidate": calibration_candidate,
                    }
                }
                if scenario_revision or calibration_candidate
                else {}
            ),
            "authored_choices": choices,
            "engineering_defaults": False,
            "release_requires_genuine_professor_approval": True,
            "release_requires_scientific_gates": True,
        },
    )
