"""Manifest-keyed diagnosis and repair metrics for frozen decision labels."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("campaign_id") or "unknown_campaign"),
        str(item.get("checkpoint_id") or "unknown_checkpoint"),
        str(item.get("decision_id") or "unknown_decision"),
    )


def _expected_values(label: dict[str, Any], singular: str, plural: str) -> set[str]:
    values = label.get(plural)
    if values is None:
        value = label.get(singular)
        values = [] if value in (None, "none", "valid") else [value]
    return {str(item) for item in values if item is not None}


def _safe_ratio(numerator: int | float, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator else None


def diagnostic_metric_rows(
    predictions: list[dict[str, Any]], labels: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Join predictions to labels by campaign/checkpoint/decision and score them."""

    label_index = {_key(item): item for item in labels}
    if len(label_index) != len(labels):
        raise ValueError("frozen diagnostic labels contain duplicate identities")
    prediction_index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in predictions:
        identity = (*_key(item), str(item.get("method")))
        if identity in prediction_index:
            raise ValueError(
                "diagnostic predictions contain duplicate method identities"
            )
        prediction_index[identity] = item
    methods = sorted({str(item.get("method")) for item in predictions})
    scenarios = sorted(
        {str(item.get("scenario_id") or item.get("scenario")) for item in labels}
    )
    rows: list[dict[str, Any]] = []
    for method in methods:
        for scenario in (*scenarios, "pooled"):
            selected = [
                item
                for item in labels
                if scenario == "pooled"
                or str(item.get("scenario_id") or item.get("scenario")) == scenario
            ]
            if not selected:
                continue
            confusion: Counter[tuple[str, str]] = Counter()
            class_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
            exact_prerequisite = actor_correct = scope_correct = version_correct = 0
            covered = unresolved = legal_repair = unnecessary = deadline_correct = 0
            repair_label_count = deadline_label_count = failure_label_count = 0
            for label in selected:
                prediction = prediction_index.get((*_key(label), method), {})
                raw_witnesses: Any = prediction.get("witnesses") or ()
                raw_repairs: Any = prediction.get("repairs") or ()
                witnesses: list[dict[str, Any]] = [
                    item for item in raw_witnesses if isinstance(item, dict)
                ]
                repairs: list[dict[str, Any]] = [
                    item for item in raw_repairs if isinstance(item, dict)
                ]
                expected_mechanisms = _expected_values(label, "mechanism", "mechanisms")
                predicted_mechanisms = {
                    str(item.get("mechanism"))
                    for item in witnesses
                    if item.get("determination") == "supported"
                }
                expected_primary = (
                    sorted(expected_mechanisms)[0] if expected_mechanisms else "none"
                )
                predicted_primary = (
                    sorted(predicted_mechanisms)[0] if predicted_mechanisms else "none"
                )
                confusion[(expected_primary, predicted_primary)] += 1
                for mechanism in expected_mechanisms | predicted_mechanisms:
                    if (
                        mechanism in expected_mechanisms
                        and mechanism in predicted_mechanisms
                    ):
                        class_counts[mechanism]["tp"] += 1
                    elif mechanism in predicted_mechanisms:
                        class_counts[mechanism]["fp"] += 1
                    else:
                        class_counts[mechanism]["fn"] += 1
                expected_prerequisites = _expected_values(
                    label, "prerequisite_id", "prerequisite_ids"
                )
                predicted_prerequisites = {
                    str(item.get("prerequisite_id")) for item in witnesses
                }
                exact_prerequisite += int(
                    expected_prerequisites == predicted_prerequisites
                )
                if expected_mechanisms:
                    failure_label_count += 1
                    covered += int(bool(predicted_mechanisms))
                unresolved += int(
                    any(item.get("determination") == "unresolved" for item in witnesses)
                    or (bool(expected_mechanisms) and not witnesses)
                )
                first = witnesses[0] if witnesses else {}
                actor_correct += int(
                    str(first.get("actor_id")) == str(label.get("actor_id"))
                )
                scope_correct += int(first.get("target_scope") == label.get("scope"))
                version_correct += int(
                    str(first.get("source_version_id"))
                    == str(label.get("source_version_id"))
                )
                expected_repair = _expected_values(
                    label, "repair_primitive", "repair_primitives"
                )
                predicted_repair: set[str] = set()
                for repair in repairs:
                    raw_primitives = repair.get("primitives")
                    if not isinstance(raw_primitives, (list, tuple)):
                        continue
                    for primitive in raw_primitives:
                        if isinstance(primitive, dict):
                            predicted_repair.add(str(primitive.get("primitive")))
                if expected_repair:
                    repair_label_count += 1
                    legal_repair += int(expected_repair == predicted_repair)
                if not expected_mechanisms:
                    unnecessary += int(bool(repairs))
                if label.get("repair_feasible") is not None:
                    deadline_label_count += 1
                    predicted_feasible = any(
                        repair.get("feasibility") == "feasible" for repair in repairs
                    )
                    deadline_correct += int(
                        predicted_feasible is bool(label["repair_feasible"])
                    )
            per_class_f1 = {}
            totals = Counter()
            for mechanism, counts in class_counts.items():
                for label in ("tp", "fp", "fn"):
                    totals[label] += counts[label]
                denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
                per_class_f1[mechanism] = _safe_ratio(2 * counts["tp"], denominator)
            valid_f1 = [value for value in per_class_f1.values() if value is not None]
            micro_denominator = 2 * totals["tp"] + totals["fp"] + totals["fn"]
            rows.append(
                {
                    "method": method,
                    "scenario": scenario,
                    "assigned_labels": len(selected),
                    "failure_labels": failure_label_count,
                    "coverage": _safe_ratio(covered, failure_label_count),
                    "unresolved_rate": _safe_ratio(unresolved, len(selected)),
                    "mechanism_confusion": {
                        f"{expected}->{predicted}": count
                        for (expected, predicted), count in sorted(confusion.items())
                    },
                    "mechanism_macro_f1": (
                        sum(valid_f1) / len(valid_f1) if valid_f1 else None
                    ),
                    "mechanism_micro_f1": _safe_ratio(
                        2 * totals["tp"], micro_denominator
                    ),
                    "mechanism_f1_by_class": per_class_f1,
                    "exact_prerequisite_accuracy": _safe_ratio(
                        exact_prerequisite, len(selected)
                    ),
                    "actor_accuracy": _safe_ratio(actor_correct, len(selected)),
                    "scope_accuracy": _safe_ratio(scope_correct, len(selected)),
                    "version_accuracy": _safe_ratio(version_correct, len(selected)),
                    "legal_repair_accuracy": _safe_ratio(
                        legal_repair, repair_label_count
                    ),
                    "unnecessary_intervention_rate": _safe_ratio(
                        unnecessary,
                        sum(
                            not _expected_values(item, "mechanism", "mechanisms")
                            for item in selected
                        ),
                    ),
                    "deadline_correctness": _safe_ratio(
                        deadline_correct, deadline_label_count
                    ),
                }
            )
    return rows


__all__ = ["diagnostic_metric_rows"]
