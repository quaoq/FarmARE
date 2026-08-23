from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from are.simulation.distributed.cli import main
from are.simulation.distributed.evaluator_v4 import evaluate_farm_dcore_v4
from are.simulation.distributed.knowledge import KnowledgeStore
from are.simulation.distributed.models import (
    AgentIntent,
    DistributedRunnerConfig,
    EpistemicStatus,
    GuardResult,
    GuardVerdict,
    IntentKind,
    KnowledgeItem,
    stable_digest,
)
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.petri import (
    BranchAlternativeSpec,
    ChoiceGroupSpec,
    DataGuardSpec,
    InformationPolicySpec,
    TransitionKind,
    WorldBranchSpec,
    transition_dependencies,
)
from are.simulation.distributed.review import (
    adjudicate_reviews,
    adjudicate_team_reviews,
    compare_team_submissions,
    confirm_adjudication,
    export_neutral_review_packet,
    freeze_confirmed_review,
    freeze_confirmed_team_review,
    neutral_submission_template,
    submission_attestation_digest,
    validate_submission,
    validate_team_submission,
    wetjune_review_template,
)
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.distributed.trace import validate_trace
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    compile_native_petri_net,
    compile_paper_petri_net,
)
from are.simulation.scenarios.scenario_dcore.wetjune_fixtures import (
    controlled_wetjune_policy_fixtures,
)
from are.simulation.scenarios.scenario_dcore.wetjune_petri_v3 import (
    build_wetjune_template,
    expand_wetjune_template,
)


def test_wetjune_hierarchy_expands_deterministically_with_module_budgets():
    first = compile_paper_petri_net("farm_wetjune_recheck")
    second = compile_paper_petri_net("farm_wetjune_recheck")
    assert first == second
    assert first.schema_version == "farm_petri_v3"
    assert len(first.modules) == 10
    assert len(first.transitions) == (
        len(compile_native_petri_net("farm_wetjune_recheck").transitions) + 3
    )
    assert {
        "policy:midseason:abstain",
        "policy:r5:abstain",
        "policy:harvest:abstain",
    } <= {item.transition_id for item in first.transitions}
    for module in first.modules:
        assert sum(
            transition.weight
            for transition in first.transitions
            if transition.module_id == module.module_id and transition.required
        ) == pytest.approx(module.weight_budget)


def test_module_budget_is_invariant_to_transition_template_refinement():
    base = compile_native_petri_net("farm_wetjune_recheck")
    template = build_wetjune_template(base)
    candidate = next(
        item
        for item in template.transition_templates
        if item.expansion.expected_count >= 2 and item.required
    )
    midpoint = candidate.expansion.expected_count // 2
    from are.simulation.distributed.petri import ExpansionRuleSpec

    left = candidate.model_copy(
        update={
            "template_id": f"{candidate.template_id}:a",
            "within_module_weight": candidate.within_module_weight / 2,
            "expansion": ExpansionRuleSpec(
                kind=candidate.expansion.kind,
                source_transition_ids=candidate.expansion.source_transition_ids[
                    :midpoint
                ],
                expected_count=midpoint,
            ),
        }
    )
    right_ids = candidate.expansion.source_transition_ids[midpoint:]
    right = candidate.model_copy(
        update={
            "template_id": f"{candidate.template_id}:b",
            "within_module_weight": candidate.within_module_weight / 2,
            "expansion": ExpansionRuleSpec(
                kind=candidate.expansion.kind,
                source_transition_ids=right_ids,
                expected_count=len(right_ids),
            ),
        }
    )
    refined = template.model_copy(
        update={
            "transition_templates": tuple(
                item
                for item in template.transition_templates
                if item.template_id != candidate.template_id
            )
            + (
                left,
                right,
            )
        }
    )
    expanded = expand_wetjune_template(refined, base)
    module = next(
        item for item in expanded.modules if item.module_id == candidate.module_id
    )
    assert sum(
        transition.weight
        for transition in expanded.transitions
        if transition.module_id == module.module_id and transition.required
    ) == pytest.approx(module.weight_budget)


def test_policy_schema_rejects_incomplete_truth_table():
    with pytest.raises(ValidationError, match="incomplete truth table"):
        InformationPolicySpec(
            policy_id="incomplete",
            actor_id="operations",
            action_patterns=("write",),
            phases=("midseason",),
            requirement_fact_keys=("fact",),
            rules=(),
        )


