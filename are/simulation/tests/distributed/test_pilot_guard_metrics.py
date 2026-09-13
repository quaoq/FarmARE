from types import SimpleNamespace

import pytest

from are.simulation.distributed.evaluator_v5 import (
    _guard_effectiveness,
    _physical_guard_prevention,
    _proposal_validity,
)
from are.simulation.distributed.experiments import aggregate_directory
from are.simulation.distributed.models import (
    AgentIntent,
    DecisionRecord,
    DistributedRunnerConfig,
    DistributedTrace,
    EventKind,
    GuardResult,
    KnowledgeSnapshot,
    TraceEvent,
)
from are.simulation.distributed.petri import (
    ArgumentConstraint,
    PolicyResponse,
    TransitionSpec,
)
from are.simulation.distributed.scientific_v5 import TransitionAcceptanceSpec


def example(status="blocked", mode="enforce", dose=2, scope=(0, 3)):
    event = TraceEvent(
        event_id="d",
        kind="decision",
        actor_id="ops",
        local_sequence=1,
        logical_time=1,
        world_time=10,
        vector_clock={"ops": 1},
    )
    decision = DecisionRecord(
        decision_id="d",
        actor_id="ops",
        logical_time=1,
        season_phase="r5",
        knowledge_snapshot=KnowledgeSnapshot(
            actor_id="ops",
            logical_time=1,
            item_ids=(),
            vector_clock={"ops": 1},
            digest="x",
        ),
        proposed_intent=AgentIntent(
            kind="act", action="apply", args={"dose": dose}, scope=scope
        ),
        guard=GuardResult(verdict="block"),
    )
    action = event.model_copy(
        update={
            "event_id": "a",
            "kind": EventKind.ACTION,
            "decision_context_id": "d",
            "status": status,
            "payload": {"blocked_before_farmare": True},
            "farmare_event_id": "native" if status in {"ok", "error"} else None,
        }
    )
    trace = DistributedTrace(
        run_id="t",
        task_id="t",
        actors=("ops",),
        events=(event, action),
        decisions=(decision,),
        configuration={"enforcement_mode": mode},
    )
    transition = TransitionSpec(
        transition_id="t",
        label="apply",
        actor_id="ops",
        action="apply",
        phase="r5",
        scope=(0, 3),
    )
    acceptance = TransitionAcceptanceSpec(
        transition_id="t",
        arguments=(ArgumentConstraint(name="dose", expected=2),),
        window_start=0,
        window_end=20,
    )
    process = SimpleNamespace(
        occurrence_net=SimpleNamespace(transitions=(transition,)),
        acceptance=(acceptance,),
    )
    rule = SimpleNamespace(permitted_responses=(PolicyResponse.EXECUTE,))
    return process, trace, decision, rule


@pytest.mark.parametrize("status", ["blocked", "deferred", "ok", "error"])
def test_proposal_validity_does_not_depend_on_execution(status):
    p, t, d, r = example(status=status)
    assert _proposal_validity(p, t, d, "r5", {"ready": "true"}, r) is True
    assert _physical_guard_prevention(t, d) == (status in {"blocked", "deferred"})


@pytest.mark.parametrize("mode", ["off", "audit"])
def test_guard_recommendation_is_not_physical_prevention(mode):
    _, t, d, _ = example(mode=mode)
    assert not _physical_guard_prevention(t, d)


def test_conflicting_engineering_phase_hints_are_unassessable():
    p, t, d, r = example()
    d = d.model_copy(update={"season_phase": "midseason"})
    assert _proposal_validity(p, t, d, "r5", {"ready": "true"}, r) is None
    # A frozen phase window, when present, supersedes runtime hints.
    p.phase_windows = ("frozen",)
    assert _proposal_validity(p, t, d, "r5", {"ready": "true"}, r) is True


@pytest.mark.parametrize("change", ["dose", "scope", "time", "policy", "unknown"])
def test_proposal_uses_preexecution_constraints_and_retains_unknown(change):
    p, t, d, r = example(
        dose=9 if change == "dose" else 2, scope=(5, 9) if change == "scope" else (0, 3)
    )
    if change == "time":
        t = t.model_copy(
            update={
                "events": (
                    t.events[0].model_copy(update={"world_time": 30}),
                    t.events[1],
                )
            }
        )
    if change == "policy":
        r = SimpleNamespace(permitted_responses=(PolicyResponse.DEFER,))
    result = _proposal_validity(
        p, t, d, "r5", {"ready": "unknown" if change == "unknown" else "true"}, r
    )
    assert result is (None if change == "unknown" else False)


def test_false_blocks_and_unsafe_prevention_have_determinate_denominators():
    rows = [
        dict(
            response="execute",
            proposal_valid=valid,
            guard_prevented_write=blocked,
            global_conforming=False,
            global_execute_permitted=True,
            global_verdicts={"ready": "true"},
        )
        for valid, blocked in [
            (True, True),
            (False, True),
            (None, True),
            (False, False),
            (True, False),
        ]
    ]
    result = _guard_effectiveness(rows, {"recovered_count": 0, "opportunity_count": 0})
    assert result["false_blocks"] == 1 and result["prevented_unsafe_writes"] == 1
    assert result["physical_blocks"] == 3 and result["unassessable_blocks"] == 1
    assert result["false_block_rate"] == 0.5 and result["safety_benefit_rate"] == 0.5


def pilot(**updates):
    return DistributedRunnerConfig(
        **dict(
            controller_mode="llm",
            scenario_id="farm_wetjune_recheck",
            scientific_contract="v5",
            engineering_llm_pilot=True,
            max_model_calls=12,
            team_token_budget=50000,
            **updates,
        )
    )


