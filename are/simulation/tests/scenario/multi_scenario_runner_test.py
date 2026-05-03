# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


import time
from unittest.mock import patch

import pytest

from are.simulation.cli.utils import run_scenarios_by_json_files
from are.simulation.multi_scenario_runner import (
    MultiScenarioRunner,
    ScenarioTimeoutError,
    _create_scenario_runner_config,
)
from are.simulation.scenario_runner import ScenarioRunner
from are.simulation.scenarios.config import MultiScenarioRunnerConfig
from are.simulation.scenarios.scenario import Scenario, ScenarioValidationResult


class MockSlowScenario(Scenario):
    """Mock scenario that simulates a long-running scenario."""

    def __init__(
        self, scenario_id: str = "test_slow_scenario", sleep_duration: float = 2.0
    ):
        self.scenario_id = scenario_id
        self.sleep_duration = sleep_duration

    def __str__(self):
        return f"MockSlowScenario({self.scenario_id})"


def test_create_scenario_runner_config_preserves_a2a_fields():
    scenario = MockSlowScenario("scenario_with_a2a")
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="farm_baseline_react",
        a2a_app_prop=0.4,
        a2a_app_agent="default_app_agent",
        a2a_model="o4-mini",
        a2a_model_provider="llama-api",
        a2a_endpoint="https://eu.api.openai.com/v1",
        a2a_policy="typed_experts",
        agent_max_iterations=9,
    )

    runner_config = _create_scenario_runner_config(config, scenario)
    assert runner_config.a2a_app_prop == 0.4
    assert runner_config.a2a_app_agent == "default_app_agent"
    assert runner_config.a2a_model == "o4-mini"
    assert runner_config.a2a_model_provider == "llama-api"
    assert runner_config.a2a_endpoint == "https://eu.api.openai.com/v1"
    assert runner_config.a2a_policy == "typed_experts"
    assert runner_config.agent_max_iterations == 9


def mock_slow_scenario_runner_run(runner_config, scenario, completed_events):
    """Mock ScenarioRunner.run that simulates a slow scenario."""
    # Simulate a scenario that takes longer than the timeout
    time.sleep(scenario.sleep_duration)
    return ScenarioValidationResult(success=True)


def test_scenario_timeout_functionality():
    """Test that scenarios timeout correctly when they exceed the configured timeout."""
    # Create a config with a very short timeout (1 second)
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        timeout_seconds=1,  # 1 second timeout
        max_concurrent_scenarios=1,
        export=False,
        output_dir="/tmp/test_timeout",
    )

    # Create a mock scenario that will take 2 seconds (longer than timeout)
    slow_scenario = MockSlowScenario("test_timeout_scenario", sleep_duration=2.0)
    scenarios: list[Scenario] = [slow_scenario]

    # Mock the ScenarioRunner.run method to simulate slow execution
    with patch.object(ScenarioRunner, "run", side_effect=mock_slow_scenario_runner_run):
        runner = MultiScenarioRunner()
        result = runner.run(config, scenarios)

    # Verify the results
    assert result is not None
    assert result.failed_count == 1
    assert result.successful_count == 0
    assert result.exception_count == 0

    # Check that the scenario result contains the timeout error
    scenario_result = result.scenario_results[("test_timeout_scenario", None)]
    assert scenario_result.success is False
    assert isinstance(scenario_result.exception, ScenarioTimeoutError)
    assert "timed out after 1 seconds" in str(scenario_result.exception)


