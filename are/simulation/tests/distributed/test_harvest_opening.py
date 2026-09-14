from types import SimpleNamespace

import pytest

from are.simulation.distributed.authored_specs import author_process
from are.simulation.distributed.calibration import (
    instrument_harvest_opening,
    run_drought_calibration,
)
from are.simulation.distributed.controllers import (
    OracleCeilingController,
    OracleCeilingCoordinator,
)
from are.simulation.distributed.models import AgentIntent, IntentKind
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario


def test_native_opening_preserves_inspection_and_never_rewinds():
    scenario = create_native_scenario("farm_disease_drought", world_seed=0)
    event = next(
        event
        for event in scenario.events
        if event.event_id == "o_wait_harvest_advance_day_001"
    )
    original = dict(event.make_event(None).action.args)
    records = instrument_harvest_opening(scenario, 100000.0)
    assert event.make_event(None).action.args == original
    for now, expected in [(0, 86400), (95000.75, 5000), (110000, 1)]:
        env = SimpleNamespace(time_manager=SimpleNamespace(time=lambda: now))
        action = event.make_event(env).action
        assert action.args["seconds"] == expected
        assert action.args["days"] == 0
        assert action.args["self"] is original["self"]
    assert [row["executed_seconds"] for row in records] == [86400, 5000, 1]


def test_shared_reference_opening_does_not_clip_recovery_waits():
    wait = AgentIntent(
        kind=IntentKind.ACT, action="SystemApp__advance_time", args={"days": 1}
    )
    harvest = AgentIntent(
        kind=IntentKind.ACT,
        action="TractorApp__harvest",
        args={"start_ridge": 20, "end_ridge": 23},
    )
    coordinator = OracleCeilingCoordinator(
        [("clock", "harvest", wait), ("ops", "harvest", harvest)]
    )
    settings = dict(
        harvest_openings={"harvest": 100000},
        harvest_deadlines={"harvest": 400000},
        harvest_clock_actor="clock",
        retry_wet_soil=True,
    )
    clock = OracleCeilingController("clock", coordinator, **settings)
    operations = OracleCeilingController("ops", coordinator, **settings)
    assert clock.decide(SimpleNamespace(world_time=95000.75)).args["seconds"] == 5000
    assert operations.decide(SimpleNamespace(world_time=100000)) == harvest
    rejection = {"result": {"error": "Cannot harvest in rainy conditions"}}
    operations.observe(rejection)
    assert clock.decide(SimpleNamespace(world_time=100000)).args == {"days": 1}
    assert operations.decide(SimpleNamespace(world_time=186400)) == harvest
    assert operations.results == [rejection]

    wet_soil = {"result": {"error": "Soil too wet for harvest (avg VWC 0.41 > 0.40)"}}
    operations.observe(wet_soil)
    assert clock.decide(SimpleNamespace(world_time=186400)).args == {"days": 1}
    assert operations.decide(SimpleNamespace(world_time=272800)) == harvest


def test_opening_is_explicit_and_bound_to_the_process(tmp_path):
    settings = dict(
        scenario_revision="drought_water_balance_v5",
        calibration_candidate=True,
        reference_harvest_calendar=True,
    )
    legacy = author_process("farm_disease_drought", **settings)
    process = author_process(
        "farm_disease_drought", **settings, reference_harvest_opening=True
    )
    assert process.digest != legacy.digest
    assert "reference_harvest_openings" not in legacy.occurrence_net.metadata
    assert process.occurrence_net.metadata["reference_harvest_openings"] == {
        "harvest": 1788220800.0
    }
    assert process.metadata["authored_choices"]["reference_harvest_retries"] == [
        "rain",
        "immaturity",
        "grain_moisture",
        "soil_trafficability",
    ]
    path = tmp_path / "process.json"
    path.write_text(process.model_dump_json())
    plan = run_drought_calibration(
        tmp_path / "unused",
        world_seeds=[0],
        candidate=True,
        scenario_revision="drought_water_balance_v5",
        retry_immaturity=True,
        retry_wet_grain=True,
        retry_wet_soil=True,
        reference_process_path=path,
        dry_run=True,
    )
    assert plan["workflow_variant"] == "authored_opening_harvest_v6"
    assert plan["harvest_opening_world_time"] == 1788220800.0
    assert plan["reference_process_digest"] == process.digest
    assert not (tmp_path / "unused").exists()
    with pytest.raises(ValueError, match="bounded drought calendar"):
        author_process("farm_wetjune_recheck", reference_harvest_opening=True)
