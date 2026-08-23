"""Frozen twenty-case scientific validation suite for D-CORE v5."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from are.simulation.distributed.evaluator_v5 import evaluate_farm_dcore_v5
from are.simulation.distributed.farm_mutants import build_farm_mutant_suite
from are.simulation.distributed.farm_mutants_v5 import build_farm_mutant_suite_v5
from are.simulation.distributed.models import DistributedTrace, EventKind
from are.simulation.distributed.mutants import _replace_events
from are.simulation.distributed.petri import unfold_petri_net, validate_petri_net
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5
from are.simulation.distributed.teams import (
    FOUR_AGENT_TEAM_ID,
    THREE_AGENT_TEAM_ID,
    build_builtin_team,
    built_in_role_refinement,
    refine_petri_for_team,
)
from are.simulation.scenarios.scenario_dcore.farm_catalog import create_native_scenario

Relation = Literal["same", "decrease", "increase", "not_increase", "reported"]


@dataclass(frozen=True)
class ControlledFixtureV5:
    fixture_id: str
    trace: DistributedTrace
    expected_ef: Relation
    expected_cc: Relation
    expected_igd: Relation
    expected_localization: str | None = None
    property_kind: str = "trace_mutation"


def _duplicate_delivery(trace: DistributedTrace) -> DistributedTrace:
    events = list(trace.events)
    receive = next(item for item in events if item.kind == EventKind.MESSAGE_RECEIVE)
    events.append(
        receive.model_copy(
            update={
                "event_id": f"{receive.event_id}:controlled_duplicate",
                "status": "duplicate",
                "payload": {**receive.payload, "duplicate": True},
                "logical_time": max(item.logical_time for item in events) + 0.1,
            }
        )
    )
    return _replace_events(trace, events)


def _expired_delivery(
    original: DistributedTrace, transit_gap: DistributedTrace
) -> DistributedTrace:
    """Deliver the planted missing receive only after the action horizon closes."""

    transit_messages = {
        item.message_id
        for item in transit_gap.events
        if item.kind == EventKind.MESSAGE_SEND and item.message_id
    }
    missing = next(
        item
        for item in original.events
        if item.kind == EventKind.MESSAGE_RECEIVE
        and item.message_id in transit_messages
        and not any(
            candidate.kind == EventKind.MESSAGE_RECEIVE
            and candidate.message_id == item.message_id
            for candidate in transit_gap.events
        )
    )
    end = max(item.logical_time for item in transit_gap.events) + 1.0
    delayed = missing.model_copy(
        update={
            "event_id": f"{missing.event_id}:expired_delivery",
            "logical_time": end,
            "world_time": max(item.world_time for item in transit_gap.events) + 1.0,
            "payload": {**missing.payload, "controlled_expired_delivery": True},
        }
    )
    return _replace_events(transit_gap, [*transit_gap.events, delayed])


def build_controlled_suite_v5(
    process: FarmProcessSpecV5, trace: DistributedTrace
) -> tuple[ControlledFixtureV5, ...]:
    legacy = {
        item.name: item
        for item in build_farm_mutant_suite(
            trace, process.occurrence_net, unfold_petri_net(process.occurrence_net)
        )
    }
    provenance = {
        item.name: item for item in build_farm_mutant_suite_v5(process, trace)
    }

    def old(name: str) -> DistributedTrace:
        if name not in legacy:
            raise ValueError(f"controlled fixture source {name!r} is unavailable")
        return legacy[name].trace

    def exact(name: str) -> DistributedTrace:
        if name not in provenance:
            raise ValueError(f"provenance fixture source {name!r} is unavailable")
        return provenance[name].trace

    fixtures = (
        ControlledFixtureV5("perfect_replay", trace, "same", "same", "same"),
        ControlledFixtureV5("concurrent_permutation", old("concurrent_permutation"), "same", "same", "same"),
        # The executable role-refinement tests establish the path refinement;
        # this row binds that metamorphic property to the exported suite.
        ControlledFixtureV5("communication_hop_refinement", trace, "same", "same", "same", property_kind="role_refinement_metamorphic"),
        ControlledFixtureV5("duplicate_idempotency", _duplicate_delivery(trace), "same", "same", "same"),
        ControlledFixtureV5("benign_read", old("harmless_redundant_read"), "same", "same", "same"),
        ControlledFixtureV5("correct_recovery", trace, "same", "same", "same", property_kind="policy_recovery_reference"),
        ControlledFixtureV5("within_agent_reorder", old("within_agent_reorder"), "same", "decrease", "same", "composition_error"),
        ControlledFixtureV5("missing_prerequisite", old("missing_required_action"), "decrease", "not_increase", "same"),
        ControlledFixtureV5("observation_gap", exact("observation_gap"), "decrease", "decrease", "same", "observation_gap"),
        ControlledFixtureV5("handoff_omission", exact("handoff_omission"), "decrease", "decrease", "same", "handoff_omission"),
        ControlledFixtureV5("dropped_delivery", exact("transit_gap"), "decrease", "decrease", "same", "transit_gap"),
        ControlledFixtureV5("expired_delivery", _expired_delivery(trace, exact("transit_gap")), "same", "decrease", "same", "transit_gap"),
        ControlledFixtureV5("stale_inversion", exact("stale_information"), "same", "decrease", "same", "stale_information"),
        ControlledFixtureV5("unsupported_claim", exact("unsupported_claim"), "same", "decrease", "same", "unsupported_claim"),
        ControlledFixtureV5("uptake_error", exact("uptake_error"), "same", "decrease", "same", "uptake_error"),
        ControlledFixtureV5("wrong_ridge", old("wrong_ridge_scope"), "decrease", "not_increase", "same"),
        ControlledFixtureV5("wrong_amount", old("wrong_treatment_amount"), "decrease", "not_increase", "increase"),
        ControlledFixtureV5("missing_beneficial_action", old("missing_required_action"), "decrease", "not_increase", "same"),
        ControlledFixtureV5(
            "harmful_write",
            old("harmful_extra_write"),
            "decrease" if process.annotation_status == "frozen" else "not_increase",
            "not_increase",
            "same",
            property_kind=(
                "trace_mutation"
                if process.annotation_status == "frozen"
                else "engineering_requires_frozen_negative_obligation"
            ),
        ),
        ControlledFixtureV5("unnecessary_abstention", old("safe_unnecessary_abstention"), "decrease", "not_increase", "same"),
    )
    if len(fixtures) != 20:
        raise AssertionError("the frozen controlled suite must contain exactly 20 cases")
    return fixtures


def _relation(relation: Relation, observed: float | None, baseline: float | None) -> bool:
    if relation == "reported":
        return observed is not None
    if observed is None or baseline is None:
        return False
    tolerance = 1e-9
    if relation == "same":
        return abs(observed - baseline) <= tolerance
    if relation == "decrease":
        return observed < baseline - tolerance
    if relation == "increase":
        return observed > baseline + tolerance
    return observed <= baseline + tolerance


def _positive_property_check(
    process: FarmProcessSpecV5,
    fixture: ControlledFixtureV5,
    metrics: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Check positive controls with evidence beyond score equality."""

    if fixture.fixture_id == "perfect_replay":
        evidence = {
            "event_fidelity": metrics["event_fidelity"],
            "causal_conformance": metrics["causal_conformance"],
        }
        return all(value == 1.0 for value in evidence.values()), evidence
    if fixture.fixture_id == "correct_recovery":
        recovery = metrics["recovery"]
        evidence = {
            "opportunity_count": recovery["opportunity_count"],
            "recovered_count": recovery["recovered_count"],
            "evaluation_source": recovery["evaluation_source"],
        }
        return (
            recovery["opportunity_count"] > 0
            and recovery["recovered_count"] == recovery["opportunity_count"]
            and recovery["evaluation_source"]
            == "independent_policy_decision_sequence"
        ), evidence
    if fixture.fixture_id == "communication_hop_refinement":
        base_budgets = {
            item.module_id: item.weight_budget
            for item in process.occurrence_net.modules
        }
        scenario = create_native_scenario(process.scenario_id, world_seed=0)
        evidence: dict[str, Any] = {}
        passed = True
        for team_id in (THREE_AGENT_TEAM_ID, FOUR_AGENT_TEAM_ID):
            team = build_builtin_team(team_id, scenario.get_tools())
            refined = refine_petri_for_team(
                process.occurrence_net,
                team,
                built_in_role_refinement(team, process.scenario_id),
            )
            refined_budgets = {
                item.module_id: item.weight_budget for item in refined.modules
            }
            terminal_reachable = validate_petri_net(refined)["terminal_reachable"]
            evidence[team_id] = {
                "module_budgets_preserved": refined_budgets == base_budgets,
                "terminal_reachable": terminal_reachable,
            }
            passed = passed and refined_budgets == base_budgets and terminal_reachable
        return passed, evidence
    return True, {}


