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
# Outcome (O) — Scheme B: growing / harvest / unharvested_mature buckets
# ---------------------------------------------------------------------------


@dataclass
class _FakeYieldState:
    biological_yield_g_m2: float
    recovered_yield_g_m2_at_market_moisture: float
    harvested: bool = False
    r8_reached: bool = False


@dataclass
class _FakePhenState:
    planted: bool


class _FakePhysics:
    """Minimal stand-in for FarmPhysicsState that _compute_outcome can read.

    Only the attributes _compute_outcome actually touches are populated:
        - engines_active (bool)
        - phenology.states[ridge_id].planted
        - yield_recovery.states[ridge_id]: biological_yield_g_m2,
          recovered_yield_g_m2_at_market_moisture, harvested, r8_reached
    """

    def __init__(self, ridges: dict[int, tuple[bool, _FakeYieldState]]):
        # ridges: {ridge_id: (planted, yield_state)}
        self.engines_active = True
        self.phenology = SimpleNamespace(
            states={rid: _FakePhenState(planted=p) for rid, (p, _) in ridges.items()}
        )
        self.yield_recovery = SimpleNamespace(
            states={rid: ys for rid, (_, ys) in ridges.items()}
        )


def _scenario_with_physics(physics: _FakePhysics) -> SimpleNamespace:
    farm_world = SimpleNamespace(_physics=physics)
    scenario = make_scenario()
    scenario.get_typed_app = lambda cls: farm_world
    return scenario


def test_outcome_harvest_loss_bucket_only():
    """Classic round-1+2 harvest scenario: every ridge harvested but
    machine-loss kicked some below 50% recovery.
    """
    ridges = {
        i: (
            True,
            _FakeYieldState(
                biological_yield_g_m2=400.0,
                recovered_yield_g_m2_at_market_moisture=120.0 if i < 5 else 360.0,
                harvested=True,
                r8_reached=True,
            ),
        )
        for i in range(10)
    }
    physics = _FakePhysics(ridges)
    score, breakdown = _compute_outcome(
        _scenario_with_physics(physics), make_env([]), crop_loss_threshold=0.5
    )
    assert breakdown.harvest_loss_count == 5
    assert breakdown.growing_loss_count == 0
    assert breakdown.unharvested_mature_count == 0
    assert breakdown.crop_loss_count == 5  # legacy alias preserved
    assert breakdown.crop_loss_fraction == pytest.approx(5 / 10)


def test_outcome_unharvested_mature_bucket():
    """Round-3-style 'agent recommended a date but never executed harvest'
    scenario: every ridge hit R8 with full biological yield, none harvested.
    yield_ratio should reflect 100% loss because mature ridges count
    against the denominator at zero recovery.
    """
    ridges = {
        i: (
            True,
            _FakeYieldState(
                biological_yield_g_m2=400.0,
                recovered_yield_g_m2_at_market_moisture=0.0,
                harvested=False,
                r8_reached=True,
            ),
        )
        for i in range(10)
    }
    physics = _FakePhysics(ridges)
    score, breakdown = _compute_outcome(
        _scenario_with_physics(physics), make_env([]), crop_loss_threshold=0.5
    )
    assert breakdown.unharvested_mature_count == 10
    assert breakdown.harvest_loss_count == 0
    assert breakdown.growing_loss_count == 0
    assert breakdown.yield_ratio == pytest.approx(0.0)
    # Outcome is bounded at 0 by _clip01, even with the 2x penalty stack.
    assert score == pytest.approx(0.0)


def test_outcome_growing_loss_bucket():
    """Mid-season collapse: a few ridges have biological yield <= 50% of
    field median (disease/drought wiped them out before harvest); none
    harvested, none mature yet.
    """
    ridges = {}
    for i in range(10):
        if i < 3:
            bio = 80.0  # collapsed
        else:
            bio = 400.0  # healthy
        ridges[i] = (
            True,
            _FakeYieldState(
                biological_yield_g_m2=bio,
                recovered_yield_g_m2_at_market_moisture=0.0,
                harvested=False,
                r8_reached=False,
            ),
        )
    physics = _FakePhysics(ridges)
    score, breakdown = _compute_outcome(
        _scenario_with_physics(physics), make_env([]), crop_loss_threshold=0.5
    )
    assert breakdown.growing_loss_count == 3
    assert breakdown.harvest_loss_count == 0
    assert breakdown.unharvested_mature_count == 0
    # No mature ridges -> mid-season episode -> yield_ratio defaults to 1.0
    assert breakdown.yield_ratio == pytest.approx(1.0)