def test_v4_oracle_ceiling_is_perfect_and_commitments_are_nonanticipating():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck", max_logical_steps=1000
        )
    )
    assert result.trace.schema_version == "dcore_trace_v4"
    assert result.metrics["metric_version"] == "dcore_eval_v4"
    assert result.metrics["event_fidelity"] == 1.0
    assert result.metrics["causal_conformance"] == 1.0
    assert result.metrics["dcore_score"] == 1.0
    assert result.metrics["information_policy_conformance"]["overall"] == 1.0
    decisions = {item.decision_id: item for item in result.trace.decisions}
    events = {item.event_id: item for item in result.trace.events}
    assert result.trace.policy_commitments
    for commitment in result.trace.policy_commitments:
        decision = decisions[commitment.decision_id]
        assert (
            commitment.knowledge_snapshot_digest == decision.knowledge_snapshot.digest
        )
        assert events[commitment.commitment_id].logical_time <= decision.logical_time


def test_policy_commitment_is_a_function_of_snapshot_not_future_intent():
    net = compile_paper_petri_net("farm_wetjune_recheck")
    store = KnowledgeStore("operations")
    for index, key in enumerate(
        ("weather:spray_window_open", "soil:trafficable", "disease:confirmed")
    ):
        store.add(
            KnowledgeItem(
                item_id=f"item-{index}",
                fact_key=key,
                value=True,
                status=EpistemicStatus.CLAIMED,
                source_actor="field_intelligence",
                evidence_ids=(f"evidence-{index}",),
                observed_at=1.0,
                learned_at=2.0,
                valid_until=100.0,
                vector_clock={"field_intelligence": 1, "operations": 1, "world": 0},
            )
        )
    kwargs = dict(
        petri_net=net,
        actor_id="operations",
        phase="midseason",
        knowledge=store,
        world_time=3.0,
        channel_closed=False,
    )
    assert NativeDistributedSeasonRunner._information_policy_commitment(
        **kwargs
    ) == NativeDistributedSeasonRunner._information_policy_commitment(**kwargs)


def test_attempted_unsafe_action_is_violation_even_when_guard_blocks():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck", max_logical_steps=1000
        )
    )
    commitment = next(
        item
        for item in result.trace.policy_commitments
        if "execute" not in item.permitted_responses
    )
    decisions = tuple(
        decision.model_copy(
            update={
                "proposed_intent": AgentIntent(
                    kind=IntentKind.ACT,
                    action="TractorApp__apply_fungicide",
                    args={},
                ),
                "guard": GuardResult(
                    verdict=GuardVerdict.BLOCK,
                    reasons=("controlled policy test",),
                ),
            }
        )
        if decision.decision_id == commitment.decision_id
        else decision
        for decision in result.trace.decisions
    )
    trace = result.trace.model_copy(update={"decisions": decisions})
    metrics = evaluate_farm_dcore_v4(
        compile_paper_petri_net("farm_wetjune_recheck"), trace
    )
    policy = metrics["information_policy_conformance"]
    assert policy["overall"] < 1.0
    assert policy["attempted_unsafe_but_blocked"] == 1


def test_trace_validation_rejects_commitment_using_unavailable_knowledge():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck", max_logical_steps=1000
        )
    )
    first = result.trace.policy_commitments[0].model_copy(
        update={"supporting_item_ids": ("future-or-private-item",)}
    )
    trace = result.trace.model_copy(
        update={"policy_commitments": (first, *result.trace.policy_commitments[1:])}
    )
    with pytest.raises(ValueError, match="unavailable knowledge"):
        validate_trace(trace)


def test_neutral_review_packet_contains_no_prefilled_scientific_answers(tmp_path):
    files = export_neutral_review_packet(tmp_path)
    submission = json.loads(Path(files["submission"]).read_text(encoding="utf-8"))
    assert all(
        row["weight_budget"] is None
        for row in submission["module_annotations"].values()
    )
    assert all(
        row["scoring_class"] is None
        for row in submission["transition_annotations"].values()
    )
    packet_text = "\n".join(
        Path(path).read_text(encoding="utf-8") for path in files.values()
    )
    assert "3.6" not in packet_text
    assert "2.8" not in packet_text
    with pytest.raises(ValueError):
        validate_submission(submission)


