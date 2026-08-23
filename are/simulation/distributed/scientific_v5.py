"""Frozen scientific contracts for Farm D-CORE v5.

The authoritative object is a hierarchical, data-aware farm workflow that has
already been expanded into a labeled 1-safe occurrence net.  These contracts
separate normative obligations from execution happens-before and from the
actor's information state.  They intentionally do not claim that a trace is a
counterfactual causal model.
"""

from __future__ import annotations

from datetime import datetime
from itertools import product
from typing import Any, Literal

from pydantic import Field, model_validator

from are.simulation.distributed.models import FrozenModel, stable_digest
from are.simulation.distributed.petri import (
    ArgumentConstraint,
    DataGuardSpec,
    FactDefinitionSpec,
    GuardOperator,
    InformationPolicySpec,
    PetriModuleSpec,
    PetriNetSpec,
    PolicyResponse,
    WorldBranchSpec,
    transition_dependencies,
)

FactVerdict = Literal["true", "false", "unknown"]
FactVerdictPattern = Literal["true", "false", "unknown", "any"]


def _guard_accepts(value: Any, guard: DataGuardSpec) -> bool:
    try:
        return bool(
            {
                GuardOperator.EQ: lambda: value == guard.expected,
                GuardOperator.NE: lambda: value != guard.expected,
                GuardOperator.GE: lambda: value >= guard.expected,
                GuardOperator.GT: lambda: value > guard.expected,
                GuardOperator.LE: lambda: value <= guard.expected,
                GuardOperator.LT: lambda: value < guard.expected,
                GuardOperator.IN: lambda: value in guard.expected,
            }[guard.operator]()
        )
    except (TypeError, ValueError):
        return False


def _candidate_values(guards: tuple[DataGuardSpec, ...]) -> tuple[Any, ...]:
    values: list[Any] = [False, True, "__farm_dcore_other__"]
    numeric: list[float] = []
    for guard in guards:
        expected = guard.expected
        if guard.operator == GuardOperator.IN and isinstance(
            expected, (list, tuple, set)
        ):
            values.extend(expected)
            numeric.extend(
                float(item)
                for item in expected
                if isinstance(item, (int, float)) and not isinstance(item, bool)
            )
        else:
            values.append(expected)
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                numeric.append(float(expected))
    points = sorted(set(numeric))
    for point in points:
        epsilon = max(1e-9, abs(point) * 1e-9)
        values.extend((point - epsilon, point, point + epsilon))
    for left, right in zip(points, points[1:]):
        values.append((left + right) / 2)
    if points:
        span = max(1.0, max(points) - min(points))
        values.extend((min(points) - span, max(points) + span))
    unique = []
    for value in values:
        if not any(
            value == existing and type(value) is type(existing) for existing in unique
        ):
            unique.append(value)
    return tuple(unique)


def _guard_conjunctions_overlap(
    left: tuple[DataGuardSpec, ...], right: tuple[DataGuardSpec, ...]
) -> bool:
    by_fact: dict[str, list[DataGuardSpec]] = {}
    for guard in (*left, *right):
        by_fact.setdefault(guard.fact_key, []).append(guard)
    return all(
        any(
            all(_guard_accepts(value, guard) for guard in guards)
            for value in _candidate_values(tuple(guards))
        )
        for guards in by_fact.values()
    )


class FactVerdictPatternSpec(FrozenModel):
    fact_key: str
    verdict: FactVerdictPattern


class FactVectorPolicyRuleSpec(FrozenModel):
    """One non-overlapping cell (or rectangular region) of a policy table."""

    rule_id: str
    fact_pattern: tuple[FactVerdictPatternSpec, ...]
    deadline_state: Literal["open", "closed", "any"] = "any"
    channel_state: Literal["open", "closed", "any"] = "any"
    permitted_responses: tuple[PolicyResponse, ...]
    required_responses: tuple[PolicyResponse, ...] = ()

    @model_validator(mode="after")
    def validate_rule(self) -> "FactVectorPolicyRuleSpec":
        keys = [item.fact_key for item in self.fact_pattern]
        if len(keys) != len(set(keys)):
            raise ValueError("fact-vector policy rule repeats a fact key")
        if not self.permitted_responses:
            raise ValueError("fact-vector rule needs a permitted response")
        if not set(self.required_responses) <= set(self.permitted_responses):
            raise ValueError("required responses must be a subset of permitted ones")
        return self