def test_outcome_three_buckets_mutually_exclusive():
    """A mixed scenario where the same ridge could hypothetically fit
    multiple buckets; verify priority order is harvested > mature > growing.
    """
    ridges = {
        # Harvested with low recovery -> harvest_loss only
        0: (True, _FakeYieldState(400.0, 100.0, harvested=True, r8_reached=True)),
        # Mature, not harvested -> unharvested_mature only (NOT growing_loss
        # even though biological is 0)
        1: (True, _FakeYieldState(0.0, 0.0, harvested=False, r8_reached=True)),
        # Growing-stage collapse -> growing_loss only
        2: (True, _FakeYieldState(50.0, 0.0, harvested=False, r8_reached=False)),
        # Healthy ridge -> nothing
        3: (True, _FakeYieldState(400.0, 0.0, harvested=False, r8_reached=False)),
    }
    physics = _FakePhysics(ridges)
    _, breakdown = _compute_outcome(
        _scenario_with_physics(physics), make_env([]), crop_loss_threshold=0.5
    )
    assert breakdown.harvest_loss_count == 1
    assert breakdown.unharvested_mature_count == 1
    assert breakdown.growing_loss_count == 1
    assert breakdown.crop_loss_count == 1  # alias = harvest_loss only


def test_extrapolation_status_recorded_when_provided():
    """When _compute_outcome receives an extrapolation status dict, it
    should be propagated to OutcomeBreakdown.extrapolation untouched.
    """
    physics = _FakePhysics({})
    physics.engines_active = False  # short-circuit physics path
    status = {"status": "advanced", "days_ticked": 42, "reached_maturity": True}
    _, breakdown = _compute_outcome(
        _scenario_with_physics(physics),
        make_env([]),
        crop_loss_threshold=0.5,
        extrapolation_status=status,
    )
    assert breakdown.extrapolation == status


# ---------------------------------------------------------------------------
# Outcome (O) — oracle-baseline attribution + expects_agent_harvest gating
# ---------------------------------------------------------------------------


def test_outcome_oracle_baseline_yields_attribution_metrics():
    """When _compute_outcome receives oracle_biological_kg, it should
    populate yield_preserved_ratio + crop_loss_pct on the breakdown,
    and use yield_preserved_ratio as the headline term.
    """
    # Agent's run: 4 ridges all healthy, mid-season state (none mature).
    # With ridge_area_m2 = FIELD_LENGTH_M(268) * DEFAULT_RIDGE_WIDTH_M(1.1) = 294.8
    # agent_biological_kg = 4 * 200.0 g/m^2 * 294.8 m^2 / 1000 = 235.84 kg
    ridges = {
        i: (
            True,
            _FakeYieldState(
                biological_yield_g_m2=200.0,
                recovered_yield_g_m2_at_market_moisture=0.0,
                harvested=False,
                r8_reached=False,
            ),
        )
        for i in range(4)
    }
    physics = _FakePhysics(ridges)
    # Oracle baseline 472 kg → agent preserves 50% → crop_loss_pct = 0.5
    score, breakdown = _compute_outcome(
        _scenario_with_physics(physics),
        make_env([]),
        crop_loss_threshold=0.5,
        oracle_biological_kg=471.68,  # exactly 2x agent_biological_kg
    )
    assert breakdown.oracle_biological_kg == pytest.approx(471.68)
    assert breakdown.agent_biological_kg == pytest.approx(235.84, rel=1e-3)
    assert breakdown.yield_preserved_ratio == pytest.approx(0.5, rel=1e-3)
    assert breakdown.crop_loss_pct == pytest.approx(0.5, rel=1e-3)
    # No safety, no buckets ⇒ score = yield_preserved_ratio = 0.5
    assert score == pytest.approx(0.5, rel=1e-3)


def test_outcome_no_oracle_baseline_falls_back_to_yield_ratio():
    """Without an oracle baseline, headline reverts to legacy yield_ratio,
    and yield_preserved_ratio / crop_loss_pct are None.
    """
    ridges = {
        0: (True, _FakeYieldState(400.0, 200.0, harvested=True, r8_reached=True)),
        1: (True, _FakeYieldState(400.0, 300.0, harvested=True, r8_reached=True)),
    }
    physics = _FakePhysics(ridges)
    score, breakdown = _compute_outcome(
        _scenario_with_physics(physics),
        make_env([]),
        crop_loss_threshold=0.5,
        oracle_biological_kg=None,
    )
    assert breakdown.oracle_biological_kg is None
    assert breakdown.yield_preserved_ratio is None
    assert breakdown.crop_loss_pct is None
    # yield_ratio = (200+300)/(400+400) = 0.625, no penalties → outcome=0.625
    assert breakdown.yield_ratio == pytest.approx(500.0 / 800.0)
    assert score == pytest.approx(500.0 / 800.0)


