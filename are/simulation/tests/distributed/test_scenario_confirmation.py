from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from are.simulation.distributed import experiments
from are.simulation.distributed.authored_specs import author_process
from are.simulation.distributed.calibration import (
    assess_calibration,
    run_drought_calibration,
    validate_release_sensitivity,
)
from are.simulation.distributed.models import DistributedRunnerConfig, stable_digest
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.scenario_confirmation import DroughtConfirmationBinding
from are.simulation.distributed.scientific_v5 import ScientificGateManifestV5
from are.simulation.tests.distributed.test_review_hardening import valid_pair


@pytest.mark.parametrize(
    "error, before_deadline, retries",
    [
        ("Grain moisture too high for harvest (>18%)", True, True),
        ("Ridges are not mature enough for harvest: [20, 21]", True, True),
        ("Cannot harvest in rainy conditions", True, True),
        ("Insufficient fuel", True, False),
        ("Cannot harvest in rainy conditions", False, False),
        (None, True, False),
    ],
)
def test_scripted_calendar_retries_exact_harvest_and_retains_rejection(
    error, before_deadline, retries
):
    from types import SimpleNamespace

    from are.simulation.distributed.controllers import (
        OracleCeilingController,
        OracleCeilingCoordinator,
    )
    from are.simulation.distributed.models import AgentIntent, IntentKind

    intent = AgentIntent(
        kind=IntentKind.ACT,
        action="TractorApp__harvest",
        args={"start_ridge": 20, "end_ridge": 23},
    )
    coordinator = OracleCeilingCoordinator([("operations", "harvest", intent)])
    controller = OracleCeilingController(
        "operations", coordinator, harvest_deadlines={"harvest": 200000.0}
    )
    view = SimpleNamespace(world_time=100.0 if before_deadline else 200000.0 - 86400)
    assert controller.decide(view) == intent
    feedback = {
        "executed": not bool(error),
        "result": {"error": error} if error else {"status": "ok"},
    }
    controller.observe(feedback)
    assert controller.results == [feedback]
    assert len(coordinator.steps) == (2 if retries else 0)
    if retries:
        assert controller.decide(view).action == "SystemApp__advance_time"
        assert controller.decide(view) == intent


def test_calendar_reference_deadline_uses_earliest_phase_or_horizon_and_exact_spec(
    tmp_path,
):
    process = author_process(
        "farm_disease_drought",
        scenario_revision="drought_pulse_v4",
        calibration_candidate=True,
        reference_harvest_calendar=True,
    )
    phase_deadline = next(
        w.end_world_time for w in process.phase_windows if w.phase == "harvest"
    )
    horizon = process.metadata["scenario_horizon"]
    assert process.occurrence_net.metadata["reference_harvest_deadlines"] == {
        "harvest": min(phase_deadline, horizon)
    }
    assert process.occurrence_net.metadata["reference_harvest_deadline_rule"] == (
        "min(authored_phase_end,native_scenario_horizon)"
    )
    path = tmp_path / "reference.json"
    path.write_text(process.model_dump_json())
    kwargs = dict(
        world_seeds=[0],
        candidate=True,
        scenario_revision="drought_pulse_v4",
        retry_immaturity=True,
        retry_wet_grain=True,
        reference_process_path=path,
        dry_run=True,
    )
    plan = run_drought_calibration(tmp_path / "unused", **kwargs)
    assert plan["reference_process_digest"] == process.digest
    assert plan["harvest_deadline_world_time"] == min(phase_deadline, horizon)
    assert plan["workflow_variant"] == "authored_harvest_calendar_v5"
    with pytest.raises(ValueError, match="no relative-day cap"):
        run_drought_calibration(tmp_path / "unused", **kwargs, harvest_retry_days=21)
    with pytest.raises(ValueError, match="native scenario mismatch"):
        run_drought_calibration(tmp_path / "unused", **{**kwargs, "candidate": False})
    assert not (tmp_path / "unused").exists()


