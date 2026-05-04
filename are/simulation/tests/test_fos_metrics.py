"""
Unit tests for the FOS evaluation framework.

Each component is tested in isolation with synthetic event logs / physics state
so the tests run in milliseconds without spinning up a full scenario. The
end-to-end smoke against a real round-3 scenario lives in
test_fos_integration.py (added later).
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from are.simulation.scenarios.fos import (
    DEFAULT_WEIGHTS,
    EfficiencyBreakdown,
    FOSComponents,
    FOSReport,
    GateSpec,
    OutcomeBreakdown,
    evaluate_fos,
    re_weight_fos,
)
from are.simulation.scenarios.fos.evaluation import (
    _compute_efficiency,
    _compute_outcome,
    _count_redundant_reads,
    _count_safety_violations,
    _match_gate,
)
from are.simulation.scenarios.fos.predicates import (
    after_observation,
    and_,
    arg_equals,
    max_arg,
    min_arg,
    or_,
    succeeded,
    targets_ridges_overlap,
)
from are.simulation.scenarios.fos.sensitivity import re_weight_fos, weight_grid
from are.simulation.types import EventType, OperationType


# ---------------------------------------------------------------------------
# Test fixtures — synthetic CompletedEvent / Env / Scenario
# ---------------------------------------------------------------------------


def make_event(
    *,
    cls: str,
    fn: str,
    event_time: float,
    event_id: str | None = None,
    args: dict[str, Any] | None = None,
    op: OperationType = OperationType.READ,
    return_value: Any = None,
    failed: bool = False,
) -> SimpleNamespace:
    """Build a duck-typed CompletedEvent for FOS internals to consume."""
    args = args or {}
    action = SimpleNamespace(
        class_name=cls,
        function_name=fn,
        args=args,
        resolved_args=args,
        operation_type=op,
    )
    metadata = SimpleNamespace(
        return_value=return_value,
        exception=None if not failed else RuntimeError("boom"),
    )
    event = SimpleNamespace(
        event_id=event_id or f"{cls}__{fn}__{event_time}",
        event_type=EventType.AGENT,
        event_time=event_time,
        action=action,
        metadata=metadata,
    )
    event.get_args = lambda: args
    event.failed = lambda: failed
    return event


def make_env(events: list[Any]) -> SimpleNamespace:
    """Wrap an event list into an env-like object FOS can read."""
    log = SimpleNamespace(list_view=lambda: list(events))
    return SimpleNamespace(event_log=log, dump_dir=None)


def make_scenario(
    *,
    scenario_id: str = "test_scenario",
    start_time: float = 0.0,
    events: list[Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        scenario_id=scenario_id,
        start_time=start_time,
        events=events or [],
        working_dir=None,
        get_typed_app=lambda _cls: None,
    )


# ---------------------------------------------------------------------------
# Outcome (O)
# ---------------------------------------------------------------------------


def test_outcome_neutral_when_no_physics():
    """If no harvest data is available (mid-season episode or physics-off),
    outcome defaults to 1.0 (no yield-loss claim) minus violation penalties.

    The Decision and Efficiency components carry the signal in mid-season
    scenarios; outcome only becomes meaningful when biological_yield > 0
    on some ridge (i.e., scenario reached R8).
    """
    env = make_env([])
    scenario = make_scenario()
    score, breakdown = _compute_outcome(scenario, env, crop_loss_threshold=0.5)
    assert score == 1.0  # no harvest data + no penalties → neutral 1.0
    assert breakdown.recovered_yield_kg == 0.0
    assert breakdown.scenario_potential_kg == 0.0
    assert breakdown.crop_loss_count == 0
    assert breakdown.safety_violations == 0
    assert breakdown.yield_ratio == 1.0


def test_outcome_safety_violation_penalty_applies_in_mid_season():
    """Even with no harvest data, safety violations should reduce Outcome."""
    events = [
        make_event(
            cls="TractorApp",
            fn="apply_pesticide",
            event_time=100.0,
            return_value={"error": "Weather conditions do not allow spraying"},
        ),
    ]
    score, breakdown = _compute_outcome(
        make_scenario(), make_env(events), crop_loss_threshold=0.5
    )
    # 1.0 baseline - 0.10 per safety violation = 0.90
    assert breakdown.safety_violations == 1
    assert score == pytest.approx(0.90)


def test_safety_violations_counted_from_error_returns():
    """Tools that error with safety-relevant messages count as violations."""
    events = [
        make_event(
            cls="TractorApp",
            fn="apply_pesticide",
            event_time=100.0,
            return_value={"error": "Weather conditions do not allow spraying (rain or wind >= 5 m/s)"},
        ),
        make_event(
            cls="TractorApp",
            fn="harvest",
            event_time=200.0,
            return_value={"error": "Soil too wet for harvest (avg VWC > 0.35)"},
        ),
        # Non-safety error: should NOT count.
        make_event(
            cls="TractorApp",
            fn="refuel",
            event_time=300.0,
            return_value={"error": "Insufficient fuel storage in warehouse"},
        ),
        # Successful call: should NOT count.
        make_event(
            cls="WeatherApp",
            fn="get_current_weather",
            event_time=50.0,
            return_value={"date": "2026-05-20", "temp_c": 22.0},
        ),
    ]
    violations, details = _count_safety_violations(make_env(events))
    assert violations == 2
    assert len(details) == 2
    assert all("error" in d for d in details)


def test_safety_violations_ignore_aui():
    """AgentUserInterface errors are not safety violations."""
    events = [
        make_event(
            cls="AgentUserInterface",
            fn="send_message_to_user",
            event_time=10.0,
            return_value={"error": "wind detected"},
        ),
    ]
    violations, _ = _count_safety_violations(make_env(events))
    assert violations == 0


# ---------------------------------------------------------------------------
# Decision (D) — gate matching
# ---------------------------------------------------------------------------


def test_gate_unmatched_when_no_eligible_event():
    gate = GateSpec(
        name="G1",
        intent="agent must irrigate within day 1",
        window_days=(0.0, 1.0),
        eligible_tools=[("FieldOpsApp", "irrigate_range")],
    )
    env = make_env([
        make_event(cls="WeatherApp", fn="get_current_weather", event_time=3600),
    ])
    scenario = make_scenario(start_time=0.0)
    result = _match_gate(env, scenario, gate, scenario_start_time=0.0)
    assert not result.matched
    assert "no candidate" in (result.rejection_reason or "")


def test_gate_matched_in_window():
    gate = GateSpec(
        name="G1",
        intent="agent must irrigate within day 1",
        window_days=(0.0, 1.0),
        eligible_tools=[("FieldOpsApp", "irrigate_range")],
    )
    events = [
        make_event(
            cls="FieldOpsApp",
            fn="irrigate_range",
            event_time=43200.0,  # 0.5 days
            args={"start": 22, "end": 32, "duration_hours": 1.5},
            op=OperationType.WRITE,
        ),
    ]
    result = _match_gate(make_env(events), make_scenario(), gate, 0.0)
    assert result.matched
    assert result.matched_at_day == pytest.approx(0.5)
    assert result.matched_event_id == events[0].event_id


def test_gate_unmatched_outside_window():
    gate = GateSpec(
        name="G1",
        intent="agent must irrigate within day 1",
        window_days=(0.0, 1.0),
        eligible_tools=[("FieldOpsApp", "irrigate_range")],
    )
    # Event at day 2, outside window
    events = [
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=2 * 86400),
    ]
    result = _match_gate(make_env(events), make_scenario(), gate, 0.0)
    assert not result.matched
    assert "out of window" in (result.rejection_reason or "")


def test_gate_requires_after_observation():
    """Gate requires that a sensor read happened before the action."""
    gate = GateSpec(
        name="G_irrigate_after_read",
        intent="agent must observe before irrigating",
        window_days=(0.0, 5.0),
        eligible_tools=[("FieldOpsApp", "irrigate_range")],
        requires=after_observation("SensorApp", "read_soil_sensors"),
    )

    # Case A: irrigate without prior read → unmatched
    events_a = [
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=3600.0,
                   args={"start": 0, "end": 3, "duration_hours": 1.0},
                   op=OperationType.WRITE),
    ]
    result_a = _match_gate(make_env(events_a), make_scenario(), gate, 0.0)
    assert not result_a.matched

    # Case B: read first, then irrigate → matched
    events_b = [
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=1800.0),
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=3600.0,
                   args={"start": 0, "end": 3, "duration_hours": 1.0},
                   op=OperationType.WRITE),
    ]
    result_b = _match_gate(make_env(events_b), make_scenario(), gate, 0.0)
    assert result_b.matched


def test_gate_requires_targets_ridges_overlap():
    gate = GateSpec(
        name="G_irrigate_dry_zone",
        intent="agent must irrigate the dry zone (ridges 22-32)",
        window_days=(0.0, 5.0),
        eligible_tools=[("FieldOpsApp", "irrigate_range")],
        requires=targets_ridges_overlap(22, 32),
    )

    # Wrong zone → unmatched
    events_wrong = [
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=3600.0,
                   args={"start": 0, "end": 5}, op=OperationType.WRITE),
    ]
    assert not _match_gate(make_env(events_wrong), make_scenario(), gate, 0.0).matched

    # Correct zone → matched
    events_right = [
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=3600.0,
                   args={"start": 22, "end": 32}, op=OperationType.WRITE),
    ]
    assert _match_gate(make_env(events_right), make_scenario(), gate, 0.0).matched

    # Partial overlap (25-40) → matched
    events_partial = [
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=3600.0,
                   args={"start": 25, "end": 40}, op=OperationType.WRITE),
    ]
    assert _match_gate(make_env(events_partial), make_scenario(), gate, 0.0).matched


def test_gate_requires_min_arg():
    gate = GateSpec(
        name="G_substantial_irrigation",
        intent="must irrigate at least 1 hour",
        window_days=(0.0, 5.0),
        eligible_tools=[("FieldOpsApp", "irrigate_range")],
        requires=min_arg("duration_hours", 1.0),
    )
    # Too short
    e_short = [make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=100,
                          args={"duration_hours": 0.5}, op=OperationType.WRITE)]
    assert not _match_gate(make_env(e_short), make_scenario(), gate, 0.0).matched
    # Long enough
    e_long = [make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=100,
                         args={"duration_hours": 2.0}, op=OperationType.WRITE)]
    assert _match_gate(make_env(e_long), make_scenario(), gate, 0.0).matched


def test_gate_requires_composed_and():
    """and_(read_first, target_dry_zone) — both must hold."""
    gate = GateSpec(
        name="G_compound",
        intent="...",
        window_days=(0.0, 5.0),
        eligible_tools=[("FieldOpsApp", "irrigate_range")],
        requires=and_(
            after_observation("SensorApp", "read_soil_sensors"),
            targets_ridges_overlap(22, 32),
        ),
    )
    # Read but wrong zone → fail
    events = [
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=100),
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=200,
                   args={"start": 0, "end": 5}, op=OperationType.WRITE),
    ]
    assert not _match_gate(make_env(events), make_scenario(), gate, 0.0).matched
    # Read AND right zone → match
    events_ok = [
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=100),
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=200,
                   args={"start": 22, "end": 32}, op=OperationType.WRITE),
    ]
    assert _match_gate(make_env(events_ok), make_scenario(), gate, 0.0).matched


# ---------------------------------------------------------------------------
# Efficiency (E) — tool inflation, redundant reads
# ---------------------------------------------------------------------------


def test_redundant_reads_within_window_no_write():
    """Two reads of the same tool within 1h, no write between → 1 redundant."""
    events = [
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=0.0,
                   op=OperationType.READ),
        # 30 minutes later, same read, no write between
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=1800.0,
                   op=OperationType.READ),
    ]
    assert _count_redundant_reads(events) == 1


def test_redundant_reads_reset_by_write():
    events = [
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=0.0,
                   op=OperationType.READ),
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=900.0,
                   op=OperationType.WRITE),
        # Next read after a write is justified, not redundant.
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=1800.0,
                   op=OperationType.READ),
    ]
    assert _count_redundant_reads(events) == 0


def test_redundant_reads_outside_window():
    """Two reads more than 1h apart → not redundant (state may have evolved)."""
    events = [
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=0.0,
                   op=OperationType.READ),
        # 2 hours later
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=7200.0,
                   op=OperationType.READ),
    ]
    assert _count_redundant_reads(events) == 0


def test_efficiency_perfect_when_oracle_match_no_redundancy():
    events = [
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=0.0,
                   op=OperationType.READ),
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=1000.0,
                   op=OperationType.WRITE),
    ]
    score, breakdown = _compute_efficiency(
        make_scenario(), make_env(events), oracle_tool_count=2
    )
    assert breakdown.tool_inflation == pytest.approx(1.0)
    assert breakdown.redundant_reads == 0
    assert score == pytest.approx(1.0)


def test_efficiency_penalises_inflation():
    """Agent calls 4 tools; oracle only needs 2 → inflation 2.0, score 0.5."""
    events = [
        make_event(cls=f"App{i}", fn="op", event_time=i * 100.0,
                   op=OperationType.WRITE)
        for i in range(4)
    ]
    score, breakdown = _compute_efficiency(
        make_scenario(), make_env(events), oracle_tool_count=2
    )
    assert breakdown.tool_inflation == pytest.approx(2.0)
    assert score == pytest.approx(0.5)


def test_efficiency_capped_inflation():
    """Inflation > 3.0 is capped, so score floor is 1/3."""
    events = [
        make_event(cls=f"App{i}", fn="op", event_time=i * 100.0,
                   op=OperationType.WRITE)
        for i in range(20)
    ]
    score, breakdown = _compute_efficiency(
        make_scenario(), make_env(events), oracle_tool_count=2
    )
    assert breakdown.tool_inflation == pytest.approx(3.0)
    assert score == pytest.approx(1.0 / 3.0)


# ---------------------------------------------------------------------------
# Composite + sensitivity
# ---------------------------------------------------------------------------


def test_evaluate_fos_returns_full_report():
    events = [
        make_event(cls="SensorApp", fn="read_soil_sensors", event_time=0.0),
        make_event(cls="FieldOpsApp", fn="irrigate_range", event_time=1000.0,
                   args={"start": 22, "end": 32, "duration_hours": 1.5},
                   op=OperationType.WRITE),
    ]
    scenario = make_scenario(start_time=0.0)
    gates = [
        GateSpec(
            name="G_irrigate",
            intent="...",
            window_days=(0.0, 1.0),
            eligible_tools=[("FieldOpsApp", "irrigate_range")],
            requires=after_observation("SensorApp", "read_soil_sensors"),
        ),
    ]
    report = evaluate_fos(scenario, make_env(events), gates=gates)
    assert isinstance(report, FOSReport)
    assert report.scenario_id == "test_scenario"
    assert 0.0 <= report.components.outcome <= 1.0
    assert 0.0 <= report.components.decision <= 1.0
    assert 0.0 <= report.components.efficiency <= 1.0
    assert 0.0 <= report.components.fos <= 1.0
    assert sum(report.weights.values()) == pytest.approx(1.0)


def test_re_weight_fos_preserves_components():
    components = FOSComponents(outcome=0.8, decision=0.6, efficiency=0.4, fos=0.5)
    report = FOSReport(
        scenario_id="t",
        components=components,
        outcome_breakdown=OutcomeBreakdown(0.8, 200, 250, 0, 0.0, 0),
        decision_breakdown=[],
        efficiency_breakdown=EfficiencyBreakdown(8, 8, 1.0, 0, 0.0),
        weights=dict(DEFAULT_WEIGHTS),
    )
    new = re_weight_fos(report, {"outcome": 1.0, "decision": 0.0, "efficiency": 0.0})
    assert new.components.outcome == 0.8
    assert new.components.decision == 0.6
    assert new.components.efficiency == 0.4
    assert new.components.fos == pytest.approx(0.8)


def test_re_weight_fos_default_matches_evaluate():
    """re_weight with default weights should equal the original evaluation."""
    components = FOSComponents(outcome=0.8, decision=0.6, efficiency=0.4, fos=0.5)
    report = FOSReport(
        scenario_id="t",
        components=components,
        outcome_breakdown=OutcomeBreakdown(0.8, 200, 250, 0, 0.0, 0),
        decision_breakdown=[],
        efficiency_breakdown=EfficiencyBreakdown(8, 8, 1.0, 0, 0.0),
        weights=dict(DEFAULT_WEIGHTS),
    )
    new = re_weight_fos(report, dict(DEFAULT_WEIGHTS))
    expected = 0.5 * 0.8 + 0.3 * 0.6 + 0.2 * 0.4
    assert new.components.fos == pytest.approx(expected)


def test_weight_grid_sums_to_one():
    grid = weight_grid(outcome_range=(0.4, 0.5, 0.6), decision_range=(0.2, 0.3, 0.4))
    for cell in grid:
        assert sum(cell.values()) == pytest.approx(1.0)
        assert all(v >= 0.0 for v in cell.values())
