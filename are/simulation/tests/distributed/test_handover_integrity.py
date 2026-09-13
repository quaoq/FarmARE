import pytest

from are.simulation.distributed.models import (
    CausalHandoff,
    DistributedRunnerConfig,
    FreeTextEnvelope,
)
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.review_policy import validate_review_attestation
from are.simulation.distributed.transport import (
    FaultMode,
    FaultRule,
    FaultSchedule,
    InProcessTransport,
)


def test_professor_approval_is_explicit_and_content_bound():
    expected = {
        "process": "a" * 64,
        "team": "b" * 64,
        "refinement": "c" * 64,
        "protocol": "d" * 64,
    }
    record = {
        "route": "author_defined_professor_approved",
        "approved": True,
        "reviewer_name": "TEST FIXTURE ONLY",
        "reviewer_role": "professor",
        "statement": "TEST FIXTURE: no real approval",
        "signed_at": "2026-09-13T00:00:00Z",
        "subject_digests": expected,
    }
    validate_review_attestation(record, expected)
    for key, value in [
        ("approved", False),
        ("reviewer_name", ""),
        ("signed_at", "2026-09-13"),
        ("route", "independently_confirmed"),
    ]:
        tampered = {**record, key: value}
        with pytest.raises(ValueError):
            validate_review_attestation(tampered, expected)
    changed = {**expected, "protocol": "e" * 64}
    with pytest.raises(ValueError):
        validate_review_attestation(record, changed)


@pytest.mark.parametrize("envelope", [FreeTextEnvelope, CausalHandoff])
def test_selector_is_independent_of_message_name_and_representation(envelope):
    schedule = FaultSchedule(
        by_route_selector={"selector:r5:a:b:2": FaultRule(FaultMode.DROP)}
    )
    transport = InProcessTransport(("a", "b"), schedule)
    for index, phase in enumerate(["midseason", "r5", "r5"]):
        transport.send(
            envelope(
                sender="a",
                recipient="b",
                message_id=f"arbitrary-{index}",
                **({"text": "evidence"} if envelope is FreeTextEnvelope else {}),
            ),
            index,
            phase=phase,
        )
    assert transport.snapshot()["dropped"] == ["arbitrary-2"]
    report = transport.fault_manifestation("drop")
    assert report["qualifying_handoff_count"] == 1
    assert report["activation_status"] == "activated"
    empty = InProcessTransport(("a", "b"), schedule)
    assert (
        empty.fault_manifestation("drop")["activation_status"]
        == "no_qualifying_handoff"
    )


@pytest.mark.parametrize(
    "mode",
    [
        "drop",
        "duplicate",
        "delay_within_validity",
        "delay_past_validity",
        "delay_past_deadline",
        "reorder",
        "mixed",
    ],
)
def test_named_faults_accept_frozen_route_selectors(mode):
    selectors = tuple(
        f"selector:r5:a:b:{i}"
        for i in range(1, 1 + ({"reorder": 2, "mixed": 3}.get(mode, 1)))
    )
    config = DistributedRunnerConfig(fault=mode, fault_target_ids=selectors)
    schedule = NativeDistributedSeasonRunner._fault_schedule(config)
    assert set(schedule.by_route_selector) == set(selectors)
    assert not schedule.by_message_id and not schedule.by_message_prefix


def test_resume_rejects_changed_source_configuration_and_uncertain_writes(
    tmp_path, monkeypatch
):
    import json

    import are.simulation.distributed.experiments as matrix
    from are.simulation.distributed.models import stable_digest

    monkeypatch.setattr(matrix, "execution_source_digest", lambda: "source-a")
    row = {
        "execution": "dcore",
        "scenario_id": "farm_wetjune_recheck",
        "condition_id": "test",
        "run_key": "k",
        "world_seed": 0,
    }
    run = tmp_path / row["scenario_id"] / "test_k"
    run.mkdir(parents=True)
    identity = {
        "schema_version": "dcore_run_identity_v2",
        "configuration_digest": stable_digest(row),
        "source_digest": "source-a",
    }
    (run / "RUN_IDENTITY.json").write_text(json.dumps(identity))
    with pytest.raises(RuntimeError, match="uncertain native writes"):
        matrix.run_resolved_matrix([row], tmp_path)
    with pytest.raises(FileExistsError):
        matrix.run_resolved_matrix([row], tmp_path, resume=False)
    with pytest.raises(ValueError, match="mismatch"):
        matrix.run_resolved_matrix([{**row, "world_seed": 1}], tmp_path)
    monkeypatch.setattr(matrix, "execution_source_digest", lambda: "source-b")
    with pytest.raises(ValueError, match="mismatch"):
        matrix.run_resolved_matrix([row], tmp_path)
    assert sorted(p.name for p in run.iterdir()) == ["RUN_IDENTITY.json"]