class InformationPolicySpecV5(FrozenModel):
    policy_id: str
    actor_id: str
    action_patterns: tuple[str, ...]
    phases: tuple[str, ...]
    requirements: tuple[DataGuardSpec, ...]
    deadline_world_time: float | None = None
    decision_weight: float = Field(default=1.0, gt=0)
    rules: tuple[FactVectorPolicyRuleSpec, ...]

    @model_validator(mode="after")
    def validate_complete_disjoint_table(self) -> "InformationPolicySpecV5":
        if not self.action_patterns or not self.phases:
            raise ValueError("v5 policy needs an action pattern and at least one phase")
        if len(self.action_patterns) != len(set(self.action_patterns)):
            raise ValueError("v5 policy action patterns must be unique")
        if len(self.phases) != len(set(self.phases)):
            raise ValueError("v5 policy phases must be unique")
        if any(item.source == "world" for item in self.requirements):
            raise ValueError(
                "information policies may not read authoritative world truth"
            )
        keys = tuple(item.fact_key for item in self.requirements)
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("v5 policy requirements need unique fact keys")
        expected = set(keys)
        for rule in self.rules:
            if {item.fact_key for item in rule.fact_pattern} != expected:
                raise ValueError(
                    f"policy {self.policy_id!r} rule {rule.rule_id!r} must cover "
                    "every requirement exactly once"
                )
        for values in product(("true", "false", "unknown"), repeat=len(keys)):
            state = dict(zip(keys, values, strict=True))
            for deadline, channel in product(("open", "closed"), repeat=2):
                matched = [
                    rule
                    for rule in self.rules
                    if _rule_matches(rule, state, deadline, channel)
                ]
                if len(matched) != 1:
                    raise ValueError(
                        f"policy {self.policy_id!r} must be exhaustive and disjoint; "
                        f"state={state}, deadline={deadline}, channel={channel} "
                        f"matched {len(matched)} rules"
                    )
        return self


def _rule_matches(
    rule: FactVectorPolicyRuleSpec,
    verdicts: dict[str, str],
    deadline_state: str,
    channel_state: str,
) -> bool:
    return (
        rule.deadline_state in {deadline_state, "any"}
        and rule.channel_state in {channel_state, "any"}
        and all(
            item.verdict in {verdicts[item.fact_key], "any"}
            for item in rule.fact_pattern
        )
    )


def select_policy_rule(
    policy: InformationPolicySpecV5,
    verdicts: dict[str, str],
    deadline_state: str,
    channel_state: str,
) -> FactVectorPolicyRuleSpec:
    matched = [
        rule
        for rule in policy.rules
        if _rule_matches(rule, verdicts, deadline_state, channel_state)
    ]
    if len(matched) != 1:
        raise ValueError(
            f"policy {policy.policy_id!r} state does not select exactly one rule"
        )
    return matched[0]


class TransitionAcceptanceSpec(FrozenModel):
    """Predeclared categorical acceptance predicates for one occurrence."""

    transition_id: str
    arguments: tuple[ArgumentConstraint, ...] = ()
    scope_iou_threshold: float = Field(default=1.0, ge=0, le=1)
    window_start: float | None = None
    window_end: float | None = None
    accepted_statuses: tuple[str, ...] = ("ok",)

    @model_validator(mode="after")
    def validate_window(self) -> "TransitionAcceptanceSpec":
        if self.window_start is not None and self.window_end is not None:
            if self.window_end < self.window_start:
                raise ValueError("acceptance window end precedes its start")
        if not self.accepted_statuses:
            raise ValueError("acceptance requires at least one successful status")
        return self


class PhaseWindowSpec(FrozenModel):
    phase: str
    start_world_time: float
    end_world_time: float

    @model_validator(mode="after")
    def validate_window(self) -> "PhaseWindowSpec":
        if self.end_world_time <= self.start_world_time:
            raise ValueError("phase window end must be after its start")
        return self


