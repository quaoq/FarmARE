import json
import subprocess
from pathlib import Path

import pytest
import yaml

from are.simulation.agent_suite.suite_runner import (
    SuiteConfigError,
    _build_command,
    expand_run_specs,
    load_suite_config,
    run_suite,
)


def _write_config(tmp_path, payload):
    config_path = tmp_path / "suite.yaml"
    with open(config_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle)
    return config_path


def test_load_suite_config_missing_sections(tmp_path):
    config_path = _write_config(tmp_path, {"scenario_sets": {}})
    with pytest.raises(SuiteConfigError):
        load_suite_config(config_path)


def test_expand_run_specs_deterministic(tmp_path):
    payload = {
        "defaults": {"output_root": "outputs/agent_suite/test", "repeats": 1},
        "scenario_sets": {"farm": ["scenario_farm_world_irrigation"]},
        "model_profiles": {"mock": {"model": "mock-model", "provider": "mock"}},
        "packs": {
            "pack_a": {
                "families": ["farm_baseline_react", "farm_tree_search"],
                "scenario_sets": ["farm"],
                "model_profile": "mock",
                "repeats": 2,
                "a2a": {
                    "enabled": True,
                    "app_prop": 0.5,
                    "policy": "typed_experts",
                },
            }
        },
    }
    config_path = _write_config(tmp_path, payload)
    config = load_suite_config(config_path)
    first = expand_run_specs(config, force_mock=False, force_real=False)
    second = expand_run_specs(config, force_mock=False, force_real=False)

    first_ids = [spec.run_id for spec in first]
    second_ids = [spec.run_id for spec in second]
    assert first_ids == second_ids
    assert len(first_ids) == 4
    assert first[0].a2a_enabled is True
    assert first[0].a2a_policy == "typed_experts"
    assert first[0].a2a_app_prop == 0.5


def test_run_suite_dry_run_outputs_manifest(tmp_path):
    payload = {
        "defaults": {"output_root": "outputs/agent_suite/test", "repeats": 1},
        "scenario_sets": {"farm": ["scenario_farm_world_irrigation"]},
        "model_profiles": {"mock": {"model": "mock-model", "provider": "mock"}},
        "packs": {
            "smoke": {
                "families": ["farm_baseline_react"],
                "scenario_sets": ["farm"],
                "model_profile": "mock",
            }
        },
    }
    config_path = _write_config(tmp_path, payload)
    config = load_suite_config(config_path)
    specs = expand_run_specs(config, force_mock=True, force_real=False)
    result = run_suite(specs, dry_run=True, repo_root=tmp_path)

    manifest_path = result["manifest_path"]
    assert result["mode"] == "dry_run"
    with open(manifest_path, "r", encoding="utf-8") as handle:
        rows = json.load(handle)
    assert len(rows) == 1
    assert rows[0]["family"] == "farm_baseline_react"


def test_build_command_includes_a2a_flags(tmp_path):
    payload = {
        "defaults": {"output_root": "outputs/agent_suite/test", "repeats": 1},
        "scenario_sets": {"farm": ["scenario_farm_world_irrigation"]},
        "model_profiles": {
            "mock": {"model": "mock-model", "provider": "mock"},
            "mock_alt": {"model": "mock-model-2", "provider": "mock"},
        },
        "packs": {
            "smoke": {
                "families": ["farm_baseline_react"],
                "scenario_sets": ["farm"],
                "model_profile": "mock",
                "a2a": {
                    "enabled": True,
                    "app_prop": 0.4,
                    "policy": "typed_experts",
                    "app_agent": "default_app_agent",
                    "model_profile": "mock_alt",
                },
            }
        },
    }
    config_path = _write_config(tmp_path, payload)
    config = load_suite_config(config_path)
    specs = expand_run_specs(config, force_mock=False, force_real=False)
    command = _build_command(specs[0])
    joined = " ".join(command)
    assert "--a2a-app-prop 0.4" in joined
    assert "--a2a-policy typed_experts" in joined
    assert "--a2a-model mock-model-2" in joined
    assert "--a2a-model-provider mock" in joined


