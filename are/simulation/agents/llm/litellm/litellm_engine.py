# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


import json
import logging
import time
from typing import Any

from litellm import completion
from litellm.exceptions import APIError, AuthenticationError
from litellm.types.utils import Choices, ModelResponse
from pydantic import BaseModel

from are.simulation.agents.llm.llm_engine import LLMEngine, LLMEngineException
from are.simulation.agents.llm.types import MessageRole
from are.simulation.agents.llm.usage_metadata import extract_token_usage
from are.simulation.agents.multimodal import Attachment

logger = logging.getLogger(__name__)

# TODO: Instead of tool, llama has ipython support. We should use that instead of user role.
role_conversions = {"tool-response": "user", "tool-call": "assistant"}


class LiteLLMModelConfig(BaseModel):
    model_name: str
    provider: str
    endpoint: str | None = None
    api_key: str | None = None
    temperature: float | None = 0.1
    max_tokens: int | None = None


DEEPSEEK_JSON_MODE_SYSTEM_MESSAGE = """
For this DeepSeek JSON-mode experiment, return exactly one valid json object
and no extra text. Encode the next ReAct tool call with this schema:
{
  "thought": "short reasoning string",
  "action": "tool_name",
  "action_input": {}
}
Do not output markdown, Thought:, Action:, Observation:, <end_action>, a list,
or multiple JSON objects.
Use the exact tool name and exact action_input parameter names from the tool
descriptions in the conversation.
Example json output:
{
  "thought": "I need one tool call to make progress.",
  "action": "ExampleApp__example_tool",
  "action_input": {
    "parameter": "value"
  }
}
"""


QWEN_JSON_MODE_SYSTEM_MESSAGE = """
For this Qwen JSON-mode experiment, return exactly one valid JSON object
and no extra text. Encode the next ReAct tool call with this schema:
{
  "thought": "short reasoning string",
  "action": "tool_name",
  "action_input": {}
}
Do not output markdown, Thought:, Action:, Observation:, <end_action>, a list,
or multiple JSON objects.
Use the exact tool name and exact action_input parameter names from the tool
descriptions in the conversation.
Example JSON output:
{
  "thought": "I need one tool call to make progress.",
  "action": "ExampleApp__example_tool",
  "action_input": {
    "parameter": "value"
  }
}
"""


