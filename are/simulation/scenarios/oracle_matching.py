from dataclasses import dataclass
from typing import Any

from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import Action, CompletedEvent, EventType
from are.simulation.utils import make_serializable


@dataclass
class OracleStepSpec:
    function_name: str
    class_name: str
    penalty_if_repeated: float = 0.0


def _event_matches_step(event: CompletedEvent, step: OracleStepSpec) -> bool:
    action = event.action
    if not isinstance(action, Action):
        return False
    return (
        action.function_name == step.function_name
        and action.class_name == step.class_name
        and event.event_type == EventType.AGENT
    )


def _extract_agent_events(env: Any) -> list[CompletedEvent]:
    event_log = env.event_log.list_view()
    return [
        event
        for event in event_log
        if isinstance(event, CompletedEvent) and event.event_type == EventType.AGENT
    ]


def oracle_validate(
    scenario: Any,
    env: Any,
    step_specs: list[OracleStepSpec],
    success_threshold: float = 0.8,
    harmless_extra_penalty: float = 0.02,
) -> ScenarioValidationResult:
    agent_events = _extract_agent_events(env)
    if len(step_specs) == 0:
        return ScenarioValidationResult(
            success=True,
            rationale="oracle matching skipped because no expected steps were provided",
        )

    matched_steps = 0
    repeated_penalty = 0.0
    cursor = 0
    matched_details: list[dict[str, Any]] = []

    for index, step in enumerate(step_specs):
        found_idx: int | None = None
        repeat_count = 0
        for event_index in range(cursor, len(agent_events)):
            event = agent_events[event_index]
            if _event_matches_step(event, step):
                if found_idx is None:
                    found_idx = event_index
                else:
                    repeat_count += 1
            if found_idx is not None and not _event_matches_step(event, step):
                break
        if found_idx is None:
            matched_details.append(
                {
                    "expected_index": index,
                    "function_name": step.function_name,
                    "class_name": step.class_name,
                    "matched": False,
                }
            )
            continue

        matched_steps += 1
        cursor = found_idx + 1
        if repeat_count > 0 and step.penalty_if_repeated > 0:
            repeated_penalty += repeat_count * step.penalty_if_repeated
        matched_details.append(
            {
                "expected_index": index,
                "function_name": step.function_name,
                "class_name": step.class_name,
                "matched": True,
                "repeat_count": repeat_count,
            }
        )

    extra_steps = max(0, len(agent_events) - matched_steps)
    base_score = matched_steps / len(step_specs)
    penalty = repeated_penalty + extra_steps * harmless_extra_penalty
    final_score = max(0.0, base_score - penalty)
    success = final_score >= success_threshold

    rationale_payload = {
        "scenario_id": getattr(scenario, "scenario_id", "unknown"),
        "matched_steps": matched_steps,
        "expected_steps": len(step_specs),
        "executed_agent_steps": len(agent_events),
        "extra_steps": extra_steps,
        "base_score": round(base_score, 4),
        "penalty": round(penalty, 4),
        "final_score": round(final_score, 4),
        "success_threshold": success_threshold,
        "details": matched_details,
    }
    rationale_text = (
        "oracle_matching: "
        f"matched={matched_steps}/{len(step_specs)}, "
        f"extra={extra_steps}, "
        f"penalty={penalty:.4f}, "
        f"score={final_score:.4f}, "
        f"threshold={success_threshold:.4f}\n"
        f"oracle_matching_details={make_serializable(rationale_payload)}"
    )
    return ScenarioValidationResult(success=success, rationale=rationale_text)