def test_review_cli_and_confirmation_gate(tmp_path):
    output = tmp_path / "packet"
    result = CliRunner().invoke(main, ["review", "export", "--output-dir", str(output)])
    assert result.exit_code == 0, result.output
    validation = CliRunner().invoke(
        main,
        ["review", "validate", str(output / "reviewer_submission.template.json")],
    )
    assert validation.exit_code != 0
    with pytest.raises(ValueError, match="confirmed"):
        freeze_confirmed_review({"status": "adjudicated"})


def test_controlled_fixture_catalog_is_complete_and_seed_independent():
    fixtures = controlled_wetjune_policy_fixtures()
    assert len(fixtures) == 12
    assert len({item.fixture_id for item in fixtures}) == len(fixtures)
    assert all(item.communication_condition_independent for item in fixtures)
    assert len({item.exogenous_digest for item in fixtures}) == len(fixtures)
    net = compile_paper_petri_net("farm_wetjune_recheck")
    for fixture in fixtures:
        store = KnowledgeStore("operations")
        stale_key = fixture.facts.get("__stale_fact_key")
        for index, (fact_key, value) in enumerate(fixture.facts.items()):
            if fact_key.startswith("__"):
                continue
            store.add(
                KnowledgeItem(
                    item_id=f"{fixture.fixture_id}:{index}",
                    fact_key=fact_key,
                    value=value,
                    scope=(0, 63),
                    status=EpistemicStatus.OBSERVED,
                    source_actor="field_intelligence",
                    evidence_ids=(f"evidence:{fixture.fixture_id}:{index}",),
                    observed_at=1.0,
                    learned_at=1.0,
                    valid_until=0.0 if fact_key == stale_key else 100.0,
                    vector_clock={
                        "field_intelligence": 1,
                        "operations": 1,
                        "world": 0,
                    },
                )
            )
        commitment = NativeDistributedSeasonRunner._information_policy_commitment(
            petri_net=net,
            actor_id="operations",
            phase=(
                "harvest"
                if fixture.policy_id == "wetjune-harvest-policy"
                else "midseason"
            ),
            knowledge=store,
            world_time=10.0,
            channel_closed=False,
            deadline_closed=fixture.deadline_state == "closed",
        )
        assert commitment is not None
        verdicts = set(commitment["requirement_verdicts"].values())
        aggregate = (
            "false"
            if any(value.value == "false" for value in verdicts)
            else (
                "unknown"
                if any(value.value == "unknown" for value in verdicts)
                else "true"
            )
        )
        assert aggregate == fixture.requirement_state
        assert commitment["permitted_responses"] == fixture.expected_responses


def test_legacy_farm_scenarios_remain_on_readable_v3_contract():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_disease_drought", max_logical_steps=1000
        )
    )
    assert result.trace.schema_version == "dcore_trace_v3"
    assert result.metrics["metric_version"] == "dcore_eval_v3"


def test_neutral_template_digest_is_stable():
    template = wetjune_review_template()
    assert neutral_submission_template(template) == neutral_submission_template(
        wetjune_review_template()
    )


def test_independent_observation_chains_are_executable_concurrency():
    net = compile_paper_petri_net("farm_wetjune_recheck")
    transitions = {item.transition_id: item for item in net.transitions}
    artificial = [
        (source, target)
        for source, target in transition_dependencies(net)
        if transitions[source].kind == TransitionKind.OBSERVE
        and transitions[target].kind == TransitionKind.OBSERVE
        and transitions[source].module_id == transitions[target].module_id
    ]
    assert artificial == []
    assert len(net.metadata["relaxed_observation_components"]) == 12


def test_v4_profiles_report_module_acceptance_components_and_supersession():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck", max_logical_steps=1000
        )
    )
    assert len(result.metrics["module_profile"]) == 10
    for row in result.metrics["module_profile"].values():
        assert {
            "argument_acceptance",
            "spatial_fidelity",
            "timing_fidelity",
            "execution_fidelity",
        } <= set(row)
    superseding = [
        fact for fact in result.trace.fact_versions if fact.supersedes_version_ids
    ]
    assert superseding
    facts = {item.version_id: item for item in result.trace.fact_versions}
    assert all(
        facts[prior].fact_key == fact.fact_key
        for fact in superseding
        for prior in fact.supersedes_version_ids
    )