def test_raw_provider_usage_covers_nested_calls_and_unknown_failures():
    from types import SimpleNamespace

    from are.simulation.distributed.llm_budget import team_llm_budget
    from are.simulation.distributed.pilot_budget import (
        RequestBudgetExceeded,
        actor_request_scope,
        provider_completion,
    )

    requests = []

    def provider(**kwargs):
        requests.append(kwargs)
        if len(requests) == 2:
            raise TimeoutError("unknown provider usage")
        return SimpleNamespace(usage={"prompt_tokens": 20, "completion_tokens": 5})

    with team_llm_budget(3, 100000) as budget:
        budget.per_actor_calls = {"operations": 2}
        with actor_request_scope("operations"):
            provider_completion(provider, messages=[], max_completion_tokens=100)
            with pytest.raises(TimeoutError):
                provider_completion(provider, messages=[], max_completion_tokens=100)
            with pytest.raises(RequestBudgetExceeded, match="actor"):
                provider_completion(provider, messages=[], max_completion_tokens=100)
        with actor_request_scope("specialist"):
            provider_completion(provider, messages=[], max_completion_tokens=100)
        assert budget.calls == len(requests) == 3
        assert budget.tokens == 50 + budget.provider_records[1]["reserved_tokens"]
        assert budget.provider_records[1]["status"] == "usage_unknown"
        assert all(request["num_retries"] == 0 for request in requests)


def test_three_cultivar_slug_does_not_turn_preparation_into_harvest():
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        compile_native_petri_net,
    )

    net = compile_native_petri_net("farm_three_cultivar", world_seed=0)
    preparation = [
        t
        for t in net.transitions
        if t.action
        in {
            "TractorApp__level",
            "TractorApp__base_fertilize",
            "TractorApp__form_ridges",
        }
    ]
    assert len(preparation) == 3
    assert {t.phase for t in preparation} == {"field_prep"}
    assert all(
        t.phase == "harvest"
        for t in net.transitions
        if t.action == "TractorApp__harvest"
    )


def test_prompt_trimming_keeps_last_complete_exchange_and_full_audit_log():
    from types import SimpleNamespace

    from are.simulation.agents.agent_log import (
        LLMInputLog,
        ObservationLog,
        StepLog,
        SystemPromptLog,
        TaskLog,
    )
    from are.simulation.agents.default_agent.base_agent import BaseAgent

    common = {"timestamp": 0, "agent_id": "operations"}
    system = SystemPromptLog(content="role and tools", **common)
    old = TaskLog(content="Choose exactly one role-owned tool old", **common)
    current = TaskLog(content="Choose exactly one role-owned tool current", **common)
    steps = [StepLog(iteration=i, **common) for i in range(3)]
    last_result = ObservationLog(
        content="exact rejected arguments and receipt", **common
    )
    hidden = LLMInputLog(content=[{"role": "user", "content": "x " * 100000}], **common)
    logs = [system, old, steps[0], hidden, steps[1], last_result, steps[2], current]
    agent = SimpleNamespace(
        deduplicate_local_state=True, distributed_prompt_tokens=10000
    )
    compact = BaseAgent._compact_distributed_history(agent, logs)
    assert steps[0] in compact  # hidden audit input does not inflate prompt size
    assert old not in compact and current in compact
    agent.distributed_prompt_tokens = 1
    compact = BaseAgent._compact_distributed_history(agent, logs)
    assert steps[0] not in compact
    assert all(
        item in compact for item in [system, steps[1], last_result, steps[2], current]
    )
    assert old in logs and hidden in logs  # immutable full trace is untouched


def test_recent_failure_feedback_retains_exact_request_and_native_receipt():
    from collections import deque
    from types import SimpleNamespace

    from are.simulation.distributed.controllers import FarmAREBaseAgentController

    controller = object.__new__(FarmAREBaseAgentController)
    logs = []
    controller.base_agent = SimpleNamespace(
        append_agent_log=logs.append, make_timestamp=lambda: 0, agent_id="operations"
    )
    controller.recent_failures = deque(maxlen=8)
    controller.accepted_write_receipts = deque(maxlen=32)
    failure = {
        "intent_kind": "act",
        "selected_action": "TractorApp__irrigate",
        "arguments": {"ridge_start": 22, "ridge_end": 32, "hours": 1},
        "intent_id": "request-1",
        "executed": False,
        "error": "quota exceeded",
        "execution_receipt": {"status": "rejected", "request_id": "request-1"},
    }
    controller.observe(failure)
    assert controller.recent_failures[0]["arguments"] == failure["arguments"]
    assert (
        controller.recent_failures[0]["execution_receipt"]
        == failure["execution_receipt"]
    )
    assert not controller.accepted_write_receipts
    assert "quota exceeded" in logs[0].content


