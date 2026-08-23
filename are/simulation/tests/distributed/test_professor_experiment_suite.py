from __future__ import annotations

import json
from pathlib import Path

import pytest

from are.simulation.agents.llm.llm_engine import LLMEngine
from are.simulation.distributed.experiments import (
    aggregate_directory,
    load_manifest,
    resolve_manifest,
    shard_rows,
)
from are.simulation.distributed.handoff import build_professor_handoff
from are.simulation.distributed.llm_budget import (
    team_llm_budget,
    wrap_with_active_budget,
)
from are.simulation.distributed.paper_report import (
    FIGURES,
    TABLES,
    generate_paper_report,
)
from are.simulation.distributed.preflight import run_no_model_preflight

CONFIG_ROOT = Path(__file__).parents[2] / "distributed" / "configs"


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("farm_dcore_primary_pass1.yaml", 480),
        ("farm_dcore_primary_pass2.yaml", 450),
        ("farm_dcore_controller_robustness.yaml", 270),
        ("farm_dcore_scalability.yaml", 45),
        ("farm_dcore_prior_model_continuity.template.yaml", 60),
    ),
)
def test_frozen_matrix_counts(name: str, expected: int):
    rows = resolve_manifest(load_manifest(CONFIG_ROOT / name))
    assert len(rows) == expected


def test_matrix_shards_are_disjoint_and_exhaustive():
    rows = resolve_manifest(load_manifest(CONFIG_ROOT / "farm_dcore_primary_pass1.yaml"))
    shards = [shard_rows(rows, shard_count=7, shard_index=index) for index in range(7)]
    keys = [{row["run_key"] for row in shard} for shard in shards]
    assert sum(len(group) for group in keys) == len(rows)
    assert set().union(*keys) == {row["run_key"] for row in rows}
    assert all(not keys[left] & keys[right] for left in range(7) for right in range(left + 1, 7))


class _CountingEngine(LLMEngine):
    def __init__(self):
        super().__init__("mock")

    def chat_completion(self, messages, stop_sequences=[], **kwargs):  # noqa: B006
        return "ok", {"total_tokens": 7}


def test_run_scoped_budget_is_shared_and_fail_closed():
    with team_llm_budget(2, 14) as budget:
        first = wrap_with_active_budget(_CountingEngine())
        second = wrap_with_active_budget(_CountingEngine())
        first([])
        second([])
        with pytest.raises(RuntimeError, match="team LLM budget exhausted"):
            first([])
    assert budget.calls == 2
    assert budget.tokens == 14


def test_report_generates_frozen_inventory(tmp_path: Path):
    source = tmp_path / "results.jsonl"
    row = {
        "scenario": "farm_wetjune_recheck",
        "team_id": "wetjune_2agent",
        "condition": "local_causal_audit",
        "fault": "none",
        "controller_profile_id": "primary_react",
        "model_configuration_id": "m1",
        "world_cluster_id": "farm_wetjune_recheck:w0",
        "pair_id": "p0",
        "success": True,
        "safety_success": True,
        "infrastructure_failure": False,
        "dcore_score": 1.0,
        "event_fidelity": 1.0,
        "causal_conformance": 1.0,
        "information_global_discordance": 0.0,
        "marketable_yield_shortfall": 0.0,
        "scientific_contract": "v5",
        "metric_version": "dcore_eval_v5",
    }
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")
    target = tmp_path / "report"
    manifest = generate_paper_report(source, target)
    assert len(manifest["tables"]) == 6
    assert len(manifest["figures"]) == 5
    for name in TABLES:
        assert (target / f"{name}.csv").is_file()
        assert (target / f"{name}.tex").is_file()
    for name in FIGURES:
        assert (target / f"{name}.png").is_file()
        assert (target / f"{name}.pdf").is_file()
    second = generate_paper_report(source, tmp_path / "report_second")
    assert manifest["artifact_sha256"] == second["artifact_sha256"]


def test_aggregate_and_report_recursively_merge_passes_and_shards(tmp_path: Path):
    root = tmp_path / "results"
    for index, relative in enumerate(("pass1/shard0", "pass2/shard1")):
        target = root / relative
        target.mkdir(parents=True)
        row = {
            "scenario": "farm_wetjune_recheck",
            "team_id": "wetjune_2agent",
            "condition": "local_causal_audit",
            "fault": "none",
            "controller_profile_id": "primary_react",
            "model_configuration_id": "m1",
            "world_cluster_id": f"farm_wetjune_recheck:w{index}",
            "pair_id": f"p{index}",
            "run_key": f"r{index}",
            "success": True,
            "event_fidelity": 1.0,
            "causal_conformance": 1.0,
            "dcore_score": 1.0,
            "marketable_yield_shortfall": 0.0,
            "scientific_contract": "v5",
            "metric_version": "dcore_eval_v5",
        }
        (target / "results.jsonl").write_text(
            json.dumps(row) + "\n", encoding="utf-8"
        )
    merged = tmp_path / "merged"
    aggregate_directory(root, merged)
    assert len((merged / "results.jsonl").read_text().splitlines()) == 2
    manifest = generate_paper_report(root, tmp_path / "report_recursive")
    assert manifest["source_row_count"] == 2


def test_handoff_refuses_placeholder_manifests(tmp_path: Path):
    with pytest.raises(ValueError, match="professor handoff refused"):
        build_professor_handoff(
            manifests=(CONFIG_ROOT / "farm_dcore_primary_pass1.yaml",),
            process_specs=(),
            team_specs=(),
            role_refinements=(),
            gate_manifests=(),
            output_dir=tmp_path / "handoff",
        )
    assert not (tmp_path / "handoff").exists()


def test_scientific_preflight_refuses_unresolved_review_artifacts(tmp_path: Path):
    with pytest.raises(ValueError, match="requires resolved reviewed artifacts"):
        run_no_model_preflight(
            CONFIG_ROOT / "farm_dcore_primary_pass1.yaml", tmp_path / "preflight"
        )


def test_paper_aggregation_rejects_missing_confirmatory_repeat(tmp_path: Path):
    row = {
        "analysis_block": "primary_pass_1",
        "paper_mode": True,
        "scientific_contract": "v5",
        "trace_version": "are_simulation_v1",
        "event_fidelity": None,
        "causal_conformance": None,
        "dcore_score": None,
        "scenario": "farm_wetjune_recheck",
        "condition": "farmare_direct",
        "fault": "none",
        "world_seed": 0,
        "repeat_index": 0,
        "controller_profile_id": "primary_react",
        "model_configuration_id": "m1",
        "run_key": "abc",
        "success": True,
        "infrastructure_failure": False,
    }
    source = tmp_path / "results.jsonl"
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing confirmatory repeats"):
        aggregate_directory(source, paper_mode=True)