@pytest.fixture
def binding(monkeypatch):
    monkeypatch.setattr(experiments, "execution_source_digest", lambda: "a" * 64)
    protocol = Path(experiments.__file__).with_name("EXPERIMENT_PROTOCOL.md")
    return DroughtConfirmationBinding(
        scenario_revision="drought_pulse_v4",
        calibration_candidate=True,
        harvest_retry_days=21,
        retry_immaturity=True,
        retry_wet_grain=True,
        development_worlds=list(range(10)),
        confirmation_worlds=list(range(20, 25)),
        live_smoke_worlds=[30, 31],
        study_worlds=list(range(100, 110)),
        process_digest="b" * 64,
        protocol_digest=hashlib.sha256(protocol.read_bytes()).hexdigest(),
        execution_source_digest="a" * 64,
    )


def test_unreached_branch_remains_unknown_and_common_obligations_are_not_erased(
    monkeypatch,
):
    from are.simulation.distributed import evaluator_v5
    from are.simulation.distributed.models import DistributedTrace

    process = author_process("farm_wetjune_recheck")
    trace = DistributedTrace(
        schema_version="dcore_trace_v5",
        run_id="partial-fixture",
        task_id=process.process_id,
        actors=process.occurrence_net.actors,
        events=(),
    )
    monkeypatch.setattr(
        evaluator_v5, "_pm4py_sequential_projection", lambda *args: {"available": False}
    )
    result = evaluator_v5.evaluate_farm_dcore_v5(process, trace)
    audit = result["world_branch_audit"]
    assert audit["unassessable_count"] == 1
    assert audit["runtime_commitments_agree"] is None
    assert audit["details"][0]["recomputed"] is None
    assert result["occurrence_net"]["branch_commitments"] == []
    assert result["module_profile"]["disease"]["required_transition_count"] > 0
    assert result["event_fidelity"] == 0
    assert result["causal_conformance"] is None


