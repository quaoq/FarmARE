from types import SimpleNamespace

import pytest

from are.simulation.distributed.authored_specs import author_process
from are.simulation.distributed.farm_adapter import FarmScenarioAdapter
from are.simulation.distributed.petri import PolicyResponse
from are.simulation.distributed.scientific_v5 import select_policy_rule


@pytest.mark.parametrize(
    "scenario", ["farm_wetjune_recheck", "farm_disease_drought", "farm_three_cultivar"]
)
def test_authored_contract_is_complete_deterministic_and_not_approved(scenario):
    process = author_process(scenario)
    assert process.digest == author_process(scenario).digest
    assert process.annotation_status == "frozen"
    assert process.expert_review_status == "author_defined"
    assert not process.metadata["authored_choices"]["professor_approved"]
    assert not process.metadata["engineering_defaults"]
    assert len(process.fault_treatments) == 8
    assert all(
        not g.fact_key.startswith("phase_evidence:")
        for t in process.occurrence_net.transitions
        for g in t.guards
    )
    for policy in process.information_policies:
        unknown = {g.fact_key: "unknown" for g in policy.requirements}
        assert (
            PolicyResponse.EXECUTE
            not in select_policy_rule(
                policy, unknown, "open", "open"
            ).permitted_responses
        )
        assert (
            PolicyResponse.EXECUTE
            not in select_policy_rule(
                policy, {k: "true" for k in unknown}, "closed", "open"
            ).permitted_responses
        )


def test_three_cultivar_harvest_and_storage_share_each_scoped_window():
    process = author_process("farm_three_cultivar")
    for zone, scope in zip("abc", ((0, 20), (21, 42), (43, 63)), strict=True):
        phase = f"harvest_{zone}"
        policy = next(p for p in process.information_policies if phase in p.phases)
        assert all(g.scope == scope for g in policy.requirements)
        stores = [
            t
            for t in process.occurrence_net.transitions
            if t.action == "FarmWorldApp__store_grain"
            and f"zone_{zone}_" in t.transition_id
        ]
        assert stores and all(t.phase == phase for t in stores)


def test_surface_proxy_does_not_expose_hidden_root_moisture():
    adapter = object.__new__(FarmScenarioAdapter)
    records = [{"ridge_id": r, "soil_vwc": 0.21} for r in (20, 21)]
    facts = adapter.extract_observed(
        action="FarmWorldApp__get_ridge_range_state",
        args={"start": 20, "end": 21},
        result={"ridges": records},
        phase="reproduction",
    )
    values = {f.key: f.value for f in facts}
    assert values["drought:surface_dry"] is False
    assert not any("root" in key for key in values)
    adapter.farm_world = SimpleNamespace(
        physics_active=True,
        get_state=lambda: {"ridges": records},
        physics=SimpleNamespace(
            soil=SimpleNamespace(
                states={r: SimpleNamespace(root_vwc=0.15) for r in (20, 21)}
            )
        ),
    )
    adapter.weather = SimpleNamespace(
        is_sprayable=True, rainfall_mm=0.0, is_trafficable=True
    )
    truth = adapter.authoritative_snapshot(
        source_event_id="world",
        farmare_event_id=None,
        action="check",
        args={"start": 20, "end": 21},
        world_time=1.0,
        phase="reproduction",
    )
    assert next(f for f in truth if f.fact_key == "drought:root_stressed").value is True
    assert all(not f.visible_to for f in truth)


@pytest.mark.parametrize("representation", ["causal", "free_text"])
@pytest.mark.parametrize(
    "mode",
    [
        "none",
        "delay_within_validity",
        "delay_past_validity",
        "delay_past_deadline",
        "drop",
        "duplicate",
        "reorder",
        "mixed",
    ],
)
def test_every_authored_fault_executes_on_real_send_selectors(mode, representation):
    from are.simulation.distributed.models import (
        CausalHandoff,
        DistributedRunnerConfig,
        FreeTextEnvelope,
    )
    from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
    from are.simulation.distributed.transport import InProcessTransport

    process = author_process("farm_wetjune_recheck")
    treatment = next(
        t
        for t in process.fault_treatments
        if t.mode == ("reliable" if mode == "none" else mode)
    )
    phase = next(w for w in process.phase_windows if w.phase == "reproduction")
    start = phase.start_world_time + 7 * 86400  # later than the old absolute delay
    config = DistributedRunnerConfig(
        fault=mode,
        fault_target_ids=treatment.target_ids,
        delay=treatment.delivery_delay_seconds or 0,
        fault_delivery_world_time=treatment.delivery_world_time,
        fault_deadline_world_time=phase.end_world_time,
    )
    transport = InProcessTransport(
        ("field_intelligence", "operations"),
        NativeDistributedSeasonRunner._fault_schedule(config),
    )
    for index in range(3):
        args = dict(
            sender="field_intelligence",
            recipient="operations",
            text="observed evidence",
        )
        message = (
            CausalHandoff(**args)
            if representation == "causal"
            else FreeTextEnvelope(**args)
        )
        transport.send(
            message,
            start + index,
            phase="reproduction",
            evidence_valid_until=start + 86400,
        )
    while transport.next_delivery_time() is not None:
        transport.deliver_next(transport.next_delivery_time())
    audit = transport.fault_manifestation(mode)
    assert audit["manifested"], audit
    assert audit["activation_status"] == "activated"


