# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


import json

from are.simulation.agents.agent_log import LLMOutputThoughtActionLog
from are.simulation.apps.app import App
from are.simulation.data_handler.exporter import JsonScenarioExporter
from are.simulation.data_handler.importer import JsonScenarioImporter
from are.simulation.data_handler.models import ExportedActionArg
from are.simulation.environment import Environment, EnvironmentConfig
from are.simulation.scenarios.config import ScenarioRunnerConfig
from are.simulation.scenarios.scenario import Scenario, ScenarioStatus
from are.simulation.tests.scenario.scenario_test import (
    add_agent_aui_event,
    add_user_aui_event,
    init_test_scenario,
)
from are.simulation.tool_utils import OperationType
from are.simulation.types import Action


def test_convert_action_args_with_dict():
    dict_arg = {"key1": "value1", "key2": 2}
    result = JsonScenarioExporter.convert_action_args("dict_arg", dict_arg)
    assert result == ExportedActionArg(
        name="dict_arg",
        value='{"key1": "value1", "key2": 2}',
        value_type="dict",
    )


def test_convert_action_args_with_list():
    list_arg = ["item1", "item2", "item3"]
    result = JsonScenarioExporter.convert_action_args("list_arg", list_arg)
    assert result == ExportedActionArg(
        name="list_arg",
        value='["item1", "item2", "item3"]',
        value_type="list",
    )


def test_convert_action_args_with_non_serializable_object():
    non_serializable_arg = object()
    result = JsonScenarioExporter.convert_action_args(
        "non_serializable_arg", non_serializable_arg
    )
    assert result == ExportedActionArg(
        name="non_serializable_arg",
        value=str(non_serializable_arg),
        value_type="object",
    )


def test_convert_action_args_with_int():
    int_arg = 42
    result = JsonScenarioExporter.convert_action_args("int_arg", int_arg)
    assert result == ExportedActionArg(name="int_arg", value="42", value_type="int")


def test_convert_action_args_with_string():
    string_arg = "hello"
    result = JsonScenarioExporter.convert_action_args("string_arg", string_arg)
    assert result == ExportedActionArg(
        name="string_arg", value="hello", value_type="str"
    )


