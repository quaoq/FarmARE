from __future__ import annotations

import json
from collections import defaultdict

import pytest
from pydantic import ValidationError

from are.simulation.distributed.controlled_suite_v5 import (
    build_controlled_suite_v5,
    evaluate_controlled_suite_v5,
)
from are.simulation.distributed.evaluator_v5 import (
    _argument_acceptance,
    _event_kind,
    _petri_edges,
    _pm4py_sequential_projection,
    evaluate_farm_dcore_v5,
)
from are.simulation.distributed.experiments import aggregate_directory, aggregate_rows
from are.simulation.distributed.farm_mutants_v5 import (
    build_farm_mutant_suite_v5,
    evaluate_farm_mutant_suite_v5,
)
from are.simulation.distributed.models import (
    CausalHandoff,
    Claim,
    DistributedRunnerConfig,
    DistributedTrace,
    EpistemicStatus,
    RequirementVerdict,
    TraceEvent,
    stable_digest,
)
from are.simulation.distributed.petri import (
    BranchAlternativeSpec,
    DataGuardSpec,
    PolicyResponse,
    WorldBranchSpec,
)
from are.simulation.distributed.review_v5 import (
    adjudicate_v5_reviews,
    compare_v5_submissions,
    confirm_v5_adjudication,
    export_neutral_v5_packet,
    freeze_v5_process,
    neutral_v5_review_template,
)
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.distributed.scientific_v5 import (
    CausalObligationGroupSpec,
    CausalPathSpec,
    FactVectorPolicyRuleSpec,
    FactVerdictPatternSpec,
    FarmProcessSpecV5,
    InformationPolicySpecV5,
    ScientificGateManifestV5,
    _guard_conjunctions_overlap,
    engineering_process_from_v4,
)
from are.simulation.distributed.transport import (
    FaultMode,
    FaultRule,
    FaultSchedule,
    InProcessTransport,
)
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    compile_paper_petri_net,
)


@pytest.fixture(scope="module")
def wetjune_v5():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            max_logical_steps=1000,
        )
    )
    process = engineering_process_from_v4(
        compile_paper_petri_net("farm_wetjune_recheck")
    )
    return result, process


@pytest.mark.parametrize(
    "scenario_id",
    (
        "farm_wetjune_recheck",
        "farm_disease_drought",
        "farm_three_cultivar",
    ),
)
def test_v5_engineering_migration_declares_modules_for_every_transfer_scenario(
    scenario_id,
    tmp_path,
):
    process = engineering_process_from_v4(compile_paper_petri_net(scenario_id))
    assert process.scenario_id == scenario_id
    assert process.occurrence_net.metadata["public_scenario_id"] == scenario_id
    module_ids = {item.module_id for item in process.occurrence_net.modules}
    assert module_ids
    assert all(
        (item.module_id or item.phase) in module_ids
        for item in process.occurrence_net.transitions
    )
    assert all(
        item.module_id in module_ids for item in process.causal_obligations
    )
    assert process.annotation_status == "draft"
    assert process.expert_review_status == "unreviewed"
    export_neutral_v5_packet(process, tmp_path)
    packet = json.loads(
        (tmp_path / "packet_manifest.json").read_text(encoding="utf-8")
    )
    assert packet["scenario_id"] == scenario_id
    assert len((tmp_path / "farmare_tools.csv").read_text().splitlines()) > 1


def test_v5_oracle_fixture_is_perfect_and_uses_no_runtime_conclusions(wetjune_v5):
    result, _ = wetjune_v5
    assert result.trace.schema_version == "dcore_trace_v5"
    assert result.metrics["metric_version"] == "dcore_eval_v5"
    assert result.metrics["event_fidelity"] == 1.0
    assert result.metrics["causal_conformance"] == 1.0
    assert result.metrics["dcore_score"] == 1.0
    assert result.metrics["metric_profile"]["runtime_transition_ids_consumed"] is False
    assert result.metrics["metric_profile"]["runtime_guard_verdicts_consumed"] is False
    assert result.metrics["event_acceptance"]["runtime_harmful_flags_consumed"] is False
    assert result.metrics["phase_profile"]
    assert result.metrics["long_horizon_profile"]["first_critical_divergence"] is None
    assert result.metrics["recovery"]["evaluation_source"] == (
        "independent_policy_decision_sequence"
    )