class NegativeActionObligationSpec(FrozenModel):
    """Evaluator-side classification of an unmatched observed action."""

    obligation_id: str
    classification: Literal["prohibited", "unnecessary", "benign"]
    actor_id: str
    action: str
    phases: tuple[str, ...] = ()
    world_guards: tuple[DataGuardSpec, ...] = ()
    module_id: str
    weight: float = Field(gt=0)


class CommunicationFaultTreatmentSpec(FrozenModel):
    """A predeclared treatment tied to stable semantic/message identifiers."""

    fault_id: str
    mode: Literal[
        "reliable",
        "delay_within_validity",
        "delay_past_validity",
        "delay_past_deadline",
        "drop",
        "duplicate",
        "reorder",
        "mixed",
    ]
    target_ids: tuple[str, ...]
    target_fact_keys: tuple[str, ...] = ()
    valid_until_world_time: float | None = None
    delivery_world_time: float | None = None
    deadline_id: str | None = None

    @model_validator(mode="after")
    def validate_treatment(self) -> "CommunicationFaultTreatmentSpec":
        if self.mode != "reliable" and not self.target_ids:
            raise ValueError("fault treatment requires stable targets")
        if self.mode == "reorder" and len(self.target_ids) != 2:
            raise ValueError("reorder treatment requires ordered old/new targets")
        if self.mode in {"delay_within_validity", "delay_past_validity"} and (
            self.valid_until_world_time is None
        ):
            raise ValueError("validity-delay treatment requires a reviewed expiry")
        if self.mode.startswith("delay_") and self.delivery_world_time is None:
            raise ValueError("delay treatment requires a reviewed delivery time")
        if (
            self.mode == "delay_within_validity"
            and self.delivery_world_time is not None
            and self.valid_until_world_time is not None
            and self.delivery_world_time > self.valid_until_world_time
        ):
            raise ValueError("within-validity delivery occurs after expiry")
        if (
            self.mode == "delay_past_validity"
            and self.delivery_world_time is not None
            and self.valid_until_world_time is not None
            and self.delivery_world_time <= self.valid_until_world_time
        ):
            raise ValueError("past-validity delivery does not cross expiry")
        if self.mode == "delay_past_deadline" and not self.deadline_id:
            raise ValueError("past-deadline treatment requires a reviewed deadline")
        return self


class CausalPathSpec(FrozenModel):
    """One acceptable realization of an end-to-end semantic obligation."""

    path_id: str
    transition_edges: tuple[tuple[str, str], ...] = ()
    guard_ids: tuple[str, ...] = ()
    required_actor_path: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_path(self) -> "CausalPathSpec":
        if not self.transition_edges and not self.guard_ids:
            raise ValueError("causal path needs a transition edge or fact guard")
        if self.required_actor_path and len(self.required_actor_path) < 2:
            raise ValueError("a required actor path needs at least two actors")
        return self


class CausalObligationGroupSpec(FrozenModel):
    """One semantic obligation, invariant to communication-hop refinement."""

    obligation_id: str
    label: str
    module_id: str
    target_transition_ids: tuple[str, ...]
    alternatives: tuple[CausalPathSpec, ...]
    fact_key: str | None = None
    weight: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def validate_group(self) -> "CausalObligationGroupSpec":
        if not self.target_transition_ids or not self.alternatives:
            raise ValueError("causal obligation needs targets and path alternatives")
        return self