def test_export_import_roundtrip():
    """
    Test that creates a scenario with events, executes it in Oracle mode,
    exports it to JSON, imports it back, and verifies the data matches.
    """
    # Create a test scenario with some events
    scenario = init_test_scenario()
    scenario.initialize()

    # Set run_number for testing
    scenario.run_number = 3

    add_user_aui_event(scenario, [], "user_message_1")
    add_agent_aui_event(scenario, ["user_message_1"], "agent_response_1")

    original_events_count = len(scenario.events)
    original_event_ids = [event.event_id for event in scenario.events]
    original_scenario_id = scenario.scenario_id
    original_start_time = scenario.start_time
    original_duration = scenario.duration
    original_run_number = scenario.run_number

    # Execute scenario in Oracle mode
    env = Environment(
        EnvironmentConfig(
            oracle_mode=True, queue_based_loop=True, start_time=scenario.start_time
        )
    )
    env.run(scenario)
    env.stop()

    # Export scenario to JSON
    exporter = JsonScenarioExporter()
    json_str = exporter.export_to_json(
        env=env,
        scenario=scenario,
        scenario_id=scenario.scenario_id,
        runner_config=None,
    )

    # Import scenario back from JSON
    importer = JsonScenarioImporter()
    imported_scenario, completed_events, _ = importer.import_from_json(json_str)
    imported_scenario.initialize()

    # Compare original and imported scenario data
    assert imported_scenario is not None
    assert imported_scenario.scenario_id == original_scenario_id
    assert imported_scenario.start_time == original_start_time
    assert imported_scenario.duration == original_duration
    # Test run_number roundtrip
    assert imported_scenario.run_number == original_run_number

    # Test that the new fields added to ExportedTraceDefinitionMetadata are preserved
    # Since the test scenario doesn't set these fields, they should have default values
    assert hasattr(imported_scenario, "config")
    assert hasattr(imported_scenario, "has_a2a_augmentation")
    assert hasattr(imported_scenario, "has_tool_augmentation")
    assert hasattr(imported_scenario, "has_env_events_augmentation")
    assert hasattr(imported_scenario, "has_exception")
    assert hasattr(imported_scenario, "exception_type")
    assert hasattr(imported_scenario, "exception_message")

    assert len(imported_scenario.events) == original_events_count
    imported_event_ids = [event.event_id for event in imported_scenario.events]
    assert set(imported_event_ids) == set(original_event_ids)

    original_events_by_id = {event.event_id: event for event in scenario.events}
    imported_events_by_id = {
        event.event_id: event for event in imported_scenario.events
    }

    for event_id in original_event_ids:
        original_event = original_events_by_id[event_id]
        imported_event = imported_events_by_id[event_id]

        assert original_event.event_id == imported_event.event_id
        assert original_event.event_type == imported_event.event_type
        assert len(original_event.dependencies) == len(imported_event.dependencies)

        original_dep_ids = [dep.event_id for dep in original_event.dependencies]
        imported_dep_ids = [dep.event_id for dep in imported_event.dependencies]
        assert set(original_dep_ids) == set(imported_dep_ids)

    # Verify completed events
    assert completed_events is not None
    assert len(completed_events) > 0

    assert len(completed_events) == original_events_count

    completed_event_ids = [event.event_id for event in completed_events]
    assert set(completed_event_ids) == set(original_event_ids)

    completed_events_by_id = {event.event_id: event for event in completed_events}

    for event_id in original_event_ids:
        original_event = original_events_by_id[event_id]
        completed_event = completed_events_by_id[event_id]

        assert completed_event.event_id == original_event.event_id
        assert completed_event.event_type == original_event.event_type

        if (
            hasattr(original_event, "action")
            and getattr(original_event, "action", None) is not None
        ):
            assert completed_event.action is not None
            assert (
                completed_event.action.app.__class__.__name__  # type: ignore
                == original_event.action.app.__class__.__name__  # type: ignore
            )
            assert (
                completed_event.action.function.__name__  # type: ignore
                == original_event.action.function.__name__  # type: ignore
            )

            # Verify action arguments are preserved
            original_args = original_event.action.args  # type: ignore
            completed_args = completed_event.action.args  # type: ignore

            # Filter out 'self' argument for comparison
            original_filtered_args = {
                k: v for k, v in original_args.items() if k != "self"
            }
            completed_filtered_args = {
                k: v for k, v in completed_args.items() if k != "self"
            }
            assert original_filtered_args == completed_filtered_args

        # Verify metadata exists and has expected structure
        assert completed_event.metadata is not None
        assert hasattr(completed_event.metadata, "return_value")
        assert hasattr(completed_event.metadata, "exception")
        assert hasattr(completed_event.metadata, "exception_stack_trace")


def test_convert_action_uses_resolved_args_when_available():
    """
    Test that convert_action uses resolved_args when available, otherwise falls back to args.
    """

    class MockApp(App):
        def __init__(self):
            super().__init__()
            self.name = "test_app"

        def mock_function(self, param1, param2):
            return f"{param1}_{param2}"

    mock_app = MockApp()

    # Test case 1: resolved_args is available and not empty - should use resolved_args
    action_with_resolved_args = Action(
        function=mock_app.mock_function,
        args={"param1": "original_value1", "param2": "original_value2"},
        resolved_args={"param1": "resolved_value1", "param2": "resolved_value2"},
        app=mock_app,
        operation_type=OperationType.READ,
    )

    exported_action = JsonScenarioExporter.convert_action(action_with_resolved_args)
    assert exported_action is not None
    assert exported_action.args is not None

    # Should use resolved_args, not original args
    exported_args = {arg.name: arg.value for arg in exported_action.args}
    assert exported_args["param1"] == "resolved_value1"
    assert exported_args["param2"] == "resolved_value2"
    assert "original_value1" not in exported_args.values()
    assert "original_value2" not in exported_args.values()

    # Test case 2: resolved_args is not provided (defaults to empty dict) - should fall back to args
    action_without_resolved_args = Action(
        function=mock_app.mock_function,
        args={"param1": "fallback_value1", "param2": "fallback_value2"},
        app=mock_app,
        operation_type=OperationType.READ,
    )

    exported_action = JsonScenarioExporter.convert_action(action_without_resolved_args)
    assert exported_action is not None
    assert exported_action.args is not None

    # Should use original args since resolved_args is empty (default)
    exported_args = {arg.name: arg.value for arg in exported_action.args}
    assert exported_args["param1"] == "fallback_value1"
    assert exported_args["param2"] == "fallback_value2"