def test_review_packet_enumerates_compact_dependency_decisions():
    submission = neutral_submission_template(wetjune_review_template())
    dependencies = submission["direct_dependency_decisions"]
    assert dependencies
    assert all(
        row == {"decision": None, "rationale": None} for row in dependencies.values()
    )
    original = submission_attestation_digest(submission)
    submission["notes"] = "scientific judgment changed"
    assert submission_attestation_digest(submission) != original


def test_real_llm_and_paper_modes_are_fail_closed(tmp_path):
    with pytest.raises(ValidationError, match="paper_mode"):
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck", controller_mode="llm"
        )
    net = compile_paper_petri_net("farm_wetjune_recheck")
    path = tmp_path / "draft.json"
    path.write_text(net.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="paper mode rejects"):
        DistributedScenarioRunner().run(
            DistributedRunnerConfig(
                scenario_id="farm_wetjune_recheck",
                paper_mode=True,
                petri_spec_path=str(path),
            )
        )


def test_world_branch_is_committed_before_action_from_authoritative_fact(tmp_path):
    net = compile_paper_petri_net("farm_wetjune_recheck")
    candidates = net.transitions[:2]
    candidate_ids = {item.transition_id for item in candidates}
    transitions = tuple(
        item.model_copy(update={"required": False})
        if item.transition_id in candidate_ids
        else item
        for item in net.transitions
    )
    branch = WorldBranchSpec(
        branch_id="controlled-spray-window",
        commit_phase="field_prep",
        commitment_fact_keys=("weather:spray_window_open",),
        alternatives=(
            BranchAlternativeSpec(
                alternative_id="open",
                label="Sprayable",
                transition_ids=(candidates[0].transition_id,),
                required_transition_ids=(candidates[0].transition_id,),
                guards=(
                    DataGuardSpec(
                        guard_id="world-spray-open",
                        fact_key="weather:spray_window_open",
                        expected=True,
                        source="world",
                        branch_selector=True,
                    ),
                ),
            ),
            BranchAlternativeSpec(
                alternative_id="closed",
                label="Not sprayable",
                transition_ids=(candidates[1].transition_id,),
                required_transition_ids=(candidates[1].transition_id,),
                default=True,
            ),
        ),
    )
    branched = net.model_copy(
        update={"transitions": transitions, "exogenous_branches": (branch,)}
    )
    path = tmp_path / "branched.json"
    path.write_text(branched.model_dump_json(), encoding="utf-8")
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            petri_spec_path=str(path),
            max_logical_steps=1000,
        )
    )
    assert len(result.trace.world_branch_commitments) == 1
    commitment = result.trace.world_branch_commitments[0]
    decision_times = [
        item.logical_time
        for item in result.trace.decisions
        if item.season_phase == "field_prep"
    ]
    assert commitment.logical_time <= min(decision_times)
    assert commitment.specification_digest == stable_digest(
        branch.model_dump(mode="json")
    )
    evidence = {item.version_id: item for item in result.trace.fact_versions}
    assert all(
        evidence[item].authoritative for item in commitment.evidence_fact_version_ids
    )


