"""D-CORE v4: module-normalized and information-policy-aware evaluation."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, median, pstdev
from typing import Any

from are.simulation.distributed.evaluator_v2 import (
    HARMFUL_EXTRA_LAMBDA,
    transition_similarity,
)
from are.simulation.distributed.evaluator_v3 import evaluate_farm_dcore as _v3
from are.simulation.distributed.models import (
    AgentTeamSpec,
    DistributedTrace,
    IntentKind,
    stable_digest,
)
from are.simulation.distributed.petri import (
    InformationPolicySpec,
    PetriNetSpec,
    ScoringClass,
    WorldBranchSpec,
)
from are.simulation.distributed.teams import shortest_path

METRIC_VERSION = "dcore_eval_v4"


def _fact_handoff_depths(trace: DistributedTrace) -> list[int]:
    by_id = {item.version_id: item for item in trace.fact_versions}
    memo: dict[str, int] = {}

    def depth(version_id: str, visiting: set[str]) -> int:
        if version_id in memo:
            return memo[version_id]
        if version_id in visiting:
            raise ValueError("fact-version provenance contains a cycle")
        item = by_id[version_id]
        origin = item.origin_version_id
        value = (
            1 + depth(origin, {*visiting, version_id})
            if origin is not None and origin in by_id
            else 0
        )
        memo[version_id] = value
        return value

    return [depth(version_id, set()) for version_id in by_id]


def _validate_branch_commitments(net: PetriNetSpec, trace: DistributedTrace) -> None:
    records = {item.branch_id: item for item in trace.world_branch_commitments}
    configured = trace.configuration.get("committed_branches", {})
    facts = {item.version_id: item for item in trace.fact_versions}
    for branch in net.exogenous_branches:
        if not isinstance(branch, WorldBranchSpec):
            raise ValueError("D-CORE v4 branches require WorldBranchSpec semantics")
        record = records.get(branch.branch_id)
        if record is None:
            raise ValueError(f"missing world commitment for {branch.branch_id!r}")
        if configured.get(branch.branch_id) != record.alternative_id:
            raise ValueError("trace configuration and branch commitment disagree")
        if record.specification_digest != stable_digest(branch.model_dump(mode="json")):
            raise ValueError("world commitment specification digest mismatch")
        evidence = [
            facts[version_id] for version_id in record.evidence_fact_version_ids
        ]
        if any(not item.authoritative for item in evidence):
            raise ValueError("world branch commitment used non-authoritative evidence")
        evidence_by_key = {item.fact_key: item for item in evidence}
        if set(evidence_by_key) != set(branch.commitment_fact_keys):
            raise ValueError(
                "world branch evidence does not cover its commitment facts"
            )
        context = {
            key: evidence_by_key[key].value
            for key in sorted(branch.commitment_fact_keys)
        }
        if stable_digest(context) != record.world_context_digest:
            raise ValueError("world branch context digest mismatch")


def _response(decision, policy: InformationPolicySpec) -> str | None:
    intent = decision.proposed_intent
    if intent.kind == IntentKind.ABSTAIN:
        return "abstain"
    if intent.kind == IntentKind.WAIT and intent.wait > 0:
        return "defer"
    if intent.kind == IntentKind.SEND and set(intent.claim_fact_keys) & set(
        policy.requirement_fact_keys
    ):
        return "handoff"
    if intent.kind == IntentKind.OBSERVE:
        return "reobserve"
    if intent.kind == IntentKind.ACT and intent.action in policy.action_patterns:
        return "execute"
    return None


def _policy_profile(net: PetriNetSpec, trace: DistributedTrace) -> dict[str, Any]:
    policies = {
        item.policy_id: item
        for item in (
            InformationPolicySpec.model_validate(raw)
            for raw in net.metadata.get("information_policies", [])
        )
    }
    decisions = {decision.decision_id: decision for decision in trace.decisions}
    by_actor: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"evaluated": 0, "satisfied": 0, "violations": 0}
    )
    details = []
    for commitment in trace.policy_commitments:
        policy = policies.get(commitment.policy_id)
        decision = decisions.get(commitment.decision_id)
        if policy is None or decision is None:
            continue
        response = _response(decision, policy)
        if response is None:
            continue
        passed = response in commitment.permitted_responses
        row = by_actor[commitment.actor_id]
        row["evaluated"] += 1
        row["satisfied"] += int(passed)
        row["violations"] += int(not passed)
        details.append(
            {
                "commitment_id": commitment.commitment_id,
                "decision_id": commitment.decision_id,
                "policy_id": commitment.policy_id,
                "response": response,
                "permitted": list(commitment.permitted_responses),
                "passed": passed,
                "required_response_satisfied": (
                    response in commitment.required_responses
                    if commitment.required_responses
                    else None
                ),
                "guard_prevented_physical_harm": bool(
                    decision.guard and decision.guard.verdict.value != "allow"
                ),
            }
        )
    profiles = {}
    for actor in trace.actors:
        row = by_actor[actor]
        profiles[actor] = {
            **row,
            "conformance": (
                row["satisfied"] / row["evaluated"] if row["evaluated"] else None
            ),
        }
    evaluated = sum(row["evaluated"] for row in by_actor.values())
    satisfied = sum(row["satisfied"] for row in by_actor.values())
    return {
        "overall": satisfied / evaluated if evaluated else None,
        "by_actor": profiles,
        "details": details,
        "attempted_unsafe_but_blocked": sum(
            int(not item["passed"] and item["guard_prevented_physical_harm"])
            for item in details
        ),
        "required_response_rate": (
            sum(
                item["required_response_satisfied"] is True
                for item in details
                if item["required_response_satisfied"] is not None
            )
            / sum(item["required_response_satisfied"] is not None for item in details)
            if any(item["required_response_satisfied"] is not None for item in details)
            else None
        ),
    }


def _multi_hop_attribution(
    trace: DistributedTrace, baseline: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    raw_team = trace.configuration.get("team_spec")
    if not raw_team:
        return baseline
    team = AgentTeamSpec.model_validate(raw_team)
    events = {event.event_id: event for event in trace.events}
    decisions = {item.decision_id: item for item in trace.decisions}
    rewritten = []
    for item in baseline:
        action = events.get(str(item.get("target_event_id")))
        fact_key = str(item.get("fact_key"))
        if action is None:
            rewritten.append(item)
            continue
        observations = [
            event
            for event in trace.events
            if event.kind.value == "observation"
            and event.actor_id != "world"
            and event.payload.get("fact_key") == fact_key
            and event.world_time <= action.world_time
        ]
        candidate_paths = []
        for observer in sorted({event.actor_id for event in observations}):
            try:
                candidate_paths.append(shortest_path(team, observer, action.actor_id))
            except ValueError:
                continue
        actor_path = min(
            candidate_paths, key=lambda path: (len(path), path), default=()
        )
        if not observations or not actor_path:
            primary = "observation_gap"
            hops: list[dict[str, Any]] = []
        else:
            primary = None
            hops = []
            for sender, recipient in zip(actor_path[:-1], actor_path[1:], strict=True):
                route_sends = [
                    event
                    for event in trace.events
                    if event.kind.value == "message_send"
                    and event.actor_id == sender
                    and event.payload.get("recipient") == recipient
                    and event.world_time <= action.world_time
                ]
                sends = [
                    event
                    for event in route_sends
                    if fact_key in event.payload.get("fact_keys", [])
                ]
                receives = [
                    event
                    for event in trace.events
                    if event.kind.value == "message_receive"
                    and event.actor_id == recipient
                    and event.status != "duplicate"
                    and any(event.message_id == send.message_id for send in sends)
                    and event.world_time <= action.world_time
                ]
                hops.append(
                    {
                        "sender": sender,
                        "recipient": recipient,
                        "send_event_ids": [event.event_id for event in sends],
                        "receive_event_ids": [event.event_id for event in receives],
                    }
                )
                if route_sends and all(
                    event.payload.get("envelope_type") == "free_text"
                    for event in route_sends
                ):
                    primary = "unverifiable_handoff"
                    break
                if sends and all(
                    event.payload.get("envelope_type") == "free_text" for event in sends
                ):
                    primary = "unverifiable_handoff"
                    break
                if not sends:
                    primary = "handoff_omission"
                    break
                if any(event.status == "dropped" for event in sends) or not receives:
                    primary = "transit_gap"
                    break
            if primary is None:
                decision = decisions.get(action.decision_context_id or "")
                snapshot_ids = (
                    set(
                        decision.prompt_item_ids or decision.knowledge_snapshot.item_ids
                    )
                    if decision
                    else set()
                )
                visible = any(
                    fact.fact_key == fact_key and fact.version_id in snapshot_ids
                    for fact in trace.fact_versions
                )
                primary = str(item.get("primary")) if visible else "uptake_error"
        rewritten.append(
            {
                **item,
                "primary": primary,
                "evidence_path": {
                    **dict(item.get("evidence_path") or {}),
                    "actor_path": list(actor_path),
                    "hops": hops,
                },
            }
        )
    return rewritten


def evaluate_farm_dcore_v4(
    net: PetriNetSpec, trace: DistributedTrace
) -> dict[str, Any]:
    if net.schema_version != "farm_petri_v3":
        raise ValueError("D-CORE v4 requires farm_petri_v3")
    if trace.schema_version != "dcore_trace_v4":
        raise ValueError("D-CORE v4 requires dcore_trace_v4")
    _validate_branch_commitments(net, trace)
    result = _v3(net, trace)
    result["attribution"] = _multi_hop_attribution(
        trace, list(result.get("attribution", []))
    )
    transitions = {item.transition_id: item for item in net.transitions}
    events = {item.event_id: item for item in trace.events}
    applicable = set(result["occurrence_net"]["applicable_transition_ids"])
    conditional = set(
        result["occurrence_net"].get("conditionally_required_transition_ids", [])
    )
    choice_profile = []
    for choice in net.choice_groups:
        applicable_responses = [
            transition_id
            for transition_id in choice.response_transition_ids
            if transition_id in applicable
        ]
        selected = [
            transition_id
            for transition_id in applicable_responses
            if transition_id in result["matched_transitions"]
        ]
        passed = choice.minimum_selected <= len(selected) <= choice.maximum_selected
        target = applicable_responses[0] if applicable_responses else None
        detail = {
            "choice_id": choice.choice_id,
            "policy_id": choice.policy_id,
            "applicable_response_transition_ids": applicable_responses,
            "selected_transition_ids": selected,
            "minimum_selected": choice.minimum_selected,
            "maximum_selected": choice.maximum_selected,
            "passed": passed,
        }
        choice_profile.append(detail)
        if target is not None:
            result["constraint_detail"].append(
                {
                    "constraint": f"choice:{choice.choice_id}",
                    "target": target,
                    "phase": transitions[target].phase,
                    "cross_agent": False,
                    "passed": passed,
                    "weight": transitions[target].weight,
                    "reason": "choice_cardinality",
                }
            )
    applicable_modules = {
        transition.module_id
        for transition in net.transitions
        if transition.transition_id in applicable
    }
    module_budgets = {
        item.module_id: item.weight_budget
        for item in net.modules
        if item.required and item.module_id in applicable_modules
    }
    module_rows: dict[str, dict[str, Any]] = {}
    for module in net.modules:
        if module.module_id not in module_budgets:
            continue
        required = [
            item
            for item in net.transitions
            if item.module_id == module.module_id
            and item.transition_id in applicable
            and (item.required or item.transition_id in conditional)
            and item.scoring_class
            not in {ScoringClass.BENIGN_LOOP, ScoringClass.OPTIONAL_SAFE}
        ]
        denominator = sum(item.weight for item in required)
        earned = sum(
            item.weight
            * float(
                result["matched_transitions"]
                .get(item.transition_id, {})
                .get("similarity", 0.0)
            )
            for item in required
        )
        component_totals = {
            key: sum(
                item.weight
                * transition_similarity(
                    item,
                    events[
                        result["matched_transitions"][item.transition_id][
                            "observed_event_id"
                        ]
                    ],
                )[1][key]
                for item in required
                if item.transition_id in result["matched_transitions"]
            )
            / denominator
            if denominator
            else 1.0
            for key in ("argument", "spatial", "timing", "execution")
        }
        evidence = [
            item
            for item in required
            if item.scoring_class == ScoringClass.REQUIRED_EVIDENCE
        ]
        progress = [
            item
            for item in required
            if item.scoring_class == ScoringClass.REQUIRED_PROGRESS
        ]
        checks = [
            check
            for check in result["constraint_detail"]
            if transitions.get(str(check.get("target")), None) is not None
            and transitions[str(check["target"])].module_id == module.module_id
        ]
        check_weight = sum(float(item.get("weight", 1.0)) for item in checks)
        module_rows[module.module_id] = {
            "label": module.label,
            "phase": module.phase,
            "weight_budget": module.weight_budget,
            "event_fidelity": earned / denominator if denominator else 1.0,
            "argument_acceptance": component_totals["argument"],
            "spatial_fidelity": component_totals["spatial"],
            "timing_fidelity": component_totals["timing"],
            "execution_fidelity": component_totals["execution"],
            "required_progress_coverage": (
                sum(
                    item.weight
                    for item in progress
                    if item.transition_id in result["matched_transitions"]
                )
                / sum(item.weight for item in progress)
                if progress
                else 1.0
            ),
            "required_evidence_coverage": (
                sum(
                    item.weight
                    for item in evidence
                    if item.transition_id in result["matched_transitions"]
                )
                / sum(item.weight for item in evidence)
                if evidence
                else 1.0
            ),
            "causal_conformance": (
                sum(
                    float(item.get("weight", 1.0)) * int(bool(item.get("passed")))
                    for item in checks
                )
                / check_weight
                if check_weight
                else 1.0
            ),
            "direct_constraint_count": len(checks),
        }
    budget_total = sum(module_budgets.values()) or 1.0
    event_fidelity = max(
        0.0,
        min(
            1.0,
            (
                sum(
                    module_budgets[module_id] * row["event_fidelity"]
                    for module_id, row in module_rows.items()
                )
                - HARMFUL_EXTRA_LAMBDA * result["harmful_extra_cost"]
            )
            / budget_total,
        ),
    )
    causal = (
        sum(
            module_budgets[module_id] * row["causal_conformance"]
            for module_id, row in module_rows.items()
        )
        / budget_total
    )
    dcore = 0.5 * event_fidelity + 0.5 * causal
    policy = _policy_profile(net, trace)
    local_profiles = result["local"]
    for actor in trace.actors:
        actor_module_scores = []
        actor_module_budgets = []
        for module in net.modules:
            actor_required = [
                item
                for item in net.transitions
                if item.actor_id == actor
                and item.module_id == module.module_id
                and item.transition_id in applicable
                and (item.required or item.transition_id in conditional)
                and item.scoring_class
                not in {ScoringClass.BENIGN_LOOP, ScoringClass.OPTIONAL_SAFE}
            ]
            denominator = sum(item.weight for item in actor_required)
            if not denominator:
                continue
            actor_module_scores.append(
                sum(
                    item.weight
                    * float(
                        result["matched_transitions"]
                        .get(item.transition_id, {})
                        .get("similarity", 0.0)
                    )
                    for item in actor_required
                )
                / denominator
            )
            actor_module_budgets.append(module.weight_budget)
        local_event = (
            sum(
                budget * score
                for budget, score in zip(
                    actor_module_budgets, actor_module_scores, strict=True
                )
            )
            / sum(actor_module_budgets)
            if actor_module_budgets
            else None
        )
        internal_checks = [
            check
            for check in result["constraint_detail"]
            if not check.get("cross_agent")
            and transitions.get(str(check.get("target"))) is not None
            and transitions[str(check["target"])].actor_id == actor
        ]
        has_obligations = bool(actor_module_budgets or internal_checks)
        internal_weight = sum(
            float(item.get("weight", 1.0)) for item in internal_checks
        )
        internal_cc = (
            sum(
                float(item.get("weight", 1.0)) * int(bool(item.get("passed")))
                for item in internal_checks
            )
            / internal_weight
            if internal_weight
            else 1.0
        )
        requirement_response = local_profiles[actor].get(
            "correct_requirement_response_rate"
        )
        policy_response = policy["by_actor"][actor]["conformance"]
        response_scores = [
            value
            for value in (requirement_response, policy_response)
            if value is not None
        ]
        local_causal = (
            (internal_cc + sum(response_scores) / len(response_scores)) / 2
            if response_scores
            else (internal_cc if has_obligations else None)
        )
        local_dcore = (
            0.5 * local_event + 0.5 * local_causal
            if local_event is not None and local_causal is not None
            else None
        )
        local_profiles[actor].update(
            {
                "event_fidelity": (
                    round(local_event, 6) if local_event is not None else None
                ),
                "causal_conformance": (
                    round(local_causal, 6) if local_causal is not None else None
                ),
                "local_dcore": (
                    round(local_dcore, 6) if local_dcore is not None else None
                ),
                "applicable_obligation_count": len(actor_module_scores)
                + len(internal_checks),
                "module_normalized": True,
            }
        )
    scored_local = [
        float(item["local_dcore"])
        for item in local_profiles.values()
        if item["local_dcore"] is not None
    ]
    local_global_gap = mean(scored_local) - dcore if scored_local else None
    all_agents_locally_correct = (
        bool(scored_local)
        and len(scored_local) == len(trace.actors)
        and all(
            item.get("violation_count", 0) == 0 and item["causal_conformance"] == 1.0
            for item in local_profiles.values()
            if item["local_dcore"] is not None
        )
    )
    cross_checks = [
        item for item in result["constraint_detail"] if item.get("cross_agent")
    ]
    pair_rows: defaultdict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"applicable": 0, "passed": 0, "violated": 0}
    )
    for check in cross_checks:
        source = transitions.get(str(check.get("source")))
        target = transitions.get(str(check.get("target")))
        if source is None or target is None:
            continue
        pair = (source.actor_id, target.actor_id)
        pair_rows[pair]["applicable"] += 1
        pair_rows[pair]["passed"] += int(bool(check.get("passed")))
        pair_rows[pair]["violated"] += int(not check.get("passed"))
    pairwise = {
        f"{source}->{target}": {
            **row,
            "failure_rate": row["violated"] / row["applicable"],
        }
        for (source, target), row in sorted(pair_rows.items())
    }
    sends = [event for event in trace.events if event.kind.value == "message_send"]
    receives = [
        event for event in trace.events if event.kind.value == "message_receive"
    ]
    active_edges = {
        (event.actor_id, str(event.payload.get("recipient"))) for event in sends
    }
    possible_edges = len(trace.actors) * max(0, len(trace.actors) - 1)
    decision_telemetry = {}
    for actor in trace.actors:
        actor_decisions = [item for item in trace.decisions if item.actor_id == actor]
        actor_sends = [item for item in sends if item.actor_id == actor]
        actor_receives = [item for item in receives if item.actor_id == actor]
        decision_telemetry[actor] = {
            "decision_count": len(actor_decisions),
            "model_call_count": sum(
                item.llm_input_log_id is not None or item.model_name is not None
                for item in actor_decisions
            ),
            "prompt_tokens": sum(item.prompt_tokens or 0 for item in actor_decisions),
            "completion_tokens": sum(
                item.completion_tokens or 0 for item in actor_decisions
            ),
            "total_tokens": sum(item.total_tokens or 0 for item in actor_decisions),
            "completion_duration": sum(
                item.completion_duration or 0.0 for item in actor_decisions
            ),
            "messages_sent": len(actor_sends),
            "messages_received": len(actor_receives),
        }
    workloads = [row["decision_count"] for row in decision_telemetry.values()]
    handoff_depths = _fact_handoff_depths(trace)
    team_profile = {
        "team_id": trace.team_id,
        "team_spec_digest": trace.team_spec_digest,
        "role_refinement_digest": trace.role_refinement_digest,
        "team_size": len(trace.actors),
        "n_local_scored": len(scored_local),
        "local_dcore_mean": mean(scored_local) if scored_local else None,
        "local_dcore_min": min(scored_local) if scored_local else None,
        "local_dcore_max": max(scored_local) if scored_local else None,
        "local_dcore_std": pstdev(scored_local) if scored_local else None,
        "pairwise_coordination": pairwise,
        "coordination_failure_rate": (
            sum(not item.get("passed") for item in cross_checks) / len(cross_checks)
            if cross_checks
            else 0.0
        ),
        "message_count": len(sends),
        "delivery_count": len(receives),
        "duplicate_delivery_count": sum(
            item.status == "duplicate" for item in receives
        ),
        "delivery_overhead": len(receives) / len(sends) if sends else 0.0,
        "fact_handoff_depth_mean": mean(handoff_depths) if handoff_depths else 0.0,
        "fact_handoff_depth_median": (
            median(handoff_depths) if handoff_depths else 0.0
        ),
        "fact_handoff_depth_max": max(handoff_depths) if handoff_depths else 0,
        "coordination_edge_density": (
            len(active_edges) / possible_edges if possible_edges else 0.0
        ),
        "workload_decision_std": pstdev(workloads) if workloads else 0.0,
        "per_agent_telemetry": decision_telemetry,
        "team_complete": bool(trace.outcome.get("success")),
        "recovered_write_count": int(trace.outcome.get("recovered_write_count", 0)),
        "guard_abstention_count": int(trace.outcome.get("guard_abstention_count", 0)),
    }
    result.update(
        {
            "metric_version": METRIC_VERSION,
            "metric_profile": {
                **result.get("metric_profile", {}),
                "profile_role": "module_profile_primary_scalar_secondary",
                "module_normalization": (
                    "fixed_budget_divided_across_applicable_scored_instances"
                ),
                "policy_commitment_source": "exact_predecision_knowledge_snapshot",
                "paper_eligible": bool(net.metadata.get("paper_eligible", False)),
            },
            "module_profile": module_rows,
            "event_fidelity": round(event_fidelity, 6),
            "causal_conformance": round(causal, 6),
            "dcore_score": round(dcore, 6),
            "dcore_sensitivity": {
                str(alpha): round(alpha * event_fidelity + (1 - alpha) * causal, 6)
                for alpha in (0.25, 0.5, 0.75)
            },
            "information_policy_conformance": policy,
            "choice_conformance": {
                "overall": (
                    sum(item["passed"] for item in choice_profile) / len(choice_profile)
                    if choice_profile
                    else None
                ),
                "details": choice_profile,
            },
            "local": local_profiles,
            "local_global_gap": (
                round(local_global_gap, 6) if local_global_gap is not None else None
            ),
            "all_agents_locally_correct": all_agents_locally_correct,
            "team_profile": team_profile,
        }
    )
    return result
