from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from are.simulation.agents.agent_builder import AgentBuilder
from are.simulation.agents.agent_config_builder import AgentConfigBuilder
from are.simulation.agents.llm.llm_engine import LLMEngine
from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.distributed.cli import main
from are.simulation.distributed.controllers import (
    FarmAREBaseAgentController,
    OracleCeilingController,
    OracleCeilingCoordinator,
)
from are.simulation.distributed.evaluator_v3 import evaluate_farm_dcore
from are.simulation.distributed.experiments import (
    aggregate_rows,
    evaluate_legacy_farmare_trace,
    load_manifest,
    resolve_manifest,
    write_normalized_outputs,
)
from are.simulation.distributed.farm_mutants import (
    attribution_confusion,
    build_farm_mutant_suite,
)
from are.simulation.distributed.models import (
    ActorSpec,
    DistributedRunnerConfig,
    DistributedTrace,
    EpistemicStatus,
    EventKind,
    FreeTextEnvelope,
    KnowledgeItem,
    LocalView,
    TraceEvent,
    stable_digest,
)
from are.simulation.distributed.mutants import swap_concurrent
from are.simulation.distributed.native_season import (
    NativeDistributedSeasonRunner,
    _farm_outcome,
)
from are.simulation.distributed.petri import (
    ArcSpec,
    BranchAlternativeSpec,
    DataGuardSpec,
    ExogenousBranchSpec,
    GuardOperator,
    PetriNetSpec,
    PlaceSpec,
    TransitionKind,
    TransitionSpec,
    unfold_petri_net,
    validate_petri_net,
)
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.distributed.tool_gateway import RoleToolGateway
from are.simulation.environment import Environment, EnvironmentConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    ACTORS,
    FARM_SCENARIOS,
    compile_native_petri_net,
    create_native_scenario,
)


@pytest.mark.parametrize("scenario_id", tuple(FARM_SCENARIOS))
def test_native_full_season_oracles_are_perfect_and_finish_storage(scenario_id):
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(scenario_id=scenario_id, max_logical_steps=1000)
    )
    assert result.metrics["event_fidelity"] == 1.0
    assert result.metrics["causal_conformance"] == 1.0
    assert result.metrics["dcore_score"] == 1.0
    assert result.metrics["petri_token_fitness"] == 1.0
    assert result.trace.outcome["harvest_complete"] is True
    assert result.trace.outcome["storage_complete"] is True
    assert result.trace.outcome["marketable_yield_kg"] > 0
    assert result.trace.outcome["farmare_task_validation"]["success"] is True
    assert all(
        "transition_id" not in event.payload
        and "enabled_action" not in event.payload
        and "enabled_args" not in event.payload
        for event in result.trace.events
    )
    assert result.trace.configuration["controller_task_briefing_policy"] == (
        "nonprocedural_public_task_contract_v1"
    )


@pytest.mark.parametrize("scenario_id", tuple(FARM_SCENARIOS))
def test_distributed_ceiling_reproduces_native_human_oracle_yield(scenario_id):
    """The distributed ceiling must not silently change FarmARE mechanics."""
    scenario = create_native_scenario(scenario_id, world_seed=0)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    initial_inventory = dict(farm_world.get_state().get("inventory", {}))
    environment = Environment(
        config=EnvironmentConfig(
            start_time=scenario.start_time,
            duration=scenario.duration,
            time_increment_in_seconds=scenario.time_increment_in_seconds,
            oracle_mode=True,
            queue_based_loop=True,
            exit_when_no_events=True,
            verbose=False,
        )
    )
    try:
        environment.run(scenario, wait_for_end=False)
        environment.join()
        native_outcome = _farm_outcome(farm_world, initial_inventory)
    finally:
        environment.stop()

    distributed_outcome = (
        DistributedScenarioRunner()
        .run(DistributedRunnerConfig(scenario_id=scenario_id, max_logical_steps=1000))
        .trace.outcome
    )
    assert distributed_outcome["biological_yield_kg"] == pytest.approx(
        native_outcome["biological_yield_kg"], abs=1e-6
    )
    assert distributed_outcome["marketable_yield_kg"] == pytest.approx(
        native_outcome["marketable_yield_kg"], abs=1e-6
    )
    assert distributed_outcome["harvest_complete"] == native_outcome["harvest_complete"]
    assert distributed_outcome["storage_complete"] == native_outcome["storage_complete"]