def test_unavailable_expiry_does_not_manufacture_validity_crossing():
    from are.simulation.distributed.models import FreeTextEnvelope
    from are.simulation.distributed.transport import (
        FaultMode,
        FaultRule,
        FaultSchedule,
        InProcessTransport,
    )

    transport = InProcessTransport(
        ("a", "b"), FaultSchedule(default=FaultRule(FaultMode.DELAY, delay=100))
    )
    transport.send(FreeTextEnvelope(sender="a", recipient="b", text="unknown"), 0)
    transport.deliver_next(100)
    audit = transport.fault_manifestation("delay_past_validity")
    assert not audit["manifested"]
    assert audit["activation_status"] == "unassessable_validity"


def test_pulse_candidate_changes_only_declared_resource_and_soil_parameters():
    from are.simulation.apps.farm_world import FarmWorldApp
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        create_native_scenario,
    )
    from are.simulation.types import Action, OracleEvent

    old = create_native_scenario(
        "farm_disease_drought",
        world_seed=0,
        calibration_candidate=True,
        scenario_revision="drought_rootzone_v2",
    )
    new = create_native_scenario(
        "farm_disease_drought",
        world_seed=0,
        calibration_candidate=True,
        scenario_revision="drought_pulse_v4",
    )
    old_farm, new_farm = (s.get_typed_app(FarmWorldApp) for s in (old, new))
    before, after = (
        old_farm._management_regime.to_dict(),
        new_farm._management_regime.to_dict(),
    )
    assert after.pop("irrigation_quota_mm_total") == 9.375
    assert before.pop("irrigation_quota_mm_total") == 6.0
    assert before == after
    assert (
        old_farm.physics.soil.hydraulic_modifiers
        == new_farm.physics.soil.hydraulic_modifiers
    )
    doses = []
    for event in new.events:
        if isinstance(event, OracleEvent) and "r5" in event.event_id:
            action = getattr(event.make_event(None), "action", None)
            if isinstance(action, Action) and action.function_name == "irrigate":
                doses.append(action.args["hours"])
    assert doses == [5.0]


def test_closed_entry_weather_keeps_later_conditional_spray_obligations():
    from are.simulation.distributed.evaluator_v5 import _event_profile
    from are.simulation.distributed.models import DistributedTrace
    from are.simulation.distributed.petri import unfold_petri_net

    process = author_process("farm_wetjune_recheck")
    context = {"weather:spray_window_open": False}
    occurrence = unfold_petri_net(
        process.occurrence_net,
        world_context=context,
        committed_branches={"disease_window_at_entry": "closed"},
        decision_guards_at_execution=True,
    )
    sprays = {
        t.transition_id
        for t in process.occurrence_net.transitions
        if t.phase == "disease" and t.high_impact
    }
    assert sprays <= set(occurrence.applicable_transition_ids)
    assert sprays == set(occurrence.conditionally_required_transition_ids)
    trace = DistributedTrace(
        run_id="omission", task_id=process.process_id, actors=("operations",), events=()
    )
    profile, _ = _event_profile(
        process,
        set(occurrence.applicable_transition_ids),
        {},
        {},
        set(),
        trace,
        frozenset(occurrence.conditionally_required_transition_ids),
    )
    disease = profile["modules"]["disease"]
    assert disease["required_transition_count"] == len(sprays) > 0
    assert disease["event_fidelity"] == 0.0


def test_batch_actions_retain_predecision_management_region_requirements():
    process = author_process("farm_wetjune_recheck")
    policy = next(p for p in process.information_policies if p.phases == ("disease",))
    scopes = {g.fact_key: g.scope for g in policy.requirements}
    sprays = [
        t
        for t in process.occurrence_net.transitions
        if t.phase == "disease" and t.high_impact
    ]
    assert any(t.scope != (20, 43) for t in sprays)
    for action in sprays:
        assert all(g.scope == scopes[g.fact_key] for g in action.guards)
        acceptance = next(
            a for a in process.acceptance if a.transition_id == action.transition_id
        )
        assert acceptance.scope_iou_threshold == 1.0