def test_missing_required_action_produces_a_frozen_prefix_divergence(wetjune_v5):
    result, process = wetjune_v5
    required_ids = {
        item.transition_id
        for item in process.occurrence_net.transitions
        if item.required and item.actor_id != "world"
    }
    events_by_id = {item.event_id: item for item in result.trace.events}
    transition_id, match = next(
        item
        for item in result.metrics["event_acceptance"]["matches"].items()
        if item[0] in required_ids
        and events_by_id[item[1]["observed_event_id"]].kind.value == "action"
    )
    event_id = match["observed_event_id"]
    trace = result.trace.model_copy(
        update={
            "events": tuple(
                item for item in result.trace.events if item.event_id != event_id
            )
        }
    )

    metrics = evaluate_farm_dcore_v5(process, trace)

    assert metrics["event_fidelity"] < 1.0
    divergence = metrics["long_horizon_profile"]["first_critical_divergence"]
    transition = next(
        item
        for item in process.occurrence_net.transitions
        if item.transition_id == transition_id
    )
    assert divergence["module_id"] == transition.module_id
    assert (
        metrics["long_horizon_profile"]["core_style_prefix_profile"][-1]["dcore_score"]
        < 1.0
    )


def test_runtime_policy_tampering_is_detected_but_cannot_change_v5_score(wetjune_v5):
    result, process = wetjune_v5
    baseline_mismatches = result.metrics["information_policy_conformance"][
        "runtime_commitment_mismatch_count"
    ]
    commitment = result.trace.policy_commitments[0]
    tampered = commitment.model_copy(
        update={
            "requirement_verdicts": {
                key: RequirementVerdict.FALSE for key in commitment.requirement_verdicts
            },
            "permitted_responses": ("abstain",),
            "required_responses": ("abstain",),
        }
    )
    trace = result.trace.model_copy(
        update={
            "policy_commitments": (
                tampered,
                *result.trace.policy_commitments[1:],
            )
        }
    )
    metrics = evaluate_farm_dcore_v5(process, trace)
    assert metrics["dcore_score"] == result.metrics["dcore_score"]
    assert (
        metrics["information_policy_conformance"]["runtime_commitment_mismatch_count"]
        == baseline_mismatches + 1
    )
    assert metrics["metric_profile"]["paper_eligible"] is False


def test_missing_runtime_branch_commitment_cannot_change_branch_selection(tmp_path):
    net = compile_paper_petri_net("farm_wetjune_recheck")
    first, second = net.transitions[:2]
    branch = WorldBranchSpec(
        branch_id="v5-audit-only-branch",
        commit_phase="field_prep",
        commitment_fact_keys=("weather:spray_window_open",),
        alternatives=(
            BranchAlternativeSpec(
                alternative_id="open",
                label="open",
                transition_ids=(first.transition_id,),
                required_transition_ids=(first.transition_id,),
                guards=(
                    DataGuardSpec(
                        guard_id="v5-audit-open",
                        fact_key="weather:spray_window_open",
                        expected=True,
                        source="world",
                        branch_selector=True,
                    ),
                ),
            ),
            BranchAlternativeSpec(
                alternative_id="closed",
                label="closed",
                transition_ids=(second.transition_id,),
                required_transition_ids=(second.transition_id,),
                default=True,
            ),
        ),
    )
    branched = net.model_copy(
        update={
            "transitions": tuple(
                item.model_copy(update={"required": False})
                if item.transition_id in {first.transition_id, second.transition_id}
                else item
                for item in net.transitions
            ),
            "exogenous_branches": (branch,),
        }
    )
    spec_path = tmp_path / "branched-petri.json"
    spec_path.write_text(branched.model_dump_json(), encoding="utf-8")
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            petri_spec_path=str(spec_path),
            max_logical_steps=1000,
        )
    )
    process = engineering_process_from_v4(branched)
    assert len(result.trace.world_branch_commitments) == 1
    trace = result.trace.model_copy(update={"world_branch_commitments": ()})

    metrics = evaluate_farm_dcore_v5(process, trace)

    assert metrics["dcore_score"] == result.metrics["dcore_score"]
    assert metrics["occurrence_net"] == result.metrics["occurrence_net"]
    assert all(
        item["missing_runtime_commitment"]
        for item in metrics["world_branch_audit"]["details"]
    )
    assert metrics["world_branch_audit"]["runtime_branch_selection_consumed"] is False


def test_missing_runtime_commitment_cannot_hide_a_decision_from_v5_evaluation(
    wetjune_v5,
):
    result, process = wetjune_v5
    profile = result.metrics["information_policy_conformance"]
    decision_phases = {
        item.decision_id: item.season_phase for item in result.trace.decisions
    }
    audited = next(
        item
        for item in profile["runtime_commitment_audit"]
        if item["agrees"] and decision_phases[item["decision_id"]] == item["phase"]
    )
    removed_decision_id = audited["decision_id"]
    trace = result.trace.model_copy(
        update={
            "policy_commitments": tuple(
                item
                for item in result.trace.policy_commitments
                if item.decision_id != removed_decision_id
            )
        }
    )
    metrics = evaluate_farm_dcore_v5(process, trace)
    rescored = metrics["information_policy_conformance"]

    assert {item["decision_id"] for item in rescored["details"]} == {
        item["decision_id"] for item in profile["details"]
    }
    assert (
        metrics["information_global_discordance"]
        == result.metrics["information_global_discordance"]
    )
    assert rescored["runtime_commitment_mismatch_count"] == (
        profile["runtime_commitment_mismatch_count"] + 1
    )
    assert rescored["runtime_commitments_consumed_for_applicability"] is False


