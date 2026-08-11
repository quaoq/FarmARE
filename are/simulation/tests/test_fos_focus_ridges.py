"""Tests for the focus-ridge subset metrics in FOS Outcome.

Cover both routes for focus_donothing_biological_kg:
  1. The pre-built oracle baseline JSON (legacy) — already covered by
     existing scenarios via _load_focus_ridge_baselines indirectly. Here
     we focus on the *new* inline path:
  2. ``donothing_per_ridge_g_m2`` passed through evaluate_fos /
     _compute_outcome — replay-friendly, no JSON file needed.
"""
from __future__ import annotations

from types import SimpleNamespace

from are.simulation.scenarios.fos.evaluation import _compute_outcome


class _FakeYldState:
    def __init__(
        self,
        biological_yield_g_m2: float,
        recovered: float = 0.0,
        harvested: bool = False,
        r8_reached: bool = False,
    ) -> None:
        self.biological_yield_g_m2 = biological_yield_g_m2
        self.recovered_yield_g_m2_at_market_moisture = recovered
        self.harvested = harvested
        self.r8_reached = r8_reached


class _FakePhenState:
    def __init__(self, planted: bool) -> None:
        self.planted = planted


def _build_fake_scenario_env_with_physics(
    *,
    biological_per_ridge: dict[int, float],
    planted_per_ridge: dict[int, bool] | None = None,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Mock a scenario whose get_typed_app(FarmWorldApp) returns a stub
    farm_world with the bare minimum the Outcome path needs."""
    yield_states = {
        rid: _FakeYldState(bio, recovered=0.0)
        for rid, bio in biological_per_ridge.items()
    }
    phen_default = {rid: True for rid in biological_per_ridge}
    if planted_per_ridge:
        phen_default.update(planted_per_ridge)
    phen_states = {rid: _FakePhenState(p) for rid, p in phen_default.items()}

    physics = SimpleNamespace(
        engines_active=True,
        yield_recovery=SimpleNamespace(states=yield_states),
        phenology=SimpleNamespace(states=phen_states),
    )
    farm_world = SimpleNamespace(_physics=physics)

    def get_typed_app(cls):
        # _try_get_farm_world imports FarmWorldApp lazily; we just return
        # our stub regardless of the class object passed in.
        return farm_world

    scenario = SimpleNamespace(
        scenario_id="test_focus_inline",
        start_time=0.0,
        events=[],
        working_dir=None,
        get_typed_app=get_typed_app,
    )
    env = SimpleNamespace(
        event_log=SimpleNamespace(list_view=lambda: []),
        dump_dir=None,
    )
    return scenario, env


def test_focus_inline_donothing_overrides_baseline():
    """When ``donothing_per_ridge_g_m2`` is given, focus_donothing_biological_kg
    is computed inline as Σ(per_ridge[rid] * ridge_area / 1000) over the
    focus_ridge_ids set — independent of any oracle_baselines JSON."""
    # Field with 4 ridges, biological in g/m^2.
    biological_per_ridge = {0: 100.0, 1: 200.0, 2: 300.0, 3: 400.0}
    scenario, env = _build_fake_scenario_env_with_physics(
        biological_per_ridge=biological_per_ridge,
    )

    # Inline donothing per-ridge (lower than agent — agent improved over baseline).
    inline_dn_per_ridge = [50.0, 80.0, 120.0, 200.0]

    # Focus on ridges 1 and 2.
    focus_ridge_ids = [1, 2]

    score, breakdown = _compute_outcome(
        scenario,
        env,
        crop_loss_threshold=0.5,
        focus_ridge_ids=focus_ridge_ids,
        donothing_per_ridge_g_m2=inline_dn_per_ridge,
    )

    # Field length 100m × default ridge width 2m = 200 m^2 per ridge in the
    # current FarmWorldApp constants.  We compute the expected sum directly
    # using the same constants the function uses.
    from are.simulation.apps.farm_world.farm_world_app import (
        DEFAULT_RIDGE_WIDTH_M,
        FIELD_LENGTH_M,
    )

    ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M

    expected_focus_agent_kg = (200.0 + 300.0) * ridge_area_m2 / 1000.0  # ridges 1,2
    expected_focus_dn_kg = (80.0 + 120.0) * ridge_area_m2 / 1000.0  # ridges 1,2

    assert breakdown.focus_ridge_ids == [1, 2]
    assert breakdown.focus_agent_biological_kg == expected_focus_agent_kg
    assert breakdown.focus_donothing_biological_kg == expected_focus_dn_kg
    # No oracle baseline supplied → focus_oracle / focus_ypr / focus_nys = None.
    assert breakdown.focus_oracle_biological_kg is None
    assert breakdown.focus_yield_preserved_ratio is None
    assert breakdown.focus_normalized_yield_score is None


def test_focus_without_inline_or_baseline_only_agent_kg_is_filled():
    """No inline donothing + no baseline JSON → only focus_agent is
    populated; all other focus fields stay None (graceful degradation)."""
    biological_per_ridge = {0: 100.0, 1: 200.0, 2: 300.0}
    scenario, env = _build_fake_scenario_env_with_physics(
        biological_per_ridge=biological_per_ridge,
    )

    score, breakdown = _compute_outcome(
        scenario,
        env,
        crop_loss_threshold=0.5,
        focus_ridge_ids=[0, 2],
    )

    from are.simulation.apps.farm_world.farm_world_app import (
        DEFAULT_RIDGE_WIDTH_M,
        FIELD_LENGTH_M,
    )

    ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
    expected_focus_agent_kg = (100.0 + 300.0) * ridge_area_m2 / 1000.0

    assert breakdown.focus_ridge_ids == [0, 2]
    assert breakdown.focus_agent_biological_kg == expected_focus_agent_kg
    assert breakdown.focus_oracle_biological_kg is None
    assert breakdown.focus_donothing_biological_kg is None
    assert breakdown.focus_yield_preserved_ratio is None
    assert breakdown.focus_normalized_yield_score is None


def test_focus_inline_handles_short_per_ridge_array():
    """Per-ridge array shorter than focus ids → out-of-range indices are
    skipped silently, in-range ones still summed."""
    biological_per_ridge = {0: 100.0, 1: 200.0, 5: 500.0}
    scenario, env = _build_fake_scenario_env_with_physics(
        biological_per_ridge=biological_per_ridge,
    )
    # Array only covers ridges 0..2 (length 3); focus on [1, 5] → only 1
    # contributes inline (5 is out of range).
    inline_dn_per_ridge = [10.0, 20.0, 30.0]

    score, breakdown = _compute_outcome(
        scenario,
        env,
        crop_loss_threshold=0.5,
        focus_ridge_ids=[1, 5],
        donothing_per_ridge_g_m2=inline_dn_per_ridge,
    )

    from are.simulation.apps.farm_world.farm_world_app import (
        DEFAULT_RIDGE_WIDTH_M,
        FIELD_LENGTH_M,
    )

    ridge_area_m2 = FIELD_LENGTH_M * DEFAULT_RIDGE_WIDTH_M
    expected_dn_kg = 20.0 * ridge_area_m2 / 1000.0  # only ridge 1 in range

    assert breakdown.focus_donothing_biological_kg == expected_dn_kg
