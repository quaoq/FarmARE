from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from are.simulation.distributed.cli import main
from are.simulation.distributed.diagnostic_validation import (
    _ordered_cc,
    _reduced_inputs,
    evaluate_validation_variants,
    flat_evidence_baseline,
)
from are.simulation.distributed.models import (
    AgentIntent,
    DecisionRecord,
    DistributedTrace,
    EventKind,
    FactVersionRecord,
    KnowledgeSnapshot,
    TraceEvent,
    stable_digest,
)
from are.simulation.distributed.petri import DataGuardSpec
from are.simulation.distributed.validation_study import (
    StudyPlan,
    agreement,
    freeze_plan,
    load_annotations,
    load_plan,
    predict_packet,
    sample_episodes,
    score_predictions,
    write_json,
)
from are.simulation.tests.distributed import test_farm_dcore_v5 as reference_tests


@pytest.fixture(scope="module")
def study_reference():
    return reference_tests.wetjune_v5.__wrapped__()


def tiny(available=("old", "new")):
    facts = tuple(
        FactVersionRecord(
            version_id=name,
            fact_key="ready",
            value=value,
            source_event_id="obs",
            evidence_ids=("obs",),
            world_time=when,
            learned_time=when,
            scope=(22, 32),
            valid_until=30,
        )
        for name, value, when in (("old", True, 1), ("new", False, 10))
    )
    events = tuple(
        TraceEvent(
            event_id=name,
            actor_id="ops",
            kind=kind,
            local_sequence=i + 1,
            logical_time=when,
            world_time=when,
            vector_clock={"ops": i + 1},
        )
        for i, (name, kind, when) in enumerate(
            (("obs", EventKind.OBSERVATION, 10), ("decision", EventKind.DECISION, 20))
        )
    )
    decision = DecisionRecord(
        decision_id="decision",
        actor_id="ops",
        logical_time=20,
        season_phase="r5",
        knowledge_snapshot=KnowledgeSnapshot(
            actor_id="ops",
            logical_time=20,
            item_ids=available,
            vector_clock={"ops": 2},
            digest="snapshot",
        ),
        proposed_intent=AgentIntent(kind="act", action="write"),
    )
    trace = DistributedTrace(
        run_id="tiny",
        task_id="tiny",
        actors=("ops",),
        events=events,
        fact_versions=facts,
        decisions=(decision,),
    )
    guard = DataGuardSpec(
        guard_id="ready",
        fact_key="ready",
        source="knowledge",
        expected=True,
        scope=(22, 32),
        required_evidence=True,
    )
    process = SimpleNamespace(
        digest="a" * 64,
        phase_windows=(),
        information_policies=(
            SimpleNamespace(
                actor_id="ops",
                phases=("r5",),
                action_patterns=("write",),
                requirements=(guard,),
            ),
        ),
    )
    return process, trace


def test_same_action_projection_can_hide_different_information():
    process, delivered = tiny()
    _, absent = tiny(("old",))

    def projection(trace):
        return [
            (d.proposed_intent.action, d.proposed_intent.args) for d in trace.decisions
        ]

    assert projection(delivered) == projection(absent)
    assert (
        flat_evidence_baseline(process, delivered)["decisions"][0][
            "prerequisites_satisfied"
        ]
        is False
    )
    assert (
        flat_evidence_baseline(process, absent)["decisions"][0][
            "prerequisites_satisfied"
        ]
        is True
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("scope", "missing_scoped_evidence"),
        ("expiry", "expired_evidence"),
        ("future", "missing_scoped_evidence"),
        ("support", "unsupported_evidence"),
    ],
)
def test_flat_checker_preserves_unknown_and_temporal_scope_limits(change, reason):
    process, trace = tiny(("new",))
    update = {
        "scope": {"scope": (0, 10)},
        "expiry": {"valid_until": 19},
        "future": {"learned_time": 21},
        "support": {"evidence_ids": ("missing",)},
    }[change]
    trace = trace.model_copy(
        update={"fact_versions": (trace.fact_versions[1].model_copy(update=update),)}
    )
    report = flat_evidence_baseline(process, trace)
    assert report["decisions"][0]["prerequisites_satisfied"] is None
    assert report["decisions"][0]["requirements"][0]["reason"] == reason
    assert report["coverage"] == 0.0