def test_runtime_harmful_flag_cannot_classify_an_unmatched_action(wetjune_v5):
    result, process = wetjune_v5
    source = next(
        item
        for item in result.trace.events
        if item.kind.value == "action" and item.actor_id != "world"
    )
    extra = source.model_copy(
        update={
            "event_id": "event:unmatched-runtime-harmful-flag",
            "logical_time": max(item.logical_time for item in result.trace.events) + 1,
            "payload": {**source.payload, "harmful": True},
            "causal_parents": (),
        }
    )
    trace = result.trace.model_copy(update={"events": (*result.trace.events, extra)})
    metrics = evaluate_farm_dcore_v5(process, trace)
    row = next(
        item
        for item in metrics["event_acceptance"]["unmatched_action_classification"]
        if item["event_id"] == extra.event_id
    )
    assert row["classification"] == "unresolved"
    assert row["source"] == "no_applicable_classifier"
    assert metrics["event_acceptance"]["runtime_harmful_flags_consumed"] is False


def test_zero_obligation_module_is_na_not_perfect(wetjune_v5):
    result, process = wetjune_v5
    module_id = process.occurrence_net.modules[0].module_id
    changed = process.model_copy(
        update={
            "causal_obligations": tuple(
                item
                for item in process.causal_obligations
                if item.module_id != module_id
            )
        }
    )
    metrics = evaluate_farm_dcore_v5(changed, result.trace)
    assert metrics["module_profile"][module_id]["causal_conformance"] is None
    assert metrics["module_profile"][module_id]["semantic_obligation_count"] == 0


def test_multi_edge_semantic_path_is_one_scoring_obligation(wetjune_v5):
    result, process = wetjune_v5
    edges = {
        edge
        for obligation in process.causal_obligations
        for alternative in obligation.alternatives
        for edge in alternative.transition_edges
    }
    chain = next(
        ((a, b, c) for a, b in edges for x, c in edges if b == x),
        None,
    )
    assert chain is not None
    source, middle, target = chain
    transition = next(
        item
        for item in process.occurrence_net.transitions
        if item.transition_id == target
    )
    obligation = CausalObligationGroupSpec(
        obligation_id="semantic:two-hop-test",
        label="one end-to-end semantic obligation",
        module_id=transition.module_id or transition.phase,
        target_transition_ids=(target,),
        alternatives=(
            CausalPathSpec(
                path_id="semantic:two-hop-path",
                transition_edges=((source, middle), (middle, target)),
            ),
        ),
        weight=1.0,
    )
    changed = process.model_copy(update={"causal_obligations": (obligation,)})
    metrics = evaluate_farm_dcore_v5(changed, result.trace)
    assert metrics["semantic_causal_obligations"]["applicable"] == 1
    assert (
        len(
            metrics["semantic_causal_obligations"]["details"][0]["alternatives"][0][
                "edges"
            ]
        )
        == 2
    )
    assert metrics["causal_conformance"] == 1.0


def test_numeric_acceptance_has_an_exact_binary_boundary(wetjune_v5):
    result, process = wetjune_v5
    acceptance = next(
        item
        for item in process.acceptance
        if any(constraint.tolerance is not None for constraint in item.arguments)
    )
    transition = next(
        item
        for item in process.occurrence_net.transitions
        if item.transition_id == acceptance.transition_id
    )
    event_id = result.metrics["event_acceptance"]["matches"][transition.transition_id][
        "observed_event_id"
    ]
    event = next(item for item in result.trace.events if item.event_id == event_id)
    numeric = next(item for item in acceptance.arguments if item.tolerance is not None)
    boundary = float(numeric.expected) + float(numeric.tolerance)
    at_boundary = event.model_copy(
        update={"args": {**event.args, numeric.name: boundary}}
    )
    outside = event.model_copy(
        update={"args": {**event.args, numeric.name: boundary + 1e-6}}
    )
    assert _argument_acceptance(transition, acceptance, at_boundary)[0] is True
    assert _argument_acceptance(transition, acceptance, outside)[0] is False


