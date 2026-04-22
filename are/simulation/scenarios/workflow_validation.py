from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import Action, CompletedEvent, Event, EventType, OracleEvent
from are.simulation.utils import make_serializable
from are.simulation.validation.utils.scenario_utils import run_oracle_mode

logger = logging.getLogger(__name__)

@dataclass
class WorkflowStep:
    name: str
    content: Any | None
    op_type: str | None
    tool_name: str | None
    tool_args: dict[str, Any]
    depends_on: list[str]
    time: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "content": make_serializable(self.content),
            "op_type": self.op_type,
            "tool_name": self.tool_name,
            "tool_args": make_serializable(self.tool_args),
            "depends_on": list(self.depends_on),
            "time": self.time,
        }


def _normalize_value(value: Any) -> Any:
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, list):
        return tuple(_normalize_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _normalize_value(v)) for k, v in value.items()))
    return value


def _make_key(tool_name: str, tool_args: dict[str, Any] | None) -> tuple[Any, ...]:
    if not tool_args:
        return (tool_name,)
    normalized = tuple(
        sorted((k, _normalize_value(v)) for k, v in tool_args.items())
    )
    return (tool_name, normalized)


def _extract_tool_steps(workflow: dict[str, dict[str, Any]] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = workflow.values() if isinstance(workflow, dict) else workflow
    return [
        step
        for step in steps
        if step.get("tool_name") and step.get("op_type") != "USER"
    ]


def _levenshtein_distance(
    s1: list[str], s2: list[str], k_ins: int = 1, k_del: int = 1, k_sub: int = 1
) -> int:
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i * k_del
    for j in range(n + 1):
        dp[0][j] = j * k_ins
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else k_sub
            dp[i][j] = min(
                dp[i - 1][j] + k_del,
                dp[i][j - 1] + k_ins,
                dp[i - 1][j - 1] + cost,
            )
    return dp[m][n]


def _ktc(predicted: list[str], gold: list[str]) -> tuple[float, list[str]]:
    from itertools import combinations

    seen: set[str] = set()
    matched: list[str] = []
    for symbol in predicted:
        if symbol in gold and symbol not in seen:
            seen.add(symbol)
            matched.append(symbol)

    n = len(matched)
    if n < 2:
        return 0.0, []

    rank: dict[str, int] = {}
    for idx, symbol in enumerate(gold):
        if symbol in seen and symbol not in rank:
            rank[symbol] = idx
    ranks = [rank[symbol] for symbol in matched]

    concordant = 0
    discordant = 0
    for i, j in combinations(range(n), 2):
        if (ranks[i] - ranks[j]) * (i - j) > 0:
            concordant += 1
        else:
            discordant += 1

    tau = (concordant - discordant) / (0.5 * n * (n - 1))
    return (tau + 1) / 2.0, matched

def _format_args(tool_args: dict | None) -> str:
    if not tool_args:
        return ""
    return ", ".join(f"{k}={v}" for k, v in tool_args.items())

def evaluate_workflows(
    oracle_workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
    agent_workflow: dict[str, dict[str, Any]] | list[dict[str, Any]],
) -> dict[str, float]:
    oracle_steps = _extract_tool_steps(oracle_workflow)
    agent_steps = _extract_tool_steps(agent_workflow)

    alphabet: dict[tuple[Any, ...], dict[str, Any]] = {}
    next_letter_idx = 0

    def get_symbol(tool_name: str, tool_args: dict[str, Any] | None) -> str:
        nonlocal next_letter_idx
        key = _make_key(tool_name, tool_args)
        if key not in alphabet:
            if next_letter_idx < 26:
                symbol = chr(ord("A") + next_letter_idx)
            else:
                symbol = "A" + chr(ord("A") + (next_letter_idx - 26))
            alphabet[key] = {
                "symbol": symbol,
                "tool_name": tool_name,
                "tool_args": tool_args or {},
            }
            next_letter_idx += 1
        return alphabet[key]["symbol"]

    oracle_symbols = [
        get_symbol(step["tool_name"], step.get("tool_args")) for step in oracle_steps
    ]
    agent_symbols = [
        get_symbol(step["tool_name"], step.get("tool_args")) for step in agent_steps
    ]
    logger.info("\n=== Alphabet ===")
    for entry in alphabet.values():
        args_str = _format_args(entry["tool_args"])
        label = f"{entry['tool_name']}({args_str})" if args_str else entry["tool_name"]
        logger.info(f"  {entry['symbol']}: {label}")

    logger.info(f"\nOracle  ({len(oracle_symbols)}): {oracle_symbols}")
    logger.info(f"Agent   ({len(agent_symbols)}): {agent_symbols}")

    ld = _levenshtein_distance(agent_symbols, oracle_symbols)
    max_len = max(len(agent_symbols), len(oracle_symbols), 1)
    path_correctness = 1.0 - ld / max_len

    oracle_set = set(oracle_symbols)
    ktc_raw, matched = _ktc(agent_symbols, oracle_symbols)
    coverage = len(matched) / len(oracle_set) if oracle_set else 0.0
    ktc_adjusted = ktc_raw * coverage
    combined = 0.5 * path_correctness + 0.5 * ktc_adjusted

    return {
        "path_correctness": round(path_correctness, 4),
        "ktc_raw": round(ktc_raw, 4),
        "coverage": round(coverage, 4),
        "ktc_adjusted": round(ktc_adjusted, 4),
        "combined": round(combined, 4),
    }


def _resolve_tool_name(action: Action) -> str:
    app_name = action.app_name
    if action.class_name in {"DroneApp", "RobotApp"} and app_name:
        return f"{app_name}__{action.function_name}"
    return f"{action.class_name}__{action.function_name}"


def _resolve_op_type(action: Action) -> str | None:
    operation_type = action.operation_type
    if operation_type is None:
        return None
    return operation_type.value.upper()


def _extract_action_args(action: Action, completed_event: CompletedEvent | None = None) -> dict[str, Any]:
    if completed_event is not None:
        args = completed_event.get_args()
    else:
        args = action.args
    return {k: make_serializable(v) for k, v in args.items() if k != "self"}


def workflow_from_event_log(event_log: list[CompletedEvent]) -> dict[str, dict[str, Any]]:
    workflow: dict[str, dict[str, Any]] = {}
    previous_step_name: str | None = None
    step_index = 0

    for event in event_log:
        if event.event_type != EventType.AGENT:
            continue
        if not isinstance(event.action, Action):
            continue
        if event.action.class_name == "AgentUserInterface":
            continue

        step = WorkflowStep(
            name=f"step{step_index}",
            content=event.metadata.return_value if event.metadata else None,
            op_type=_resolve_op_type(event.action),
            tool_name=_resolve_tool_name(event.action),
            tool_args=_extract_action_args(event.action, event),
            depends_on=[previous_step_name] if previous_step_name else [],
            time=event.event_time,
        )
        workflow[step.name] = step.to_dict()
        previous_step_name = step.name
        step_index += 1

    return workflow


def workflow_from_oracle_events(scenario: Scenario) -> dict[str, dict[str, Any]]:
    workflow: dict[str, dict[str, Any]] = {}
    event_name_map: dict[str, str] = {}
    step_index = 0

    for event in scenario.events:
        if not isinstance(event, OracleEvent):
            continue
        source_event = event.make_event(None)
        if not isinstance(source_event, Event):
            continue
        if source_event.action.class_name == "AgentUserInterface":
            continue
        tool_args = _extract_action_args(source_event.action)
        step_name = f"step{step_index}"
        depends_on = [
            event_name_map[dependency.event_id]
            for dependency in event.dependencies
            if dependency.event_id in event_name_map
        ]
        step = WorkflowStep(
            name=step_name,
            content=None,
            op_type=_resolve_op_type(source_event.action),
            tool_name=_resolve_tool_name(source_event.action),
            tool_args=tool_args,
            depends_on=depends_on,
            time=event.event_time,
        )
        workflow[step_name] = step.to_dict()
        event_name_map[event.event_id] = step_name
        step_index += 1

    return workflow


def ensure_oracle_workflow(scenario: Scenario) -> dict[str, dict[str, Any]]:
    if getattr(scenario, "_cached_oracle_workflow", None) is None:
        if getattr(scenario, "oracle_run_event_log", None) is None:
            try:
                run_oracle_mode(scenario)
            except Exception:
                pass
        scenario._cached_oracle_workflow = workflow_from_oracle_events(scenario)  # type: ignore[attr-defined]
    return scenario._cached_oracle_workflow  # type: ignore[attr-defined]


def _default_workflow_dir(scenario: Scenario, env: Any) -> Path:
    env_dump_dir = getattr(env, "dump_dir", None)
    if env_dump_dir:
        return Path(env_dump_dir)
    if scenario.working_dir:
        return Path(scenario.working_dir)
    return Path.cwd() / "workflow_exports"


def save_workflow_json(
    workflow: dict[str, dict[str, Any]],
    output_dir: str | os.PathLike[str],
    filename: str,
) -> str:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / filename
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(workflow, handle, ensure_ascii=False, indent=2)
    return str(file_path)


def append_workflow_evaluation(
    scenario: Scenario,
    env: Any,
    result: ScenarioValidationResult,
    workflow_subdir: str = "workflows",
) -> ScenarioValidationResult:
    oracle_workflow = ensure_oracle_workflow(scenario)
    agent_workflow = workflow_from_event_log(env.event_log.list_view())
    metrics = evaluate_workflows(oracle_workflow, agent_workflow)

    workflow_dir = _default_workflow_dir(scenario, env) / workflow_subdir
    oracle_path = save_workflow_json(
        oracle_workflow,
        workflow_dir,
        f"workflow_oracle_{scenario.scenario_id}.json",
    )
    agent_path = save_workflow_json(
        agent_workflow,
        workflow_dir,
        f"workflow_agent_{scenario.scenario_id}.json",
    )

    metric_text = ", ".join(f"{key}={value:.4f}" for key, value in metrics.items())
    workflow_text = f"workflow_oracle={oracle_path}, workflow_agent={agent_path}"
    parts = [result.rationale] if result.rationale else []
    parts.append(f"workflow_eval: {metric_text}")
    parts.append(workflow_text)
    result.rationale = "\n".join(parts)
    return result