@pytest.mark.parametrize("scenario_id", tuple(FARM_SCENARIOS))
def test_model_task_contract_does_not_expose_procedural_oracle(scenario_id):
    briefing = NativeDistributedSeasonRunner._task_briefing(scenario_id)
    forbidden = (
        "请按以下步骤",
        "3.6 L/ridge",
        "2.8 L/ridge",
        "3.8 L/ridge",
        "hours=0.8",
        "seed_spacing_cm",
        "target_moisture_pct",
    )
    assert all(token not in briefing for token in forbidden)


def test_event_fidelity_counts_each_applicable_transition_once():
    runner = DistributedScenarioRunner()
    result = runner.run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck", max_logical_steps=1000
        )
    )
    net = compile_native_petri_net("farm_wetjune_recheck")
    applicable = [
        transition
        for transition in net.transitions
        if transition.actor_id != "world" and transition.required
    ]
    required_action = next(
        event
        for event in result.trace.events
        if event.kind == EventKind.ACTION and event.status == "ok"
    )
    mutant = result.trace.model_copy(
        update={
            "events": tuple(
                event
                for event in result.trace.events
                if event.event_id != required_action.event_id
            )
        }
    )
    metrics = evaluate_farm_dcore(net, mutant)
    assert metrics["event_fidelity"] == pytest.approx(
        (len(applicable) - 1) / len(applicable), abs=1e-6
    )


def test_required_transition_has_lexicographic_priority_over_optional_match():
    net = PetriNetSpec(
        net_id="required-first",
        scenario_id="required-first",
        actors=("operations",),
        places=(),
        transitions=(
            TransitionSpec(
                transition_id="optional-copy",
                label="optional copy",
                actor_id="operations",
                action="apply",
                phase="treatment",
                required=False,
            ),
            TransitionSpec(
                transition_id="required-action",
                label="required action",
                actor_id="operations",
                action="apply",
                phase="treatment",
                required=True,
            ),
        ),
        arcs=(),
    )
    trace = DistributedTrace(
        run_id="required-first-run",
        task_id="required-first",
        actors=("operations",),
        events=(
            TraceEvent(
                event_id="action-1",
                kind=EventKind.ACTION,
                actor_id="operations",
                local_sequence=1,
                logical_time=1.0,
                world_time=1.0,
                vector_clock={"operations": 1, "world": 0},
                action="apply",
            ),
        ),
    )
    metrics = evaluate_farm_dcore(net, trace)
    assert metrics["coverage"] == 1.0
    assert metrics["event_fidelity"] == 1.0
    assert set(metrics["matched_transitions"]) == {"required-action"}
    assert metrics["metric_profile"]["event_matching"] == (
        "required_first_lexicographic_max_weight_v1"
    )