class LiteLLMEngine(LLMEngine):
    """
    A class that extends the LLMEngine to provide a specific implementation for the Litellm model.
    Attributes:
        model_config (ModelConfig): The configuration for the model.
    """

    def __init__(self, model_config: LiteLLMModelConfig):
        super().__init__(model_config.model_name)

        self.model_config = model_config

        self.mock_response = None
        if model_config.provider == "mock":
            self.mock_response = """Thought: Good choice, this is a mock, so I can't do anything. Let's return the result.
Action:
{
  "action": "_mock",
  "action_input": "Mock result"
}<end_action>
"""

    def _convert_message_to_litellm_format(
        self, message: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert a message to LiteLLM format, handling both text and multimodal content."""
        role = MessageRole(message["role"]).value
        role = role_conversions.get(role, role)

        # Handle attachments if present
        attachments: list[Attachment] | None = message.get("attachments")
        content = message.get("content", "")

        if attachments and len(attachments) > 0:
            # Create multimodal content with both text and images
            content_list = []

            # Add text content if present
            if content:
                content_list.append({"type": "text", "text": content})

            # Add image attachments
            for attachment in attachments:
                if attachment.mime.startswith("image/"):
                    content_list.append(attachment.to_openai_json())
                else:
                    logger.warning(
                        f"Unsupported attachment mime type: {attachment.mime}"
                    )

            return {"role": role, "content": content_list}
        else:
            # Text-only message
            return {"role": role, "content": content}

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stop_sequences=[],
        **kwargs,
    ) -> tuple[str, dict | None]:
        try:
            # Convert messages to LiteLLM format with multimodal support
            converted_messages = []
            for message in messages:
                converted_message = self._convert_message_to_litellm_format(message)
                converted_messages.append(converted_message)

            provider = (
                self.model_config.provider
                if self.model_config.provider != "local"
                else None
            )

            start_time = time.perf_counter()
            logger.info(
                "LLM request: model=%s provider=%s temperature=%s",
                self.model_config.model_name,
                provider,
                self.model_config.temperature,
            )
            response = completion(
                model=self.model_config.model_name,
                custom_llm_provider=provider,
                messages=converted_messages,
                api_base=self.model_config.endpoint,
                api_key=self.model_config.api_key,
                temperature=self.model_config.temperature,
                mock_response=self.mock_response,
            )
            completion_duration = time.perf_counter() - start_time

            assert type(response) is ModelResponse
            assert len(response.choices) >= 1
            assert type(response.choices[0]) is Choices

            res = response.choices[0].message.content
            assert res is not None

            res = res.replace("False", "false").replace("True", "true")
            for stop_token in stop_sequences:
                res = res.split(stop_token)[0]

            metadata = extract_token_usage(getattr(response, "usage", None))
            metadata.update(
                {
                    "completion_duration": completion_duration,
                    "model_name": self.model_config.model_name,
                    "model_provider": self.model_config.provider,
                }
            )
            return res, metadata
        except (AuthenticationError, APIError) as e:
            raise LLMEngineException("Auth error in litellm.") from e


class DeepSeekJSONModeEngine(LiteLLMEngine):
    """
    JSON Output adapter for ReAct-style tool calls.

    The model is constrained to return a JSON object, then this adapter converts
    it back to the legacy Thought/Action text consumed by JsonActionExecutor.
    """

    json_mode_provider_label = "deepseek-json"
    json_mode_system_message = DEEPSEEK_JSON_MODE_SYSTEM_MESSAGE
    default_json_max_tokens: int | None = None
    default_json_max_completion_tokens: int | None = None

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stop_sequences=[],
        **kwargs,
    ) -> tuple[str, dict | None]:
        try:
            converted_messages = [
                self._convert_message_to_litellm_format(message) for message in messages
            ]
            converted_messages = self._inject_json_mode_instruction(
                converted_messages
            )

            provider = (
                self.model_config.provider
                if self.model_config.provider != "local"
                else None
            )
            max_tokens = (
                self.model_config.max_tokens
                if self.model_config.max_tokens is not None
                else self.default_json_max_tokens
            )
            completion_kwargs: dict[str, Any] = {}
            if self.default_json_max_completion_tokens is not None:
                completion_kwargs["max_completion_tokens"] = (
                    self.default_json_max_completion_tokens
                )
            elif max_tokens is not None:
                completion_kwargs["max_tokens"] = max_tokens
            start_time = time.perf_counter()
            logger.info(
                "LLM request: model=%s provider=%s temperature=%s json_mode=true",
                self.model_config.model_name,
                provider,
                self.model_config.temperature,
            )
            response = completion(
                model=self.model_config.model_name,
                custom_llm_provider=provider,
                messages=converted_messages,
                api_base=self.model_config.endpoint,
                api_key=self.model_config.api_key,
                temperature=self.model_config.temperature,
                mock_response=self.mock_response,
                response_format={"type": "json_object"},
                **completion_kwargs,
            )
            completion_duration = time.perf_counter() - start_time

            assert type(response) is ModelResponse
            assert len(response.choices) >= 1
            assert type(response.choices[0]) is Choices

            metadata = self._response_metadata(response, completion_duration)
            raw_content = response.choices[0].message.content
            if not raw_content:
                self._log_json_mode_usage(metadata)
                raise LLMEngineException(
                    f"{self.json_mode_provider_label} returned empty content."
                )

            try:
                res = self._json_mode_content_to_react_output(raw_content)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                self._log_json_mode_usage(metadata)
                raise
            for stop_token in stop_sequences:
                res = res.split(stop_token)[0]

            return res, metadata
        except (AuthenticationError, APIError) as e:
            raise LLMEngineException(
                f"Auth error in {self.json_mode_provider_label}."
            ) from e
        except LLMEngineException:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise LLMEngineException(
                f"{self.json_mode_provider_label} response was not a valid action JSON object."
            ) from e

    def _inject_json_mode_instruction(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        injected = list(messages)
        insert_at = 0
        for index, message in enumerate(injected):
            if message.get("role") == "system":
                insert_at = index + 1
        injected.insert(
            insert_at,
            {"role": "system", "content": self.json_mode_system_message},
        )
        return injected

    def _response_metadata(
        self,
        response: ModelResponse,
        completion_duration: float,
    ) -> dict[str, Any]:
        metadata = extract_token_usage(getattr(response, "usage", None))
        metadata.update(
            {
                "completion_duration": completion_duration,
                "model_name": self.model_config.model_name,
                "model_provider": self.json_mode_provider_label,
            }
        )
        return metadata

    def _log_json_mode_usage(self, metadata: dict[str, Any]) -> None:
        logger.warning(
            "LLM usage: model=%s provider=%s prompt_tokens=%s completion_tokens=%s "
            "total_tokens=%s cached_tokens=%s reasoning_tokens=%s "
            "completion_duration=%.3fs parse_status=failed",
            metadata.get("model_name"),
            metadata.get("model_provider"),
            metadata.get("prompt_tokens", 0),
            metadata.get("completion_tokens", 0),
            metadata.get("total_tokens", 0),
            metadata.get("cached_tokens", 0),
            metadata.get("reasoning_tokens", 0),
            metadata.get("completion_duration", 0),
        )

    @staticmethod
    def _json_mode_content_to_react_output(content: str) -> str:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("JSON mode response must be a JSON object.")

        thought = data.get("thought", "")
        action = data["action"]
        action_input = data.get("action_input", {})
        if not isinstance(thought, str):
            raise TypeError("JSON mode thought must be a string.")
        if not isinstance(action, str):
            raise TypeError("JSON mode action must be a string.")
        if action_input is None:
            action_input = {}
        if not isinstance(action_input, dict):
            raise TypeError("JSON mode action_input must be an object.")

        action_blob = json.dumps(
            {"action": action, "action_input": action_input},
            ensure_ascii=False,
            indent=2,
        )
        return f"Thought: {thought}\n\nAction:\n{action_blob}<end_action>"


class QwenJSONModeEngine(DeepSeekJSONModeEngine):
    """Qwen/DashScope JSON Output adapter for the legacy ReAct executor."""

    json_mode_provider_label = "qwen-json"
    json_mode_system_message = QWEN_JSON_MODE_SYSTEM_MESSAGE
    default_json_max_completion_tokens = 4096