def test_hidden_world_and_runtime_verdicts_do_not_enter_flat_checker():
    process, trace = tiny(())
    world = trace.fact_versions[0].model_copy(update={"authoritative": True})
    trace = trace.model_copy(
        update={
            "fact_versions": (world,),
            "configuration": {"local_conforming": True},
            "outcome": {"score": 1.0},
        }
    )
    assert (
        flat_evidence_baseline(process, trace)["decisions"][0][
            "prerequisites_satisfied"
        ]
        is None
    )


def test_ablations_keep_sources_and_remove_only_declared_checks(study_reference):
    result, process = study_reference
    digest = process.digest
    trace_digest = stable_digest(result.trace.model_dump(mode="json"))
    reduced, trace = _reduced_inputs(process, result.trace, "no_expiry")
    assert all(
        g.max_age is None for p in reduced.information_policies for g in p.requirements
    )
    assert all(f.valid_until is None for f in trace.fact_versions)
    assert [f.world_time for f in trace.fact_versions] == [
        f.world_time for f in result.trace.fact_versions
    ]
    assert trace.events == result.trace.events
    no_provenance, _ = _reduced_inputs(process, result.trace, "no_provenance")
    assert all(
        not path.required_actor_path
        for o in no_provenance.causal_obligations
        for path in o.alternatives
    )
    assert (
        process.digest == digest
        and stable_digest(result.trace.model_dump(mode="json")) == trace_digest
    )


def test_serialization_ablation_exposes_false_order_evidence():
    process = SimpleNamespace(
        occurrence_net=SimpleNamespace(
            modules=(SimpleNamespace(module_id="m", weight_budget=1.0),)
        )
    )
    trace = SimpleNamespace(
        events=(SimpleNamespace(event_id="a"), SimpleNamespace(event_id="b"))
    )
    report = {
        "event_acceptance": {
            "matches": {
                "source": {"observed_event_id": "a"},
                "target": {"observed_event_id": "b"},
            }
        },
        "semantic_causal_obligations": {
            "details": [
                {
                    "module_id": "m",
                    "weight": 1.0,
                    "alternatives": [
                        {
                            "edges": [
                                {
                                    "source": "source",
                                    "target": "target",
                                    "passed": False,
                                }
                            ],
                            "actor_path_passed": True,
                            "guards": [{"passed": True}],
                        }
                    ],
                }
            ]
        },
    }
    assert _ordered_cc(process, trace, report) == 1.0
    trace.events = tuple(reversed(trace.events))
    assert _ordered_cc(process, trace, report) == 0.0


def test_saved_trace_variant_report_is_analysis_only(study_reference):
    result, process = study_reference
    report = evaluate_validation_variants(process, result.trace)
    assert report["analysis_only"] and not report["paper_eligible"]
    assert [r["variant"] for r in report["ablations"]] == [
        "full",
        "no_expiry",
        "no_provenance",
        "serialization_order",
    ]
    assert report["ablations"][0]["event_fidelity"] == 1.0
    assert report["baseline"]["decisions"]
    assert report["trace_digest"] == stable_digest(result.trace.model_dump(mode="json"))


@pytest.fixture
def pool(tmp_path, study_reference):
    result, process = study_reference
    root = tmp_path / "runs"
    for world in range(2):
        run = root / str(world)
        run.mkdir(parents=True)
        trace = result.trace.model_copy(
            update={
                "run_id": f"fixture-{world}",
                "configuration": {
                    **result.trace.configuration,
                    "world_seed": world,
                    "controller_mode": "scripted",
                    "model": "PRIVATE_MODEL_SENTINEL",
                    "fault": "PRIVATE_FAULT_SENTINEL",
                },
                "outcome": {"yield": "PRIVATE_YIELD_SENTINEL"},
            }
        )
        write_json(run / "trace.dcore_trace_v5.json", trace.model_dump(mode="json"))
        write_json(run / "farm_process_spec_v5.json", process.model_dump(mode="json"))
    plan = StudyPlan(
        manifest_sha256="a" * 64,
        process_digests=(process.digest,),
        episodes=4,
        max_per_run=2,
        fixture_only=True,
        bootstrap_replicates=100,
    )
    return root, plan


