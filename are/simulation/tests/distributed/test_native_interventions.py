from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from are.simulation.apps.farm_world import FarmWorldApp
from are.simulation.distributed.native_interventions import (
    InterventionPlan,
    instrument_intervention,
    run_native_interventions,
    selected_treatments,
)
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


@pytest.mark.parametrize(
    "scenario,arm,count",
    [
        ("farm_wetjune_recheck", "omit_mid_fungicide", 3),
        ("farm_three_cultivar", "omit_irrigation", 1),
    ],
)
def test_omission_keeps_native_graph_and_does_not_apply_management(
    scenario, arm, count
):
    native = create_native_scenario(scenario, world_seed=0)
    farm = native.get_typed_app(FarmWorldApp)
    farm.advance_physics_time(native.start_time)
    before = deepcopy(farm.get_state())
    plan = InterventionPlan(
        scenario_id=scenario,
        world_seeds=(0,),
        arms=("reference", arm),
        rationale="TEST FIXTURE: exact omission and graph preservation",
    )
    graph = {
        e.event_id: (list(e.dependencies), list(e.successors))
        for e, _, _ in selected_treatments(native)
    }
    records = instrument_intervention(native, farm, plan, arm)
    for event, phase, function in selected_treatments(native):
        if (arm == "omit_mid_fungicide" and phase == "midseason") or (
            arm == "omit_irrigation" and function == "irrigate"
        ):
            action = event.make_event(
                SimpleNamespace(
                    time_manager=SimpleNamespace(time=lambda: native.start_time)
                )
            ).action
            result = action.execute()
            assert result["calibration_omitted"] is True
        assert (list(event.dependencies), list(event.successors)) == graph[
            event.event_id
        ]
    assert len(records) == count
    assert all(r["omitted"] and not r["accepted"] for r in records)
    assert farm.get_state() == before


def test_manifest_is_required_and_confirmation_worlds_are_not_consumed(
    tmp_path, monkeypatch
):
    import are.simulation.distributed.native_interventions as module

    manifest = (
        Path(__file__).resolve().parents[4]
        / "AAMAS/handover_development/interventions/wetjune.yaml"
    )
    monkeypatch.setattr(
        module,
        "create_native_scenario",
        lambda *a, **k: pytest.fail("dry run must not initialize"),
    )
    result = run_native_interventions(manifest, tmp_path / "unused", dry_run=True)
    assert result["season_count"] == 5 and result["provider_requests"] == 0
    assert not (tmp_path / "unused").exists()
    with pytest.raises(ValueError, match="worlds 0"):
        InterventionPlan(
            scenario_id="farm_wetjune_recheck",
            world_seeds=(20,),
            arms=("reference",),
            rationale="TEST FIXTURE: forbidden confirmation world",
        )
