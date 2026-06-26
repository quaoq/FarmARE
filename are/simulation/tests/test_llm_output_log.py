# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


import json
import time
import unittest
from unittest.mock import patch

from litellm.types.utils import ModelResponse

from are.simulation.agents.agent_log import BaseAgentLog, LLMOutputThoughtActionLog
from are.simulation.agents.llm.litellm.litellm_engine import (
    DeepSeekJSONModeEngine,
    LiteLLMEngine,
    LiteLLMModelConfig,
    QwenJSONModeEngine,
)
from are.simulation.agents.llm.llm_engine import LLMEngineException
from are.simulation.agents.llm.usage_metadata import extract_token_usage
from are.simulation.data_handler.exporter import extract_llm_usage_stats_from_logs


class TestLLMOutputThoughtActionLog(unittest.TestCase):
    def test_llm_output_log_with_token_usage_and_completion_duration(self):
        """Test that LLMOutputThoughtActionLog correctly stores token usage and inference time."""
        # Create a log with token usage and inference time
        timestamp = time.time()
        content = "This is a test response from the LLM"
        prompt_tokens = 100
        completion_tokens = 50
        total_tokens = 150
        cached_tokens = 25
        reasoning_tokens = 10
        completion_duration = 1.25
        model_name = "gpt-4o-mini"
        model_provider = "openai"
        agent_id = "test_agent_id"

        log = LLMOutputThoughtActionLog(
            timestamp=timestamp,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            completion_duration=completion_duration,
            model_name=model_name,
            model_provider=model_provider,
            agent_id=agent_id,
        )

        # Verify that the values are correctly stored
        self.assertEqual(log.content, content)
        self.assertEqual(log.prompt_tokens, prompt_tokens)
        self.assertEqual(log.completion_tokens, completion_tokens)
        self.assertEqual(log.total_tokens, total_tokens)
        self.assertEqual(log.cached_tokens, cached_tokens)
        self.assertEqual(log.reasoning_tokens, reasoning_tokens)
        self.assertEqual(log.completion_duration, completion_duration)
        self.assertEqual(log.model_name, model_name)
        self.assertEqual(log.model_provider, model_provider)
        self.assertEqual(log.get_type(), "llm_output")

    def test_llm_output_log_serialization(self):
        """Test that LLMOutputThoughtActionLog correctly serializes and deserializes."""
        # Create a log with token usage and inference time
        timestamp = time.time()
        content = "This is a test response from the LLM"
        prompt_tokens = 100
        completion_tokens = 50
        total_tokens = 150
        cached_tokens = 25
        reasoning_tokens = 10
        completion_duration = 1.25
        agent_id = "test_agent_id"

        log = LLMOutputThoughtActionLog(
            timestamp=timestamp,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            completion_duration=completion_duration,
            agent_id=agent_id,
        )

        # Serialize the log
        serialized = log.serialize()

        # Deserialize the log
        deserialized_dict = json.loads(serialized)

        # Verify that the serialized data contains all fields
        self.assertEqual(deserialized_dict["content"], content)
        self.assertEqual(deserialized_dict["prompt_tokens"], prompt_tokens)
        self.assertEqual(deserialized_dict["completion_tokens"], completion_tokens)
        self.assertEqual(deserialized_dict["total_tokens"], total_tokens)
        self.assertEqual(deserialized_dict["cached_tokens"], cached_tokens)
        self.assertEqual(deserialized_dict["reasoning_tokens"], reasoning_tokens)
        self.assertEqual(deserialized_dict["completion_duration"], completion_duration)
        self.assertEqual(deserialized_dict["log_type"], "llm_output")

    def test_llm_output_log_from_dict(self):
        """Test that LLMOutputThoughtActionLog correctly reconstructs from a dict."""
        # Create a dict representing a serialized log
        timestamp = time.time()
        content = "This is a test response from the LLM"
        prompt_tokens = 100
        completion_tokens = 50
        total_tokens = 150
        completion_duration = 1.25
        log_id = "test_id_123"
        agent_id = "test_agent_id"

        log_dict = {
            "timestamp": timestamp,
            "content": content,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": 25,
            "reasoning_tokens": 10,
            "completion_duration": completion_duration,
            "model_name": "gpt-4o-mini",
            "model_provider": "openai",
            "log_type": "llm_output",
            "id": log_id,
            "agent_id": agent_id,
        }

        # Reconstruct the log from the dict
        log = BaseAgentLog.from_dict(log_dict)

        # Verify that the reconstructed log has all fields
        self.assertIsInstance(log, LLMOutputThoughtActionLog)
        # Typing ignore because we first check the log is an instance of LLMOutputThoughtActionLog
        self.assertEqual(log.content, content)  # type: ignore
        self.assertEqual(log.prompt_tokens, prompt_tokens)  # type: ignore
        self.assertEqual(log.completion_tokens, completion_tokens)  # type: ignore
        self.assertEqual(log.total_tokens, total_tokens)  # type: ignore
        self.assertEqual(log.cached_tokens, 25)  # type: ignore
        self.assertEqual(log.reasoning_tokens, 10)  # type: ignore
        self.assertEqual(log.completion_duration, completion_duration)  # type: ignore
        self.assertEqual(log.model_name, "gpt-4o-mini")  # type: ignore
        self.assertEqual(log.model_provider, "openai")  # type: ignore
        self.assertEqual(log.id, log_id)
        self.assertEqual(log.get_type(), "llm_output")

    def test_llm_output_log_default_values(self):
        """Test that LLMOutputThoughtActionLog uses default values correctly."""
        # Create a log with only required fields
        timestamp = time.time()
        content = "This is a test response from the LLM"
        agent_id = "test_agent_id"

        log = LLMOutputThoughtActionLog(
            timestamp=timestamp,
            content=content,
            agent_id=agent_id,
        )

        # Verify that default values are used
        self.assertEqual(log.content, content)
        self.assertEqual(log.prompt_tokens, 0)
        self.assertEqual(log.completion_tokens, 0)
        self.assertEqual(log.total_tokens, 0)
        self.assertEqual(log.cached_tokens, 0)
        self.assertEqual(log.reasoning_tokens, 0)
        self.assertEqual(log.completion_duration, 0.0)

    def test_extract_token_usage_reads_cached_and_reasoning_tokens(self):
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 25},
            "completion_tokens_details": {"reasoning_tokens": 10},
        }

        result = extract_token_usage(usage)

        self.assertEqual(
            result,
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cached_tokens": 25,
                "reasoning_tokens": 10,
            },
        )

    def test_exporter_includes_per_call_usage_details(self):
        timestamp = time.time()
        log = LLMOutputThoughtActionLog(
            timestamp=timestamp,
            content="Thought: test",
            agent_id="agent_1",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cached_tokens=25,
            reasoning_tokens=10,
            completion_duration=1.25,
            model_name="gpt-4o-mini",
            model_provider="openai",
        )

        stats = extract_llm_usage_stats_from_logs([log])

        self.assertEqual(stats["total_llm_calls"], 1)
        self.assertEqual(stats["cached_tokens"], [25])
        self.assertEqual(
            stats["calls"],
            [
                {
                    "timestamp": timestamp,
                    "model_name": "gpt-4o-mini",
                    "model_provider": "openai",
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "cached_tokens": 25,
                    "reasoning_tokens": 10,
                    "completion_duration": 1.25,
                }
            ],
        )

    def test_litellm_engine_returns_usage_metadata(self):
        response = ModelResponse(
            choices=[{"message": {"content": "Thought: ok<end_action>ignored"}}],
            model="gpt-4o-mini",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 25},
                "completion_tokens_details": {"reasoning_tokens": 10},
            },
        )
        engine = LiteLLMEngine(
            LiteLLMModelConfig(model_name="gpt-4o-mini", provider="openai")
        )

        with (
            patch(
                "are.simulation.agents.llm.litellm.litellm_engine.completion",
                return_value=response,
            ) as completion_mock,
        ):
            content, metadata = engine.chat_completion(
                [{"role": "user", "content": "hello"}],
                stop_sequences=["<end_action>"],
            )

        self.assertEqual(content, "Thought: ok")
        assert metadata is not None
        self.assertEqual(metadata["prompt_tokens"], 100)
        self.assertEqual(metadata["completion_tokens"], 50)
        self.assertEqual(metadata["total_tokens"], 150)
        self.assertEqual(metadata["cached_tokens"], 25)
        self.assertEqual(metadata["reasoning_tokens"], 10)
        self.assertEqual(metadata["model_name"], "gpt-4o-mini")
        self.assertEqual(metadata["model_provider"], "openai")
        self.assertGreaterEqual(metadata["completion_duration"], 0.0)
        self.assertEqual(completion_mock.call_args.kwargs["temperature"], 0.1)

    def test_deepseek_json_mode_engine_adapts_json_to_react_output(self):
        response = ModelResponse(
            choices=[
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "thought": "Plant the next block.",
                                "action": "TractorApp__plant_seeds",
                                "action_input": {
                                    "start_ridge": 12,
                                    "end_ridge": 15,
                                    "depth_cm": 4.0,
                                    "seed_spacing_cm": 10.0,
                                },
                            }
                        )
                    }
                }
            ],
            model="deepseek-chat",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 80,
                "total_tokens": 180,
            },
        )
        engine = DeepSeekJSONModeEngine(
            LiteLLMModelConfig(
                model_name="deepseek-chat",
                provider="openai",
                endpoint="https://api.deepseek.com/v1",
                api_key="test-key",
            )
        )

        with patch(
            "are.simulation.agents.llm.litellm.litellm_engine.completion",
            return_value=response,
        ) as completion_mock:
            content, metadata = engine.chat_completion(
                [{"role": "user", "content": "hello"}],
                stop_sequences=["<end_action>"],
            )

        self.assertIn("Thought: Plant the next block.", content)
        self.assertIn('"action": "TractorApp__plant_seeds"', content)
        self.assertIn('"start_ridge": 12', content)
        self.assertNotIn("<end_action>", content)
        assert metadata is not None
        self.assertEqual(metadata["prompt_tokens"], 100)
        self.assertEqual(metadata["completion_tokens"], 80)
        self.assertEqual(metadata["model_provider"], "deepseek-json")

        call_kwargs = completion_mock.call_args.kwargs
        self.assertEqual(call_kwargs["temperature"], 0.1)
        self.assertEqual(call_kwargs["response_format"], {"type": "json_object"})
        self.assertNotIn("max_tokens", call_kwargs)
        self.assertNotIn("max_completion_tokens", call_kwargs)
        self.assertIn("json object", call_kwargs["messages"][0]["content"])

    def test_qwen_json_mode_engine_adapts_json_to_react_output(self):
        response = ModelResponse(
            choices=[
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "thought": "Check the harvest window.",
                                "action": "WeatherApp__get_forecast",
                                "action_input": {"days": 3},
                            }
                        )
                    }
                }
            ],
            model="qwen-plus",
            usage={
                "prompt_tokens": 90,
                "completion_tokens": 40,
                "total_tokens": 130,
            },
        )
        engine = QwenJSONModeEngine(
            LiteLLMModelConfig(
                model_name="qwen-plus",
                provider="openai",
                endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key="test-key",
            )
        )

        with patch(
            "are.simulation.agents.llm.litellm.litellm_engine.completion",
            return_value=response,
        ) as completion_mock:
            content, metadata = engine.chat_completion(
                [{"role": "user", "content": "hello"}],
                stop_sequences=["<end_action>"],
            )

        self.assertIn("Thought: Check the harvest window.", content)
        self.assertIn('"action": "WeatherApp__get_forecast"', content)
        self.assertIn('"days": 3', content)
        self.assertNotIn("<end_action>", content)
        assert metadata is not None
        self.assertEqual(metadata["prompt_tokens"], 90)
        self.assertEqual(metadata["completion_tokens"], 40)
        self.assertEqual(metadata["model_provider"], "qwen-json")

        call_kwargs = completion_mock.call_args.kwargs
        self.assertEqual(call_kwargs["response_format"], {"type": "json_object"})
        self.assertNotIn("max_tokens", call_kwargs)
        self.assertEqual(call_kwargs["max_completion_tokens"], 4096)
        self.assertIn("JSON object", call_kwargs["messages"][0]["content"])
        self.assertEqual(call_kwargs["messages"][-1]["role"], "system")
        self.assertIn(
            'Put all reasoning inside the JSON string field "thought"',
            call_kwargs["messages"][-1]["content"],
        )
        self.assertIn(
            "Do not output Thought:, Action:",
            call_kwargs["messages"][-1]["content"],
        )

    def test_qwen_json_mode_engine_logs_usage_on_invalid_json(self):
        response = ModelResponse(
            choices=[{"message": {"content": '{"thought": "truncated'}}],
            model="qwen-plus",
            usage={
                "prompt_tokens": 1000,
                "completion_tokens": 4096,
                "total_tokens": 5096,
                "completion_tokens_details": {"reasoning_tokens": 3500},
            },
        )
        engine = QwenJSONModeEngine(
            LiteLLMModelConfig(
                model_name="qwen-plus",
                provider="openai",
                endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key="test-key",
            )
        )

        with (
            patch(
                "are.simulation.agents.llm.litellm.litellm_engine.completion",
                return_value=response,
            ),
            self.assertLogs(
                "are.simulation.agents.llm.litellm.litellm_engine",
                level="WARNING",
            ) as logs,
        ):
            with self.assertRaises(LLMEngineException):
                engine.chat_completion([{"role": "user", "content": "hello"}])

        logged = "\n".join(logs.output)
        self.assertIn("completion_tokens=4096", logged)
        self.assertIn("reasoning_tokens=3500", logged)
        self.assertIn("parse_status=failed", logged)


if __name__ == "__main__":
    unittest.main()