def test_export_with_config():
    """
    Test that exporting with a config includes the config in the trace metadata
    and generates a filename with the config hash.
    """
    import json
    import os
    import tempfile

    # Create a test scenario
    scenario = init_test_scenario()
    scenario.initialize()

    # Create a config
    config = ScenarioRunnerConfig(
        model="test-model",
        agent="test-agent",
        a2a_app_prop=0.5,
        oracle=True,
    )

    # Execute scenario
    env = Environment(
        EnvironmentConfig(
            oracle_mode=True, queue_based_loop=True, start_time=scenario.start_time
        )
    )
    env.run(scenario)
    env.stop()

    # Export with config
    exporter = JsonScenarioExporter()

    # Test export_to_json includes config in metadata
    json_str = exporter.export_to_json(
        env=env,
        scenario=scenario,
        scenario_id=scenario.scenario_id,
        runner_config=config,
    )

    # Parse the JSON and verify config is included
    trace_data = json.loads(json_str)
    assert "metadata" in trace_data
    assert "definition" in trace_data["metadata"]
    assert "config" in trace_data["metadata"]["definition"]

    # The config field in definition metadata comes from scenario.config (which may be None)
    # The runner_config field comes from the config parameter we passed
    stored_config = trace_data["metadata"]["definition"]["config"]

    # The scenario.config may be None, but runner_config should contain our config
    # This is the expected behavior based on the implementation
    if stored_config is not None:
        assert stored_config["model"] == "test-model"
        assert stored_config["agent"] == "test-agent"
        assert stored_config["a2a_app_prop"] == 0.5
        assert stored_config["oracle"] is True

    # Verify that runner_config is also included in the metadata
    assert "runner_config" in trace_data["metadata"]
    runner_config = trace_data["metadata"]["runner_config"]
    assert runner_config is not None
    assert runner_config["model"] == "test-model"
    assert runner_config["agent"] == "test-agent"
    assert runner_config["a2a_app_prop"] == 0.5
    assert runner_config["oracle"] is True

    # Test export_to_json_file includes config hash in filename
    with tempfile.TemporaryDirectory() as temp_dir:
        success, file_path = exporter.export_to_json_file(
            env=env,
            scenario=scenario,
            model_id="test-model",
            agent_id="test-agent",
            validation_decision=ScenarioStatus.Valid.value,
            validation_rationale="test passed",
            run_duration=1.0,
            output_dir=temp_dir,
            runner_config=config,
        )

        assert success
        assert file_path is not None

        # Verify the filename includes the config hash
        filename = os.path.basename(file_path)
        config_hash = config.get_config_hash()
        assert config_hash in filename

        # Verify the file contains the config
        with open(file_path, "r") as f:
            file_content = json.load(f)
            assert "metadata" in file_content
            assert "definition" in file_content["metadata"]
            assert "config" in file_content["metadata"]["definition"]
            stored_config = file_content["metadata"]["definition"]["config"]

            # The scenario.config may be None, but runner_config should contain our config
            if stored_config is not None:
                assert stored_config["model"] == "test-model"

            # Also verify runner_config is in the file
            assert "runner_config" in file_content["metadata"]
            runner_config = file_content["metadata"]["runner_config"]
            assert runner_config is not None
            assert runner_config["model"] == "test-model"


def test_export_without_config():
    """
    Test that exporting without a config still works (backward compatibility).
    """
    import json
    import os
    import tempfile

    # Create a test scenario
    scenario = init_test_scenario()
    scenario.initialize()

    # Execute scenario
    env = Environment(
        EnvironmentConfig(
            oracle_mode=True, queue_based_loop=True, start_time=scenario.start_time
        )
    )
    env.run(scenario)
    env.stop()

    # Export without config (should still work)
    exporter = JsonScenarioExporter()

    # Test export_to_json without config
    json_str = exporter.export_to_json(
        env=env,
        scenario=scenario,
        scenario_id=scenario.scenario_id,
        runner_config=None,
    )

    # Should not crash and should produce valid JSON
    trace_data = json.loads(json_str)
    assert "metadata" in trace_data

    # Test export_to_json_file without config
    with tempfile.TemporaryDirectory() as temp_dir:
        success, file_path = exporter.export_to_json_file(
            env=env,
            scenario=scenario,
            model_id="test-model",
            agent_id="test-agent",
            validation_decision=ScenarioStatus.Valid.value,
            validation_rationale="test passed",
            run_duration=1.0,
            output_dir=temp_dir,
        )

        assert success
        assert file_path is not None

        # Verify the filename does NOT include a config hash
        filename = os.path.basename(file_path)
        # Should not contain any 8-character hash patterns
        assert "_" in filename  # Should still have underscores for other parts
        # But should not have the config hash pattern
        parts = filename.split("_")
        # None of the parts should be exactly 8 characters of hex
        hex_pattern_parts = [
            p
            for p in parts
            if len(p) == 8 and all(c in "0123456789abcdef" for c in p.lower())
        ]
        assert len(hex_pattern_parts) == 0  # No config hash should be present


