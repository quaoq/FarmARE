# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

from typing import Any


def get_usage_value(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def extract_token_usage(usage: Any) -> dict[str, int]:
    prompt_tokens = as_int(
        get_usage_value(usage, "prompt_tokens", get_usage_value(usage, "input_tokens"))
    )
    completion_tokens = as_int(
        get_usage_value(
            usage, "completion_tokens", get_usage_value(usage, "output_tokens")
        )
    )
    total_tokens = as_int(get_usage_value(usage, "total_tokens"))

    prompt_details = get_usage_value(
        usage,
        "prompt_tokens_details",
        get_usage_value(usage, "input_tokens_details"),
    )
    completion_details = get_usage_value(
        usage,
        "completion_tokens_details",
        get_usage_value(usage, "output_tokens_details"),
    )

    cached_tokens = as_int(get_usage_value(prompt_details, "cached_tokens"))
    reasoning_tokens = as_int(
        get_usage_value(
            completion_details,
            "reasoning_tokens",
            get_usage_value(usage, "reasoning_tokens"),
        )
    )

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
    }
