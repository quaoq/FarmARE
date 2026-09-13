from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from are.simulation.distributed.calibration import (
    assess_calibration,
    validate_release_sensitivity,
)
from are.simulation.distributed.evaluator_v5 import (
    _guard_verdict,
    _latest,
    _policy_verdicts,
)
from are.simulation.distributed.farm_adapter import FarmScenarioAdapter
from are.simulation.distributed.guard import CausalGuard
from are.simulation.distributed.knowledge import KnowledgeStore
from are.simulation.distributed.matched_baselines import delegation_observation
from are.simulation.distributed.models import (
    ActorSpec,
    EpistemicStatus,
    EventKind,
    FactRequirement,
    FactVersionRecord,
    GuardVerdict,
    KnowledgeItem,
    RequirementVerdict,
    TraceEvent,
    stable_digest,
)
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.petri import DataGuardSpec
from are.simulation.distributed.recovery import native_retry_profile
from are.simulation.distributed.scientific_v5 import (
    ScientificGateManifestV5,
    engineering_process_from_v4,
)
from are.simulation.scenarios.scenario_dcore.farm_catalog import compile_paper_petri_net
from are.simulation.tests.distributed.test_review_hardening import valid_pair


@pytest.mark.parametrize(
    "result, expected_scope",
    [
        (
            {
                "sensor_id": "S3",
                "ridge_id": 25,
                "ridge_start": 22,
                "ridge_end": 32,
                "vwc": 0.2,
            },
            (22, 32),
        ),
        ({"sensor_id": "S3", "ridge_id": 25, "vwc": 0.2}, None),
        (
            {
                "soil_sensors": [
                    {"ridge_start": 0, "ridge_end": 10, "vwc": 0.2},
                    {"ridge_start": 22, "ridge_end": 32, "vwc": 0.2},
                ]
            },
            None,
        ),
        (
            {
                "soil_sensors": [
                    {"ridge_start": 0, "ridge_end": 21, "vwc": 0.2},
                    {"ridge_start": 22, "ridge_end": 63, "vwc": 0.2},
                ]
            },
            (0, 63),
        ),
    ],
)
def test_sensor_evidence_uses_actual_contiguous_coverage(result, expected_scope):
    adapter = object.__new__(FarmScenarioAdapter)
    facts = adapter.extract_observed(
        action="SensorApp__read_soil_sensor",
        args={"sensor_id": "S3"},
        result=result,
        phase="r5",
    )
    assert all(fact.scope == expected_scope for fact in facts)
    assert any(fact.key == "soil:mean_vwc" for fact in facts)
    assert not any(fact.key == "crop:mature" for fact in facts)


def test_forecast_cannot_replace_current_spray_window_evidence():
    adapter = object.__new__(FarmScenarioAdapter)
    weather = {"rainfall_mm": 0.0, "wind_speed_ms": 2.0}
    current = adapter.extract_observed(
        action="WeatherApp__get_current_weather", args={}, result=weather, phase="r1"
    )
    forecast = adapter.extract_observed(
        action="WeatherApp__get_forecast",
        args={},
        result={"forecast": [weather]},
        phase="r1",
    )
    assert any(f.key == "weather:spray_window_open" and f.value for f in current)
    assert not any(f.key == "weather:spray_window_open" for f in forecast)
    assert any(
        f.key == "weather:forecast_spray_window_open" and f.value for f in forecast
    )


def test_legacy_forecast_plan_keeps_execution_weather_independent():
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        compile_native_petri_net,
    )

    net = compile_native_petri_net("farm_wetjune_recheck")
    sprays = [t for t in net.transitions if t.action == "TractorApp__apply_fungicide"]
    assert sprays
    for transition in sprays:
        weather = [
            (g.source, g.fact_key)
            for g in transition.guards
            if g.fact_key.startswith("weather:")
        ]
        assert ("knowledge", "weather:forecast_spray_window_open") in weather
        assert ("world", "weather:spray_window_open") in weather
        assert ("world", "weather:forecast_spray_window_open") not in weather
    paper_net = compile_paper_petri_net("farm_wetjune_recheck")
    policy = next(
        p
        for p in paper_net.metadata["information_policies"]
        if p["policy_id"] == "wetjune-fungicide-policy"
    )
    assert "weather:forecast_spray_window_open" in policy["requirement_fact_keys"]
    assert "weather:spray_window_open" not in policy["requirement_fact_keys"]