def test_evaluator_failure_preserves_raw_native_trace_and_outcome(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from are.simulation.distributed import evaluator_v5
    from are.simulation.distributed.models import DistributedTrace
    from are.simulation.distributed.runner import DistributedScenarioRunner

    process = author_process("farm_wetjune_recheck")
    trace = DistributedTrace(
        schema_version="dcore_trace_v5",
        run_id="failed-fixture",
        task_id=process.process_id,
        actors=process.occurrence_net.actors,
        events=(),
        outcome={"harvest_complete": False, "provider_request_count": 2},
    )
    execution = SimpleNamespace(
        trace=trace,
        process_spec=process,
        petri_net=process.occurrence_net,
        farmare_trace_json='{"fixture": true}',
    )
    monkeypatch.setattr(NativeDistributedSeasonRunner, "run", lambda *args: execution)

    def broken(*args):
        raise ValueError("fixture evaluator defect")

    monkeypatch.setattr(evaluator_v5, "evaluate_farm_dcore_v5", broken)
    config = DistributedRunnerConfig(
        scenario_id="farm_wetjune_recheck", output_dir=str(tmp_path)
    )
    with pytest.raises(ValueError, match="fixture evaluator defect"):
        DistributedScenarioRunner()._run_native_farm(config)
    capture = tmp_path / "evaluation_failure_capture"
    assert (
        DistributedTrace.model_validate_json((capture / "trace.json").read_text())
        == trace
    )
    assert (
        json.loads((capture / "outcome.json").read_text())["provider_request_count"]
        == 2
    )
    assert (
        json.loads((capture / "evaluation_failure.json").read_text())[
            "diagnosis_status"
        ]
        == "unassessable"
    )


def report_file(tmp_path, binding, *, corrupt=None):
    freeze = tmp_path / "freeze.json"
    freeze.write_text(binding.model_dump_json())
    plan = run_drought_calibration(
        tmp_path / "never_run",
        world_seeds=binding.confirmation_worlds,
        candidate=True,
        scenario_revision="drought_pulse_v4",
        harvest_retry_days=21,
        retry_immaturity=True,
        retry_wet_grain=True,
        confirmation_manifest=freeze,
        dry_run=True,
    )
    assert not (tmp_path / "never_run").exists()
    pairs = [valid_pair(seed) for seed in binding.confirmation_worlds]
    if corrupt:
        corrupt(pairs)
    report = assess_calibration(
        pairs,
        expected_seeds=binding.confirmation_worlds,
        min_shortfall=0.01,
        min_stressed_fraction=0.5,
    )
    report.update(
        plan=plan,
        plan_digest=stable_digest(plan),
        pairs=pairs,
        confirmation_source_unchanged=True,
    )
    path = tmp_path / "synthetic_confirmation.json"
    path.write_text(json.dumps(report))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_held_out_confirmation_authorizes_different_study_world_not_development(
    tmp_path, binding
):
    path, digest = report_file(tmp_path, binding)
    gate = ScientificGateManifestV5(
        scenario_id=binding.scenario_id,
        scenario_confirmation=binding.model_dump(),
        confirmed_process_digest=binding.process_digest,
        analysis_protocol_digest=binding.protocol_digest,
        scenario_sensitivity_report_path=path.name,
        scenario_sensitivity_report_digest=digest,
    ).model_copy(update={"status": "complete"})
    for world in [30, 31, 100, 109]:
        assert gate.verify_sensitivity(tmp_path / "gate.json", world_seed=world) == path
    for world in [0, 20, 99, 110]:
        with pytest.raises(ValueError, match="outside the frozen"):
            gate.verify_sensitivity(tmp_path / "gate.json", world_seed=world)
    with pytest.raises(ValueError, match="process digest mismatch"):
        gate.model_copy(
            update={"confirmed_process_digest": "c" * 64}
        ).verify_sensitivity(tmp_path / "gate.json")


@pytest.mark.parametrize(
    "corruption", ["incomplete", "missing", "inactive", "null", "stress"]
)
def test_confirmation_retains_and_rejects_every_failed_required_pair(
    tmp_path, binding, corruption
):
    def corrupt(pairs):
        pair = pairs[0]
        if corruption == "incomplete":
            pair["control"]["harvest_complete"] = False
        elif corruption == "missing":
            pairs.pop()
        elif corruption == "inactive":
            pair["control"]["target_records"][0]["accepted"] = False
        elif corruption == "null":
            pair["omission"]["marketable_yield_kg"] = 1000
        else:
            for arm in ["control", "omission"]:
                record = pair[arm]["target_records"][0]
                record["root_vwc_by_ridge"] = {str(r): 0.3 for r in range(20, 44)}
                record["stressed_fraction"] = 0.0

    path, digest = report_file(tmp_path, binding, corrupt=corrupt)
    with pytest.raises(ValueError, match="recomputed acceptance"):
        validate_release_sensitivity(
            path, digest, confirmation_binding=binding.model_dump()
        )


def test_revision_protocol_and_variant_mismatches_fail_closed(
    tmp_path, binding, monkeypatch
):
    path, digest = report_file(tmp_path, binding)
    with pytest.raises(ValueError, match="not a candidate"):
        validate_release_sensitivity(path, digest)
    with pytest.raises(ValueError, match="variant mismatch"):
        validate_release_sensitivity(
            path,
            digest,
            confirmation_binding=binding.model_dump(),
            native_scenario={"scenario_revision": None, "calibration_candidate": False},
        )
    with pytest.raises(ValueError, match="protocol digest mismatch"):
        binding.model_copy(update={"protocol_digest": "d" * 64}).verify_current_source()
    monkeypatch.setattr(experiments, "execution_source_digest", lambda: "c" * 64)
    with pytest.raises(ValueError, match="execution source mismatch"):
        validate_release_sensitivity(
            path, digest, confirmation_binding=binding.model_dump()
        )


@pytest.mark.parametrize(
    "change",
    [
        {"confirmation_worlds": [0, 20, 21, 22, 23]},
        {"confirmation_worlds": [20, 21, 22, 23]},
        {"min_shortfall": 0.009},
        {"min_stressed_fraction": 0.49},
        {"harvest_retry_days": 22},
        {"calibration_candidate": False},
    ],
)
def test_binding_cannot_weaken_thresholds_mix_cohorts_or_change_candidate(
    binding, change
):
    with pytest.raises(ValueError):
        DroughtConfirmationBinding.model_validate({**binding.model_dump(), **change})


def test_variant_specification_has_exact_dose_and_runtime_rejects_default(tmp_path):
    process = author_process(
        "farm_disease_drought",
        scenario_revision="drought_pulse_v4",
        calibration_candidate=True,
    )
    doses = [
        c.expected
        for t in process.occurrence_net.transitions
        if t.action == "FieldOpsApp__irrigate" and t.phase == "reproduction"
        for c in t.arguments
        if c.name == "hours"
    ]
    assert doses == [5.0]
    path = tmp_path / "candidate.process.json"
    path.write_text(process.model_dump_json())
    config = DistributedRunnerConfig(
        scenario_id="farm_disease_drought",
        petri_spec_path=str(path),
        scenario_revision="drought_pulse_v4",
        calibration_candidate=True,
    )
    assert (
        NativeDistributedSeasonRunner._load_petri_net(config)[1].digest
        == process.digest
    )
    with pytest.raises(ValueError, match="variant mismatch"):
        NativeDistributedSeasonRunner._load_petri_net(
            config.model_copy(
                update={"scenario_revision": None, "calibration_candidate": False}
            )
        )


def test_matrix_variant_reaches_config_and_cannot_pair_with_historical_world():
    payload = {
        "scenarios": ["farm_disease_drought"],
        "world_seeds": [0],
        "conditions": [{"id": "reference"}],
    }
    historical = experiments.resolve_manifest(payload)[0]
    candidate = experiments.resolve_manifest(
        {
            **payload,
            "native_scenario_by_id": {
                "farm_disease_drought": {
                    "scenario_revision": "drought_pulse_v4",
                    "calibration_candidate": True,
                }
            },
        }
    )[0]
    assert (
        experiments._config_from_row(candidate).scenario_revision == "drought_pulse_v4"
    )
    assert all(
        candidate[k] != historical[k]
        for k in ["run_key", "pair_id", "world_cluster_id"]
    )


def test_reserved_cohorts_require_explicit_confirmation_design(tmp_path):
    with pytest.raises(ValueError, match="prospective confirmation manifest"):
        run_drought_calibration(
            tmp_path / "unused",
            world_seeds=list(range(20, 25)),
            candidate=False,
            dry_run=True,
        )
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("location", ["top_level", "condition"])
def test_portable_manifest_resolves_direct_spec_paths_from_manifest_directory(
    tmp_path, location
):
    import yaml

    folder = tmp_path / "manifests"
    folder.mkdir()
    spec = tmp_path / "process.json"
    spec.write_text("{}")
    payload = {
        "schema_version": "farm_dcore_matrix_v1",
        "scenarios": ["farm_wetjune_recheck"],
        "conditions": [{"id": "reference"}],
    }
    target = payload if location == "top_level" else payload["conditions"][0]
    target["petri_spec_path"] = "../process.json"
    path = folder / "manifest.yaml"
    path.write_text(yaml.safe_dump(payload))
    row = experiments.resolve_manifest(experiments.load_manifest(path))[0]
    assert Path(experiments._config_from_row(row).petri_spec_path) == spec


@pytest.mark.parametrize(
    "change",
    [
        {"world_seeds": [21, 22, 23, 24, 25]},
        {"scenario_revision": "drought_rootzone_v2"},
        {"harvest_retry_days": 14},
    ],
)
def test_confirmation_cannot_run_different_cohort_dose_or_reference_policy(
    tmp_path, binding, change
):
    path, _ = report_file(tmp_path, binding)
    plan = json.loads(path.read_text())["plan"]
    with pytest.raises(ValueError, match="prospectively frozen design"):
        binding.verify_plan({**plan, **change})


def test_preflight_uses_frozen_routes_instead_of_scripted_message_names():
    from are.simulation.distributed.preflight import _fault_contract

    selector = "selector:reproduction:field_intelligence:operations:1"
    kwargs = {"target_ids": (selector,)}
    assert _fault_contract("drop", {"opaque:42"}, route_selectors={selector}, **kwargs)[
        0
    ]
    active, reason = _fault_contract(
        "drop", {"handoff:midseason:v1"}, route_selectors=set(), **kwargs
    )
    assert not active and "no qualifying handoff" in reason


@pytest.mark.parametrize("execution", ["farmare_direct", "farmare_a2a"])
def test_matched_paper_baselines_call_shared_release_gate_before_provider(
    tmp_path, monkeypatch, execution
):
    from are.simulation.distributed.matched_baselines import run_matched_baseline

    process = author_process("farm_disease_drought")
    path = tmp_path / "process.json"
    path.write_text(process.model_dump_json())
    row = experiments.resolve_manifest(
        {
            "scenarios": ["farm_disease_drought"],
            "world_seeds": [100],
            "conditions": [{"id": "baseline", "execution": execution}],
        }
    )[0]
    row.update(paper_mode=True, scientific_contract="v5", petri_spec_path=str(path))

    def gate(config, net, team, process):
        assert config.paper_mode and process.digest
        raise ValueError("shared release gate blocked")

    monkeypatch.setattr(
        NativeDistributedSeasonRunner, "_validate_scientific_gate", gate
    )
    with pytest.raises(ValueError, match="shared release gate blocked"):
        run_matched_baseline(row, tmp_path / "no_provider")


def test_preflight_selects_treatment_mode_and_passes_exact_variant_to_reference(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from are.simulation.distributed import preflight

    process = author_process(
        "farm_disease_drought",
        scenario_revision="drought_pulse_v4",
        calibration_candidate=True,
    )
    path = tmp_path / "candidate.json"
    path.write_text(process.model_dump_json())
    selector = next(t for t in process.fault_treatments if t.mode == "drop").target_ids[
        0
    ]
    payload = {
        "scenarios": [process.scenario_id],
        "world_seeds": [0],
        "conditions": [{"id": "reference", "faults": ["drop", "delay"]}],
        "petri_spec_by_scenario": {process.scenario_id: str(path)},
        "native_scenario_by_id": {
            process.scenario_id: process.metadata["native_scenario"]
        },
    }
    monkeypatch.setattr(preflight, "load_manifest", lambda _: payload)
    outcome = {
        "exogenous_world_digest": "a" * 64,
        "harvest_complete": True,
        "storage_complete": True,
        "biological_yield_kg": 1000,
        "marketable_yield_kg": 900,
        "validation_success": True,
        "fault_manifestation": {"applied_rules": [{"route_selector": selector}]},
    }

    def run(config):
        assert (
            config.scenario_revision == "drought_pulse_v4"
            and config.calibration_candidate
        )
        return SimpleNamespace(trace=SimpleNamespace(outcome=outcome, events=[]))

    monkeypatch.setattr(
        preflight, "DistributedScenarioRunner", lambda: SimpleNamespace(run=run)
    )

    def native(scenario, seed, output, **settings):
        assert settings == process.metadata["native_scenario"]
        return outcome

    monkeypatch.setattr(preflight, "_native_oracle", native)
    report = preflight.run_no_model_preflight(
        tmp_path / "manifest.yaml", tmp_path / "preflight"
    )
    contracts = report["worlds"][0]["fault_contracts"]
    assert all(contracts[f]["active"] for f in ["drop", "delay"])
    assert all(
        "not an injected-fault test" in contracts[f]["assessment"] for f in contracts
    )