def test_adversarial_controller_arguments_are_executed_without_oracle_substitution():
    scenario_id = "farm_wetjune_recheck"
    baseline = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id=scenario_id,
            enforcement_mode="off",
            max_logical_steps=1000,
        )
    )
    scenario = create_native_scenario(scenario_id, world_seed=0)
    net = compile_native_petri_net(scenario_id)
    steps = list(NativeDistributedSeasonRunner._oracle_ceiling_steps(scenario, net))
    changed = 0
    for index, (actor, phase, intent) in enumerate(steps):
        if intent.action != "TractorApp__apply_fungicide":
            continue
        steps[index] = (
            actor,
            phase,
            intent.model_copy(
                update={
                    "args": {**intent.args, "start_ridge": 0, "end_ridge": 9},
                    "scope": (0, 9),
                }
            ),
        )
        changed += 1
    assert changed > 0
    coordinator = OracleCeilingCoordinator(steps)
    controllers = {
        actor: OracleCeilingController(actor, coordinator) for actor in ACTORS
    }
    adversarial = NativeDistributedSeasonRunner(controllers=controllers).run(
        DistributedRunnerConfig(
            scenario_id=scenario_id,
            enforcement_mode="off",
            max_logical_steps=1000,
        )
    )
    executed = [
        event
        for event in adversarial.trace.events
        if event.kind == EventKind.ACTION
        and event.action == "TractorApp__apply_fungicide"
        and event.status == "ok"
    ]
    assert executed
    assert all(
        event.args["start_ridge"] == 0 and event.args["end_ridge"] == 9
        for event in executed
    )
    assert all(event.farmare_event_id for event in executed)
    assert (
        adversarial.trace.outcome["marketable_yield_kg"]
        != baseline.trace.outcome["marketable_yield_kg"]
    )


def test_role_gateway_rejects_cross_role_calls_and_executes_intents_once():
    scenario = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    environment = Environment(
        config=EnvironmentConfig(
            start_time=scenario.start_time,
            duration=scenario.duration,
            time_increment_in_seconds=scenario.time_increment_in_seconds,
            oracle_mode=False,
            verbose=False,
        )
    )
    environment.register_apps(scenario.apps or [])
    gateway = RoleToolGateway(environment, scenario.get_tools())
    before = len(environment.event_log.list_view())
    with pytest.raises(PermissionError):
        gateway.execute(
            actor_id="field_intelligence",
            intent_id="unauthorized-intent",
            action="SystemApp__advance_time",
            arguments={"days": 1},
        )
    assert len(environment.event_log.list_view()) == before
    first = gateway.execute(
        actor_id="field_intelligence",
        intent_id="stable-observation-intent",
        action="WeatherApp__get_current_weather",
        arguments={},
    )
    after_first = len(environment.event_log.list_view())
    duplicate = gateway.execute(
        actor_id="field_intelligence",
        intent_id="stable-observation-intent",
        action="WeatherApp__get_current_weather",
        arguments={},
    )
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.completed_event == first.completed_event
    assert len(environment.event_log.list_view()) == after_first


@pytest.mark.parametrize("scenario_id", tuple(FARM_SCENARIOS))
def test_petri_oracles_are_safe_and_model_explicit_handoffs(scenario_id):
    net = compile_native_petri_net(scenario_id)
    report = validate_petri_net(net)
    assert report["one_safe"] is True
    assert report["terminal_reachable"] is True
    assert report["validation_mode"] == "symbolic_occurrence_net"
    assert any(t.kind == TransitionKind.SEND for t in net.transitions)
    assert any(t.kind == TransitionKind.RECEIVE for t in net.transitions)
    assert any(t.kind == TransitionKind.WAIT for t in net.transitions)
    assert all(t.guards for t in net.transitions if t.high_impact)


def test_petri_validator_rejects_production_into_marked_place():
    unsafe = PetriNetSpec(
        net_id="unsafe_marked_output",
        scenario_id="test",
        actors=("agent",),
        places=(
            PlaceSpec(place_id="source", initially_marked=True),
            PlaceSpec(place_id="already_marked", initially_marked=True, terminal=True),
        ),
        transitions=(
            TransitionSpec(
                transition_id="produce",
                label="produce",
                actor_id="agent",
                action="produce",
                phase="test",
            ),
        ),
        arcs=(
            ArcSpec(arc_id="consume", source="source", target="produce"),
            ArcSpec(arc_id="produce_again", source="produce", target="already_marked"),
        ),
    )
    with pytest.raises(ValueError, match="violates 1-safety"):
        validate_petri_net(unsafe)