def knowledge(item_id, observed, learned, scope, value=True):
    return KnowledgeItem(
        item_id=item_id,
        fact_key="ready",
        value=value,
        scope=scope,
        status=EpistemicStatus.OBSERVED,
        source_actor="scout",
        evidence_ids=("evidence",),
        observed_at=observed,
        learned_at=learned,
    )


def test_calibration_clock_ignores_host_time_and_preserves_queue_offsets():
    from are.simulation.distributed.calibration import CalibrationClock
    from are.simulation.environment import Environment, EnvironmentConfig
    from are.simulation.types import Event

    clock = CalibrationClock()
    clock.reset(100.0)
    clock.real_start_time = -1e9
    assert clock.time() == 100.0
    clock.pause()
    clock.add_offset(3.0)
    assert clock.time() == 103.0
    clock.resume()
    assert clock.time() == 103.0
    clock.pause()
    clock.add_offset(7.0)
    clock.reset(100.0)
    assert clock.time() == 100.0
    env = Environment(
        config=EnvironmentConfig(
            start_time=100.0,
            duration=10,
            oracle_mode=True,
            queue_based_loop=True,
            exit_when_no_events=True,
            verbose=False,
        ),
        time_manager=clock,
    )
    observed = []

    def observe():
        observed.append(env.time_manager.time())
        env.time_manager.real_start_time -= 1000.0

    try:
        env.schedule(
            [
                Event.from_function(observe).at_absolute_time(102.0),
                Event.from_function(observe).at_absolute_time(104.0),
            ]
        )
        env.start()
        env.join()
        assert env.time_manager is clock
        assert observed == [102.0, 104.0]
    finally:
        env.stop()


def test_guard_selects_the_required_region_and_excludes_future_evidence():
    store = KnowledgeStore("operator")
    store.add(knowledge("correct-region", 10, 10, (20, 43)))
    store.add(knowledge("other-region", 11, 11, (0, 19), False))
    store.add(knowledge("future-region", 30, 30, (20, 43), False))
    result = CausalGuard().evaluate(
        actor=ActorSpec(actor_id="operator", permitted_actions=("write",)),
        action="write",
        requirements=[
            FactRequirement(
                requirement_id="r",
                actor_id="operator",
                action="write",
                fact_key="ready",
                scope=(20, 43),
            )
        ],
        knowledge=store,
        logical_time=12,
        evidence_ids={"evidence"},
        transport_closed=True,
    )
    assert result.verdict == GuardVerdict.ALLOW
    assert result.supporting_item_ids == ("correct-region",)


def test_generator_requirements_cannot_bypass_closed_deadline():
    requirement = FactRequirement(
        requirement_id="r",
        actor_id="operator",
        action="write",
        fact_key="ready",
        deadline=5,
    )
    result = CausalGuard().evaluate(
        actor=ActorSpec(actor_id="operator", permitted_actions=("write",)),
        action="write",
        requirements=iter([requirement]),
        knowledge=KnowledgeStore("operator"),
        logical_time=6,
        evidence_ids=set(),
        transport_closed=False,
    )
    assert result.verdict == GuardVerdict.BLOCK


def version(name, observed, learned, value, scope=(20, 43)):
    return FactVersionRecord(
        version_id=name,
        fact_key="ready",
        value=value,
        scope=scope,
        source_event_id="evidence",
        evidence_ids=("evidence",),
        world_time=observed,
        learned_time=learned,
    )


def test_saved_trace_evaluation_uses_observation_freshness():
    newer, late_old = version("new", 20, 21, True), version("old", 10, 30, False)
    trace = SimpleNamespace(
        fact_versions=(newer, late_old), events=[SimpleNamespace(event_id="evidence")]
    )
    assert _latest(trace.fact_versions, at=30) == newer
    verdict, fact, _ = _guard_verdict(
        DataGuardSpec(
            guard_id="r",
            fact_key="ready",
            expected=True,
            source="knowledge",
            scope=(20, 43),
        ),
        trace=trace,
        at=30,
        snapshot_item_ids=("new", "old"),
    )
    assert verdict == "true" and fact == newer