def test_wrong_action_arguments_are_localized_as_reasoning_not_transport(wetjune_v5):
    result, process = wetjune_v5
    matches = result.metrics["event_acceptance"]["matches"]
    transition_by_event = {
        item["observed_event_id"]: transition_id
        for transition_id, item in matches.items()
    }
    acceptance_by_transition = {item.transition_id: item for item in process.acceptance}
    chosen = None
    for row in result.metrics["information_policy_conformance"]["details"]:
        if row["response"] != "execute" or not row["global_conforming"]:
            continue
        action = next(
            (
                item
                for item in result.trace.events
                if item.decision_context_id == row["decision_id"]
                and item.kind.value == "action"
            ),
            None,
        )
        transition_id = transition_by_event.get(action.event_id) if action else None
        acceptance = acceptance_by_transition.get(transition_id or "")
        if action is not None and acceptance is not None and acceptance.arguments:
            chosen = (row, action, acceptance)
            break
    assert chosen is not None
    row, action, acceptance = chosen
    constraint = acceptance.arguments[0]
    args = dict(action.args)
    if isinstance(constraint.expected, bool):
        args[constraint.name] = not constraint.expected
    elif isinstance(constraint.expected, (int, float)):
        args[constraint.name] = (
            float(constraint.expected) + float(constraint.tolerance or 0.0) + 1.0
        )
    else:
        args[constraint.name] = f"wrong:{constraint.expected}"
    trace = result.trace.model_copy(
        update={
            "events": tuple(
                event.model_copy(
                    update={
                        "args": {
                            **event.args,
                            constraint.name: args[constraint.name],
                        }
                    }
                )
                if event.kind.value == "action"
                and event.actor_id == action.actor_id
                and event.action == action.action
                else event
                for event in result.trace.events
            )
        }
    )

    metrics = evaluate_farm_dcore_v5(process, trace)

    localized = next(
        item
        for item in metrics["decision_failure_localization"]
        if item["decision_id"] == row["decision_id"]
    )
    assert localized["primary"] == "reasoning_error"
    assert "arguments" in localized["failed_acceptance_predicates"]
    assert metrics["event_fidelity"] < result.metrics["event_fidelity"]


def test_fact_vector_policy_rejects_overlap_and_missing_cells():
    requirement = DataGuardSpec(
        guard_id="g",
        fact_key="disease:confirmed",
        source="knowledge",
    )
    with pytest.raises(ValidationError, match="exhaustive and disjoint"):
        InformationPolicySpecV5(
            policy_id="bad",
            actor_id="operations",
            action_patterns=("spray",),
            phases=("r5",),
            requirements=(requirement,),
            rules=(
                FactVectorPolicyRuleSpec(
                    rule_id="overbroad-a",
                    fact_pattern=(
                        FactVerdictPatternSpec(
                            fact_key="disease:confirmed", verdict="any"
                        ),
                    ),
                    permitted_responses=(PolicyResponse.DEFER,),
                ),
                FactVectorPolicyRuleSpec(
                    rule_id="overbroad-b",
                    fact_pattern=(
                        FactVerdictPatternSpec(
                            fact_key="disease:confirmed", verdict="true"
                        ),
                    ),
                    permitted_responses=(PolicyResponse.EXECUTE,),
                ),
            ),
        )


def test_world_branch_guard_cells_have_checkable_disjoint_semantics():
    low = (
        DataGuardSpec(
            guard_id="low",
            fact_key="grain:moisture",
            operator="le",
            expected=18.0,
            source="world",
        ),
    )
    high = (
        DataGuardSpec(
            guard_id="high",
            fact_key="grain:moisture",
            operator="gt",
            expected=18.0,
            source="world",
        ),
    )
    overlapping = (
        DataGuardSpec(
            guard_id="overlap",
            fact_key="grain:moisture",
            operator="ge",
            expected=13.5,
            source="world",
        ),
    )
    assert _guard_conjunctions_overlap(low, high) is False
    assert _guard_conjunctions_overlap(low, overlapping) is True


def test_true_old_new_reorder_requires_receive_order_inversion():
    old = "handoff:midseason:v1"
    new = "handoff:midseason:v2"
    transport = InProcessTransport(
        ("scout", "operator"),
        schedule=FaultSchedule(
            by_message_id={
                old: FaultRule(FaultMode.REORDER, delay=10.0, reorder_bias=1.0),
                new: FaultRule(),
            }
        ),
    )
    envelopes = []
    for message_id, observed_at in ((old, 0.0), (new, 1.0)):
        envelopes.append(
            CausalHandoff(
                message_id=message_id,
                sender="scout",
                recipient="operator",
                claims=(
                    Claim(
                        fact_key="weather:spray_window_open",
                        value=True,
                        status=EpistemicStatus.CLAIMED,
                        observed_at=observed_at,
                        valid_until=20.0,
                    ),
                ),
            )
        )
    transport.send(envelopes[0], 0.0)
    transport.send(envelopes[1], 1.0)
    delivered = transport.deliver_next(20.0)
    assert [item.envelope.message_id for item in delivered] == [new, old]
    manifestation = transport.fault_manifestation("reorder")
    assert manifestation["manifested"] is True
    assert manifestation["receive_order_inversions"] == [(2, 1)]


