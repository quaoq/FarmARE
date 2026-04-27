# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


import json
import pprint
import re
from typing import Any, Callable

from are.simulation.agents.agent_log import (
    BaseAgentLog,
    ObservationLog,
    RationaleLog,
    ToolCallLog,
)
from are.simulation.agents.llm.types import MMObservation
from are.simulation.agents.multimodal import Attachment
from are.simulation.exceptions import (
    JsonExecutionAgentError,
    JsonParsingAgentError,
    LoggedError,
    UnavailableToolAgentError,
)
from are.simulation.tool_box import get_tool_description_with_args
from are.simulation.tools import Tool

from .action_executor import AgentAction, BaseActionExecutor, ParsedAction
from .argument_normalizer import normalize_tool_arguments


def parse_json_blob(json_blob: str) -> dict[str, str | dict[str, str]]:
    try:
        first_accolade_index = json_blob.find("{")
        last_accolade_index = [a.start() for a in list(re.finditer("}", json_blob))][-1]
        json_blob = json_blob[first_accolade_index : last_accolade_index + 1].replace(
            '\\"', "'"
        )
        # Use a more robust approach to handle triple quotes in JSON
        # Replace triple quotes with single quotes to avoid JSON parsing errors
        json_blob = re.sub(r'"""(.*?)"""', r"'\1'", json_blob, flags=re.DOTALL)
        json_data = json.loads(json_blob, strict=False)
        return json_data
    except json.JSONDecodeError as e:
        place = e.pos
        if json_blob[place - 1 : place + 2] == "},\n":
            raise JsonParsingAgentError(
                "JSON is invalid: you probably tried to provide multiple tool calls in one action. PROVIDE ONLY ONE TOOL CALL."
            )
        raise JsonParsingAgentError(
            f"The JSON blob you used is invalid due to the following error: {e}.\n"
            f"JSON blob was: {json_blob}, decoding failed on that specific part of the blob:\n"
            f"'{json_blob[place - 4 : place + 5]}'."
        )
    except Exception as e:
        raise JsonParsingAgentError(f"Error in parsing the JSON blob: {e}")


def parse_json_tool_call(json_blob: str) -> tuple[str, str | dict[str, str]]:
    json_blob = json_blob.replace("```json", "").replace("```", "")
    tool_call = parse_json_blob(json_blob)
    action = tool_call.get("action")
    action_input = tool_call.get("action_input")
    if action is None:
        missing_keys = [
            key for key in ["action", "action_input"] if key not in tool_call
        ]
        raise JsonParsingAgentError(f"Missing keys: {missing_keys} in blob {tool_call}")
    return str(action), action_input or ""


def get_observation_log(
    timestamp: float,
    content: str,
    agent_id: str,
    attachments: list[Attachment] | None = None,
) -> ObservationLog:
    if not content and not attachments:
        return ObservationLog(
            content="No observation", timestamp=timestamp, agent_id=agent_id
        )
    return ObservationLog(
        content=content.strip(),
        attachments=attachments or [],
        timestamp=timestamp,
        agent_id=agent_id,
    )


class JsonActionExecutor(BaseActionExecutor):
    def __init__(
        self, tools: dict[str, Tool] | None = None, use_custom_logger: bool = True
    ):
        super().__init__(use_custom_logger=use_custom_logger)
        self.tools = tools if tools is not None else {}
        self.tool_parser = parse_json_tool_call
        self.action_token = "Action:"
        self.thought_token = "Thought:"

    def execute_action(
        self,
        action: AgentAction,
        append_agent_log: Callable[[BaseAgentLog], None],
        make_timestamp: Callable[[], float],
        agent_id: str,
    ):
        parsed_action = self.parse_action(action)
        return self.execute_parsed_action(
            parsed_action, append_agent_log, make_timestamp, agent_id
        )

    def parse_action(self, action: AgentAction) -> ParsedAction:
        assert action.action is not None
        try:
            tool_name, arguments = self.tool_parser(action.action)
            app_name, action_name = (
                tool_name.split("__")
                if "__" in tool_name
                else (
                    tool_name,
                    None,
                )
            )
        except Exception as e:
            raise JsonParsingAgentError(
                f"Could not parse the given action: {e} - return was {pprint.pformat(action.action)}"
            )
        return ParsedAction(
            tool_name=tool_name,
            app_name=app_name,
            arguments=arguments,
            rationale=action.rationale,
            action_name=action_name,
        )

    def execute_parsed_action(
        self,
        parsed_action: ParsedAction,
        append_agent_log: Callable[[BaseAgentLog], None],
        make_timestamp: Callable[[], float],
        agent_id: str,
    ) -> None:
        tool_name = parsed_action.tool_name
        arguments = parsed_action.arguments if parsed_action.arguments else {}
        rationale = parsed_action.rationale

        if not tool_name:
            raise JsonParsingAgentError(
                "Error: error parsing the tool_name in the action."
            )

        # 1. Log the rationale, action, tool name, and arguments in logs
        if rationale is not None:
            append_agent_log(
                RationaleLog(
                    content=rationale, timestamp=make_timestamp(), agent_id=agent_id
                )
            )

        append_agent_log(
            ToolCallLog(
                tool_name=tool_name,
                tool_arguments=arguments,
                timestamp=make_timestamp(),
                agent_id=agent_id,
            )
        )

        # 2. Execute the tool
        self.logger.debug(f"Calling tool: '{tool_name}' with arguments: {arguments}")
        observation = self.execute_tool_call(
            parsed_action, append_agent_log, make_timestamp
        )

        # 3. Log the observation in logs
        if isinstance(observation, MMObservation):
            append_agent_log(
                get_observation_log(
                    make_timestamp(),
                    observation.content,
                    agent_id,
                    observation.attachments,
                )
            )
        else:
            append_agent_log(
                get_observation_log(make_timestamp(), str(observation), agent_id)
            )

        # 4. Log the final answer in logs
        if tool_name == "final_answer":
            self._append_final_answer(
                observation, append_agent_log, make_timestamp, agent_id
            )

    def execute_tool_call(
        self,
        parsed_action: ParsedAction,
        append_agent_log: Callable[[BaseAgentLog], None],
        make_timestamp: Callable[[], float],
    ) -> Any:
        tool_name = parsed_action.tool_name
        arguments = parsed_action.arguments if parsed_action.arguments else {}

        if tool_name == "_mock":
            return "Mocked observation"

        if tool_name not in self.tools:
            error_msg = f"Error: unknown tool {tool_name}, should be instead one of {list(self.tools.keys())}."
            self.logger.error(error_msg, exc_info=True)
            raise UnavailableToolAgentError(error_msg)

        normalized_arguments = arguments
        try:
            normalized_arguments = normalize_tool_arguments(
                self.tools[tool_name], arguments
            )
        except Exception as e:
            raise JsonExecutionAgentError(
                f"Tool argument normalization failed: {e}\n"
                "Use only tool-compatible primitive values for each argument."
            )

        try:
            if isinstance(normalized_arguments, str):
                observation = self.tools[tool_name](normalized_arguments)
            else:
                observation = self.tools[tool_name](**normalized_arguments)
            return observation
        except LoggedError as e:
            self.logger.error(e, exc_info=True)
            raise e
        except Exception as e:
            raise JsonExecutionAgentError(
                f"Error in tool call execution: {e}\nYou should only use this tool with a correct input.\n"
                f"As a reminder, this tool's description is the following:\n{get_tool_description_with_args(self.tools[tool_name])}"
            )

    def update_tools(self, tools: dict[str, Tool]):
        self.tools = tools