def test_forecast_belief_and_actual_weather_can_disagree():
    forecast = version("prediction", 10, 10, True).model_copy(
        update={"fact_key": "forecast"}
    )
    actual = version("actual", 20, 20, False).model_copy(
        update={"fact_key": "current", "authoritative": True}
    )
    trace = SimpleNamespace(
        fact_versions=(forecast, actual), events=[SimpleNamespace(event_id="evidence")]
    )
    guard = DataGuardSpec(
        guard_id="spray",
        fact_key="forecast",
        world_fact_key="current",
        source="knowledge",
        expected=True,
        required_evidence=True,
    )
    local = _guard_verdict(guard, trace=trace, at=20, snapshot_item_ids=("prediction",))
    world = _guard_verdict(guard, trace=trace, at=20, force_world=True)
    assert local[0] == "true" and local[1] == forecast
    assert world[0] == "false" and world[1] == actual
    missing = guard.model_copy(update={"world_fact_key": "unobserved"})
    assert _guard_verdict(missing, trace=trace, at=20, force_world=True)[0] == "unknown"


def test_policy_reconstruction_does_not_depend_on_the_subsequent_proposal():
    fact = version("fact", 10, 10, True)
    trace = SimpleNamespace(
        fact_versions=(fact,),
        events=[
            SimpleNamespace(event_id="decision", world_time=20),
            SimpleNamespace(event_id="evidence"),
        ],
    )
    policy = SimpleNamespace(
        requirements=(
            DataGuardSpec(
                guard_id="r", fact_key="ready", source="knowledge", expected=True
            ),
        )
    )
    decision = SimpleNamespace(
        decision_id="decision",
        knowledge_snapshot=SimpleNamespace(item_ids=("fact",)),
        proposed_intent=SimpleNamespace(args={"start": 20, "end": 43}, scope=None),
    )
    first = _policy_verdicts(policy, decision, trace, world=False)
    decision.proposed_intent.args = {"start": 0, "end": 63}
    assert _policy_verdicts(policy, decision, trace, world=False) == first


def test_runtime_policy_respects_frozen_scope_and_max_age():
    process = engineering_process_from_v4(
        compile_paper_petri_net("farm_wetjune_recheck")
    )
    policy = process.information_policies[0]
    requirements = tuple(
        guard.model_copy(
            update={
                "scope": (20, 43),
                "max_age": 5,
                "operator": type(guard.operator)("eq"),
            }
        )
        for guard in policy.requirements
    )
    policy = policy.model_copy(update={"requirements": requirements})
    process = process.model_copy(update={"information_policies": (policy,)})
    store = KnowledgeStore(policy.actor_id)
    for index, guard in enumerate(requirements):
        item = knowledge(f"good-{index}", 10, 10, (20, 43), guard.expected).model_copy(
            update={"fact_key": guard.fact_key}
        )
        store.add(item)
        store.add(
            item.model_copy(
                update={
                    "item_id": f"wrong-{index}",
                    "observed_at": 11,
                    "learned_at": 11,
                    "scope": (0, 19),
                    "value": "wrong",
                }
            )
        )

    def commit(at):
        return NativeDistributedSeasonRunner._information_policy_commitment(
            petri_net=process.occurrence_net,
            process_spec=process,
            actor_id=policy.actor_id,
            phase=policy.phases[0],
            knowledge=store,
            world_time=at,
            channel_closed=True,
        )

    assert set(commit(12)["requirement_verdicts"].values()) == {RequirementVerdict.TRUE}
    assert set(commit(20)["requirement_verdicts"].values()) == {
        RequirementVerdict.UNKNOWN
    }


def action(name, time, status, scope=(20, 43), actor="operations", amount=1):
    return TraceEvent(
        event_id=name,
        kind=EventKind.ACTION,
        actor_id=actor,
        local_sequence=int(time),
        logical_time=time,
        world_time=time,
        vector_clock={},
        status=status,
        action="FieldOpsApp__irrigate",
        args={"start": scope[0], "end": scope[1], "hours": amount},
        payload={"write": True},
        farmare_event_id=f"native:{name}",
    )


def test_native_retries_track_accepted_repairs_and_unresolved_failures():
    profile = native_retry_profile(
        [
            action("fail", 1, "error", amount=-1),
            action("other-region", 2, "ok", scope=(0, 19)),
            action("retry", 3, "ok"),
            action("later-failure", 4, "error"),
        ]
    )
    assert profile["failure_count"] == 2
    assert profile["accepted_retry_count"] == profile["unresolved_episode_count"] == 1
    episode = profile["episodes"][0]
    assert episode["accepted_retry_event_id"] == "retry"
    assert episode["arguments_changed"] and episode["latency_seconds"] == 2


