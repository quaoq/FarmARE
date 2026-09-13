"""Counterexamples where aggregate observations can misclassify native actions."""

from types import SimpleNamespace

import pytest

from are.simulation.distributed.farm_adapter import FarmScenarioAdapter
from are.simulation.distributed.models import EpistemicStatus
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    compile_native_petri_net,
)


def observed(result, action="FarmWorldApp__get_ridge_range_state", args=None):
    adapter = object.__new__(FarmScenarioAdapter)
    return {
        fact.key: fact
        for fact in adapter.extract_observed(
            action=action,
            args=args or {"start_ridge": 20, "end_ridge": 21},
            result=result,
            phase="harvest",
        )
    }


def test_one_wet_ridge_prevents_false_harvest_readiness():
    ridges = [
        {"ridge_id": 20, "growth_stage": "R8", "grain_moisture_pct": 13.0},
        {"ridge_id": 21, "growth_stage": "R8", "grain_moisture_pct": 21.0},
    ]
    local = observed({"ridges": ridges})
    # The mean is 17%, but native harvest rejects the second ridge (>18%).
    assert local["crop:grain_moisture"].value == 21.0
    assert local["crop:grain_moisture"].scope == (20, 21)
    adapter = object.__new__(FarmScenarioAdapter)
    adapter.farm_world = SimpleNamespace(get_state=lambda: {"ridges": ridges})
    adapter.weather = SimpleNamespace(
        is_sprayable=True, rainfall_mm=0.0, is_trafficable=True
    )
    world = {
        fact.fact_key: fact.value
        for fact in adapter.authoritative_snapshot(
            source_event_id="inspection",
            farmare_event_id=None,
            action="TractorApp__harvest",
            args={"start_ridge": 20, "end_ridge": 21},
            world_time=0.0,
            phase="harvest",
        )
    }
    assert world["crop:grain_moisture"] == 21.0


def test_partial_crop_report_cannot_certify_whole_requested_batch():
    facts = observed(
        {
            "ridges": [
                {"ridge_id": 20, "growth_stage": "R8", "grain_moisture_pct": 13.0},
            ]
        }
    )
    assert "crop:mature" not in facts
    assert "crop:grain_moisture" not in facts
    assert "tool_observation:FarmWorldApp__get_ridge_range_state" in facts


@pytest.mark.parametrize(
    "vwc, suitable", [(0.19, False), (0.20, True), (0.35, True), (0.36, False)]
)
def test_planting_moisture_matches_native_inclusive_boundaries(vwc, suitable):
    facts = observed(
        {"sensor_id": "opaque-probe", "ridge_start": 20, "ridge_end": 21, "vwc": vwc},
        action="SensorApp__read_soil_sensor",
        args={"sensor_id": "opaque-probe"},
    )
    fact = facts["planting:soil_suitable"]
    assert fact.value is suitable
    assert fact.status == EpistemicStatus.INFERRED
    assert not any("root" in key for key in facts)


def test_unequal_probe_regions_use_ridge_weighted_vwc():
    facts = observed(
        {
            "soil_sensors": [
                {"ridge_start": 0, "ridge_end": 53, "vwc": 0.18},
                {"ridge_start": 54, "ridge_end": 63, "vwc": 0.28},
            ]
        },
        action="SensorApp__read_soil_sensors",
    )
    # An unweighted mean (0.23) wrongly clears the native 0.20 lower bound.
    assert facts["soil:mean_vwc"].value == pytest.approx(0.195625)
    assert facts["planting:soil_suitable"].value is False


@pytest.mark.parametrize(
    "scenario_id, expected_scope",
    [
        ("farm_disease_drought", (20, 43)),
        ("farm_three_cultivar", (43, 63)),
    ],
)
def test_irrigation_specification_retains_native_inclusive_scope(
    scenario_id, expected_scope
):
    net = compile_native_petri_net(scenario_id)
    irrigation = [t for t in net.transitions if t.action == "FieldOpsApp__irrigate"]
    assert irrigation
    for transition in irrigation:
        assert transition.scope == expected_scope
        assert all(guard.scope == expected_scope for guard in transition.guards)