def test_exogenous_branch_is_committed_before_trace_alignment():
    net = PetriNetSpec(
        net_id="branch-test",
        scenario_id="branch-test",
        actors=("operations",),
        places=(
            PlaceSpec(place_id="start", initially_marked=True),
            PlaceSpec(place_id="done", terminal=True),
        ),
        transitions=(
            TransitionSpec(
                transition_id="treat",
                label="treat",
                actor_id="operations",
                action="treat",
                phase="midseason",
                required=False,
            ),
            TransitionSpec(
                transition_id="abstain",
                label="abstain",
                actor_id="operations",
                action="abstain",
                phase="midseason",
                required=False,
            ),
        ),
        arcs=(
            ArcSpec(arc_id="start-treat", source="start", target="treat"),
            ArcSpec(arc_id="treat-done", source="treat", target="done"),
            ArcSpec(arc_id="start-abstain", source="start", target="abstain"),
            ArcSpec(arc_id="abstain-done", source="abstain", target="done"),
        ),
        exogenous_branches=(
            ExogenousBranchSpec(
                branch_id="disease-policy",
                alternatives=(
                    BranchAlternativeSpec(
                        alternative_id="disease-present",
                        label="treat",
                        transition_ids=("treat",),
                        guards=(
                            DataGuardSpec(
                                guard_id="world-disease",
                                fact_key="world:disease",
                                operator=GuardOperator.EQ,
                                expected=True,
                                source="world",
                                branch_selector=True,
                            ),
                        ),
                    ),
                    BranchAlternativeSpec(
                        alternative_id="no-disease",
                        label="abstain",
                        transition_ids=("abstain",),
                        default=True,
                    ),
                ),
            ),
        ),
    )
    present = unfold_petri_net(net, world_context={"world:disease": True})
    absent = unfold_petri_net(net, world_context={"world:disease": False})
    assert present.applicable_transition_ids == ("treat",)
    assert absent.applicable_transition_ids == ("abstain",)
    assert present.branch_commitments[0].alternative_id == "disease-present"
    assert absent.branch_commitments[0].alternative_id == "no-disease"
    with pytest.raises(ValueError, match="contradicts world facts"):
        unfold_petri_net(
            net,
            world_context={"world:disease": False},
            committed_branches={"disease-policy": "disease-present"},
        )