def test_engineering_pilot_is_explicit_and_does_not_fabricate_reviews():
    c = pilot()
    assert not c.paper_mode and c.scientific_gate_manifest is None
    with pytest.raises(ValueError, match="paper_mode"):
        DistributedRunnerConfig(controller_mode="llm")


@pytest.mark.parametrize(
    "updates",
    [
        {"paper_mode": True, "petri_spec_path": "draft.json"},
        {"scientific_gate_manifest": "fake.json"},
        {"bounded_llm_smoke": True},
        {"max_model_calls": 129},
        {"team_token_budget": None},
        {"team_token_budget": 1000001},
        {"max_output_tokens": 2049},
        {"controller_mode": "scripted"},
        {"scientific_contract": "v4"},
    ],
)
def test_pilot_rejects_unbounded_or_paper_configuration(updates):
    settings = dict(
        controller_mode="llm",
        scenario_id="farm_wetjune_recheck",
        scientific_contract="v5",
        engineering_llm_pilot=True,
        max_model_calls=12,
        team_token_budget=50000,
    )
    with pytest.raises(ValueError):
        DistributedRunnerConfig(**(settings | updates))


def test_paper_aggregation_rejects_pilot_even_if_paper_flag_is_changed(tmp_path):
    import json

    (tmp_path / "results.jsonl").write_text(
        json.dumps({"paper_mode": True, "engineering_llm_pilot": True}) + "\n"
    )
    with pytest.raises(ValueError, match="engineering LLM pilot"):
        aggregate_directory(tmp_path, paper_mode=True)


def test_engineering_matrix_marker_and_fault_targets_survive_resolution():
    from are.simulation.distributed.experiments import (
        _config_from_row,
        resolve_manifest,
    )

    rows = resolve_manifest(
        {
            "scientific_contract": "v5",
            "engineering_llm_pilot": True,
            "controller_mode": "llm",
            "scenarios": ["farm_wetjune_recheck"],
            "world_seeds": [0, 1],
            "max_model_calls": 12,
            "team_token_budget": 100000,
            "conditions": [
                {
                    "id": "outage",
                    "execution": "dcore",
                    "faults": ["drop"],
                    "fault_target_ids": ["prefix:handoff:"],
                }
            ],
        }
    )
    assert len(rows) == 2
    for row in rows:
        config = _config_from_row(row, "/tmp/unused-engineering-test")
        assert config.engineering_llm_pilot and not config.paper_mode
        assert config.fault_target_ids == ("prefix:handoff:",)


def test_resolved_pilot_team_cannot_override_call_cap(tmp_path):
    from are.simulation.distributed.teams import build_builtin_team, load_team_spec

    team = build_builtin_team("wetjune_2agent", [])
    team = team.model_copy(update={"team_call_budget": 1000})
    path = tmp_path / "team.json"
    path.write_text(team.model_dump_json())
    config = DistributedRunnerConfig(
        scenario_id="farm_wetjune_recheck",
        controller_mode="llm",
        scientific_contract="v5",
        engineering_llm_pilot=True,
        max_model_calls=12,
        team_token_budget=100000,
        team_spec_path=str(path),
    )
    with pytest.raises(ValueError, match="resolved engineering pilot call budgets"):
        load_team_spec(config, [])


@pytest.mark.parametrize(
    "error,limit,expected_calls",
    [
        ("Cannot harvest in rainy conditions", 2, 3),
        ("Other error", 2, 1),
        (None, 2, 1),
    ],
)
def test_harvest_retry_is_bounded_and_preserves_native_failures(
    error, limit, expected_calls
):
    from copy import copy

    from are.simulation.distributed.calibration import (
        CalibrationClock,
        instrument_harvest_windows,
    )
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        create_native_scenario,
    )

    scenario = create_native_scenario("farm_disease_drought", world_seed=0)
    target = next(e for e in scenario.events if "whole_field_harvest_0_3" in e.event_id)
    original_make = target.make_event

    def make(env):
        source = copy(original_make(env))
        source.action = copy(source.action)

        def harvest(*args, **kwargs):
            return {"error": error} if error else {"status": "ok"}

        source.action.function = harvest
        return source

    target.make_event = make
    clock = CalibrationClock()
    clock.reset(start_time=0)
    environment = SimpleNamespace(time_manager=clock)
    farm = SimpleNamespace(advance_physics_time=lambda timestamp: None)
    records = instrument_harvest_windows(
        SimpleNamespace(events=[target]), farm, max_wait_days=limit
    )
    result = target.make_event(environment).action.execute()
    assert len(records) == expected_calls
    assert clock.time() == (expected_calls - 1) * 86400
    assert result == ({"error": error} if error else {"status": "ok"})


def test_harvest_retry_plan_is_explicit_and_cannot_validate_released_workflow(tmp_path):
    import hashlib
    import json

    from are.simulation.distributed.calibration import (
        run_drought_calibration,
        validate_release_sensitivity,
    )

    plan = run_drought_calibration(
        tmp_path / "unused",
        world_seeds=[0],
        candidate=False,
        dry_run=True,
        harvest_retry_days=7,
    )
    assert plan["workflow_variant"] == "bounded_rain_harvest_retry_v1"
    assert not (tmp_path / "unused").exists()
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"plan": plan}))
    with pytest.raises(ValueError, match="unchanged released workflow"):
        validate_release_sensitivity(
            path, hashlib.sha256(path.read_bytes()).hexdigest()
        )