def _synthetic_complete_submission(reviewer_id: str) -> dict:
    """Build a protocol fixture, not a domain-expert or paper specification."""

    template = wetjune_review_template()
    payload = neutral_submission_template(template)
    payload["reviewer"] = {
        "reviewer_id": reviewer_id,
        "expertise_role": "controlled-test-fixture",
        "reviewed_at_utc": "2026-01-01T00:00:00Z",
        "digest_attestation": None,
    }
    payload["no_final_model_results_seen"] = True
    for module in template.modules:
        payload["module_annotations"][module.module_id] = {
            "weight_budget": module.weight_budget,
            "required": module.required,
        }
    for transition in template.transition_templates:
        payload["transition_annotations"][transition.template_id] = {
            "scoring_class": transition.scoring_class.value,
            "within_module_weight": transition.within_module_weight,
            "required": transition.required,
        }
    for fact in template.fact_definitions:
        payload["fact_annotations"][fact.fact_key] = {
            "accepted": True,
            "valid_for_seconds": fact.engineering_valid_for,
            "notes": "no expiry" if fact.engineering_valid_for is None else "fixture",
        }
    payload["information_policies"] = {
        policy.policy_id: policy.model_copy(
            update={"deadline_world_time": 10**20}
        ).model_dump(mode="json")
        for policy in template.information_policies
    }
    for rows in (
        payload["numeric_acceptance_ranges"],
        payload["time_windows"],
        payload["scope_acceptance"],
    ):
        for row in rows.values():
            row["status"] = "accepted"
    for row in payload["scope_acceptance"].values():
        row["acceptance"] = {"minimum_iou": 1.0}
    for row in payload["direct_dependency_decisions"].values():
        row["decision"] = "retain"

    expanded = compile_paper_petri_net("farm_wetjune_recheck")
    first = expanded.transitions[0]
    second = next(
        item
        for item in expanded.transitions[1:]
        if item.template_id != first.template_id
    )
    for transition in (first, second):
        payload["transition_annotations"][transition.template_id]["required"] = False
    payload["world_branches"] = [
        WorldBranchSpec(
            branch_id="fixture-world-branch",
            commit_phase="field_prep",
            commitment_fact_keys=("weather:spray_window_open",),
            alternatives=(
                BranchAlternativeSpec(
                    alternative_id="open",
                    label="Open fixture path",
                    transition_ids=(first.transition_id,),
                    required_transition_ids=(first.transition_id,),
                    guards=(
                        DataGuardSpec(
                            guard_id="fixture-open",
                            fact_key="weather:spray_window_open",
                            expected=True,
                            source="world",
                            branch_selector=True,
                        ),
                    ),
                ),
                BranchAlternativeSpec(
                    alternative_id="closed",
                    label="Closed fixture path",
                    transition_ids=(second.transition_id,),
                    required_transition_ids=(second.transition_id,),
                    default=True,
                ),
            ),
        ).model_dump(mode="json")
    ]
    fungicide = next(
        item
        for item in expanded.transitions
        if item.action == "TractorApp__apply_fungicide"
    )
    harvest = next(
        item for item in expanded.transitions if item.action == "TractorApp__harvest"
    )
    payload["choice_groups"] = [
        ChoiceGroupSpec(
            choice_id="fixture-fungicide-choice",
            policy_id="wetjune-fungicide-policy",
            response_transition_ids=(
                fungicide.transition_id,
                "policy:midseason:abstain",
            ),
        ).model_dump(mode="json"),
        ChoiceGroupSpec(
            choice_id="fixture-harvest-choice",
            policy_id="wetjune-harvest-policy",
            response_transition_ids=(
                harvest.transition_id,
                "policy:harvest:abstain",
            ),
        ).model_dump(mode="json"),
    ]
    payload["reviewer"]["digest_attestation"] = submission_attestation_digest(payload)
    return payload


def test_synthetic_review_protocol_can_freeze_but_is_not_checked_in():
    left = _synthetic_complete_submission("fixture-expert-a")
    right = _synthetic_complete_submission("fixture-expert-b")
    validate_submission(left)
    validate_submission(right)
    resolved = copy.deepcopy(left)
    adjudication = adjudicate_reviews(
        left,
        right,
        resolved,
        {
            "reviewer_id": "fixture-adjudicator",
            "expertise_role": "controlled-test-fixture",
            "reviewed_at_utc": "2026-01-02T00:00:00Z",
            "no_final_model_results_seen": True,
            "resolutions": {},
        },
    )
    confirmation = {
        "reviewer_id": "fixture-third-expert",
        "expertise_role": "controlled-test-fixture",
        "confirmed_at_utc": "2026-01-03T00:00:00Z",
        "confirmed_digest": stable_digest(resolved),
        "no_final_model_results_seen": True,
        "no_yield_correlation_tuning": True,
        "digest_attestation": None,
    }
    attested = copy.deepcopy(confirmation)
    attested["digest_attestation"] = None
    confirmation["digest_attestation"] = stable_digest(attested)
    confirmed = confirm_adjudication(adjudication, confirmation)
    frozen_template, frozen_net = freeze_confirmed_review(confirmed)
    assert frozen_template["annotation_status"] == "frozen"
    assert frozen_net["metadata"]["paper_eligible"] is True
    assert frozen_net["metadata"]["reviewed_acceptance_annotations"] is True