def test_scenario_no_timeout_when_within_limit():
    """Test that scenarios complete successfully when they finish within the timeout."""
    # Create a config with a generous timeout (5 seconds)
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        timeout_seconds=5,  # 5 second timeout
        max_concurrent_scenarios=1,
        export=False,
        output_dir="/tmp/test_no_timeout",
    )

    # Create a mock scenario that will complete quickly (0.1 seconds)
    fast_scenario = MockSlowScenario("test_fast_scenario", sleep_duration=0.1)
    scenarios: list[Scenario] = [fast_scenario]

    # Mock the ScenarioRunner.run method
    with patch.object(ScenarioRunner, "run", side_effect=mock_slow_scenario_runner_run):
        runner = MultiScenarioRunner()
        result = runner.run(config, scenarios)

    # Verify the results
    assert result is not None
    assert result.successful_count == 1
    assert result.failed_count == 0
    assert result.exception_count == 0

    # Check that the scenario result is successful
    scenario_result = result.scenario_results[("test_fast_scenario", None)]
    assert scenario_result.success is True
    assert scenario_result.exception is None


def test_scenario_no_timeout_when_not_configured():
    """Test that scenarios run without timeout when timeout_seconds is None."""
    # Create a config without timeout
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        timeout_seconds=None,  # No timeout
        max_concurrent_scenarios=1,
        export=False,
        output_dir="/tmp/test_no_timeout_config",
    )

    # Create a mock scenario that would normally timeout
    scenario = MockSlowScenario("test_no_timeout_scenario", sleep_duration=0.1)
    scenarios: list[Scenario] = [scenario]

    # Mock the ScenarioRunner.run method
    with patch.object(ScenarioRunner, "run", side_effect=mock_slow_scenario_runner_run):
        runner = MultiScenarioRunner()
        result = runner.run(config, scenarios)

    # Verify the results - should succeed since no timeout is configured
    assert result is not None
    assert result.successful_count == 1
    assert result.failed_count == 0
    assert result.exception_count == 0

    # Check that the scenario result is successful
    scenario_result = result.scenario_results[("test_no_timeout_scenario", None)]
    assert scenario_result.success is True
    assert scenario_result.exception is None


def test_multiple_scenarios_with_mixed_timeout_results():
    """Test timeout behavior with multiple scenarios where some timeout and others succeed."""
    # Create a config with a 1.5 second timeout
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        timeout_seconds=1,  # 1 second timeout
        max_concurrent_scenarios=2,
        export=False,
        output_dir="/tmp/test_mixed_timeout",
    )

    # Create scenarios with different execution times
    fast_scenario = MockSlowScenario(
        "fast_scenario", sleep_duration=0.5
    )  # Will succeed
    slow_scenario = MockSlowScenario(
        "slow_scenario", sleep_duration=2.0
    )  # Will timeout
    scenarios: list[Scenario] = [fast_scenario, slow_scenario]

    # Mock the ScenarioRunner.run method
    with patch.object(ScenarioRunner, "run", side_effect=mock_slow_scenario_runner_run):
        runner = MultiScenarioRunner()
        result = runner.run(config, scenarios)

    # Verify the results
    assert result is not None
    assert result.successful_count == 1  # fast_scenario should succeed
    assert result.failed_count == 1  # slow_scenario should timeout
    assert result.exception_count == 0

    # Check individual scenario results
    fast_result = result.scenario_results[("fast_scenario", None)]
    assert fast_result.success is True
    assert fast_result.exception is None

    slow_result = result.scenario_results[("slow_scenario", None)]
    assert slow_result.success is False
    assert isinstance(slow_result.exception, ScenarioTimeoutError)
    assert "timed out after 1 seconds" in str(slow_result.exception)


def test_timeout_error_type():
    """Test that ScenarioTimeoutError is properly defined and can be instantiated."""
    error = ScenarioTimeoutError("Test timeout message")
    assert isinstance(error, Exception)
    assert str(error) == "Test timeout message"


