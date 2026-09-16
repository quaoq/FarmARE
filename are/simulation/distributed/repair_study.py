"""Bounded, legal information repairs and locked matched-study selections."""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

from are.simulation.distributed.evaluation_adapters.contracts import (
    DiagnosticWitness,
    RepairCandidate,
    RepairPrimitive,
)
from are.simulation.distributed.models import stable_digest

REPAIR_STUDY_CONDITIONS = (
    "fresh_untreated_continuation",
    "generic_reconsideration",
    "fixed_protocol_repair",
    "independent_checker_repair",
    "dcore_repair",
)


def load_repair_catalogue() -> dict[str, Any]:
    path = (
        Path(__file__).parents[3]
        / "AAMAS/handover_development/repair_catalogue_v2.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "dcore_repair_catalogue_v2":
        raise ValueError("unsupported repair catalogue")
    return payload


def _default_observation_action(fact_key: str | None) -> str | None:
    prefix = (fact_key or "").split(":", 1)[0]
    return {
        "weather": "WeatherApp__get_current_weather",
        "soil": "SensorApp__read_soil_sensors",
        "drought": "SensorApp__read_soil_sensors",
        "disease": "Robot0__inspect_crop_health",
        "crop": "Mavic3M__fly_survey",
        "pest": "Robot0__inspect_pests",
        "inventory": "FarmWorldApp__get_inventory",
        "equipment": "TractorApp__get_status",
    }.get(prefix)


def _scope_arguments(
    action: str | None, scope: tuple[int, int] | str | None
) -> dict[str, int]:
    if action and (
        action.endswith("__fly_survey")
        or action.endswith("__inspect_crop_health")
        or action.endswith("__inspect_pests")
    ) and isinstance(scope, tuple):
        return {"start_ridge": scope[0], "end_ridge": scope[1]}
    return {}


def _primitive_options(
    witness: DiagnosticWitness,
    *,
    observer_by_fact: dict[str, str] | None = None,
    source_actor_by_version: dict[str, str] | None = None,
    native_action_by_fact: dict[str, str] | None = None,
) -> tuple[RepairPrimitive, ...]:
    observer_by_fact = observer_by_fact or {}
    source_actor_by_version = source_actor_by_version or {}
    native_action_by_fact = native_action_by_fact or {}
    observer = observer_by_fact.get(witness.fact_key or "", witness.actor_id)
    source_version = witness.source_version_id or next(
        iter(witness.fact_version_ids), None
    )
    source_actor = source_actor_by_version.get(source_version or "", observer)
    native_action = native_action_by_fact.get(
        witness.fact_key or "", _default_observation_action(witness.fact_key)
    )
    common = {
        "fact_key": witness.fact_key,
        "scope": witness.target_scope,
    }
    if witness.mechanism == "missing_observation":
        acquire = RepairPrimitive(
            primitive="acquire_observation",
            actor_id=observer,
            native_action=native_action,
            native_arguments=_scope_arguments(native_action, witness.target_scope),
            **common,
        )
        if observer == witness.actor_id:
            return (acquire,)
        return (
            acquire,
            RepairPrimitive(
                primitive="route_evidence",
                actor_id=observer,
                recipient_actor_id=witness.actor_id,
                **common,
            ),
        )
    if witness.mechanism == "failed_delivery":
        return (
            RepairPrimitive(
                primitive="redeliver_evidence",
                actor_id=source_actor,
                recipient_actor_id=witness.actor_id,
                fact_version_id=source_version,
                **common,
            ),
        )
    if witness.mechanism == "expired_evidence":
        refresh = RepairPrimitive(
                primitive="refresh_observation",
                actor_id=observer,
                native_action=native_action,
                native_arguments=_scope_arguments(native_action, witness.target_scope),
                **common,
            )
        if observer == witness.actor_id:
            return (refresh,)
        return (
            refresh,
            RepairPrimitive(
                primitive="route_evidence",
                actor_id=observer,
                recipient_actor_id=witness.actor_id,
                **common,
            ),
        )
    if witness.mechanism == "incorrect_scope":
        return (
            RepairPrimitive(
                primitive="acquire_observation",
                actor_id=observer,
                native_action=native_action,
                native_arguments=_scope_arguments(native_action, witness.target_scope),
                **common,
            ),
            RepairPrimitive(
                primitive="route_evidence",
                actor_id=observer,
                recipient_actor_id=witness.actor_id,
                **common,
            ),
        )
    if witness.mechanism == "context_omission":
        return (
            RepairPrimitive(
                primitive="restore_context",
                actor_id=witness.actor_id,
                fact_version_id=source_version,
                **common,
            ),
        )
    if witness.mechanism in {
        "failure_to_use_available_evidence",
        "native_execution_failure",
        "transition_order_failure",
    }:
        return (
            RepairPrimitive(
                primitive="request_reconsideration",
                actor_id=witness.actor_id,
                **common,
            ),
        )
    return ()


def enumerate_repairs(
    witness: DiagnosticWitness,
    *,
    native_cost_by_primitive: dict[str, float] | None = None,
    duration_by_primitive: dict[str, float | None] | None = None,
    observer_by_fact: dict[str, str] | None = None,
    source_actor_by_version: dict[str, str] | None = None,
    native_action_by_fact: dict[str, str] | None = None,
    response_lead_time_seconds: float | None = 0.0,
) -> tuple[RepairCandidate, ...]:
    """Enumerate minimal one/two-step repairs without outcome information."""

    if witness.determination != "supported":
        return ()
    native_cost_by_primitive = native_cost_by_primitive or {}
    duration_by_primitive = duration_by_primitive or {}
    options = _primitive_options(
        witness,
        observer_by_fact=observer_by_fact,
        source_actor_by_version=source_actor_by_version,
        native_action_by_fact=native_action_by_fact,
    )
    route_required = len(options) == 2 and options[1].primitive == "route_evidence"
    sequences: list[tuple[RepairPrimitive, ...]] = (
        [tuple(options)] if route_required else [(item,) for item in options]
    )
    # Only observe/refresh followed by routing is a meaningful two-step repair.
    for left, right in combinations(options, 2):
        if left.primitive in {"acquire_observation", "refresh_observation"} and (
            right.primitive == "route_evidence"
        ):
            if (left, right) not in sequences:
                sequences.append((left, right))
    rows = []
    seen_support: set[tuple[str, ...]] = set()
    for sequence in sequences:
        key = tuple(item.primitive for item in sequence)
        if key in seen_support:
            continue
        seen_support.add(key)
        duration_values = [duration_by_primitive.get(item.primitive) for item in sequence]
        total_duration = (
            sum(float(value) for value in duration_values if value is not None)
            if all(value is not None for value in duration_values)
            else None
        )
        slack = (
            witness.deadline
            - witness.decision_time
            - total_duration
            - float(response_lead_time_seconds or 0.0)
            if witness.deadline is not None
            and witness.decision_time is not None
            and total_duration is not None
            and response_lead_time_seconds is not None
            else None
        )
        unresolved_reasons = []
        exact_version = witness.source_version_id or next(
            iter(witness.fact_version_ids), None
        )
        if witness.mechanism == "failed_delivery" and (
            not exact_version or exact_version not in source_actor_by_version
        ):
            unresolved_reasons.append("source_holder_unknown")
        if witness.mechanism == "context_omission" and (
            not exact_version
            or source_actor_by_version.get(exact_version) != witness.actor_id
        ):
            unresolved_reasons.append("recipient_does_not_hold_exact_version")
        for primitive in sequence:
            if primitive.primitive in {"acquire_observation", "refresh_observation"}:
                if primitive.native_action is None:
                    unresolved_reasons.append("native_observation_tool_unknown")
            if primitive.primitive in {
                "redeliver_evidence",
                "restore_context",
            } and not primitive.fact_version_id:
                unresolved_reasons.append("exact_evidence_version_unknown")
        if any(value is None for value in duration_values):
            unresolved_reasons.append("native_duration_unknown")
        cost_values = [
            native_cost_by_primitive.get(item.primitive) for item in sequence
        ]
        if any(value is None for value in cost_values):
            unresolved_reasons.append("native_cost_unknown")
        if response_lead_time_seconds is None:
            unresolved_reasons.append("response_lead_time_unknown")
        feasibility = (
            "infeasible"
            if slack is not None and slack < 0
            else "feasible"
            if slack is not None and not unresolved_reasons
            else "unresolved"
        )
        cost = (
            sum(float(value) for value in cost_values if value is not None)
            if all(value is not None for value in cost_values)
            else None
        )
        sequence = tuple(
            primitive.model_copy(
                update={
                    "estimated_duration_seconds": duration_by_primitive.get(
                        primitive.primitive
                    ),
                    "estimated_native_cost": native_cost_by_primitive.get(
                        primitive.primitive
                    ),
                }
            )
            for primitive in sequence
        )
        candidate_id = stable_digest(
            [witness.witness_id, [item.model_dump(mode="json") for item in sequence]]
        )[:24]
        rows.append(
            RepairCandidate(
                candidate_id=candidate_id,
                witness_id=witness.witness_id,
                primitives=sequence,
                required_evidence_ids=witness.fact_version_ids,
                total_native_cost=cost,
                timing_slack_seconds=slack,
                response_lead_time_seconds=response_lead_time_seconds,
                feasibility=feasibility,
                rejection_reasons=(
                    ("negative_timing_slack",)
                    if feasibility == "infeasible"
                    else tuple(sorted(set(unresolved_reasons)))
                ),
                feasibility_evidence={
                    "decision_time": witness.decision_time,
                    "deadline": witness.deadline,
                    "primitive_durations_seconds": duration_values,
                    "response_lead_time_seconds": response_lead_time_seconds,
                    "cost_unit": "native_operation_equivalent",
                },
                priority_key=(
                    witness.decision_time or float("inf"),
                    len(sequence),
                    cost if cost is not None else float("inf"),
                    candidate_id,
                ),
            )
        )
    return tuple(sorted(rows, key=lambda row: row.priority_key))


def select_repair(
    candidates: Iterable[RepairCandidate], *, strategy: str = "frozen_priority"
) -> RepairCandidate | None:
    eligible = [item for item in candidates if item.feasibility != "infeasible"]
    if not eligible:
        return None
    if strategy == "frozen_priority":
        return min(eligible, key=lambda item: item.priority_key)
    if strategy == "cost_only":
        return min(eligible, key=lambda item: (item.priority_key[2], item.candidate_id))
    if strategy == "unrestricted":
        return min(eligible, key=lambda item: item.candidate_id)
    raise ValueError(f"unknown repair selection strategy {strategy!r}")


def resolve_repair_context(
    run_dir: str | Path,
    witness: DiagnosticWitness,
) -> dict[str, Any]:
    """Resolve owners, native observation tools and timing from saved run data."""

    from are.simulation.distributed.models import DistributedRunnerConfig
    from are.simulation.distributed.teams import load_team_spec
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        create_native_scenario,
    )

    root = Path(run_dir)
    trace_path = next(root.glob("trace.dcore_trace*.json"), None)
    if trace_path is None:
        raise ValueError("run directory lacks a D-CORE trace")
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    raw_configuration = dict(trace.get("configuration") or {})
    raw_configuration.pop("app_random_seeds", None)
    config = DistributedRunnerConfig.model_validate(raw_configuration)
    scenario = create_native_scenario(
        config.scenario_id,
        world_seed=config.world_seed,
        scenario_revision=config.scenario_revision,
        calibration_candidate=config.calibration_candidate,
    )
    team = load_team_spec(config, scenario.get_tools())
    owners = {
        action: actor.actor_id
        for actor in team.actors
        for action in actor.permitted_actions
    }
    fact_key = witness.fact_key or ""
    native_action = _default_observation_action(fact_key)
    if native_action not in owners:
        native_action = None
    source_actor_by_version: dict[str, str] = {}
    for fact in trace.get("fact_versions", ()):
        version_id = str(fact.get("version_id", ""))
        visible = tuple(str(item) for item in fact.get("visible_to", ()))
        holders = sorted(actor for actor in visible if actor != witness.actor_id)
        if (
            version_id
            and witness.mechanism == "context_omission"
            and witness.actor_id in visible
        ):
            source_actor_by_version[version_id] = witness.actor_id
        elif version_id and holders:
            source_actor_by_version[version_id] = holders[0]
        elif version_id and witness.actor_id in visible:
            source_actor_by_version[version_id] = witness.actor_id
    route_delay = max(0.0, float(raw_configuration.get("delay", 0.0)))
    catalogue = load_repair_catalogue()
    cost_by_primitive = {
        str(key): float(value) for key, value in catalogue["costs"].items()
    }
    context = {
        "observer_by_fact": ({fact_key: owners[native_action]} if native_action else {}),
        "source_actor_by_version": source_actor_by_version,
        "native_action_by_fact": ({fact_key: native_action} if native_action else {}),
        # Costs are expressed in native-operation equivalents. Communication,
        # prompt restoration and reconsideration do not invoke a farm tool.
        "native_cost_by_primitive": cost_by_primitive,
        "duration_by_primitive": {
            "acquire_observation": 0.001,
            "refresh_observation": 0.001,
            "route_evidence": route_delay,
            "redeliver_evidence": route_delay,
            "restore_context": 0.0,
            "request_reconsideration": 0.0,
        },
        "response_lead_time_seconds": 0.0,
        "team_id": trace.get("team_id"),
        "resolution_digest": stable_digest(
            {
                "owners": owners,
                "native_action": native_action,
                "route_delay": route_delay,
                "team_id": trace.get("team_id"),
                "repair_catalogue_digest": stable_digest(catalogue),
            }
        ),
    }
    return context