@pytest.mark.parametrize("failed", [False, True])
def test_compatible_resume_preserves_completed_and_failed_attempts(
    tmp_path, monkeypatch, failed
):
    import json

    import are.simulation.distributed.experiments as matrix
    from are.simulation.distributed.models import stable_digest

    monkeypatch.setattr(matrix, "execution_source_digest", lambda: "source-a")

    def forbidden_execution(*args, **kwargs):
        pytest.fail("resume must not execute a retained native attempt")

    monkeypatch.setattr(matrix.DistributedScenarioRunner, "run", forbidden_execution)
    row = {
        "execution": "dcore",
        "scenario_id": "farm_wetjune_recheck",
        "condition_id": "test",
        "run_key": "k",
        "world_seed": 0,
    }
    run = tmp_path / row["scenario_id"] / "test_k"
    run.mkdir(parents=True)
    (run / "RUN_IDENTITY.json").write_text(
        json.dumps(
            {
                "schema_version": "dcore_run_identity_v2",
                "configuration_digest": stable_digest(row),
                "source_digest": "source-a",
            }
        )
    )
    saved = {
        **row,
        "status": "failed" if failed else "completed",
        "marketable_yield_kg": None,
    }
    artifact = run / ("failure_row.json" if failed else "experiment_row.json")
    artifact.write_text(json.dumps(saved))
    if not failed:
        (run / "COMPLETED.json").write_text(
            json.dumps({"status": "completed", "run_key": "k"})
        )
    original = artifact.read_bytes()
    result = matrix.run_resolved_matrix([row], tmp_path)
    assert len(result) == 1 and result[0]["status"] == saved["status"]
    assert artifact.read_bytes() == original
    assert result[0]["marketable_yield_kg"] is None


def test_world_cluster_uncertainty_does_not_invent_replication_or_zero_p_values():
    from are.simulation.distributed.experiments import (
        _bootstrap_ci,
        _paired_bootstrap_p,
    )

    assert _bootstrap_ci([]) is None
    assert _bootstrap_ci([1.0]) is None
    assert _paired_bootstrap_p([1.0]) is None
    assert _paired_bootstrap_p([1.0, 1.0]) == 0.5
    assert _paired_bootstrap_p([1.0, -1.0]) == 1.0
    assert _paired_bootstrap_p([1.0] * 10) == 2 / 1024
    assert _paired_bootstrap_p([1.0] * 17, draws=100) >= 1 / 101


def test_report_retains_metric_denominators_and_unavailable_scores():
    from are.simulation.distributed.paper_report import _table_rows

    group = {
        "condition": "local_causal_audit",
        "scenario": "farm_wetjune_recheck",
        "fault": "none",
        "n": 3,
        "n_primary": 2,
        "event_fidelity": {
            "mean": 0.8,
            "n_available": 1,
            "n_missing": 1,
            "cluster_count": 1,
        },
        "marketable_yield_shortfall": {"n_available": 0, "n_missing": 2},
    }
    tables = _table_rows([], {"groups": [group], "predeclared_contrasts": []}, [])
    diagnostic = tables["table2_local_global_discordance"][0]
    assert diagnostic["event_fidelity"] == 0.8
    assert diagnostic["event_fidelity_n_available"] == 1
    assert diagnostic["event_fidelity_n_missing"] == 1
    assert diagnostic["event_fidelity_cluster_count"] == 1
    assert diagnostic["n"] == 3 and diagnostic["n_primary"] == 2
    outcome = tables["table3_fault_outcomes"][0]
    assert outcome["marketable_yield_shortfall"] is None
    assert outcome["marketable_yield_shortfall_n_available"] == 0
    assert outcome["marketable_yield_shortfall_n_missing"] == 2


def test_primary_paired_contrasts_keep_unknown_scores_missing():
    from are.simulation.distributed.experiments import aggregate_rows

    common = {
        "scenario": "farm_wetjune_recheck",
        "team_id": "wetjune_2agent",
        "pair_id": "p",
        "world_cluster_id": "w0",
        "fault": "none",
    }
    rows = [
        {**common, "condition": "local_causal_audit", "dcore_score": None},
        {**common, "condition": "local_free_text", "dcore_score": 0.8},
    ]
    contrast = next(
        item
        for item in aggregate_rows(rows)["predeclared_contrasts"]
        if item["contrast"] == "causal_audit_minus_free_text_reliable"
        and item["metric"] == "dcore_score"
    )
    assert contrast["n_observed_pair_assignments"] == 1
    assert contrast["n_missing_pair_outcomes"] == 1
    assert contrast["n_pairs"] == 0 and contrast["mean_paired_difference"] is None
    assert contrast["p_value"] is None


def test_execution_identity_ignores_unshipped_javascript_dependency_python(
    tmp_path, monkeypatch
):
    import are.simulation.distributed.experiments as matrix

    source = tmp_path / "are/simulation/distributed/experiments.py"
    source.parent.mkdir(parents=True)
    source.write_text("# executable package source\n")
    (tmp_path / "uv.lock").write_text("lock\n")
    (tmp_path / "pyproject.toml").write_text("project\n")
    monkeypatch.setattr(matrix, "__file__", str(source))
    original = matrix.execution_source_digest()
    ignored = (
        tmp_path / "are/simulation/gui/client/node_modules/flatted/python/flatted.py"
    )
    ignored.parent.mkdir(parents=True)
    ignored.write_text("# local frontend installation artifact\n")
    assert matrix.execution_source_digest() == original
    source.write_text("# changed executable package source\n")
    assert matrix.execution_source_digest() != original