@pytest.mark.parametrize("timeout_value", [1, 5, 10])
def test_different_timeout_values(timeout_value):
    """Test that different timeout values work correctly."""
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        timeout_seconds=timeout_value,
        max_concurrent_scenarios=1,
        export=False,
        output_dir="/tmp/test_timeout_values",
    )

    # Create a scenario that will timeout (takes longer than any reasonable timeout)
    slow_scenario = MockSlowScenario(
        "timeout_test_scenario", sleep_duration=timeout_value + 1
    )
    scenarios: list[Scenario] = [slow_scenario]

    with patch.object(ScenarioRunner, "run", side_effect=mock_slow_scenario_runner_run):
        runner = MultiScenarioRunner()
        result = runner.run(config, scenarios)

    # Verify timeout occurred
    assert result.failed_count == 1
    scenario_result = result.scenario_results[("timeout_test_scenario", None)]
    assert scenario_result.success is False
    assert isinstance(scenario_result.exception, ScenarioTimeoutError)
    assert f"timed out after {timeout_value} seconds" in str(scenario_result.exception)


def test_run_scenarios_by_json_files_multiple_files():
    """Test that run_scenarios_by_json_files handles multiple JSON files correctly."""
    # Create mock scenarios
    mock_scenario1 = MockSlowScenario("json_scenario_1", sleep_duration=0.1)
    mock_scenario2 = MockSlowScenario("json_scenario_2", sleep_duration=0.1)

    # Create config
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        scenario_initialization_params="{}",
        scenario_creation_params="{}",
        export=False,
        output_dir="/tmp/test_multiple_json_scenarios",
        max_concurrent_scenarios=1,
    )

    # Mock the load_scenario function to return different scenarios for different paths
    def mock_load_scenario_side_effect(path):
        if "scenario1.json" in path:
            return mock_scenario1
        elif "scenario2.json" in path:
            return mock_scenario2
        else:
            raise ValueError(f"Unexpected path: {path}")

    with (
        patch(
            "are.simulation.scenarios.utils.load_utils.load_scenario",
            side_effect=mock_load_scenario_side_effect,
        ) as mock_load_scenario,
        patch(
            "are.simulation.cli.utils._initialize_loaded_scenario",
            side_effect=lambda s, c: s,
        ) as mock_initialize,
        patch.object(
            ScenarioRunner, "run", return_value=ScenarioValidationResult(success=True)
        ) as _,
    ):
        json_file_paths = ["/tmp/scenario1.json", "/tmp/scenario2.json"]
        result = run_scenarios_by_json_files(config, json_file_paths)

    # Verify that load_scenario was called for both files
    assert mock_load_scenario.call_count == 2
    mock_load_scenario.assert_any_call("/tmp/scenario1.json")
    mock_load_scenario.assert_any_call("/tmp/scenario2.json")

    # Verify that _initialize_loaded_scenario was called for both scenarios
    assert mock_initialize.call_count == 2

    # Verify the results
    assert result is not None
    assert result.successful_count == 2
    assert result.failed_count == 0
    assert result.exception_count == 0

    # Check that both scenario results are successful
    scenario_result1 = result.scenario_results[("json_scenario_1", None)]
    assert scenario_result1.success is True

    scenario_result2 = result.scenario_results[("json_scenario_2", None)]
    assert scenario_result2.success is True


def test_run_scenarios_by_json_files_empty_list():
    """Test that run_scenarios_by_json_files raises ValueError when no JSON files are provided."""
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        scenario_initialization_params="{}",
        scenario_creation_params="{}",
        export=False,
        output_dir="/tmp/test_empty_json_list",
    )
    with pytest.raises(ValueError, match="No JSON files provided"):
        run_scenarios_by_json_files(config, [])


def test_run_scenarios_by_json_files_load_failure():
    """Test that run_scenarios_by_json_files handles load_scenario failures correctly."""
    config = MultiScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        scenario_initialization_params="{}",
        scenario_creation_params="{}",
        export=False,
        output_dir="/tmp/test_json_load_failure",
    )

    # Mock load_scenario to raise an exception
    with patch(
        "are.simulation.scenarios.utils.load_utils.load_scenario",
        side_effect=Exception("Failed to load JSON"),
    ) as mock_load_scenario:
        with pytest.raises(
            ValueError,
            match="Failed to load scenario from /tmp/invalid.json: Failed to load JSON",
        ):
            run_scenarios_by_json_files(config, ["/tmp/invalid.json"])

    # Verify that load_scenario was called
    mock_load_scenario.assert_called_once_with("/tmp/invalid.json")