def test_sampling_is_reproducible_balanced_and_blinded(tmp_path, pool):
    root, plan = pool
    a, b = tmp_path / "a", tmp_path / "b"
    first, second = sample_episodes(plan, root, a), sample_episodes(plan, root, b)
    assert first == second and first["sampled"] == 4
    text = (a / "public/episodes.json").read_text()
    assert all(
        s not in text
        for s in (
            "PRIVATE_MODEL_SENTINEL",
            "PRIVATE_FAULT_SENTINEL",
            "PRIVATE_YIELD_SENTINEL",
            '"guard_reconstruction"',
            '"local_conforming"',
        )
    )
    public = json.loads(text)
    assert all(e["annotation"]["assessment"] is None for e in public["episodes"])
    manifest = json.loads((a / "private/manifest.json").read_text())
    assert len({r["trace_sha256"] for r in manifest["selected"]}) == 2
    assert not manifest["population_prevalence_estimable"]


def test_no_silent_resampling_or_duplicate_runs(tmp_path, pool):
    root, plan = pool
    output = tmp_path / "packet"
    result = sample_episodes(plan.model_copy(update={"episodes": 20}), root, output)
    assert result["shortfall"] == 16
    with pytest.raises(FileExistsError):
        sample_episodes(plan, root, output)
    target = root / "copy"
    target.mkdir()
    for name in ("trace.dcore_trace_v5.json", "farm_process_spec_v5.json"):
        (target / name).write_bytes((root / "0" / name).read_bytes())
    with pytest.raises(ValueError, match="duplicate trace"):
        sample_episodes(plan, root, tmp_path / "duplicates")


def test_fixture_pool_cannot_enter_real_model_study(tmp_path, pool):
    root, plan = pool
    with pytest.raises(ValueError, match="no eligible"):
        sample_episodes(
            plan.model_copy(update={"fixture_only": False}), root, tmp_path / "paper"
        )
    assert not (tmp_path / "paper").exists()


def completed(packet, who, assessments):
    return {
        "packet_digest": stable_digest(packet),
        "annotator_id": who,
        "episodes": [
            {
                "episode_id": e["episode_id"],
                "assessment": a,
                "failure_labels": ["transit_gap"] if a == "failure" else [],
                "prerequisites_satisfied": None,
                "evidence_ids": [],
                "message_mappings": [],
                "rationale": "Synthetic annotation for a software test only.",
            }
            for e, a in zip(packet["episodes"], assessments, strict=True)
        ],
    }


@pytest.fixture
def annotated(tmp_path, pool):
    root, plan = pool
    destination = tmp_path / "packet"
    sample_episodes(plan, root, destination)
    packet = json.loads((destination / "public/episodes.json").read_text())
    paths = []
    for who, labels in (
        ("left", ["failure", "no_failure", "insufficient_evidence", "no_failure"]),
        ("right", ["no_failure", "no_failure", "insufficient_evidence", "no_failure"]),
        ("judge", ["failure", "no_failure", "insufficient_evidence", "no_failure"]),
    ):
        path = tmp_path / f"{who}.json"
        write_json(path, completed(packet, who, labels))
        paths.append(path)
    return destination, packet, paths


def test_annotation_validation_retains_uncertainty_and_disagreements(annotated):
    _, packet, (left, right, _) = annotated
    report = agreement(packet, left, right)
    assert report["assessment_agreement"] == 0.75
    assert len(report["disagreements"]) == 1
    with pytest.raises(ValueError, match="distinct"):
        agreement(packet, left, left)
    broken = json.loads(left.read_text())
    broken["episodes"] = broken["episodes"][:-1]
    left.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="exactly once"):
        load_annotations(left, packet)