class FarmProcessSpecV5(FrozenModel):
    schema_version: Literal["farm_process_spec_v5"] = "farm_process_spec_v5"
    process_id: str
    scenario_id: str
    occurrence_net: PetriNetSpec
    information_policies: tuple[InformationPolicySpecV5, ...]
    acceptance: tuple[TransitionAcceptanceSpec, ...]
    phase_windows: tuple[PhaseWindowSpec, ...] = ()
    causal_obligations: tuple[CausalObligationGroupSpec, ...]
    negative_action_obligations: tuple[NegativeActionObligationSpec, ...] = ()
    fault_treatments: tuple[CommunicationFaultTreatmentSpec, ...] = ()
    expert_review_status: Literal[
        "unreviewed", "two_expert_draft", "adjudicated", "confirmed"
    ] = "unreviewed"
    annotation_status: Literal["draft", "frozen"] = "draft"
    review_digest: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_process(self) -> "FarmProcessSpecV5":
        net = self.occurrence_net
        if (
            net.scenario_id != self.scenario_id
            and net.metadata.get("public_scenario_id") != self.scenario_id
        ):
            raise ValueError("process and occurrence-net scenario IDs disagree")
        transition_ids = {item.transition_id for item in net.transitions}
        module_ids = {item.module_id for item in net.modules}
        acceptance_ids = [item.transition_id for item in self.acceptance]
        if len(acceptance_ids) != len(set(acceptance_ids)):
            raise ValueError("acceptance transition IDs must be unique")
        unknown_acceptance = set(acceptance_ids) - transition_ids
        if unknown_acceptance:
            raise ValueError(
                f"acceptance references unknown transitions: {unknown_acceptance}"
            )
        required_agent = {
            item.transition_id
            for item in net.transitions
            if item.required and item.actor_id != "world"
        }
        if not required_agent <= set(acceptance_ids):
            raise ValueError(
                "every required agent transition needs acceptance predicates"
            )
        policy_ids = [item.policy_id for item in self.information_policies]
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("v5 policy IDs must be unique")
        policy_cells = [
            (item.actor_id, phase)
            for item in self.information_policies
            for phase in item.phases
        ]
        if len(policy_cells) != len(set(policy_cells)):
            raise ValueError(
                "v5 requires one independently discoverable policy per actor/phase"
            )
        obligation_ids = [item.obligation_id for item in self.causal_obligations]
        if len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("semantic causal-obligation IDs must be unique")
        permitted_edges = transition_dependencies(net)
        guard_ids = {
            guard.guard_id
            for transition in net.transitions
            for guard in transition.guards
        }
        for obligation in self.causal_obligations:
            if obligation.module_id not in module_ids:
                raise ValueError("causal obligation references an unknown module")
            if not set(obligation.target_transition_ids) <= transition_ids:
                raise ValueError("causal obligation references an unknown target")
            for path in obligation.alternatives:
                if not set(path.transition_edges) <= permitted_edges:
                    raise ValueError(
                        "causal obligation uses a non-normative Petri edge"
                    )
                if not set(path.guard_ids) <= guard_ids:
                    raise ValueError("causal obligation references an unknown guard")
                if not set(path.required_actor_path) <= set(net.actors):
                    raise ValueError("causal path references an unknown actor")
                if path.required_actor_path:
                    target_actors = {
                        next(
                            item.actor_id
                            for item in net.transitions
                            if item.transition_id == target
                        )
                        for target in obligation.target_transition_ids
                    }
                    if len(target_actors) != 1 or path.required_actor_path[-1] not in (
                        target_actors
                    ):
                        raise ValueError(
                            "required actor path must terminate at the target owner"
                        )
                    if len(path.required_actor_path) != len(
                        set(path.required_actor_path)
                    ):
                        raise ValueError(
                            "required actor path may not cycle through a role"
                        )
        for item in self.negative_action_obligations:
            if item.module_id not in module_ids:
                raise ValueError("negative action obligation references unknown module")
            if item.actor_id not in net.actors:
                raise ValueError("negative action obligation references unknown actor")
            if any(guard.source != "world" for guard in item.world_guards):
                raise ValueError("negative action classifiers may use world truth only")
        if self.annotation_status == "frozen":
            if self.expert_review_status != "confirmed" or not self.review_digest:
                raise ValueError("a frozen v5 process requires confirmed expert review")
            if self.metadata.get("engineering_defaults"):
                raise ValueError(
                    "paper specifications cannot contain engineering defaults"
                )
            phase_ids = [item.phase for item in self.phase_windows]
            if len(phase_ids) != len(set(phase_ids)):
                raise ValueError("frozen phase windows must have unique phase IDs")
            required_phases = (
                {
                    phase
                    for policy in self.information_policies
                    for phase in policy.phases
                }
                | {
                    branch.commit_phase
                    for branch in self.occurrence_net.exogenous_branches
                    if isinstance(branch, WorldBranchSpec)
                }
                | {module.phase for module in self.occurrence_net.modules}
                | {
                    phase
                    for obligation in self.negative_action_obligations
                    for phase in obligation.phases
                }
            )
            if not required_phases <= set(phase_ids):
                raise ValueError(
                    "every frozen policy phase requires a world-time window"
                )
            high_impact_cells = {
                (item.actor_id, item.phase)
                for item in net.transitions
                if item.high_impact and item.actor_id != "world"
            }
            if not high_impact_cells <= set(policy_cells):
                raise ValueError("every high-impact actor/phase requires a v5 policy")
            policy_by_cell = {
                (policy.actor_id, phase): policy
                for policy in self.information_policies
                for phase in policy.phases
            }
            for transition in net.transitions:
                if transition.high_impact and transition.actor_id != "world":
                    policy = policy_by_cell[(transition.actor_id, transition.phase)]
                    if transition.action not in policy.action_patterns:
                        raise ValueError(
                            "high-impact transition is not covered by its phase policy"
                        )
            ordered_windows = sorted(
                self.phase_windows, key=lambda item: item.start_world_time
            )
            if any(
                left.end_world_time > right.start_world_time
                for left, right in zip(ordered_windows, ordered_windows[1:])
            ):
                raise ValueError("frozen phase windows may not overlap")
            if any(
                abs(left.end_world_time - right.start_world_time) > 1e-9
                for left, right in zip(ordered_windows, ordered_windows[1:])
            ):
                raise ValueError("frozen phase windows must cover a contiguous horizon")
            required_faults = {
                "reliable",
                "delay_within_validity",
                "delay_past_validity",
                "delay_past_deadline",
                "drop",
                "duplicate",
                "reorder",
                "mixed",
            }
            if {item.mode for item in self.fault_treatments} != required_faults:
                raise ValueError("frozen v5 process must predeclare every paper fault")
            policy_by_id = {item.policy_id: item for item in self.information_policies}
            for treatment in self.fault_treatments:
                if treatment.mode != "delay_past_deadline":
                    continue
                policy = policy_by_id.get(str(treatment.deadline_id))
                if policy is None or policy.deadline_world_time is None:
                    raise ValueError(
                        "past-deadline treatment must reference a policy with a "
                        "frozen world-time deadline"
                    )
                if (
                    treatment.delivery_world_time is None
                    or treatment.delivery_world_time <= policy.deadline_world_time
                ):
                    raise ValueError(
                        "past-deadline treatment delivery must cross its policy deadline"
                    )
            if not self.occurrence_net.exogenous_branches:
                raise ValueError(
                    "frozen v5 process requires authoritative world branches"
                )
            facts = tuple(
                FactDefinitionSpec.model_validate(item)
                for item in self.occurrence_net.metadata.get("fact_definitions", ())
            )
            if not facts or any(item.paper_status != "frozen" for item in facts):
                raise ValueError(
                    "frozen v5 process requires a fully frozen fact registry"
                )
            fact_keys = {item.fact_key for item in facts}
            high_impact = [
                item
                for item in self.occurrence_net.transitions
                if item.high_impact and item.actor_id != "world"
            ]
            if any(not item.guards for item in high_impact):
                raise ValueError(
                    "every frozen high-impact transition requires normative guards"
                )
            transition_guard_fact_keys = {
                guard.fact_key
                for transition in self.occurrence_net.transitions
                for guard in transition.guards
            }
            if not transition_guard_fact_keys <= fact_keys:
                raise ValueError("transition guard uses an undefined fact")
            policy_fact_keys = {
                guard.fact_key
                for policy in self.information_policies
                for guard in policy.requirements
            }
            if not policy_fact_keys <= fact_keys:
                raise ValueError("information policy uses an undefined fact")
            observable = {item.fact_key for item in facts if item.observation_actions}
            if not policy_fact_keys <= observable:
                raise ValueError(
                    "every information-policy fact needs a reviewed observation path"
                )
            if any(not item.truth_source.strip() for item in facts):
                raise ValueError(
                    "frozen facts require a named authoritative truth source"
                )
            for treatment in self.fault_treatments:
                if not set(treatment.target_fact_keys) <= fact_keys:
                    raise ValueError("fault treatment targets an undefined fact")
            for branch in self.occurrence_net.exogenous_branches:
                if not isinstance(branch, WorldBranchSpec):
                    raise ValueError(
                        "frozen v5 branches require commitment phase and fact keys"
                    )
                if not set(branch.commitment_fact_keys) <= fact_keys:
                    raise ValueError("world branch uses an undefined fact")
                if not branch.commitment_fact_keys:
                    raise ValueError("world branch needs a commitment fact")
                if sum(item.default for item in branch.alternatives) != 1:
                    raise ValueError(
                        "frozen world branch requires one explicit complement/default"
                    )
                guard_signatures = [
                    stable_digest(
                        [guard.model_dump(mode="json") for guard in item.guards]
                    )
                    for item in branch.alternatives
                    if not item.default
                ]
                if len(guard_signatures) != len(set(guard_signatures)):
                    raise ValueError("world branch repeats an exogenous guard cell")
                guarded = [item for item in branch.alternatives if not item.default]
                if any(not item.guards for item in guarded):
                    raise ValueError(
                        "every non-default world alternative needs authoritative guards"
                    )
                if any(
                    guard.source != "world" or not guard.branch_selector
                    for item in guarded
                    for guard in item.guards
                ):
                    raise ValueError(
                        "world alternatives require authoritative branch-selector guards"
                    )
                if any(
                    not {guard.fact_key for guard in item.guards}
                    <= set(branch.commitment_fact_keys)
                    for item in guarded
                ):
                    raise ValueError(
                        "world alternative guard is outside its commitment facts"
                    )
                for index, left in enumerate(guarded):
                    for right in guarded[index + 1 :]:
                        if _guard_conjunctions_overlap(left.guards, right.guards):
                            raise ValueError(
                                "world branch has overlapping authoritative guard cells"
                            )
            if not self.negative_action_obligations:
                raise ValueError(
                    "frozen v5 process requires reviewed unmatched-action definitions"
                )
            transition_by_id = {
                item.transition_id: item for item in self.occurrence_net.transitions
            }
            always_required = {
                item.transition_id
                for item in self.occurrence_net.transitions
                if item.required
                and item.actor_id != "world"
                and item.scoring_class
                not in {
                    "benign_loop",
                    "optional_safe",
                    "harmful",
                }
            }
            branch_choices = [
                branch.alternatives for branch in self.occurrence_net.exogenous_branches
            ]
            for choices in product(*branch_choices):
                required = set(always_required)
                for choice in choices:
                    required.update(choice.required_transition_ids)
                for module in self.occurrence_net.modules:
                    weights = [
                        transition_by_id[item].weight
                        for item in required
                        if transition_by_id[item].module_id == module.module_id
                    ]
                    if weights and abs(sum(weights) - module.weight_budget) > 1e-9:
                        raise ValueError(
                            f"module {module.module_id!r} transition expansion does "
                            "not preserve its frozen weight budget"
                        )
        return self

    @property
    def digest(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class ScientificGateManifestV5(FrozenModel):
    """Digest-bound evidence that a reviewed process is safe to experiment on."""

    schema_version: Literal["farm_dcore_scientific_gates_v5"] = (
        "farm_dcore_scientific_gates_v5"
    )
    scenario_id: str
    confirmed_process_digest: str | None = None
    confirmed_team_digest: str | None = None
    status: Literal["incomplete", "offline_complete", "complete"] = "incomplete"
    offline_semantic_gates: bool = False
    oracle_yield_equivalence: bool = False
    prompt_leakage_check: bool = False
    saved_trace_replay: bool = False
    blocked_write_nonmutation: bool = False
    allowed_write_exactly_once: bool = False
    mock_matrix_complete: bool = False
    bounded_real_llm_smoke: bool = False
    code_commit: str | None = None
    release_tag: str | None = None
    completed_at_utc: str | None = None
    test_report_digest: str | None = None
    environment_lock_digest: str | None = None
    analysis_protocol_digest: str | None = None

    @model_validator(mode="after")
    def validate_complete_gate(self) -> "ScientificGateManifestV5":
        if self.status in {"offline_complete", "complete"}:
            required = (
                self.confirmed_process_digest,
                self.confirmed_team_digest,
                self.code_commit,
                self.completed_at_utc,
                self.test_report_digest,
                self.environment_lock_digest,
                self.analysis_protocol_digest,
            )
            if not all(required):
                raise ValueError("complete scientific gate lacks release evidence")
            digests = (
                self.confirmed_process_digest,
                self.confirmed_team_digest,
                self.test_report_digest,
                self.environment_lock_digest,
                self.analysis_protocol_digest,
            )
            if any(
                len(str(item)) != 64
                or any(character not in "0123456789abcdef" for character in str(item))
                for item in digests
            ):
                raise ValueError(
                    "complete scientific gate has a malformed SHA-256 digest"
                )
            if len(str(self.code_commit)) not in {40, 64} or any(
                character not in "0123456789abcdef"
                for character in str(self.code_commit)
            ):
                raise ValueError("scientific gate has a malformed commit hash")
            completed = datetime.fromisoformat(
                str(self.completed_at_utc).replace("Z", "+00:00")
            )
            if completed.tzinfo is None:
                raise ValueError(
                    "scientific gate completion time must include UTC offset"
                )
            if (
                completed.utcoffset() is None
                or completed.utcoffset().total_seconds() != 0
            ):
                raise ValueError("scientific gate completion time must be UTC")
            checks = (
                self.offline_semantic_gates,
                self.oracle_yield_equivalence,
                self.prompt_leakage_check,
                self.saved_trace_replay,
                self.blocked_write_nonmutation,
                self.allowed_write_exactly_once,
                self.mock_matrix_complete,
            )
            if not all(checks):
                raise ValueError("complete scientific gate has a failed offline check")
        if self.status == "complete":
            if not self.release_tag:
                raise ValueError("complete scientific gate lacks a release tag")
            if (
                self.scenario_id == "farm_wetjune_recheck"
                and not self.bounded_real_llm_smoke
            ):
                raise ValueError("Wet-June release gate requires the bounded LLM smoke")
        return self


def _expand_aggregate_policy(policy: InformationPolicySpec) -> InformationPolicySpecV5:
    """Compatibility conversion only; it is explicitly not paper-eligible."""

    keys = tuple(item.fact_key for item in policy.requirements)
    rules: list[FactVectorPolicyRuleSpec] = []
    for values in product(("true", "false", "unknown"), repeat=len(keys)):
        verdicts = dict(zip(keys, values, strict=True))
        aggregate = (
            "false"
            if "false" in values
            else "unknown"
            if "unknown" in values
            else "true"
        )
        for deadline, channel in product(("open", "closed"), repeat=2):
            candidates = [
                item
                for item in policy.rules
                if item.requirement_state == aggregate
                and item.deadline_state == deadline
                and item.channel_state in {channel, "any"}
            ]
            if len(candidates) != 1:
                raise ValueError("v4 policy cannot be expanded unambiguously")
            old = candidates[0]
            rules.append(
                FactVectorPolicyRuleSpec(
                    rule_id=(
                        f"{policy.policy_id}:"
                        + ",".join(values)
                        + f":{deadline}:{channel}"
                    ),
                    fact_pattern=tuple(
                        FactVerdictPatternSpec(fact_key=key, verdict=verdicts[key])
                        for key in keys
                    ),
                    deadline_state=deadline,
                    channel_state=channel,
                    permitted_responses=old.permitted_responses,
                    required_responses=old.required_responses,
                )
            )
    return InformationPolicySpecV5(
        policy_id=policy.policy_id,
        actor_id=policy.actor_id,
        action_patterns=policy.action_patterns,
        phases=policy.phases,
        requirements=policy.requirements,
        deadline_world_time=policy.deadline_world_time,
        decision_weight=1.0,
        rules=tuple(rules),
    )


def engineering_process_from_v4(net: PetriNetSpec) -> FarmProcessSpecV5:
    """Produce an auditable v5 engineering fixture from a v4 net.

    This converter exists for tests and migration.  Its output is deliberately
    marked unreviewed and can never pass paper mode.
    """

    # The first Wet-June hierarchical specification already carries module
    # budgets.  Older L3 occurrence nets predate that schema and expose phases
    # only.  Migration must make those phases explicit modules; otherwise
    # obligations would reference identifiers absent from the v5 process.  The
    # synthesized budgets are engineering scaffolding, never paper defaults.
    if not net.modules:
        phases = tuple(dict.fromkeys(item.phase for item in net.transitions))
        transitions = tuple(
            item.model_copy(update={"module_id": item.phase})
            for item in net.transitions
        )
        modules = tuple(
            PetriModuleSpec(
                module_id=phase,
                label=phase.replace("_", " ").title(),
                phase=phase,
                weight_budget=sum(
                    item.weight
                    for item in transitions
                    if item.phase == phase
                    and item.required
                    and item.actor_id != "world"
                    and item.scoring_class
                    not in {"benign_loop", "optional_safe", "harmful"}
                )
                or 1.0,
            )
            for phase in phases
        )
        net = net.model_copy(update={"transitions": transitions, "modules": modules})

    policies = tuple(
        _expand_aggregate_policy(InformationPolicySpec.model_validate(raw))
        for raw in net.metadata.get("information_policies", ())
    )
    acceptance = tuple(
        TransitionAcceptanceSpec(
            transition_id=item.transition_id,
            arguments=item.arguments,
            scope_iou_threshold=item.scope_iou_threshold,
            window_start=item.window_start,
            window_end=item.window_end,
            accepted_statuses=(
                ("ok", "dropped") if item.kind.value == "send" else ("ok",)
            ),
        )
        for item in net.transitions
        if item.actor_id != "world"
    )
    transitions = {item.transition_id: item for item in net.transitions}
    obligations: list[CausalObligationGroupSpec] = []
    for source, target in sorted(transition_dependencies(net)):
        if (
            transitions[source].actor_id == "world"
            or transitions[target].actor_id == "world"
        ):
            continue
        obligations.append(
            CausalObligationGroupSpec(
                obligation_id=f"dependency:{source}->{target}",
                label=f"{source} happens-before {target}",
                module_id=transitions[target].module_id or transitions[target].phase,
                target_transition_ids=(target,),
                alternatives=(
                    CausalPathSpec(
                        path_id=f"path:{source}->{target}",
                        transition_edges=((source, target),),
                    ),
                ),
                weight=transitions[target].weight,
            )
        )
    for transition in net.transitions:
        for guard in transition.guards:
            obligations.append(
                CausalObligationGroupSpec(
                    obligation_id=f"guard:{transition.transition_id}:{guard.guard_id}",
                    label=f"{guard.fact_key} supports {transition.transition_id}",
                    module_id=transition.module_id or transition.phase,
                    target_transition_ids=(transition.transition_id,),
                    alternatives=(
                        CausalPathSpec(
                            path_id=f"path:{transition.transition_id}:{guard.guard_id}",
                            guard_ids=(guard.guard_id,),
                        ),
                    ),
                    fact_key=guard.fact_key,
                    weight=transition.weight,
                )
            )
    return FarmProcessSpecV5(
        process_id=f"{net.net_id}:engineering-v5",
        scenario_id=str(net.metadata.get("public_scenario_id", net.scenario_id)),
        occurrence_net=net,
        information_policies=policies,
        acceptance=acceptance,
        causal_obligations=tuple(obligations),
        metadata={
            "engineering_defaults": True,
            "source_v4_digest": stable_digest(net.model_dump(mode="json")),
            "formalism": "hierarchical_data_aware_workflow_to_1safe_occurrence_net",
        },
    )


def load_process_spec(path: str) -> FarmProcessSpecV5:
    return FarmProcessSpecV5.model_validate_json(open(path, encoding="utf-8").read())


__all__ = [
    "CausalObligationGroupSpec",
    "CausalPathSpec",
    "CommunicationFaultTreatmentSpec",
    "FactVectorPolicyRuleSpec",
    "FactVerdictPatternSpec",
    "FarmProcessSpecV5",
    "InformationPolicySpecV5",
    "NegativeActionObligationSpec",
    "PhaseWindowSpec",
    "TransitionAcceptanceSpec",
    "ScientificGateManifestV5",
    "engineering_process_from_v4",
    "select_policy_rule",
]
