"""Offline evidence-matched checks and bounded evaluator ablations.

Nothing here changes a controller, a native season, or the primary evaluator.
Reduced specifications are analysis copies and cannot authorize paper runs.
"""

from __future__ import annotations

import fnmatch
import operator
from copy import deepcopy
from typing import Any

from are.simulation.distributed.models import DistributedTrace, EventKind, stable_digest
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5


def decision_context(process, trace, decision):
    event = next(e for e in trace.events if e.event_id == decision.decision_id)
    phases = [
        w.phase
        for w in process.phase_windows
        if w.start_world_time <= event.world_time < w.end_world_time
    ]
    phase = (
        phases[0]
        if len(phases) == 1
        else (decision.season_phase if not process.phase_windows else None)
    )
    policy = next(
        (
            p
            for p in process.information_policies
            if p.actor_id == decision.actor_id
            and phase in p.phases
            and any(
                fnmatch.fnmatchcase(decision.proposed_intent.action or "", pattern)
                for pattern in p.action_patterns
            )
        ),
        None,
    )
    return event, policy


def snapshot_facts(trace, decision):
    """Resolve explicit fact IDs and the runtime's shared-blackboard aliases."""
    ids = set(decision.knowledge_snapshot.item_ids)
    prefix = f"blackboard:{decision.actor_id}:"
    ids.update(item[len(prefix) :] for item in tuple(ids) if item.startswith(prefix))
    return [
        f for f in trace.fact_versions if f.version_id in ids and not f.authoritative
    ]


def _covers(actual, required):
    if required is None:
        return True
    if isinstance(actual, (list, tuple)) and isinstance(required, (list, tuple)):
        return actual[0] <= required[0] and required[1] <= actual[1]
    return actual is not None and actual == required


def flat_evidence_baseline(
    process: FarmProcessSpecV5, trace: DistributedTrace
) -> dict[str, Any]:
    """Latest scoped facts + validity + receipt status; no alignment or graph.

    Reference predicates and available trace metadata match D-CORE's inputs.
    This baseline reports prerequisite readiness, not policy/action conformance
    or a fine-grained claim about why a message was missing.
    """
    checks = {
        "eq": operator.eq,
        "ne": operator.ne,
        "gt": operator.gt,
        "ge": operator.ge,
        "lt": operator.lt,
        "le": operator.le,
        "in": lambda a, b: a in b,
    }
    rows = []
    for decision in trace.decisions:
        event, policy = decision_context(process, trace, decision)
        if policy is None:
            continue
        available = snapshot_facts(trace, decision)
        evidence = {
            e.event_id
            for e in trace.events
            if e.world_time <= event.world_time
            and e.logical_time <= decision.logical_time
        }
        requirements = []
        for guard in policy.requirements:
            candidates = [
                f
                for f in available
                if f.fact_key == guard.fact_key
                and _covers(f.scope, guard.scope)
                and f.world_time <= event.world_time
                and (f.learned_time is None or f.learned_time <= event.world_time)
            ]
            latest = max(
                candidates,
                key=lambda f: (
                    f.world_time,
                    f.learned_time or f.world_time,
                    f.version_id,
                ),
                default=None,
            )
            verdict, reason = None, "missing_scoped_evidence"
            if latest is not None:
                if (
                    latest.valid_until is not None
                    and event.world_time > latest.valid_until
                ) or (
                    guard.max_age is not None
                    and event.world_time - latest.world_time > guard.max_age
                ):
                    reason = "expired_evidence"
                elif guard.required_evidence and (
                    not latest.evidence_ids or not set(latest.evidence_ids) <= evidence
                ):
                    reason = "unsupported_evidence"
                else:
                    try:
                        verdict = bool(
                            checks[guard.operator.value](latest.value, guard.expected)
                        )
                        reason = "predicate_true" if verdict else "predicate_false"
                    except (TypeError, ValueError):
                        reason = "invalid_value"
            requirements.append(
                {
                    "fact_key": guard.fact_key,
                    "verdict": verdict,
                    "reason": reason,
                    "fact_version_id": latest.version_id if latest else None,
                }
            )
        values = [r["verdict"] for r in requirements]
        ready = (
            False
            if False in values
            else (True if all(v is True for v in values) else None)
        )
        actions = [
            e
            for e in trace.events
            if e.kind == EventKind.ACTION
            and e.decision_context_id == decision.decision_id
        ]
        rows.append(
            {
                "decision_id": decision.decision_id,
                "prerequisites_satisfied": ready,
                "fully_observed": all(v is not None for v in values),
                "requirements": requirements,
                "native_execution": [
                    {
                        "event_id": e.event_id,
                        "status": e.status,
                        "accepted": e.status == "ok" and bool(e.farmare_event_id),
                    }
                    for e in actions
                ],
            }
        )
    return {
        "schema_version": "flat_evidence_baseline_v1",
        "process_digest": process.digest,
        "trace_digest": stable_digest(trace.model_dump(mode="json")),
        "decisions": rows,
        "coverage": sum(r["fully_observed"] for r in rows) / len(rows)
        if rows
        else None,
        "fine_grained_localization": None,
        "interpretation": "Prerequisite readiness and native execution only; absent metadata is unknown, not demonstrated coordination failure.",
    }