def test_faults_do_not_change_exogenous_world_and_enforcement_never_substitutes_recovery():
    runner = DistributedScenarioRunner()
    common = dict(scenario_id="farm_wetjune_recheck", max_logical_steps=1000)
    oracle = runner.run(DistributedRunnerConfig(**common))
    audit = runner.run(
        DistributedRunnerConfig(
            **common, handoff_mode="causal", enforcement_mode="audit", fault="drop"
        )
    )
    enforced = runner.run(
        DistributedRunnerConfig(
            **common,
            handoff_mode="causal",
            enforcement_mode="enforce",
            fault="drop",
            max_deferrals=0,
        )
    )
    different_world = runner.run(DistributedRunnerConfig(**common, world_seed=1))
    assert (
        oracle.trace.outcome["exogenous_world_digest"]
        == audit.trace.outcome["exogenous_world_digest"]
        == enforced.trace.outcome["exogenous_world_digest"]
    )
    assert oracle.trace.outcome["exogenous_world_days"] >= 160
    assert (
        oracle.trace.outcome["exogenous_world_digest"]
        != different_world.trace.outcome["exogenous_world_digest"]
    )
    assert audit.metrics["dcore_score"] < 1.0
    assert {item["primary"] for item in audit.attribution} == {"transit_gap"}
    assert oracle.metrics["synchronization_lag"]["sent_fact_versions"] > 0
    assert oracle.metrics["synchronization_lag"]["never_received_versions"] == []
    # This dropped handoff is later superseded/re-sent, so the fact-version
    # metric correctly records eventual first uptake instead of counting every
    # delivery copy as another lag observation.
    assert (
        audit.metrics["synchronization_lag"]["count"]
        <= audit.metrics["synchronization_lag"]["sent_fact_versions"]
    )
    assert enforced.trace.outcome["blocked_write_count"] > 0
    assert enforced.trace.outcome["deferred_write_count"] == 0
    assert enforced.trace.outcome["recovered_write_count"] == 0
    assert any(
        event.kind == EventKind.ACTION
        and event.status in {"blocked", "deferred"}
        and event.payload.get("blocked_before_farmare") is True
        and event.farmare_event_id is None
        for event in enforced.trace.events
    )
    assert enforced.metrics["event_fidelity"] < 1.0
    # LGG is a signed diagnostic, not a forced-positive quantity. This run's
    # downstream local tool errors reduce the operations projection as well as
    # global conformance, so it is not the canonical locally-correct/global-
    # incorrect fixture.
    assert enforced.metrics["all_agents_locally_correct"] is False
    local_mean = sum(
        row["local_dcore"] for row in enforced.metrics["local"].values()
    ) / len(enforced.metrics["local"])
    assert enforced.metrics["local_global_gap"] == pytest.approx(
        local_mean - enforced.metrics["dcore_score"], abs=1e-6
    )
    assert all(
        profile["evaluation_source"] == "predecision_snapshot_reconstruction_v3"
        for profile in enforced.metrics["local"].values()
    )


def test_faults_can_target_stable_handoff_versions():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            fault="drop",
            fault_target_ids=("handoff:harvest:v1",),
            enforcement_mode="audit",
            max_logical_steps=1000,
        )
    )
    dropped = [
        event.message_id
        for event in result.trace.events
        if event.kind == EventKind.MESSAGE_SEND and event.status == "dropped"
    ]
    assert dropped == ["handoff:harvest:v1"]


def test_reliable_free_text_is_not_treated_as_verified_causal_evidence():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            handoff_mode="free_text",
            enforcement_mode="off",
            fault="none",
            max_logical_steps=1000,
        )
    )
    assert result.metrics["causal_conformance"] < 1.0
    assert result.metrics["dcore_score"] < 1.0
    assert {item["primary"] for item in result.attribution} == {"unverifiable_handoff"}


def test_mock_agents_use_isolated_decision_contexts_for_full_season():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck",
            controller_mode="mock_llm",
            max_logical_steps=1000,
        )
    )
    assert result.metrics["dcore_score"] == 1.0
    assert result.trace.configuration["controller_adapter"] == (
        "native_base_agent_step_v1"
    )
    assert all(decision.llm_input_log_id for decision in result.trace.decisions)
    assert all(decision.response_id for decision in result.trace.decisions)
    assert all(decision.model_provider == "mock" for decision in result.trace.decisions)
    assert all(
        set(decision.prompt_item_ids) <= set(decision.knowledge_snapshot.item_ids)
        for decision in result.trace.decisions
    )
    assert all(
        set(decision.prompt_message_ids)
        <= set(decision.knowledge_snapshot.inbox_frontier)
        for decision in result.trace.decisions
    )
    operations = [
        decision
        for decision in result.trace.decisions
        if decision.actor_id == "operations"
    ]
    assert all(
        not item_id.startswith("evidence:")
        for decision in operations
        for item_id in decision.knowledge_snapshot.item_ids
    )