def _synthetic_team_submission(tmp_path: Path, reviewer_id: str) -> dict:
    files = export_neutral_review_packet(tmp_path / reviewer_id)
    payload = json.loads(Path(files["team_review"]).read_text(encoding="utf-8"))
    payload["reviewer"] = {
        "reviewer_id": reviewer_id,
        "expertise_role": "controlled-test-fixture",
        "reviewed_at_utc": "2026-01-01T00:00:00Z",
        "no_final_model_results_seen": True,
        "no_yield_correlation_tuning": True,
        "digest_attestation": None,
    }
    for team_id, row in payload["team_decisions"].items():
        for field in (
            "role_partition_accepted",
            "aggregate_capability_preserved",
            "tool_ownership_accepted",
            "observation_ownership_accepted",
            "time_authority_accepted",
            "topology_accepted",
        ):
            row[field] = True
        row["rationale"] = "Controlled test acceptance."
        if team_id == "wetjune_3agent":
            row["communication_paths"] = {
                "agronomic_evidence": ["scouting", "agronomy", "operations"]
            }
        elif team_id == "wetjune_4agent":
            row["communication_paths"] = {
                "agronomic_evidence": ["scouting", "agronomy", "operations"],
                "resource_readiness": ["resource_management", "operations"],
            }
        for module_id in row["module_weight_budgets"]:
            row["module_weight_budgets"][module_id] = 1.0
    payload["reviewer"]["digest_attestation"] = submission_attestation_digest(
        payload
    )
    return payload


def test_team_review_protocol_validates_compares_and_freezes(tmp_path):
    left = _synthetic_team_submission(tmp_path, "fixture-team-expert-a")
    right = _synthetic_team_submission(tmp_path, "fixture-team-expert-b")
    assert validate_team_submission(left)["team_count"] == 3
    assert compare_team_submissions(left, right)["unresolved_items"] == []
    adjudication = adjudicate_team_reviews(
        left,
        right,
        copy.deepcopy(left),
        {
            "reviewer_id": "fixture-team-adjudicator",
            "expertise_role": "controlled-test-fixture",
            "reviewed_at_utc": "2026-01-02T00:00:00Z",
            "no_final_model_results_seen": True,
            "no_yield_correlation_tuning": True,
            "resolutions": {},
        },
    )
    confirmation = {
        "reviewer_id": "fixture-team-third-expert",
        "expertise_role": "controlled-test-fixture",
        "confirmed_at_utc": "2026-01-03T00:00:00Z",
        "confirmed_digest": adjudication["resolved_specification_digest"],
        "no_final_model_results_seen": True,
        "no_yield_correlation_tuning": True,
        "digest_attestation": None,
    }
    confirmation["digest_attestation"] = stable_digest(confirmation)
    confirmed = confirm_adjudication(adjudication, confirmation)

    farm_left = _synthetic_complete_submission("fixture-farm-expert-a")
    farm_right = _synthetic_complete_submission("fixture-farm-expert-b")
    farm_adjudication = adjudicate_reviews(
        farm_left,
        farm_right,
        copy.deepcopy(farm_left),
        {
            "reviewer_id": "fixture-farm-adjudicator",
            "expertise_role": "controlled-test-fixture",
            "reviewed_at_utc": "2026-01-02T00:00:00Z",
            "no_final_model_results_seen": True,
            "resolutions": {},
        },
    )
    farm_confirmation = {
        "reviewer_id": "fixture-farm-third-expert",
        "expertise_role": "controlled-test-fixture",
        "confirmed_at_utc": "2026-01-03T00:00:00Z",
        "confirmed_digest": farm_adjudication["resolved_specification_digest"],
        "no_final_model_results_seen": True,
        "digest_attestation": None,
    }
    farm_confirmation["digest_attestation"] = stable_digest(farm_confirmation)
    _, frozen_net = freeze_confirmed_review(
        confirm_adjudication(farm_adjudication, farm_confirmation)
    )
    bundle = freeze_confirmed_team_review(confirmed, frozen_net)
    assert len(bundle["teams"]) == 3
    assert len(bundle["role_refinements"]) == 2
    assert all(
        item["team_spec"]["expert_review_status"] == "confirmed"
        for item in bundle["teams"]
    )