def test_unfilled_templates_and_unavailable_evidence_are_rejected(annotated):
    destination, packet, (left, _, _) = annotated
    with pytest.raises(ValueError):
        load_annotations(destination / "public/annotator_a.json", packet)
    broken = json.loads(left.read_text())
    broken["episodes"][0]["evidence_ids"] = ["unknown"]
    left.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="unavailable evidence"):
        load_annotations(left, packet)


def test_scores_separate_coverage_from_accuracy_and_use_world_clusters(
    tmp_path, annotated
):
    destination, packet, (left, right, judge) = annotated
    ids = [e["episode_id"] for e in packet["episodes"]]
    dcore = [
        {
            "episode_id": k,
            "assessment": a,
            "failure_labels": ["transit_gap"] if a == "failure" else [],
        }
        for k, a in zip(
            ids, ("failure", "no_failure", "no_failure", "no_failure"), strict=True
        )
    ]
    simple = [
        {"episode_id": k, "assessment": "insufficient_evidence", "failure_labels": None}
        for k in ids
    ]
    predictions = tmp_path / "predictions.json"
    write_json(
        predictions,
        {
            "packet_digest": stable_digest(packet),
            "methods": {"dcore": dcore, "flat_evidence": simple},
        },
    )
    report = score_predictions(destination, judge, predictions, left, right)
    assert report["human_insufficient_evidence"] == 1
    assert report["methods"]["flat_evidence"]["coverage"] == 0.0
    assert report["methods"]["flat_evidence"]["selective_accuracy"] is None
    assert report["methods"]["flat_evidence"]["per_label"] is None
    assert report["paired_comparison"]["dcore_minus_flat_accuracy"] == 1.0
    assert report["paired_comparison"]["world_clusters"] == 2
    assert report["paired_comparison"]["world_cluster_bootstrap_95_interval"] == [
        1.0,
        1.0,
    ]
    with pytest.raises(ValueError, match="distinct adjudicator"):
        score_predictions(destination, left, predictions, left, right)


def test_prediction_packet_checks_source_integrity(tmp_path, pool):
    root, plan = pool
    destination = tmp_path / "packet"
    sample_episodes(plan, root, destination)
    predictions = predict_packet(destination)
    assert set(predictions["methods"]) == {"dcore", "flat_evidence"}
    assert all(len(v) == 4 for v in predictions["methods"].values())
    path = root / "0/trace.dcore_trace_v5.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="source has changed"):
        predict_packet(destination)


def test_plan_precommitment_and_cli_refuse_unreviewed_or_changed_inputs(tmp_path, pool):
    root, _ = pool
    spec = root / "0/farm_process_spec_v5.json"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("study: fixture\n")
    output = tmp_path / "plan.json"
    with pytest.raises(ValueError, match="complete frozen specifications"):
        freeze_plan(manifest, [spec], output)
    assert not output.exists()
    args = [
        "validation",
        "plan",
        "--manifest",
        str(manifest),
        "--process",
        str(spec),
        "--output",
        str(output),
        "--fixture-only",
        "--episodes",
        "4",
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert load_plan(output).episodes == 4
    changed = json.loads(output.read_text())
    changed["plan"]["episodes"] = 5
    output.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="digest mismatch"):
        load_plan(output)


