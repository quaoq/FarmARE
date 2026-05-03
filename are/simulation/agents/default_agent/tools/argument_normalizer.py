from __future__ import annotations

import math
import re
from typing import Any

from are.simulation.tools import Tool


_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
_NUMBER_PATTERN = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")


def _extract_string_from_schema_dict(value: dict[str, Any]) -> str | None:
    description = value.get("description")
    if isinstance(description, str):
        return description

    content = value.get("content")
    if isinstance(content, dict):
        nested_description = content.get("description")
        if isinstance(nested_description, str):
            return nested_description

    return None


def _coerce_integer(value: Any, *, argument_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(
            f"argument '{argument_name}' expected integer but got boolean '{value}'"
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(
            f"argument '{argument_name}' expected integer but got non-integer float '{value}'"
        )
    if isinstance(value, str):
        stripped = value.strip()
        if _INTEGER_PATTERN.fullmatch(stripped):
            return int(stripped)
    raise ValueError(
        f"argument '{argument_name}' expected integer but got value of type {type(value).__name__}"
    )


def _coerce_number(value: Any, *, argument_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(
            f"argument '{argument_name}' expected number but got boolean '{value}'"
        )
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric
        raise ValueError(
            f"argument '{argument_name}' expected finite number but got '{value}'"
        )
    if isinstance(value, str):
        stripped = value.strip()
        if _NUMBER_PATTERN.fullmatch(stripped):
            numeric = float(stripped)
            if math.isfinite(numeric):
                return numeric
    raise ValueError(
        f"argument '{argument_name}' expected number but got value of type {type(value).__name__}"
    )


def _coerce_boolean(value: Any, *, argument_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    raise ValueError(
        f"argument '{argument_name}' expected boolean but got value of type {type(value).__name__}"
    )


def _coerce_string(value: Any, *, argument_name: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        extracted = _extract_string_from_schema_dict(value)
        if extracted is not None:
            return extracted
    if isinstance(value, (int, float, bool)):
        return str(value)
    raise ValueError(
        f"argument '{argument_name}' expected string but got value of type {type(value).__name__}"
    )


def _coerce_argument(value: Any, argument_type: str, *, argument_name: str) -> Any:
    if argument_type == "integer":
        return _coerce_integer(value, argument_name=argument_name)
    if argument_type == "number":
        return _coerce_number(value, argument_name=argument_name)
    if argument_type == "boolean":
        return _coerce_boolean(value, argument_name=argument_name)
    if argument_type == "string":
        return _coerce_string(value, argument_name=argument_name)
    return value


def normalize_tool_arguments(tool: Tool, arguments: Any) -> Any:
    if isinstance(arguments, str):
        if len(tool.inputs) == 1:
            argument_name = next(iter(tool.inputs.keys()))
            argument_info = tool.inputs[argument_name]
            argument_type = str(argument_info.get("type", "string"))
            return {
                argument_name: _coerce_argument(
                    arguments,
                    argument_type,
                    argument_name=argument_name,
                )
            }
        return arguments

    if not isinstance(arguments, dict):
        raise ValueError(
            f"tool '{tool.name}' expected arguments object, got {type(arguments).__name__}"
        )

    normalized: dict[str, Any] = {}
    for argument_name, argument_value in arguments.items():
        argument_info = tool.inputs.get(argument_name)
        if not isinstance(argument_info, dict):
            normalized[argument_name] = argument_value
            continue
        argument_type = str(argument_info.get("type", "string"))
        normalized[argument_name] = _coerce_argument(
            argument_value,
            argument_type,
            argument_name=argument_name,
        )
    return normalized