def test_outcome_unharvested_mature_penalty_suppressed_when_not_expected():
    """For mid-season episodes where harvest isn't the agent's mandate,
    unharvested_mature_count must NOT contribute to the Outcome penalty.
    """
    ridges = {
        i: (True, _FakeYieldState(400.0, 0.0, harvested=False, r8_reached=True))
        for i in range(4)
    }
    physics = _FakePhysics(ridges)

    # expects_agent_harvest=True (default): 4 ridges × 2 × 0.05 = 0.4 penalty
    score_strict, _ = _compute_outcome(
        _scenario_with_physics(physics),
        make_env([]),
        crop_loss_threshold=0.5,
        expects_agent_harvest=True,
    )
    # expects_agent_harvest=False: penalty zeroed
    score_lenient, b = _compute_outcome(
        _scenario_with_physics(physics),
        make_env([]),
        crop_loss_threshold=0.5,
        expects_agent_harvest=False,
    )
    assert b.unharvested_mature_count == 4  # bucket count unchanged
    assert b.expects_agent_harvest is False
    # Lenient case keeps yield_ratio (0.0) but does not stack the
    # 2x_per_ridge unharvested penalty on top.
    assert score_strict == 0.0  # clipped from -0.4
    assert score_lenient == 0.0  # also 0 because yield_ratio is 0
    # The penalty difference is observable when an oracle baseline lifts
    # outcome_main above zero — verify with a baseline injected.
    score_strict_b, _ = _compute_outcome(
        _scenario_with_physics(physics),
        make_env([]),
        crop_loss_threshold=0.5,
        oracle_biological_kg=471.68,
        expects_agent_harvest=True,
    )
    score_lenient_b, _ = _compute_outcome(
        _scenario_with_physics(physics),
        make_env([]),
        crop_loss_threshold=0.5,
        oracle_biological_kg=471.68,
        expects_agent_harvest=False,
    )
    # Same oracle baseline ⇒ outcome_main ≈ 1.0; strict subtracts 0.4,
    # lenient subtracts 0. Strict ≈ 0.6, lenient ≈ 1.0.
    assert score_strict_b == pytest.approx(0.6, rel=1e-3)
    assert score_lenient_b == pytest.approx(1.0, rel=1e-3)


def test_outcome_oracle_baseline_clips_above_one():
    """If the agent somehow produces more biological yield than the oracle
    (e.g. lucky stochastic weather or numerical drift), preservation should
    cap at 1.0 — paper-readable interpretation: never 'better than oracle'.
    """
    ridges = {
        0: (True, _FakeYieldState(400.0, 0.0, harvested=False, r8_reached=False)),
    }
    physics = _FakePhysics(ridges)
    _, b = _compute_outcome(
        _scenario_with_physics(physics),
        make_env([]),
        crop_loss_threshold=0.5,
        oracle_biological_kg=10.0,  # tiny vs. 117.92 kg agent
    )
    assert b.yield_preserved_ratio == pytest.approx(1.0)
    assert b.crop_loss_pct == pytest.approx(0.0)


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


def _empty_outcome_breakdown(yield_ratio: float = 0.8) -> OutcomeBreakdown:
    return OutcomeBreakdown(
        yield_ratio=yield_ratio,
        recovered_yield_kg=200.0,
        scenario_potential_kg=250.0,
        agent_biological_kg=250.0,
        oracle_biological_kg=None,
        yield_preserved_ratio=None,
        crop_loss_pct=None,
        growing_loss_count=0,
        harvest_loss_count=0,
        unharvested_mature_count=0,
        crop_loss_count=0,
        crop_loss_fraction=0.0,
        safety_violations=0,
        expects_agent_harvest=True,
    )


def test_re_weight_fos_preserves_components():
    components = FOSComponents(outcome=0.8, decision=0.6, efficiency=0.4, fos=0.5)
    report = FOSReport(
        scenario_id="t",
        components=components,
        outcome_breakdown=_empty_outcome_breakdown(),
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
        outcome_breakdown=_empty_outcome_breakdown(),
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