def test_predictions_cannot_be_written_into_blinded_packet(tmp_path, pool):
    root, plan = pool
    destination = tmp_path / "packet"
    sample_episodes(plan, root, destination)
    output = destination / "public/predictions.json"
    result = CliRunner().invoke(
        main,
        [
            "validation",
            "predict",
            "--packet-dir",
            str(destination),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0 and "outside the blinded" in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    "broken", [None, "dropped", "quote", "future", "hidden", "sender"]
)
def test_human_text_mapping_requires_received_and_available_evidence(tmp_path, broken):
    episode = {
        "episode_id": "episode_001",
        "decision": {"actor": "agent_2", "logical_time": 5},
        "events": [
            {"id": "obs", "kind": "observation", "logical_time": 1},
            {
                "id": "send",
                "kind": "message_send",
                "message_id": "m1",
                "actor": "agent_1",
                "logical_time": 2,
                "world_time": 2,
                "text": "The window is closed.",
            },
            {
                "id": "receive",
                "kind": "message_receive",
                "message_id": "m1",
                "actor": "agent_2",
                "logical_time": 3,
            },
        ],
        "facts": [
            {
                "id": "f1",
                "authoritative": False,
                "observed_at": 1,
                "learned_at": 1,
                "visible_to": ["agent_1"],
                "source_event": "obs",
            }
        ],
    }
    if broken == "dropped":
        episode["events"].pop()
    elif broken == "future":
        episode["events"][0]["logical_time"] = 4
    elif broken == "hidden":
        episode["facts"][0]["authoritative"] = True
    elif broken == "sender":
        episode["facts"][0]["visible_to"] = ["agent_2"]
    packet = {"episodes": [episode]}
    data = completed(packet, "independent", ["insufficient_evidence"])
    data["episodes"][0]["message_mappings"] = [
        {
            "message_id": "m1",
            "source_fact_id": "f1",
            "quote": "fabricated" if broken == "quote" else "window is closed",
        }
    ]
    path = tmp_path / "labels.json"
    write_json(path, data)
    if broken:
        with pytest.raises(ValueError, match="mapping requires|not available"):
            load_annotations(path, packet)
    else:
        _, rows = load_annotations(path, packet)
        assert rows["episode_001"].assessment == "insufficient_evidence"
        assert len(rows["episode_001"].message_mappings) == 1


def test_unverifiable_handoffs_are_abstentions_even_with_known_world(
    tmp_path, pool, monkeypatch
):
    from are.simulation.distributed import evaluator_v5

    root, plan = pool
    destination = tmp_path / "packet"
    sample_episodes(plan, root, destination)

    def unverifiable_report(process, trace):
        return {
            "attribution": [
                {"target_event_id": e.event_id, "primary": "unverifiable_handoff"}
                for e in trace.events
                if e.kind == EventKind.ACTION
            ],
            "information_policy_conformance": {
                "details": [
                    {
                        "decision_id": d.decision_id,
                        "global_verdicts": {"ready": "true"},
                        "global_conforming": True,
                    }
                    for d in trace.decisions
                ]
            },
        }

    monkeypatch.setattr(evaluator_v5, "evaluate_farm_dcore_v5", unverifiable_report)
    predictions = predict_packet(destination)["methods"]["dcore"]
    assert any(r["unverifiable_handoff"] for r in predictions)
    assert all(
        r["assessment"] == "insufficient_evidence" and not r["failure_labels"]
        for r in predictions
        if r["unverifiable_handoff"]
    )


def test_cli_rejects_omitted_precommitted_sensitivity_variant(tmp_path, pool):
    root, plan = pool
    from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5

    process_path = root / "0/farm_process_spec_v5.json"
    process = FarmProcessSpecV5.model_validate_json(process_path.read_text())
    plan = plan.model_copy(
        update={"sensitivity_families": {process.process_id: ("b" * 64,)}}
    )
    plan_path = tmp_path / "plan.json"
    write_json(
        plan_path,
        {
            "plan": plan.model_dump(mode="json"),
            "plan_digest": stable_digest(plan.model_dump(mode="json")),
        },
    )
    output = tmp_path / "analysis.json"
    result = CliRunner().invoke(
        main,
        [
            "validation",
            "evaluate",
            "--plan",
            str(plan_path),
            "--process",
            str(process_path),
            "--trace",
            str(root / "0/trace.dcore_trace_v5.json"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0 and "every sensitivity variant" in result.output
    assert not output.exists()
