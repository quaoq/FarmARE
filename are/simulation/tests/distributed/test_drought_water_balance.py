"""Versioned subdaily physics conserves water independently of yield outcomes."""

from dataclasses import asdict

import pytest

from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.apps.farm_world.farm_physics_state import FarmPhysicsState
from are.simulation.apps.farm_world.physics_orchestrator import (
    _apply_subdaily_irrigation,
)
from are.simulation.distributed.calibration import drought_target
from are.simulation.distributed.models import DistributedRunnerConfig
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


@pytest.mark.parametrize(
    "top,root,dose", [(0.17, 0.14, 25), (0.17, 0.14, 60), (0.47, 0.479, 120)]
)
def test_effective_input_equals_storage_runoff_and_drainage(top, root, dose):
    p = FarmPhysicsState(num_ridges=2, conserve_subdaily_irrigation=True)
    state = p.soil.states[0]
    state.top_vwc, state.root_vwc = top, root
    untouched = asdict(p.soil.states[1])
    params = p.soil.params_for_ridge(0)
    before = top * params.top_depth_m * 1000 + root * params.root_depth_m * 1000
    _apply_subdaily_irrigation(p, {0: dose})
    after = (
        state.top_vwc * params.top_depth_m * 1000
        + state.root_vwc * params.root_depth_m * 1000
    )
    assert (
        after - before + state.cumulative_runoff_mm + state.cumulative_drainage_mm
        == pytest.approx(dose * params.irrigation_efficiency)
    )
    assert state.top_vwc <= params.saturation_vwc
    assert state.root_vwc <= params.saturation_vwc
    assert asdict(p.soil.states[1]) == untouched


def test_legacy_water_path_is_preserved_and_exposes_original_saturation_loss():
    legacy, revised = (
        FarmPhysicsState(num_ridges=1, conserve_subdaily_irrigation=x)
        for x in (False, True)
    )
    for p in (legacy, revised):
        p.soil.params.max_infiltration_mm_day = 85
        _apply_subdaily_irrigation(p, {0: 60})
    assert revised.soil.states[0].root_vwc > legacy.soil.states[0].root_vwc
    assert legacy.soil.states[0].cumulative_runoff_mm == 0


def test_revised_candidate_changes_only_declared_pulse_quota_and_water_path():
    old, new = [
        create_native_scenario(
            "farm_disease_drought",
            world_seed=0,
            calibration_candidate=True,
            scenario_revision=revision,
        )
        for revision in ("drought_pulse_v4", "drought_water_balance_v5")
    ]
    a, b = (s.get_typed_app(FarmWorldApp) for s in (old, new))
    assert not a.physics.conserve_subdaily_irrigation
    assert b.physics.conserve_subdaily_irrigation
    regimes = [f._management_regime.to_dict() for f in (a, b)]
    assert regimes[0].pop("irrigation_quota_mm_total") == 9.375
    assert regimes[1].pop("irrigation_quota_mm_total") == 22.5
    assert regimes[0] == regimes[1]
    assert a.physics.soil.params == b.physics.soil.params
    assert a.physics.soil.hydraulic_modifiers == b.physics.soil.hydraulic_modifiers
    assert a.physics.yield_recovery.params == b.physics.yield_recovery.params
    assert a.physics.profile == b.physics.profile
    target, scope = drought_target(new)
    assert scope == (20, 43)
    assert target.make_event(None).action.args["hours"] == 12.0


def test_new_revision_requires_explicit_candidate_and_cannot_bypass_paper_gate():
    with pytest.raises(ValueError, match="candidate weather"):
        DistributedRunnerConfig(
            scenario_id="farm_disease_drought",
            scenario_revision="drought_water_balance_v5",
        )
    with pytest.raises(ValueError, match="confirmation evidence"):
        DistributedRunnerConfig(
            scenario_id="farm_disease_drought",
            scenario_revision="drought_water_balance_v5",
            calibration_candidate=True,
            paper_mode=True,
        )