def test_hf_export_includes_environment_world_logs_by_default():
    scenario = Scenario(scenario_id="test_world_logs_export")
    env = Environment(
        EnvironmentConfig(
            oracle_mode=True, queue_based_loop=True, start_time=scenario.start_time
        )
    )
    env.append_to_world_logs(
        LLMOutputThoughtActionLog(
            timestamp=123.0,
            content="Thought: test",
            agent_id="agent_1",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cached_tokens=25,
            reasoning_tokens=10,
            completion_duration=1.25,
            model_name="qwen3.5-flash-2026-02-23",
            model_provider="llama-api",
        )
    )

    trace_data = json.loads(
        JsonScenarioExporter().export_to_json(
            env=env,
            scenario=scenario,
            scenario_id=scenario.scenario_id,
        )
    )

    assert len(trace_data["world_logs"]) == 1
    exported_log = json.loads(trace_data["world_logs"][0])
    assert exported_log["log_type"] == "llm_output"
    assert exported_log["prompt_tokens"] == 100
    assert exported_log["completion_tokens"] == 50
    assert exported_log["total_tokens"] == 150
    assert exported_log["cached_tokens"] == 25
    assert exported_log["reasoning_tokens"] == 10
    assert exported_log["completion_duration"] == 1.25


def test_run_number_roundtrip():
    """
    Test that run_number is properly preserved through export/import cycle.
    """
    import json

    # Create a test scenario with run_number
    scenario = init_test_scenario()
    scenario.initialize()
    scenario.run_number = 7  # Set a specific run number

    add_user_aui_event(scenario, [], "user_message_1")

    # Execute scenario in Oracle mode
    env = Environment(
        EnvironmentConfig(
            oracle_mode=True,
            queue_based_loop=True,
            start_time=scenario.start_time,
        )
    )
    env.run(scenario)
    env.stop()

    # Export scenario to JSON
    exporter = JsonScenarioExporter()
    json_str = exporter.export_to_json(
        env=env,
        scenario=scenario,
        scenario_id=scenario.scenario_id,
        runner_config=None,
    )

    # Verify run_number is in the exported JSON
    trace_data = json.loads(json_str)
    assert trace_data["metadata"]["definition"]["run_number"] == 7

    # Import scenario back from JSON
    importer = JsonScenarioImporter()
    imported_scenario, completed_events, _ = importer.import_from_json(json_str)
    imported_scenario.initialize()

    # Verify run_number is preserved
    assert imported_scenario.run_number == 7

    # Test with None run_number
    scenario_no_run = init_test_scenario()
    scenario_no_run.initialize()
    scenario_no_run.run_number = None

    add_user_aui_event(scenario_no_run, [], "user_message_1")

    env_no_run = Environment(
        EnvironmentConfig(
            oracle_mode=True,
            queue_based_loop=True,
            start_time=scenario_no_run.start_time,
        )
    )
    env_no_run.run(scenario_no_run)
    env_no_run.stop()

    json_str_no_run = exporter.export_to_json(
        env=env_no_run,
        scenario=scenario_no_run,
        scenario_id=scenario_no_run.scenario_id,
        runner_config=None,
    )

    # Verify None run_number is in the exported JSON
    trace_data_no_run = json.loads(json_str_no_run)
    assert trace_data_no_run["metadata"]["definition"]["run_number"] is None

    # Import and verify None is preserved
    imported_scenario_no_run, _, _ = importer.import_from_json(json_str_no_run)
    assert imported_scenario_no_run.run_number is None