def _resolved_candidates(
    witness: DiagnosticWitness,
    run_dir: str | Path,
) -> tuple[RepairCandidate, ...]:
    context = resolve_repair_context(run_dir, witness)
    return enumerate_repairs(
        witness,
        native_cost_by_primitive=context["native_cost_by_primitive"],
        duration_by_primitive=context["duration_by_primitive"],
        observer_by_fact=context["observer_by_fact"],
        source_actor_by_version=context["source_actor_by_version"],
        native_action_by_fact=context["native_action_by_fact"],
        response_lead_time_seconds=context["response_lead_time_seconds"],
    )


def _condition_candidate(
    condition: str,
    *,
    packet: Any,
    run_dir: str | Path,
) -> tuple[RepairCandidate | None, dict[str, Any]]:
    from are.simulation.distributed.evaluation_adapters import run_adapters

    if condition == "fresh_untreated_continuation":
        return None, {"selection": "no_intervention"}
    if condition == "generic_reconsideration":
        result = run_adapters(packet, ["generic_reconsideration"])[0]
        return (
            result.repairs[0] if result.status == "ok" and result.repairs else None,
            {"method_result": result.model_dump(mode="json")},
        )
    method = (
        "full_information_checker"
        if condition in {"fixed_protocol_repair", "independent_checker_repair"}
        else "dcore"
    )
    result = run_adapters(packet, [method])[0]
    if result.status != "ok" or not result.witnesses:
        return None, {"method_result": result.model_dump(mode="json")}
    witnesses = sorted(
        result.witnesses,
        key=lambda item: (
            item.decision_time if item.decision_time is not None else float("inf"),
            item.prerequisite_id,
            item.witness_id,
        ),
    )
    witness = witnesses[0]
    candidates = list(_resolved_candidates(witness, run_dir))
    if condition == "fixed_protocol_repair":
        # This frozen baseline attempts one direct condition-action primitive.
        # It cannot compose an observation with a routed handoff.
        candidates = [item for item in candidates if len(item.primitives) == 1]
    selected = select_repair(candidates)
    return selected, {
        "method_result": result.model_dump(mode="json"),
        "resolved_candidates": [item.model_dump(mode="json") for item in candidates],
    }