def test_past_deadline_fault_uses_deadline_not_validity_proxy():
    envelope = CausalHandoff(
        message_id="handoff:r5:v1",
        sender="scout",
        recipient="operator",
        claims=(
            Claim(
                fact_key="disease:confirmed",
                value=True,
                status=EpistemicStatus.CLAIMED,
                observed_at=0.0,
                # The evidence remains valid beyond the agronomic deadline.
                valid_until=100.0,
            ),
        ),
    )
    transport = InProcessTransport(
        ("scout", "operator"),
        schedule=FaultSchedule(
            by_message_id={
                envelope.message_id: FaultRule(
                    FaultMode.DELAY,
                    delay=20.0,
                    deadline_world_time=10.0,
                )
            }
        ),
    )
    transport.send(envelope, 0.0)
    transport.deliver_next(20.0)
    manifestation = transport.fault_manifestation("delay_past_deadline")
    assert manifestation["manifested"] is True
    assert manifestation["past_validity"][0]["past_validity"] is False
    assert manifestation["past_deadline"][0]["past_deadline"] is True


def test_delay_fault_does_not_claim_to_cause_preexisting_staleness():
    envelope = CausalHandoff(
        message_id="handoff:already-stale",
        sender="scout",
        recipient="operator",
        claims=(
            Claim(
                fact_key="weather:spray_window_open",
                value=True,
                status=EpistemicStatus.CLAIMED,
                observed_at=0.0,
                valid_until=5.0,
            ),
        ),
    )
    transport = InProcessTransport(
        ("scout", "operator"),
        schedule=FaultSchedule(
            by_message_id={
                envelope.message_id: FaultRule(
                    FaultMode.DELAY,
                    delay=2.0,
                    valid_until_world_time=5.0,
                )
            }
        ),
    )
    transport.send(envelope, 10.0)
    transport.deliver_next(12.0)

    manifestation = transport.fault_manifestation("delay_past_validity")
    assert manifestation["past_validity"][0]["past_validity"] is True
    assert manifestation["past_validity"][0]["crossed_validity"] is False
    assert manifestation["manifested"] is False


def test_aggregation_never_pools_controller_or_model_profiles():
    rows = []
    for profile, model, score in (("react", "m1", 1.0), ("plan", "m2", 0.0)):
        rows.append(
            {
                "scenario": "farm_wetjune_recheck",
                "team_id": "wetjune_2agent",
                "condition": "local_causal_audit",
                "fault": "none",
                "controller_profile_id": profile,
                "model_configuration_id": model,
                "pair_id": f"pair:{profile}",
                "world_cluster_id": "farm_wetjune_recheck:w0",
                "success": bool(score),
                "safety_success": True,
                "infrastructure_failure": False,
                "dcore_score": score,
            }
        )
    report = aggregate_rows(rows)
    assert report["schema_version"] == "dcore_aggregate_v2"
    assert len(report["groups"]) == 2
    assert {row["controller_profile_id"] for row in report["groups"]} == {
        "react",
        "plan",
    }
    assert report["metric_yield_calibration"]["interpretation"] == (
        "predictive_validity_not_causal_effect"
    )


def test_paired_contrasts_cluster_repeats_by_world_seed():
    rows = []
    for pair_id, cluster, difference in (
        ("pair-1", "wetjune:w0", 1.0),
        ("pair-2", "wetjune:w0", 3.0),
        ("pair-3", "wetjune:w1", 5.0),
    ):
        common = {
            "scenario": "farm_wetjune_recheck",
            "team_id": "wetjune_2agent",
            "fault": "none",
            "controller_profile_id": "react",
            "model_configuration_id": "model-a",
            "pair_id": pair_id,
            "world_cluster_id": cluster,
            "success": True,
            "safety_success": True,
            "infrastructure_failure": False,
        }
        rows.extend(
            (
                {
                    **common,
                    "condition": "local_causal_audit",
                    "dcore_score": difference,
                },
                {**common, "condition": "local_free_text", "dcore_score": 0.0},
            )
        )

    report = aggregate_rows(rows)
    contrast = next(
        item
        for item in report["predeclared_contrasts"]
        if item["contrast"] == "causal_audit_minus_free_text_reliable"
        and item["metric"] == "dcore_score"
    )
    assert contrast["n_pairs"] == 3
    assert contrast["n_world_clusters"] == 2
    assert contrast["mean_paired_difference"] == 3.5