def test_native_retries_require_same_actor_region_and_native_receipt():
    no_receipt = action("no-receipt", 4, "ok").model_copy(
        update={"farmare_event_id": None}
    )
    result = native_retry_profile(
        [
            action("fail", 1, "error"),
            action("other", 2, "ok", actor="other"),
            action("wrong-region", 3, "ok", scope=(0, 19)),
            no_receipt,
        ]
    )
    assert (
        result["accepted_retry_count"] == 0 and result["unresolved_episode_count"] == 1
    )


def write_report(path, *, candidate=False, corrupt=None):
    pairs = [valid_pair(seed) for seed in range(5)]
    plan = {
        "scenario_id": "farm_disease_drought",
        "candidate": candidate,
        "clock_mode": "event_queue_only",
        "world_seeds": list(range(5)),
        "min_shortfall": 0.01,
        "min_stressed_fraction": 0.5,
    }
    report = assess_calibration(
        pairs,
        expected_seeds=plan["world_seeds"],
        min_shortfall=0.01,
        min_stressed_fraction=0.5,
    )
    report.update(plan=plan, plan_digest=stable_digest(plan), pairs=pairs)
    if corrupt:
        corrupt(report)
    path.write_text(json.dumps(report))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_sensitivity_verifies_file_pairs_and_actual_world(tmp_path):
    path = tmp_path / "sensitivity.json"
    digest = write_report(path)
    validate_release_sensitivity(path, digest, world_seed=1, exogenous_digest="a" * 64)
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_release_sensitivity(path, "b" * 64)
    with pytest.raises(ValueError, match="world seed"):
        validate_release_sensitivity(path, digest, world_seed=99)
    with pytest.raises(ValueError, match="released world"):
        validate_release_sensitivity(
            path, digest, world_seed=1, exogenous_digest="b" * 64
        )
    gate = ScientificGateManifestV5(
        scenario_id="farm_disease_drought",
        scenario_sensitivity_report_path=path.name,
        scenario_sensitivity_report_digest=digest,
    ).model_copy(update={"status": "complete"})
    assert gate.verify_sensitivity(tmp_path / "gate.json") == path


def test_candidate_and_forged_acceptance_cannot_authorize_release(tmp_path):
    path = tmp_path / "sensitivity.json"
    digest = write_report(path, candidate=True)
    with pytest.raises(ValueError, match="not a candidate"):
        validate_release_sensitivity(path, digest)

    def corrupt(report):
        report["pairs"][0]["omission"]["marketable_yield_kg"] = 1001

    digest = write_report(path, corrupt=corrupt)
    with pytest.raises(ValueError, match="recomputed acceptance"):
        validate_release_sensitivity(path, digest)


@pytest.mark.parametrize(
    "mode", ["above-threshold", "missing-ridge", "nonfinite", "boolean"]
)
def test_calibration_recomputes_stress_from_complete_ridge_evidence(mode):
    pairs = [valid_pair(seed) for seed in range(5)]
    for condition in ("control", "omission"):
        roots = pairs[0][condition]["target_records"][0]["root_vwc_by_ridge"]
        if mode == "above-threshold":
            roots.update({key: 0.256 for key in roots})
        elif mode == "missing-ridge":
            roots.pop("20")
        else:
            roots["20"] = float("nan") if mode == "nonfinite" else True
    report = assess_calibration(
        pairs,
        expected_seeds=list(range(5)),
        min_shortfall=0.01,
        min_stressed_fraction=0.5,
    )
    assert not report["engineering_acceptance_passed"]


def test_a2a_assignment_is_not_evidence_that_delegation_occurred():
    assert delegation_observation({})["delegation_observed"] is None
    assert delegation_observation({"world_logs": []})["delegation_observed"] is False
    subagent = {
        "log_type": "subagent",
        "group_id": "call-1",
        "children": [{"log_type": "step"}],
    }
    observed = delegation_observation({"world_logs": [json.dumps(subagent), subagent]})
    assert (
        observed["delegation_observed"] is True
        and observed["delegation_group_count"] == 1
    )