def test_native_base_agent_step_adapter_captures_without_executing():
    class OneStepEngine(LLMEngine):
        def __init__(self):
            super().__init__("offline-base-agent")
            self.prompts = []

        def chat_completion(self, messages, stop_sequences=[], **kwargs):
            self.prompts.append(messages)
            return (
                "Thought: inspect the permitted local state.\nAction:\n"
                '{"action":"FarmWorldApp__get_state","action_input":{}}'
                "<end_action>",
                {
                    "model_name": self.model_name,
                    "model_provider": "mock",
                    "response_id": "offline-response-1",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            )

    engine = OneStepEngine()

    class OfflineEngineBuilder:
        def create_engine(self, engine_config, mock_responses=None):
            return engine

    environment = Environment(
        config=EnvironmentConfig(
            start_time=0.0,
            duration=86400.0,
            time_increment_in_seconds=60,
            oracle_mode=False,
            verbose=False,
        )
    )
    agent_config = AgentConfigBuilder().build("farm_planner_executor")
    wrapper = AgentBuilder(OfflineEngineBuilder()).build(agent_config, env=environment)
    controller = FarmAREBaseAgentController(wrapper, max_decisions=3, max_model_calls=3)
    actor = ActorSpec(
        actor_id="field_intelligence",
        role="observe",
        permitted_actions=("FarmWorldApp__get_state",),
        tool_schemas={
            "FarmWorldApp__get_state": {
                "description": "read state",
                "arguments": {},
                "observation": True,
                "write": False,
                "high_impact": False,
            }
        },
    )
    fact = KnowledgeItem(
        item_id="fact:local:v1",
        fact_key="soil:trafficable",
        value=True,
        status=EpistemicStatus.OBSERVED,
        source_actor=actor.actor_id,
        observed_at=1.0,
        learned_at=1.0,
    )
    delivered = FreeTextEnvelope(
        message_id="delivered:m1",
        sender="operations",
        recipient="field_intelligence",
        text="delivered local evidence",
    )
    view = LocalView(
        actor=actor,
        logical_time=1.0,
        world_time=1.0,
        knowledge=(fact,),
        inbox=(delivered,),
        vector_clock={"field_intelligence": 1},
    )
    controller.initialize(actor, view)
    intent = controller.decide(view)
    assert intent.kind.value == "observe"
    assert intent.action == "FarmWorldApp__get_state"
    assert intent.args == {}
    assert intent.llm_input_log_id
    assert controller.last_prompt_item_ids == ("fact:local:v1",)
    assert controller.last_prompt_message_ids == ("delivered:m1",)
    assert controller.last_metadata["response_id"] == "offline-response-1"
    assert controller.last_metadata["adapter"] == "native_base_agent_step_v1"
    assert controller.last_prompt_digest == stable_digest(engine.prompts[-1])
    assert "soil:trafficable" in json.dumps(engine.prompts)
    assert "Planning scaffold" in json.dumps(engine.prompts)


def test_valid_concurrent_permutation_is_metric_invariant():
    runner = DistributedScenarioRunner()
    result = runner.run(
        DistributedRunnerConfig(
            scenario_id="farm_three_cultivar", max_logical_steps=1000
        )
    )
    occurrence = result.metrics["occurrence_net"]
    matches = result.metrics["matched_transitions"]
    by_id = {event.event_id: event for event in result.trace.events}
    chosen = None
    for left, right in occurrence["concurrent_pairs"]:
        if left not in matches or right not in matches:
            continue
        left_event = by_id[matches[left]["observed_event_id"]]
        right_event = by_id[matches[right]["observed_event_id"]]
        if (
            left_event.kind == EventKind.ACTION
            and right_event.kind == EventKind.ACTION
            and left_event.actor_id != right_event.actor_id
        ):
            chosen = (left_event.event_id, right_event.event_id)
            break
    assert chosen is not None
    permuted, _ = swap_concurrent(result.trace, *chosen)
    metrics = evaluate_farm_dcore(
        compile_native_petri_net("farm_three_cultivar"), permuted
    )
    assert metrics["dcore_score"] == result.metrics["dcore_score"]
    assert metrics["po_pair_agreement"] == result.metrics["po_pair_agreement"]


def test_wrong_ridge_scope_reduces_spatial_fidelity():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_three_cultivar", max_logical_steps=1000
        )
    )
    net = compile_native_petri_net("farm_three_cultivar")
    scoped = next(
        transition
        for transition in net.transitions
        if transition.scope is not None
        and transition.transition_id in result.metrics["matched_transitions"]
    )
    observed_id = result.metrics["matched_transitions"][scoped.transition_id][
        "observed_event_id"
    ]
    events = tuple(
        event.model_copy(update={"payload": {**event.payload, "scope": (100, 101)}})
        if event.event_id == observed_id
        else event
        for event in result.trace.events
    )
    metrics = evaluate_farm_dcore(
        net, result.trace.model_copy(update={"events": events})
    )
    assert metrics["spatial_fidelity"] < 1.0
    assert metrics["event_fidelity"] < 1.0


