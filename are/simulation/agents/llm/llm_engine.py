# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


from typing import Any

from pydantic import BaseModel


class ModelConfig(BaseModel):
    model_name: str = ""
    provider: str | None = None
    max_tokens: int | None = 16384
    temperature: float | None = 0.1
    stop_sequences: list[str] | None = None
    url_base: str | None = None
    mm_url_base: str | None = None
    endpoint: str | None = None
    mm_endpoint: str | None = None
    mm_model: str | None = None
    access_token: str | None = None


class LLMEngineException(Exception):
    def __init__(self, message: str, inner_exception: Exception | None = None):
        super().__init__(message)
        self.inner_exception = inner_exception


class LLMEngine:
    def __init__(self, model_name: str):
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def __call__(
        self,
        messages: list[dict[str, str]],
        stop_sequences=[],
        **kwargs,
    ) -> tuple[str, dict | None]:
        return self.chat_completion(messages, stop_sequences, **kwargs)

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stop_sequences=[],
        **kwargs,
    ) -> tuple[str, dict | None]:
        """
        This is the main function that will be called by the LLMEngine.
        It takes in a list of messages and returns a tuple of the response and the metadata.
        The metadata is a dictionary that contains information about the response such as the number of tokens used, the completion time, etc.
        """
        raise NotImplementedError()

    def simple_call(self, prompt: str) -> str:
        raise NotImplementedError()


class MockLLMEngine(LLMEngine):
    def __init__(self, mock_responses: list[str], engine: LLMEngine):
        super().__init__("MockEngine")
        self.mock_responses = mock_responses
        self.response_index = 0
        self.engine = engine

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stop_sequences=[],
        **kwargs,
    ) -> tuple[str, dict | None]:
        if self.response_index < len(self.mock_responses):
            response = self.mock_responses[self.response_index]
            self.response_index += 1
            return response, None
        return self.engine.chat_completion(messages, stop_sequences, **kwargs)
