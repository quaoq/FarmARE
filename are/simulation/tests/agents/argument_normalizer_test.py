from are.simulation.agents.default_agent.tools.argument_normalizer import (
    normalize_tool_arguments,
)
from are.simulation.tools import Tool


class IrrigationTool(Tool):
    name = "FieldOpsApp__irrigate_range"
    description = "Irrigate a range."
    inputs = {
        "start": {"type": "integer", "description": "first ridge"},
        "end": {"type": "integer", "description": "last ridge"},
        "duration_hours": {"type": "number", "description": "duration"},
    }
    output_type = "string"

    def forward(self, start, end, duration_hours):
        return f"{start}-{end}@{duration_hours}"


class UserMessageTool(Tool):
    name = "AgentUserInterface__send_message_to_user"
    description = "Send a message to user."
    inputs = {"content": {"type": "string", "description": "message text"}}
    output_type = "string"

    def forward(self, content):
        return content


def test_normalize_numeric_strings_for_tool_inputs():
    tool = IrrigationTool()
    normalized = normalize_tool_arguments(
        tool,
        {"start": "22", "end": "32", "duration_hours": "1.5"},
    )
    assert normalized == {"start": 22, "end": 32, "duration_hours": 1.5}


def test_normalize_schema_dict_to_string():
    tool = UserMessageTool()
    normalized = normalize_tool_arguments(
        tool,
        {
            "content": {
                "description": "Irrigation completed.",
                "type": "string",
                "default": "",
            }
        },
    )
    assert normalized == {"content": "Irrigation completed."}


def test_normalize_invalid_integer_raises():
    tool = IrrigationTool()
    try:
        normalize_tool_arguments(
            tool,
            {"start": "ridge22", "end": 32, "duration_hours": 1.5},
        )
    except ValueError as error:
        assert "expected integer" in str(error)
    else:
        raise AssertionError("Expected ValueError for invalid integer coercion")