def test_cli_reevaluation_and_matrix_resolution(tmp_path):
    run_dir = tmp_path / "run"
    run = CliRunner().invoke(
        main,
        [
            "run",
            "--scenario-id",
            "farm_wetjune_recheck",
            "--controller-mode",
            "mock_llm",
            "--output-dir",
            str(run_dir),
        ],
    )
    assert run.exit_code == 0, run.output
    assert (run_dir / "exogenous_world.json").exists()
    assert (run_dir / "expert_annotation_manifest.json").exists()
    evaluation = CliRunner().invoke(
        main, ["evaluate", str(run_dir / "trace.dcore_trace_v4.json")]
    )
    assert evaluation.exit_code == 0, evaluation.output
    reevaluated = json.loads(evaluation.output)
    assert reevaluated["metric_version"] == "dcore_eval_v4"
    frozen = json.loads(
        (run_dir / "metrics.dcore_eval_v4.json").read_text(encoding="utf-8")
    )
    assert reevaluated == frozen

    manifest = load_manifest("are/simulation/distributed/configs/farm_dcore_smoke.yaml")
    resolved = resolve_manifest(manifest)
    assert len(resolved) == 3
    assert len({row["pair_id"] for row in resolved}) == 1
    assert all(row["model_configuration_id"] for row in resolved)
    dry_run = CliRunner().invoke(
        main,
        [
            "matrix",
            "are/simulation/distributed/configs/farm_dcore_smoke.yaml",
            "--output-dir",
            str(tmp_path / "matrix"),
            "--dry-run",
        ],
    )
    assert dry_run.exit_code == 0, dry_run.output
    assert json.loads(dry_run.output)["runnable"] == 3


def test_aggregation_preserves_negative_yield_shortfall_and_failure_rates():
    rows = [
        {
            "pair_id": "p1",
            "scenario": "farm_wetjune_recheck",
            "condition": "scripted_petri_oracle",
            "dcore_score": 1.0,
            "marketable_yield_kg": 100.0,
            "biological_yield_kg": 110.0,
            "marketable_yield_shortfall": 0.0,
            "success": True,
            "safety_success": True,
        },
        {
            "pair_id": "p1",
            "scenario": "farm_wetjune_recheck",
            "condition": "causal_enforce",
            "dcore_score": 0.9,
            "marketable_yield_kg": 105.0,
            "biological_yield_kg": 115.0,
            "marketable_yield_shortfall": -0.05,
            "success": True,
            "safety_success": True,
        },
    ]
    report = aggregate_rows(rows)
    assert report["paired_comparisons"]
    assert "paired_cluster_bootstrap_95_ci" in report["paired_comparisons"][0]
    assert report["metric_yield_calibration"]["primary"]["n"] == 2
    assert "dcore_score" in report["metric_yield_calibration"]["by_metric"][
        "marketable_yield_shortfall"
    ]
    assert report["bootstrap_cluster"] == "scenario_world_seed"