def run_repair_study_manifest(
    manifest_path: str | Path,
    *,
    execute_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Plan or execute every matched arm in a locked v2 repair study."""

    from are.simulation.distributed.evaluation_adapters import ContinuationManifest
    from are.simulation.distributed.evaluation_adapters.contracts import (
        build_diagnostic_packet,
    )
    from are.simulation.distributed.prefix_replay import execute_repaired_continuation

    manifest_file = Path(manifest_path).resolve()
    source = json.loads(manifest_file.read_text(encoding="utf-8"))
    if source.get("schema_version") != "dcore_repair_study_manifest_v2":
        raise ValueError("repair study requires dcore_repair_study_manifest_v2")
    if tuple(source.get("conditions", ())) != REPAIR_STUDY_CONDITIONS:
        raise ValueError("repair study conditions differ from the frozen five-arm design")
    repetitions = int(source.get("suffix_repetitions", 0))
    if repetitions < 1:
        raise ValueError("suffix_repetitions must be positive")
    assignments = []
    output_root = Path(execute_output_dir).resolve() if execute_output_dir else None
    for checkpoint_row in source.get("checkpoints", ()):
        label = dict(checkpoint_row.get("independent_label") or {})
        if label.get("classification") not in {"repairable_information_failure", "valid_decision"}:
            raise ValueError("every checkpoint requires a frozen independent label")
        if not label.get("frozen_before_dcore"):
            raise ValueError("checkpoint label was not frozen before D-CORE prediction")
        checkpoint = ContinuationManifest.model_validate(
            checkpoint_row["continuation_manifest"]
        )
        if checkpoint.schema_version != "continuation_manifest_v2":
            raise ValueError("new repair studies require continuation_manifest_v2")
        run_dir = (manifest_file.parent / checkpoint_row["source_run_dir"]).resolve()
        packet = build_diagnostic_packet(
            run_dir,
            prefix_decision_id=checkpoint.checkpoint_decision_id,
            include_outcome=False,
        )
        for condition in REPAIR_STUDY_CONDITIONS:
            candidate, selection_evidence = _condition_candidate(
                condition, packet=packet, run_dir=run_dir
            )
            intervention_status = (
                "selected"
                if candidate is not None and candidate.feasibility == "feasible"
                else "infeasible"
                if candidate is not None
                else "abstained"
                if condition != "fresh_untreated_continuation"
                else "untreated"
            )
            for repetition in range(repetitions):
                assignment_key = {
                    "checkpoint_id": checkpoint_row["checkpoint_id"],
                    "condition": condition,
                    "repetition": repetition,
                }
                row: dict[str, Any] = {
                    "schema_version": "repair_study_assignment_v2",
                    **assignment_key,
                    "assignment_id": stable_digest(assignment_key)[:24],
                    "scenario_id": packet.scenario_id,
                    "world_seed": checkpoint.world_seed,
                    "run_id": packet.run_id,
                    "decision_id": checkpoint.checkpoint_decision_id,
                    "packet_digest": packet.packet_digest,
                    "intervention_status": intervention_status,
                    "repair_candidate": (
                        candidate.model_dump(mode="json") if candidate else None
                    ),
                    "selection_evidence": selection_evidence,
                }
                if output_root is not None:
                    destination = output_root / row["assignment_id"]
                    applied_candidate = (
                        candidate if intervention_status == "selected" else None
                    )
                    row["execution"] = execute_repaired_continuation(
                        run_dir,
                        destination,
                        checkpoint=checkpoint,
                        repair=applied_candidate,
                        execution_overrides=checkpoint_row.get(
                            "offline_execution_overrides", {}
                        ),
                    )
                assignments.append(row)
    expected = len(source.get("checkpoints", ())) * len(REPAIR_STUDY_CONDITIONS) * repetitions
    if len(assignments) != expected:
        raise RuntimeError("repair study assignment count is incomplete")
    result = {
        "schema_version": "repair_study_execution_v2",
        "manifest_digest": stable_digest(source),
        "assigned": expected,
        "executed": sum("execution" in item for item in assignments),
        "conditions": REPAIR_STUDY_CONDITIONS,
        "assignments": assignments,
    }
    if output_root is not None:
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "repair_study_results_v2.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return result


def select_repair_checkpoints(
    results: Path, plan_path: Path
) -> dict[str, object]:
    """Freeze scenario-balanced replayable checkpoints from completed rows."""

    from are.simulation.distributed.journal import (
        load_journal,
        uncertain_native_writes,
    )
    from are.simulation.distributed.prefix_replay import build_checkpoint_manifest

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") == "dcore_miniature_checkpoint_labels_v2":
        if plan.get("dcore_outputs_opened_before_freeze") is not False:
            raise ValueError("checkpoint labels were not frozen blind to D-CORE")
        labels = list(plan.get("labels", ()))
        if len(labels) != 6:
            raise ValueError("miniature study requires exactly six labelled checkpoints")
        rows = [
            json.loads(line)
            for line in results.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        by_assignment = {
            (str(row.get("scenario")), int(row.get("world_seed", -1))): row
            for row in rows
            if row.get("artifact_dir")
        }
        checkpoints = []
        counts: dict[tuple[str, str], int] = {}
        for label in labels:
            scenario = str(label.get("scenario_id"))
            world_seed = int(label.get("world_seed", -1))
            classification = str(label.get("classification"))
            if classification not in {
                "repairable_information_failure",
                "valid_decision",
            } or label.get("frozen_before_dcore") is not True:
                raise ValueError("miniature checkpoint has an invalid independent label")
            counts[(scenario, classification)] = (
                counts.get((scenario, classification), 0) + 1
            )
            row = by_assignment.get((scenario, world_seed), {})
            root_value = label.get("source_run_dir") or row.get("artifact_dir")
            if not root_value:
                raise ValueError("label cannot be matched to a source run")
            root = Path(str(root_value))
            trace_path = next(root.glob("trace.dcore_trace*.json"), None)
            journal_path = root / "progress.dcore.jsonl"
            if trace_path is None or not journal_path.is_file():
                raise ValueError("labelled checkpoint lacks trace or durable journal")
            journal = load_journal(journal_path)
            if uncertain_native_writes(journal):
                raise ValueError("labelled checkpoint has an uncertain native write")
            from are.simulation.distributed.journal import uncertain_provider_requests

            if uncertain_provider_requests(journal):
                raise ValueError("labelled checkpoint has an uncertain provider request")
            decision_id = str(label.get("decision_id"))
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            if not any(
                item.get("decision_id") == decision_id
                for item in trace.get("decisions", ())
            ):
                raise ValueError("independent label names an absent decision")
            proposal_index = next(
                (
                    index
                    for index, record in enumerate(journal)
                    if record.get("kind") == "parsed_proposal"
                    and record.get("payload", {}).get("intent_id") == decision_id
                ),
                None,
            )
            if proposal_index is None:
                raise ValueError("checkpoint decision lacks a parsed-proposal boundary")
            prefix = journal[:proposal_index]
            request_kinds = {
                item.get("kind") for item in prefix
            }
            request_kind = (
                "provider_request_intent"
                if "provider_request_intent" in request_kinds
                else "model_request"
            )
            requests_used = sum(
                item.get("kind") == request_kind for item in prefix
            )
            tokens_used = sum(
                int(item.get("payload", {}).get("prompt_tokens") or 0)
                + int(item.get("payload", {}).get("completion_tokens") or 0)
                for item in prefix
                if item.get("kind") == "provider_response_receipt"
            )
            configuration = trace.get("configuration", {})
            remaining_calls = int(
                configuration.get("team_call_budget")
                or configuration.get("max_model_calls", 0)
            ) - requests_used
            token_budget = configuration.get("team_token_budget")
            remaining_tokens = (
                int(token_budget) - tokens_used if token_budget is not None else None
            )
            if remaining_calls < 1 or (
                remaining_tokens is not None and remaining_tokens < 1
            ):
                raise ValueError("checkpoint has no remaining suffix budget")
            continuation = build_checkpoint_manifest(
                root,
                decision_id,
                remaining_call_budget=remaining_calls,
                remaining_token_budget=remaining_tokens,
            )
            checkpoints.append(
                {
                    "checkpoint_id": str(label.get("checkpoint_id")),
                    "scenario_id": scenario,
                    "world_seed": world_seed,
                    "source_run_dir": str(root),
                    "independent_label": label,
                    "continuation_manifest": continuation.model_dump(mode="json"),
                }
            )
        expected_scenarios = {
            "farm_wetjune_recheck",
            "farm_disease_drought",
            "farm_three_cultivar",
        }
        expected_counts = {
            (scenario, classification): 1
            for scenario in expected_scenarios
            for classification in (
                "repairable_information_failure",
                "valid_decision",
            )
        }
        if counts != expected_counts:
            raise ValueError("labels do not contain one failure and one valid decision per scenario")
        return {
            "schema_version": "dcore_repair_study_manifest_v2",
            "selection_locked": True,
            "label_bundle_digest": stable_digest(plan),
            "conditions": REPAIR_STUDY_CONDITIONS,
            "suffix_repetitions": 3,
            "expected_assignments": 90,
            "checkpoints": checkpoints,
        }
    quotas = {str(key): int(value) for key, value in plan["scenario_quotas"].items()}
    rows = [
        json.loads(line)
        for line in results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidates: dict[str, list[dict[str, object]]] = {key: [] for key in quotas}
    exclusions: list[dict[str, str]] = []
    for row in rows:
        scenario = str(row.get("scenario"))
        if scenario not in quotas or not row.get("artifact_dir"):
            continue
        root = Path(str(row["artifact_dir"]))
        trace_path = next(root.glob("trace.dcore_trace*.json"), None)
        metrics_path = next(root.glob("metrics.dcore_eval*.json"), None)
        journal_path = root / "progress.dcore.jsonl"
        if not trace_path or not metrics_path or not journal_path.is_file():
            exclusions.append({"artifact_dir": str(root), "reason": "missing_evidence"})
            continue
        journal = load_journal(journal_path)
        if uncertain_native_writes(journal):
            exclusions.append(
                {"artifact_dir": str(root), "reason": "uncertain_native_write"}
            )
            continue
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        events = {item["event_id"]: item for item in trace.get("events", ())}
        decisions = {
            item["decision_id"]: item for item in trace.get("decisions", ())
        }
        for witness in metrics.get("provenance_failure_localization", ()):
            target = events.get(witness.get("target_event_id"), {})
            decision_id = target.get("decision_context_id")
            decision = decisions.get(decision_id)
            if not decision_id or decision is None:
                continue
            logical_time = float(decision["logical_time"])
            prefix = [
                item
                for item in journal
                if float(item.get("payload", {}).get("logical_time", -1))
                < logical_time
            ]
            requests_used = sum(item.get("kind") == "model_request" for item in prefix)
            tokens_used = sum(
                int(item.get("payload", {}).get("metadata", {}).get("total_tokens") or 0)
                for item in prefix
                if item.get("kind") == "model_response"
            )
            configuration = trace.get("configuration", {})
            remaining_calls = int(
                configuration.get("team_call_budget")
                or configuration.get("max_model_calls", 0)
            ) - requests_used
            token_budget = configuration.get("team_token_budget")
            remaining_tokens = (
                int(token_budget) - tokens_used if token_budget is not None else None
            )
            if remaining_calls < 1 or (
                remaining_tokens is not None and remaining_tokens < 1
            ):
                continue
            manifest = build_checkpoint_manifest(
                root,
                str(decision_id),
                remaining_call_budget=remaining_calls,
                remaining_token_budget=remaining_tokens,
            )
            key = stable_digest(
                [
                    int(plan["selection_seed"]),
                    trace["run_id"],
                    decision_id,
                    witness.get("witness_id"),
                ]
            )
            candidates[scenario].append(
                {
                    "selection_key": key,
                    "scenario": scenario,
                    "world_seed": configuration.get("world_seed"),
                    "run_dir": str(root),
                    "decision_id": decision_id,
                    "witness": witness,
                    "continuation_manifest": manifest.model_dump(mode="json"),
                }
            )
    selected = []
    shortfalls = {}
    for scenario, quota in quotas.items():
        unique: dict[tuple[str, str], dict[str, object]] = {}
        for item in sorted(candidates[scenario], key=lambda value: value["selection_key"]):
            unique.setdefault((str(item["run_dir"]), str(item["decision_id"])), item)
        chosen = list(unique.values())[:quota]
        selected.extend(chosen)
        shortfalls[scenario] = quota - len(chosen)
    return {
        "schema_version": "dcore_repair_checkpoint_selection_v1",
        "plan_digest": stable_digest(plan),
        "selection_seed": plan["selection_seed"],
        "selected_count": len(selected),
        "shortfalls": shortfalls,
        "selection_complete": all(value == 0 for value in shortfalls.values()),
        "selected": selected,
        "exclusions": exclusions,
    }


__all__ = [
    "enumerate_repairs",
    "load_repair_catalogue",
    "resolve_repair_context",
    "run_repair_study_manifest",
    "REPAIR_STUDY_CONDITIONS",
    "select_repair",
    "select_repair_checkpoints",
]