def evaluate_controlled_suite_v5(
    process: FarmProcessSpecV5, fixtures: tuple[ControlledFixtureV5, ...]
) -> dict[str, Any]:
    baseline = evaluate_farm_dcore_v5(process, fixtures[0].trace)
    baseline_values = {
        "ef": baseline["event_fidelity"],
        "cc": baseline["causal_conformance"],
        "igd": baseline["information_global_discordance"]["information_global_discordance"],
    }
    rows = []
    for fixture in fixtures:
        metrics = evaluate_farm_dcore_v5(process, fixture.trace)
        values = {
            "ef": metrics["event_fidelity"],
            "cc": metrics["causal_conformance"],
            "igd": metrics["information_global_discordance"]["information_global_discordance"],
        }
        predicted = None
        if fixture.expected_localization:
            predicted = next(
                (
                    item["primary"]
                    for item in metrics["provenance_failure_localization"]
                    if item["primary"] == fixture.expected_localization
                ),
                None,
            )
            if predicted is None:
                predicted = next(
                    (item["primary"] for item in metrics["decision_failure_localization"]),
                    None,
                )
        effects_pass = all(
            _relation(relation, values[key], baseline_values[key])
            for key, relation in (
                ("ef", fixture.expected_ef),
                ("cc", fixture.expected_cc),
                ("igd", fixture.expected_igd),
            )
        )
        localization_pass = (
            fixture.expected_localization is None
            or predicted == fixture.expected_localization
        )
        property_pass, property_evidence = _positive_property_check(
            process, fixture, metrics
        )
        rows.append(
            {
                "fixture_id": fixture.fixture_id,
                "property_kind": fixture.property_kind,
                "expected": {"ef": fixture.expected_ef, "cc": fixture.expected_cc, "igd": fixture.expected_igd},
                "observed": values,
                "expected_localization": fixture.expected_localization,
                "predicted_localization": predicted,
                "effects_pass": effects_pass,
                "localization_pass": localization_pass,
                "property_pass": property_pass,
                "property_evidence": property_evidence,
                "passed": effects_pass and localization_pass and property_pass,
            }
        )
    pending_frozen_properties = [
        item["fixture_id"]
        for item in rows
        if str(item["property_kind"]).startswith("engineering_requires_frozen_")
    ]
    return {
        "schema_version": "farm_dcore_controlled_suite_v5",
        "fixture_count": len(rows),
        "passed": all(item["passed"] for item in rows),
        "paper_validation_passed": all(item["passed"] for item in rows)
        and not pending_frozen_properties,
        "pending_frozen_properties": pending_frozen_properties,
        "runtime_conclusions_used": False,
        "rows": rows,
    }


__all__ = ["ControlledFixtureV5", "build_controlled_suite_v5", "evaluate_controlled_suite_v5"]
