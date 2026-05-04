from click.testing import CliRunner

from are.simulation.main import main


def test_main_cli_passes_a2a_options(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_scenarios_by_id(config, scenario_ids):
        captured["config"] = config
        captured["scenario_ids"] = scenario_ids
        return None

    monkeypatch.setattr("are.simulation.main.run_scenarios_by_id", fake_run_scenarios_by_id)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-s",
            "scenario_tutorial",
            "-m",
            "mock-model",
            "-mp",
            "mock",
            "--a2a-app-prop",
            "0.5",
            "--a2a-app-agent",
            "operations_expert_app_agent",
            "--a2a-policy",
            "typed_experts",
            "--a2a-model",
            "mock-model",
            "--a2a-model-provider",
            "mock",
            "--a2a-endpoint",
            "http://localhost:1234",
        ],
    )

    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert getattr(config, "a2a_app_prop") == 0.5
    assert getattr(config, "a2a_app_agent") == "operations_expert_app_agent"
    assert getattr(config, "a2a_policy") == "typed_experts"
    assert getattr(config, "a2a_model") == "mock-model"
    assert getattr(config, "a2a_model_provider") == "mock"
    assert getattr(config, "a2a_endpoint") == "http://localhost:1234"