def test_paper_aggregation_rejects_pre_v5_and_inactive_faults(tmp_path):
    source = tmp_path / "results.jsonl"
    source.write_text(
        json.dumps(
            {
                "metric_version": "dcore_eval_v4",
                "trace_schema_version": "dcore_trace_v4",
                "paper_mode": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not dcore_eval_v5"):
        aggregate_directory(source, paper_mode=True)
    source.write_text(
        json.dumps(
            {
                "metric_version": "dcore_eval_v5",
                "trace_schema_version": "dcore_trace_v5",
                "process_spec_digest": "confirmed-digest",
                "metric_paper_eligible": True,
                "paper_mode": True,
                "fault": "drop",
                "fault_manifested": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inactive fault treatment"):
        aggregate_directory(source, paper_mode=True)
    source.write_text(
        json.dumps(
            {
                "paper_mode": True,
                "bounded_llm_smoke": True,
                "scientific_contract": "v5",
                "infrastructure_failure": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="prerelease connectivity smoke"):
        aggregate_directory(source, paper_mode=True)


def test_paper_aggregation_retains_infrastructure_failures_as_sensitivity(tmp_path):
    source = tmp_path / "results.jsonl"
    source.write_text(
        json.dumps(
            {
                "scenario": "farm_wetjune_recheck",
                "team_id": "wetjune_2agent",
                "condition": "local_causal_audit",
                "fault": "none",
                "controller_profile_id": "react",
                "model_configuration_id": "model-a",
                "paper_mode": True,
                "scientific_contract": "v5",
                "infrastructure_failure": True,
                "success": False,
                "safety_success": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = aggregate_directory(source, paper_mode=True)
    group = report["groups"][0]
    assert group["n_primary"] == 0
    assert group["completion_rate"] is None
    assert group["infrastructure_failure_rate"] == 1.0
    assert group["completion_all_failures_sensitivity"] == 0.0


def test_complete_wetjune_gate_requires_bounded_real_llm_smoke():
    payload = {
        "scenario_id": "farm_wetjune_recheck",
        "status": "complete",
        "confirmed_process_digest": "a" * 64,
        "confirmed_team_digest": "b" * 64,
        "offline_semantic_gates": True,
        "oracle_yield_equivalence": True,
        "prompt_leakage_check": True,
        "saved_trace_replay": True,
        "blocked_write_nonmutation": True,
        "allowed_write_exactly_once": True,
        "mock_matrix_complete": True,
        "bounded_real_llm_smoke": False,
        "code_commit": "d" * 40,
        "release_tag": "release",
        "completed_at_utc": "2026-08-23T00:00:00Z",
        "test_report_digest": "e" * 64,
        "environment_lock_digest": "f" * 64,
        "analysis_protocol_digest": "c" * 64,
    }
    with pytest.raises(ValidationError, match="bounded LLM smoke"):
        ScientificGateManifestV5.model_validate(payload)


def test_offline_complete_gate_authorizes_only_a_bounded_prerelease_smoke():
    gate = ScientificGateManifestV5(
        scenario_id="farm_wetjune_recheck",
        status="offline_complete",
        confirmed_process_digest="a" * 64,
        confirmed_team_digest="b" * 64,
        offline_semantic_gates=True,
        oracle_yield_equivalence=True,
        prompt_leakage_check=True,
        saved_trace_replay=True,
        blocked_write_nonmutation=True,
        allowed_write_exactly_once=True,
        mock_matrix_complete=True,
        bounded_real_llm_smoke=False,
        code_commit="d" * 40,
        completed_at_utc="2026-08-23T00:00:00Z",
        test_report_digest="e" * 64,
        environment_lock_digest="f" * 64,
        analysis_protocol_digest="c" * 64,
    )
    assert gate.release_tag is None
    with pytest.raises(ValidationError, match="at most 12 model calls"):
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            controller_mode="llm",
            paper_mode=True,
            bounded_llm_smoke=True,
            scientific_contract="v5",
            petri_spec_path="confirmed.json",
            scientific_gate_manifest="offline.json",
            max_model_calls=13,
        )


def test_neutral_review_packet_contains_no_oracle_answers(wetjune_v5, tmp_path):
    _, process = wetjune_v5
    template_path = export_neutral_v5_packet(process, tmp_path)
    template = json.loads(template_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        (tmp_path / "packet_manifest.json").read_text(encoding="utf-8")
    )
    assert template["transition_acceptance"] == {}
    assert template["safe_response_policies"] == {}
    assert template["world_branches"] == {}
    assert manifest["contains_oracle_transitions"] is False


def test_v5_review_comparison_is_order_invariant_and_digest_bound(wetjune_v5):
    _, process = wetjune_v5

    def submission(reviewer_id: str, resolved: FarmProcessSpecV5) -> dict:
        payload = neutral_v5_review_template(process)
        payload.update(
            {
                "reviewer": {
                    "pseudonymous_id": reviewer_id,
                    "expertise_role": "agronomy",
                    "utc_timestamp": "2026-08-23T00:00:00Z",
                },
                "attestation": {
                    "final_model_results_not_inspected": True,
                    "yield_correlations_not_used_for_tuning": True,
                },
                "resolved_process_spec": resolved.model_dump(mode="json"),
                "reviewed_process_digest": stable_digest(
                    resolved.model_dump(mode="json")
                ),
            }
        )
        return payload

    reordered = process.model_copy(
        update={
            "acceptance": tuple(reversed(process.acceptance)),
            "causal_obligations": tuple(reversed(process.causal_obligations)),
        }
    )
    left = submission("expert-a", process)
    right = submission("expert-b", reordered)
    comparison = compare_v5_submissions(left, right)
    assert comparison["unresolved_paths"] == []

    adjudication = adjudicate_v5_reviews(
        left,
        right,
        {},
        adjudicator_id="expert-adjudicator",
        adjudicator_expertise_role="agronomy and farm operations",
        final_model_results_not_inspected=True,
        yield_correlations_not_used_for_tuning=True,
    )
    confirmation = {
        "third_expert_id": "expert-c",
        "expertise_role": "farm operations",
        "utc_timestamp": "2026-08-24T00:00:00Z",
        "final_model_results_not_inspected": True,
        "yield_correlations_not_used_for_tuning": True,
        "resolved_process_digest": stable_digest(adjudication["resolved_process_spec"]),
    }
    confirmed = confirm_v5_adjudication(adjudication, confirmation)
    assert confirmed["adjudication_digest"] == adjudication["adjudication_digest"]
    with pytest.raises(ValidationError, match="engineering defaults"):
        freeze_v5_process(adjudication, confirmed)


def test_information_global_discordance_is_zero_for_perfect_oracle(wetjune_v5):
    result, _ = wetjune_v5
    assert (
        result.metrics["information_global_discordance"][
            "information_global_discordance"
        ]
        == 0.0
    )


def test_exact_provenance_reaches_authoritative_roots(wetjune_v5):
    result, _ = wetjune_v5
    facts = {item.version_id: item for item in result.trace.fact_versions}
    forwarded = [item for item in facts.values() if item.origin_version_id]
    assert forwarded
    rooted = 0
    for item in forwarded:
        seen = set()
        current = item
        while current.origin_version_id:
            assert current.version_id not in seen
            seen.add(current.version_id)
            current = facts[current.origin_version_id]
        rooted += int(current.authoritative)
    assert rooted > 0


def test_v5_controlled_information_path_mutants_localize_earliest_link(wetjune_v5):
    result, process = wetjune_v5
    cases = build_farm_mutant_suite_v5(process, result.trace)
    report = evaluate_farm_mutant_suite_v5(process, cases)

    assert len(cases) == 6
    assert all(item["passed"] for item in report["rows"]), report["rows"]
    assert report["localization_metrics"]["accuracy"] == 1.0
    assert report["runtime_violation_labels_used"] is False


def test_v5_frozen_twenty_case_controlled_suite(wetjune_v5):
    result, process = wetjune_v5
    cases = build_controlled_suite_v5(process, result.trace)
    report = evaluate_controlled_suite_v5(process, cases)

    assert len(cases) == 20
    assert report["passed"], [item for item in report["rows"] if not item["passed"]]
    assert report["paper_validation_passed"] is False
    assert report["pending_frozen_properties"] == ["harmful_write"]
    assert report["runtime_conclusions_used"] is False


def test_frozen_global_cc_rejects_locally_present_but_superseded_evidence(wetjune_v5):
    result, process = wetjune_v5
    stale_case = next(
        item
        for item in build_farm_mutant_suite_v5(process, result.trace)
        if item.name == "stale_information"
    )
    stale_metrics = evaluate_farm_dcore_v5(process, stale_case.trace)
    failed = next(
        item
        for item in stale_metrics["semantic_causal_obligations"]["details"]
        if item["obligation_id"] == stale_case.obligation_id
    )
    target_time = next(
        event.world_time
        for event in stale_case.trace.events
        if event.event_id == failed["target_event_ids"][0]
    )
    stale_fact_id = failed["alternatives"][0]["guards"][0]["fact_version_id"]
    stale_fact = next(
        item
        for item in stale_case.trace.fact_versions
        if item.version_id == stale_fact_id
    )
    authoritative = next(
        item
        for item in stale_case.trace.fact_versions
        if item.authoritative and item.fact_key == stale_fact.fact_key
    )
    world_event = next(
        item
        for item in stale_case.trace.events
        if item.event_id == authoritative.source_event_id
    )
    newer_event = world_event.model_copy(
        update={
            "event_id": "mutant:v5:newer-authoritative-world-event",
            "world_time": target_time - 0.1,
            "logical_time": target_time - 0.1,
            "fact_version": "mutant:v5:newer-authoritative-fact",
        }
    )
    newer_fact = authoritative.model_copy(
        update={
            "version_id": "mutant:v5:newer-authoritative-fact",
            "source_event_id": newer_event.event_id,
            "value": not bool(authoritative.value),
            "world_time": target_time - 0.1,
        }
    )
    trace = stale_case.trace.model_copy(
        update={
            "events": (*stale_case.trace.events, newer_event),
            "fact_versions": (
                *(
                    item.model_copy(update={"valid_until": target_time + 100.0})
                    if item.version_id == stale_fact_id
                    else item
                    for item in stale_case.trace.fact_versions
                ),
                newer_fact,
            ),
        }
    )
    draft_metrics = evaluate_farm_dcore_v5(process, trace)
    frozen_semantics = process.model_copy(update={"annotation_status": "frozen"})
    frozen_metrics = evaluate_farm_dcore_v5(frozen_semantics, trace)
    draft_obligation = next(
        item
        for item in draft_metrics["semantic_causal_obligations"]["details"]
        if item["obligation_id"] == stale_case.obligation_id
    )
    frozen_obligation = next(
        item
        for item in frozen_metrics["semantic_causal_obligations"]["details"]
        if item["obligation_id"] == stale_case.obligation_id
    )

    assert draft_obligation["passed"] is True
    assert frozen_obligation["passed"] is False
    guard = frozen_obligation["alternatives"][0]["guards"][0]
    assert guard["local_verdict"] == "true"
    assert guard["world_verdict"] == "false"
    assert guard["current_provenance"] is False


def test_fault_manifest_is_trace_visible(wetjune_v5):
    result, _ = wetjune_v5
    assert len(result.trace.fault_manifestations) == 1
    assert result.trace.fault_manifestations[0].mode == "none"
    assert result.trace.fault_manifestations[0].manifested is True


def test_saved_trace_reevaluation_is_byte_identical(wetjune_v5):
    result, process = wetjune_v5
    saved_trace = DistributedTrace.model_validate_json(result.trace.model_dump_json())
    saved_process = FarmProcessSpecV5.model_validate_json(process.model_dump_json())
    first = evaluate_farm_dcore_v5(saved_process, saved_trace)
    second = evaluate_farm_dcore_v5(saved_process, saved_trace)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert stable_digest(first) == stable_digest(second)


def test_pm4py_token_replay_is_perfect_on_its_sequential_fixture(wetjune_v5):
    result, process = wetjune_v5
    net = process.occurrence_net
    applicable = {item.transition_id for item in net.transitions}
    children: defaultdict[str, set[str]] = defaultdict(set)
    indegree = {item: 0 for item in applicable}
    for source, target in _petri_edges(net):
        children[source].add(target)
        indegree[target] += 1
    frontier = sorted(item for item, degree in indegree.items() if degree == 0)
    order = []
    while frontier:
        transition_id = frontier.pop(0)
        order.append(transition_id)
        for child in sorted(children[transition_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                frontier.append(child)
        frontier.sort()
    transitions = {item.transition_id: item for item in net.transitions}
    local_sequences: defaultdict[str, int] = defaultdict(int)
    events = []
    for index, transition_id in enumerate(order):
        transition = transitions[transition_id]
        if transition.actor_id == "world":
            continue
        local_sequences[transition.actor_id] += 1
        events.append(
            TraceEvent(
                event_id=f"sequential:{index}",
                kind=_event_kind(transition),
                actor_id=transition.actor_id,
                local_sequence=local_sequences[transition.actor_id],
                logical_time=float(index),
                world_time=float(index),
                vector_clock={
                    transition.actor_id: local_sequences[transition.actor_id]
                },
                action=transition.action,
            )
        )
    trace = result.trace.model_copy(update={"events": tuple(events)})
    baseline = _pm4py_sequential_projection(process, applicable, trace)
    assert baseline["available"] is True
    assert baseline["fitness"] == 1.0