def test_multi_scenario_validation_result_add_result():
    """Test that the add_result method works correctly with tuple keys."""
    from are.simulation.scenarios.config import MultiScenarioRunnerConfig
    from are.simulation.scenarios.validation_result import (
        MultiScenarioValidationResult,
        ScenarioValidationResult,
    )

    # Create a test config
    config = MultiScenarioRunnerConfig(model="test-model", agent="test-agent")

    # Create a MultiScenarioValidationResult
    result = MultiScenarioValidationResult(run_config=config)

    # Create mock scenario objects
    from are.simulation.scenarios.scenario import Scenario

    class MockScenario(Scenario):
        def __init__(self, scenario_id: str, run_number: int | None = None):
            # Initialize with minimal required fields
            super().__init__()
            self.scenario_id = scenario_id
            self.run_number = run_number

    scenario1 = MockScenario("scenario1")
    scenario2_run1 = MockScenario("scenario2", 1)
    scenario2_run2 = MockScenario("scenario2", 2)
    scenario3 = MockScenario("scenario3")

    # Test adding results with the new method
    result.add_result(ScenarioValidationResult(success=True), scenario1.scenario_id)
    result.add_result(
        ScenarioValidationResult(success=False),
        scenario_id=scenario2_run1.scenario_id,
        run_number=scenario2_run1.run_number,
    )
    result.add_result(
        ScenarioValidationResult(success=True),
        scenario_id=scenario2_run2.scenario_id,
        run_number=scenario2_run2.run_number,
    )
    result.add_result(
        ScenarioValidationResult(success=None, exception=Exception("test error")),
        scenario_id=scenario3.scenario_id,
        run_number=scenario3.run_number,
    )

    # Verify the results are stored with tuple keys
    assert len(result.scenario_results) == 4
    assert ("scenario1", None) in result.scenario_results
    assert ("scenario2", 1) in result.scenario_results
    assert ("scenario2", 2) in result.scenario_results
    assert ("scenario3", None) in result.scenario_results

    # Verify the results have correct success values
    assert result.scenario_results[("scenario1", None)].success is True
    assert result.scenario_results[("scenario2", 1)].success is False
    assert result.scenario_results[("scenario2", 2)].success is True
    assert result.scenario_results[("scenario3", None)].success is None

    # Verify counts are updated correctly
    assert result.successful_count == 2  # scenario1 and scenario2_run_2
    assert result.failed_count == 1  # scenario2_run_1
    assert result.exception_count == 1  # scenario3
    assert result.no_validation_count == 0

    # Verify that tuple keys contain the correct scenario information
    # (no separate metadata dictionary needed since info is in the keys)
    scenario_keys = list(result.scenario_results.keys())
    assert len(scenario_keys) == 4
    assert ("scenario1", None) in scenario_keys
    assert ("scenario2", 1) in scenario_keys
    assert ("scenario2", 2) in scenario_keys
    assert ("scenario3", None) in scenario_keys

    # Verify that we can extract scenario_id and run_number directly from tuple keys
    for scenario_key in scenario_keys:
        scenario_id, run_number = scenario_key
        assert isinstance(scenario_id, str)
        assert run_number is None or isinstance(run_number, int)

    # Verify specific key contents
    scenario1_key = ("scenario1", None)
    scenario1_id, scenario1_run = scenario1_key
    assert scenario1_id == "scenario1"
    assert scenario1_run is None

    scenario2_run1_key = ("scenario2", 1)
    scenario2_id, scenario2_run = scenario2_run1_key
    assert scenario2_id == "scenario2"
    assert scenario2_run == 1