def test_force_real_prefers_o4mini_when_preflight_succeeds(tmp_path, monkeypatch):
    payload = {
        "defaults": {"output_root": "outputs/agent_suite/test", "repeats": 1},
        "scenario_sets": {"farm": ["scenario_farm_world_irrigation"]},
        "model_profiles": {"openai_fast": {"model": "x", "provider": "llama-api"}},
        "packs": {
            "smoke": {
                "families": ["farm_baseline_react"],
                "scenario_sets": ["farm"],
                "model_profile": "openai_fast",
            }
        },
    }
    config_path = _write_config(tmp_path, payload)
    config = load_suite_config(config_path)

    monkeypatch.setattr(
        "are.simulation.agent_suite.suite_runner._preflight_model_access",
        lambda model, provider, endpoint: (True, None),
    )
    specs = expand_run_specs(
        config,
        force_mock=False,
        force_real=True,
        enable_real_model_preflight=True,
    )
    assert specs[0].model == "o4-mini"
    assert specs[0].model_resolution == "real_preflight_o4_mini"


def test_force_real_falls_back_when_o4mini_unavailable(tmp_path, monkeypatch):
    payload = {
        "defaults": {"output_root": "outputs/agent_suite/test", "repeats": 1},
        "scenario_sets": {"farm": ["scenario_farm_world_irrigation"]},
        "model_profiles": {"openai_fast": {"model": "x", "provider": "llama-api"}},
        "packs": {
            "smoke": {
                "families": ["farm_baseline_react"],
                "scenario_sets": ["farm"],
                "model_profile": "openai_fast",
            }
        },
    }
    config_path = _write_config(tmp_path, payload)
    config = load_suite_config(config_path)

    def _fake_preflight(model, provider, endpoint):
        if model == "o4-mini":
            return False, "unavailable"
        return True, None

    monkeypatch.setattr(
        "are.simulation.agent_suite.suite_runner._preflight_model_access",
        _fake_preflight,
    )
    specs = expand_run_specs(
        config,
        force_mock=False,
        force_real=True,
        enable_real_model_preflight=True,
    )
    assert specs[0].model == "gpt-4o-mini"
    assert specs[0].model_resolution == "real_fallback_gpt_4o_mini"


def test_run_suite_uses_timestamp_scoped_output_dirs(tmp_path, monkeypatch):
    payload = {
        "defaults": {"output_root": "outputs/agent_suite/test", "repeats": 1},
        "scenario_sets": {"farm": ["scenario_farm_world_irrigation"]},
        "model_profiles": {"mock": {"model": "mock-model", "provider": "mock"}},
        "packs": {
            "smoke": {
                "families": ["farm_baseline_react"],
                "scenario_sets": ["farm"],
                "model_profile": "mock",
            }
        },
    }
    config_path = _write_config(tmp_path, payload)
    config = load_suite_config(config_path)
    specs = expand_run_specs(config, force_mock=True, force_real=False)

    stale_dir = tmp_path / "outputs" / "agent_suite" / "test" / specs[0].run_id
    stale_dir.mkdir(parents=True, exist_ok=True)
    stale_output = stale_dir / "output.jsonl"
    stale_output.write_text(
        json.dumps(
            {
                "task_id": "stale",
                "trace_id": "stale-trace",
                "score": 0.0,
                "metadata": {"status": "stale", "telemetry": {"llm_calls": 0}},
            }
        ),
        encoding="utf-8",
    )

    def _fake_subprocess_run(command, cwd, capture_output, text, check, env):
        output_dir = Path(command[command.index("--output_dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "output.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "fresh",
                    "trace_id": "fresh-trace",
                    "score": 1.0,
                    "metadata": {"status": "success", "telemetry": {"llm_calls": 2}},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(
        "are.simulation.agent_suite.suite_runner.subprocess.run",
        _fake_subprocess_run,
    )
    result = run_suite(specs, dry_run=False, repo_root=tmp_path)
    row = result["rows"][0]

    assert row["score"] == 1.0
    assert row["task_id"] == "fresh"
    assert Path(row["output_dir"]) != stale_dir
    assert "agent_suite_runs" in row["output_dir"]