def test_legacy_baseline_import_requires_and_preserves_outcome_sidecar(tmp_path):
    trace = tmp_path / "legacy.json"
    outcome = tmp_path / "legacy.outcome.json"
    trace.write_text(
        json.dumps({"version": "are_simulation_v1", "world_logs": []}),
        encoding="utf-8",
    )
    outcome.write_text(
        json.dumps(
            {
                "success": True,
                "safety_success": True,
                "biological_yield_kg": 9000.0,
                "marketable_yield_kg": 8800.0,
                "harvest_complete": True,
                "storage_complete": True,
            }
        ),
        encoding="utf-8",
    )
    row = evaluate_legacy_farmare_trace(
        trace,
        "farm_wetjune_recheck",
        condition="single_agent_direct",
        pair_id="paired",
        outcome_path=outcome,
    )
    assert row["success"] is True
    assert row["marketable_yield_kg"] == 8800.0
    assert row["knowledge_metrics_available"] is False


def test_normalized_paper_outputs_include_phase_module_ridge_and_agent_rows(
    tmp_path,
):
    row = {
        "run_id": "r1",
        "scenario": "farm_wetjune_recheck",
        "condition": "causal",
        "phase_profile": {"midseason": {"dcore_score": 0.8}},
        "module_profile": {"disease": {"event_fidelity": 0.7}},
        "per_ridge_yield": [{"ridge_id": 20, "biological_yield_g_m2": 450.0}],
        "per_agent_telemetry": {"operations": {"total_tokens": 123}},
    }
    write_normalized_outputs([row], tmp_path)
    assert "midseason" in (tmp_path / "phase_metrics.csv").read_text()
    assert "disease" in (tmp_path / "module_metrics.csv").read_text()
    assert "450.0" in (tmp_path / "ridge_yields.csv").read_text()
    assert "operations" in (tmp_path / "agent_telemetry.csv").read_text()


def test_manifest_crosses_independent_applied_seeds():
    rows = resolve_manifest(
        {
            "schema_version": "farm_dcore_matrix_v1",
            "scenarios": ["farm_wetjune_recheck"],
            "world_seeds": [3],
            "scheduler_seeds": [5, 6],
            "model_seeds": [7, 8],
            "fault_seeds": [9],
            "conditions": [
                {
                    "id": "local",
                    "execution": "dcore",
                    "faults": ["none", "drop"],
                }
            ],
        }
    )
    assert len(rows) == 8
    assert len([row for row in rows if row["fault"] == "none"]) == 4
    assert len([row for row in rows if row["fault"] == "drop"]) == 4
    assert len({row["pair_id"] for row in rows}) == 4


def test_manifest_rejects_pseudoreplicated_named_fault_seeds():
    with pytest.raises(ValueError, match="pseudo-replication"):
        resolve_manifest(
            {
                "schema_version": "farm_dcore_matrix_v1",
                "scenarios": ["farm_wetjune_recheck"],
                "fault_seeds": [9, 10],
            }
        )


def test_controlled_farm_mutants_validate_metrics_and_attribution():
    result = DistributedScenarioRunner().run(
        DistributedRunnerConfig(
            scenario_id="farm_wetjune_recheck", max_logical_steps=1000
        )
    )
    net = compile_native_petri_net("farm_wetjune_recheck")
    cases = build_farm_mutant_suite(result.trace, net, unfold_petri_net(net))
    assert len(cases) >= 17
    expected_and_predicted = []
    for case in cases:
        metrics = evaluate_farm_dcore(net, case.trace)
        for metric in case.invariant_metrics:
            assert metrics[metric] == result.metrics[metric], (case.name, metric)
        for metric in case.decreasing_metrics:
            assert metrics[metric] < result.metrics[metric], (case.name, metric)
        if case.expected_attribution:
            predicted = (
                metrics["attribution"][0]["primary"] if metrics["attribution"] else None
            )
            expected_and_predicted.append((case.expected_attribution, predicted))
    confusion = attribution_confusion(expected_and_predicted)
    assert confusion["accuracy"] == 1.0