def _reduced_inputs(process, trace, mode):
    payload = deepcopy(process.model_dump(mode="json"))

    def visit(value):
        if isinstance(value, dict):
            if "guard_id" in value and "fact_key" in value:
                if mode == "no_expiry":
                    value["max_age"] = None
                elif mode == "no_provenance":
                    value["required_evidence"] = False
            if mode == "no_provenance":
                if "required_actor_path" in value:
                    value["required_actor_path"] = []
                if "supersession" in value:
                    value["supersession"] = "never"
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    reduced = FarmProcessSpecV5.model_validate(payload)
    facts = (
        tuple(f.model_copy(update={"valid_until": None}) for f in trace.fact_versions)
        if mode == "no_expiry"
        else trace.fact_versions
    )
    return reduced, trace.model_copy(update={"fact_versions": facts})


def _ordered_cc(process, trace, report):
    """Replace HB edge tests by recorded serialization order, keeping all guards."""
    rank = {e.event_id: i for i, e in enumerate(trace.events)}
    matches = report["event_acceptance"]["matches"]
    by_module = {}
    for obligation in report["semantic_causal_obligations"]["details"]:
        passed = False
        for path in obligation["alternatives"]:
            ordered = all(
                edge["source"] in matches
                and edge["target"] in matches
                and rank[matches[edge["source"]]["observed_event_id"]]
                < rank[matches[edge["target"]]["observed_event_id"]]
                for edge in path["edges"]
            )
            passed |= (
                ordered
                and path["actor_path_passed"]
                and all(g["passed"] for g in path["guards"])
            )
        by_module.setdefault(obligation["module_id"], []).append(
            (obligation["weight"], passed)
        )
    weighted, denominator = 0.0, 0.0
    for module in process.occurrence_net.modules:
        rows = by_module.get(module.module_id, [])
        if rows:
            weighted += (
                module.weight_budget
                * sum(w * p for w, p in rows)
                / sum(w for w, _ in rows)
            )
            denominator += module.weight_budget
    return weighted / denominator if denominator else None


def evaluate_validation_variants(process, trace, *, alternatives=()):
    """Reevaluate immutable saved evidence; no provider or simulation calls."""
    from are.simulation.distributed.evaluator_v5 import evaluate_farm_dcore_v5

    source_digest = stable_digest(trace.model_dump(mode="json"))
    full = evaluate_farm_dcore_v5(process, trace)
    rows = []
    for mode in ("full", "no_expiry", "no_provenance", "serialization_order"):
        p, t = (
            (process, trace)
            if mode in {"full", "serialization_order"}
            else _reduced_inputs(process, trace, mode)
        )
        report = (
            full
            if mode in {"full", "serialization_order"}
            else evaluate_farm_dcore_v5(p, t)
        )
        rows.append(
            {
                "variant": mode,
                "event_fidelity": report["event_fidelity"],
                "causal_conformance": _ordered_cc(process, trace, full)
                if mode == "serialization_order"
                else report["causal_conformance"],
                "localization_available": mode in {"full", "no_expiry"},
                "analysis_only": True,
                "modified_process_digest": p.digest,
            }
        )
    sensitivity = []
    for alternative in alternatives:
        # Alternative files must pass the normal schema checks and preserve the
        # declared task; external expert review and precommitment remain required.
        if (
            alternative.process_id != process.process_id
            or alternative.scenario_id != process.scenario_id
        ):
            raise ValueError("sensitivity specification targets another process")
        report = evaluate_farm_dcore_v5(alternative, trace)
        sensitivity.append(
            {
                "process_digest": alternative.digest,
                "event_fidelity": report["event_fidelity"],
                "causal_conformance": report["causal_conformance"],
                "review_status": alternative.expert_review_status,
            }
        )
    assert stable_digest(trace.model_dump(mode="json")) == source_digest
    return {
        "schema_version": "diagnostic_validation_v1",
        "analysis_only": True,
        "paper_eligible": False,
        "trace_digest": source_digest,
        "process_digest": process.digest,
        "baseline": flat_evidence_baseline(process, trace),
        "ablations": rows,
        "specification_sensitivity": sensitivity,
        "interpretation": "no_expiry removes validity and age limits, retaining observation order and supersession; no_provenance removes support/route/current-root checks, retaining predicates and temporal edges; serialization_order replaces only HB edge tests. These are reduced diagnostics, not new primary metric definitions.",
    }
