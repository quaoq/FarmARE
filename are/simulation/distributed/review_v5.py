"""Independent expert-review workflow for Farm D-CORE v5 specifications."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5


def neutral_v5_review_template(process: FarmProcessSpecV5) -> dict[str, Any]:
    """Return a packet with identifiers only and no proposed scientific answer."""

    net = process.occurrence_net
    return {
        "schema_version": "farm_dcore_review_v5",
        "scenario_id": process.scenario_id,
        "process_skeleton_digest": stable_digest(
            {
                "scenario_id": process.scenario_id,
                "actors": net.actors,
                "modules": [
                    {
                        "module_id": item.module_id,
                        "label": item.label,
                        "phase": item.phase,
                    }
                    for item in net.modules
                ],
            }
        ),
        "reviewed_process_digest": None,
        "reviewer": {
            "pseudonymous_id": None,
            "expertise_role": None,
            "utc_timestamp": None,
        },
        "attestation": {
            "final_model_results_not_inspected": None,
            "yield_correlations_not_used_for_tuning": None,
        },
        "fact_semantics": {
            key: None
            for key in sorted(
                {
                    guard.fact_key
                    for transition in net.transitions
                    for guard in transition.guards
                }
            )
        },
        # These are intentionally empty. Prepopulating oracle transition,
        # policy, guard, weight, or tolerance answers would compromise the two
        # independent specifications.
        "transition_acceptance": {},
        "safe_response_policies": {},
        "negative_action_definitions": None,
        "world_branches": {},
        "fault_treatments": None,
        "resolved_process_spec": None,
    }


def validate_v5_submission(payload: dict[str, Any]) -> FarmProcessSpecV5:
    if payload.get("schema_version") != "farm_dcore_review_v5":
        raise ValueError("submission is not farm_dcore_review_v5")
    reviewer = payload.get("reviewer") or {}
    if not all(
        reviewer.get(key)
        for key in ("pseudonymous_id", "expertise_role", "utc_timestamp")
    ):
        raise ValueError(
            "reviewer identity, expertise role, and timestamp are required"
        )
    reviewed_at = datetime.fromisoformat(
        str(reviewer["utc_timestamp"]).replace("Z", "+00:00")
    )
    if reviewed_at.tzinfo is None:
        raise ValueError("reviewer timestamp must include a UTC offset")
    if reviewed_at.utcoffset() is None or reviewed_at.utcoffset().total_seconds() != 0:
        raise ValueError("reviewer timestamp must be UTC")
    attestation = payload.get("attestation") or {}
    if attestation.get("final_model_results_not_inspected") is not True:
        raise ValueError("reviewer must attest no inspection of final model results")
    if attestation.get("yield_correlations_not_used_for_tuning") is not True:
        raise ValueError("reviewer must attest no yield-correlation tuning")
    raw = payload.get("resolved_process_spec")
    if not isinstance(raw, dict):
        raise ValueError("submission has no complete resolved v5 process")
    process = FarmProcessSpecV5.model_validate(raw)
    if payload.get("reviewed_process_digest") != stable_digest(raw):
        raise ValueError("reviewer process digest does not match the submission")
    if process.scenario_id != payload.get("scenario_id"):
        raise ValueError("submission scenario differs from the neutral packet")
    skeleton_digest = stable_digest(
        {
            "scenario_id": process.scenario_id,
            "actors": process.occurrence_net.actors,
            "modules": [
                {
                    "module_id": item.module_id,
                    "label": item.label,
                    "phase": item.phase,
                }
                for item in process.occurrence_net.modules
            ],
        }
    )
    if skeleton_digest != payload.get("process_skeleton_digest"):
        raise ValueError("submission changes the neutral module/team skeleton")
    if process.annotation_status != "draft":
        raise ValueError("independent submissions must remain draft")
    return process


def compare_v5_submissions(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, Any]:
    first, second = validate_v5_submission(left), validate_v5_submission(right)
    if left["reviewer"]["pseudonymous_id"] == right["reviewer"]["pseudonymous_id"]:
        raise ValueError("the two domain submissions must be independent")
    left_flat = _flatten(_canonicalize_review(first.model_dump(mode="json")))
    right_flat = _flatten(_canonicalize_review(second.model_dump(mode="json")))
    disagreements = [
        {"path": key, "left": left_flat.get(key), "right": right_flat.get(key)}
        for key in sorted(set(left_flat) | set(right_flat))
        if left_flat.get(key) != right_flat.get(key)
    ]
    categorical = [item for item in disagreements if not _numeric_pair(item)]
    numeric = [
        {
            **item,
            "absolute_difference": abs(float(item["left"]) - float(item["right"])),
        }
        for item in disagreements
        if _numeric_pair(item)
    ]
    all_paths = set(left_flat) | set(right_flat)
    categorical_paths = [
        key
        for key in all_paths
        if not (
            key in left_flat
            and key in right_flat
            and isinstance(left_flat[key], (int, float))
            and not isinstance(left_flat[key], bool)
            and isinstance(right_flat[key], (int, float))
            and not isinstance(right_flat[key], bool)
        )
    ]
    return {
        "schema_version": "farm_dcore_review_comparison_v5",
        "left_reviewer": left["reviewer"]["pseudonymous_id"],
        "right_reviewer": right["reviewer"]["pseudonymous_id"],
        "categorical_agreement": 1 - len(categorical) / max(1, len(categorical_paths)),
        "categorical_field_count": len(categorical_paths),
        "categorical_disagreements": categorical,
        "numerical_disagreements": numeric,
        "unresolved_paths": [item["path"] for item in disagreements],
    }


def adjudicate_v5_reviews(
    left: dict[str, Any],
    right: dict[str, Any],
    resolutions: dict[str, Any],
    *,
    adjudicator_id: str,
    adjudicator_expertise_role: str,
    final_model_results_not_inspected: bool,
    yield_correlations_not_used_for_tuning: bool,
) -> dict[str, Any]:
    comparison = compare_v5_submissions(left, right)
    if not adjudicator_id:
        raise ValueError("adjudicator pseudonymous ID is required")
    reviewer_ids = {
        left["reviewer"]["pseudonymous_id"],
        right["reviewer"]["pseudonymous_id"],
    }
    if adjudicator_id in reviewer_ids:
        raise ValueError("adjudicator must be independent of both submissions")
    if not adjudicator_expertise_role.strip():
        raise ValueError("adjudicator expertise role is required")
    if final_model_results_not_inspected is not True:
        raise ValueError("adjudicator must attest no final-results inspection")
    if yield_correlations_not_used_for_tuning is not True:
        raise ValueError("adjudicator must attest no yield-correlation tuning")
    unresolved = set(comparison["unresolved_paths"])
    if set(resolutions) != unresolved:
        raise ValueError("every and only disagreement must be adjudicated")
    resolved = _canonicalize_review(
        validate_v5_submission(left).model_dump(mode="json")
    )
    normalized_resolutions = {}
    for path, resolution in resolutions.items():
        if (
            not isinstance(resolution, dict)
            or "value" not in resolution
            or not str(resolution.get("rationale", "")).strip()
        ):
            raise ValueError(
                "every adjudication requires an explicit value and rationale"
            )
        _assign(resolved, path, resolution["value"])
        normalized_resolutions[path] = {
            "value": resolution["value"],
            "rationale": str(resolution["rationale"]).strip(),
        }
    candidate = FarmProcessSpecV5.model_validate(resolved)
    payload = {
        "schema_version": "farm_dcore_adjudication_v5",
        "status": "adjudicated",
        "adjudicator_id": adjudicator_id,
        "adjudicator_expertise_role": adjudicator_expertise_role,
        "final_model_results_not_inspected": True,
        "yield_correlations_not_used_for_tuning": True,
        "source_reviewer_ids": [
            left["reviewer"]["pseudonymous_id"],
            right["reviewer"]["pseudonymous_id"],
        ],
        "utc_timestamp": datetime.now(timezone.utc).isoformat(),
        "source_submission_digests": [stable_digest(left), stable_digest(right)],
        "comparison_digest": stable_digest(comparison),
        "resolutions": normalized_resolutions,
        "resolved_process_spec": candidate.model_dump(mode="json"),
    }
    payload["adjudication_digest"] = stable_digest(payload)
    return payload


def confirm_v5_adjudication(
    adjudication: dict[str, Any], confirmation: dict[str, Any]
) -> dict[str, Any]:
    if adjudication.get("status") != "adjudicated":
        raise ValueError("only an adjudicated review may be confirmed")
    if (
        adjudication.get("final_model_results_not_inspected") is not True
        or adjudication.get("yield_correlations_not_used_for_tuning") is not True
        or not adjudication.get("adjudicator_expertise_role")
    ):
        raise ValueError("adjudication lacks scientific-review attestations")
    process = FarmProcessSpecV5.model_validate(adjudication["resolved_process_spec"])
    if not confirmation.get("third_expert_id"):
        raise ValueError("third-expert pseudonymous ID is required")
    if confirmation["third_expert_id"] in {
        adjudication.get("adjudicator_id"),
        *adjudication.get("source_reviewer_ids", ()),
    }:
        raise ValueError("third expert must be independent of prior reviewers")
    if not confirmation.get("expertise_role"):
        raise ValueError("third-expert expertise role is required")
    if confirmation.get("final_model_results_not_inspected") is not True:
        raise ValueError("third expert must attest no final-results inspection")
    if confirmation.get("yield_correlations_not_used_for_tuning") is not True:
        raise ValueError("third expert must attest no yield-correlation tuning")
    if not confirmation.get("utc_timestamp"):
        raise ValueError("third-expert UTC timestamp is required")
    confirmed_at = datetime.fromisoformat(
        str(confirmation["utc_timestamp"]).replace("Z", "+00:00")
    )
    if confirmed_at.tzinfo is None:
        raise ValueError("third-expert timestamp must include a UTC offset")
    if confirmed_at.utcoffset() is None or confirmed_at.utcoffset().total_seconds() != 0:
        raise ValueError("third-expert timestamp must be UTC")
    if confirmation.get("resolved_process_digest") != stable_digest(
        process.model_dump(mode="json")
    ):
        raise ValueError("third expert confirmed a different process digest")
    return {
        "schema_version": "farm_dcore_confirmation_v5",
        "third_expert_id": confirmation["third_expert_id"],
        "expertise_role": confirmation["expertise_role"],
        "utc_timestamp": confirmed_at.astimezone(timezone.utc).isoformat(),
        "adjudication_digest": adjudication["adjudication_digest"],
        "resolved_process_digest": stable_digest(process.model_dump(mode="json")),
        "final_model_results_not_inspected": True,
        "yield_correlations_not_used_for_tuning": True,
    }


def freeze_v5_process(
    adjudication: dict[str, Any], confirmation: dict[str, Any]
) -> FarmProcessSpecV5:
    if confirmation.get("adjudication_digest") != adjudication.get(
        "adjudication_digest"
    ):
        raise ValueError("third-expert confirmation digest mismatch")
    raw = adjudication["resolved_process_spec"]
    if confirmation.get("resolved_process_digest") != stable_digest(raw):
        raise ValueError("confirmed process digest mismatch")
    process = FarmProcessSpecV5.model_validate(raw)
    reviewed_net = process.occurrence_net.model_copy(
        update={
            "expert_review_status": "confirmed",
            "oracle_version": "farm_dcore_review_v5",
            "metadata": {
                **process.occurrence_net.metadata,
                "metric_annotation_status": "frozen",
                "branch_annotation_status": "frozen",
                "guard_annotation_status": "frozen",
                "paper_eligible": True,
            },
        }
    )
    frozen = {
        **raw,
        **{
            "occurrence_net": reviewed_net.model_dump(mode="python"),
            "expert_review_status": "confirmed",
            "annotation_status": "frozen",
            "review_digest": stable_digest(
                {"adjudication": adjudication, "confirmation": confirmation}
            ),
        },
    }
    return FarmProcessSpecV5.model_validate(frozen)


def export_neutral_v5_packet(process: FarmProcessSpecV5, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    path = output / "reviewer_submission_v5.template.json"
    path.write_text(
        json.dumps(neutral_v5_review_template(process), indent=2), encoding="utf-8"
    )
    net = process.occurrence_net
    facts = list(net.metadata.get("fact_definitions", ()))

    def write_csv(
        name: str, headers: tuple[str, ...], rows: list[dict[str, Any]]
    ) -> None:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
        (output / name).write_text(buffer.getvalue(), encoding="utf-8")

    write_csv(
        "modules.csv",
        ("module_id", "label", "phase", "expert_weight_budget"),
        [
            {
                "module_id": item.module_id,
                "label": item.label,
                "phase": item.phase,
                "expert_weight_budget": "",
            }
            for item in net.modules
        ],
    )
    write_csv(
        "facts.csv",
        (
            "fact_key",
            "value_type",
            "units",
            "scope_kind",
            "observation_actions",
            "expert_truth_semantics",
            "expert_validity",
            "expert_supersession",
        ),
        [
            {
                "fact_key": item.get("fact_key", ""),
                "value_type": item.get("value_type", ""),
                "units": item.get("units") or "",
                "scope_kind": item.get("scope_kind", ""),
                "observation_actions": "|".join(item.get("observation_actions", ())),
                "expert_truth_semantics": "",
                "expert_validity": "",
                "expert_supersession": "",
            }
            for item in facts
        ],
    )
    try:
        from are.simulation.scenarios.scenario_dcore.farm_catalog import (
            create_native_scenario,
        )

        tools = sorted(
            tool.name
            for tool in create_native_scenario(
                process.scenario_id, world_seed=0
            ).get_tools()
        )
    except Exception:
        tools = sorted(
            {item.action for item in net.transitions if item.actor_id != "world"}
        )
    write_csv(
        "farmare_tools.csv",
        ("tool_name", "expert_owner", "expert_observation_or_write", "expert_notes"),
        [
            {
                "tool_name": tool,
                "expert_owner": "",
                "expert_observation_or_write": "",
                "expert_notes": "",
            }
            for tool in tools
        ],
    )
    module_ids = [item.module_id for item in net.modules]
    dot = ["digraph farm_dcore_neutral_modules {", "  rankdir=LR;"]
    dot.extend(f'  "{item.module_id}" [label="{item.label}"];' for item in net.modules)
    dot.extend(
        f'  "{left}" -> "{right}" [style=dashed,label="phase progression"];'
        for left, right in zip(module_ids, module_ids[1:])
    )
    dot.append("}")
    (output / "module_skeleton.dot").write_text("\n".join(dot) + "\n", encoding="utf-8")
    pnml_places = "".join(
        f'<place id="{item.module_id}"><name><text>{item.label}</text></name></place>'
        for item in net.modules
    )
    (output / "module_skeleton.pnml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<pnml><net id="neutral-module-skeleton" type="module-skeleton">'
        f"{pnml_places}</net></pnml>\n",
        encoding="utf-8",
    )
    instructions = f"""# Independent Farm D-CORE v5 domain review

Scenario: `{process.scenario_id}`

Complete this packet without seeing the other expert's submission, final model
results, metric-yield correlations, engineering oracle transitions, guards,
weights, or tolerances. The module order, public fact names, and complete
FarmARE tool catalog are context, not proposed answers.

Specify the full resolved `farm_process_spec_v5`, including fact semantics,
world branches, exhaustive information policies, categorical transition
acceptance, negative-action definitions, semantic causal obligations, and all
fault delivery/validity/deadline times. Label units and spatial scope. Sign the
two no-results/no-yield-tuning attestations. Independent submissions remain
`draft`; only adjudication and third-expert confirmation can freeze them.
"""
    (output / "INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")
    files = sorted(item.name for item in output.iterdir() if item.is_file())
    (output / "packet_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "farm_dcore_neutral_packet_v5",
                "scenario_id": process.scenario_id,
                "neutral": True,
                "contains_oracle_transitions": False,
                "files": files,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        rows = {}
        for key, child in value.items():
            rows.update(_flatten(child, f"{prefix}.{key}" if prefix else str(key)))
        return rows
    if isinstance(value, list):
        rows = {}
        for index, child in enumerate(value):
            rows.update(_flatten(child, f"{prefix}.{index}"))
        return rows
    return {prefix: value}


def _canonicalize_review(value: Any) -> Any:
    """Canonicalize identifier-keyed collections before expert comparison.

    Domain submissions should not disagree merely because experts serialized
    modules, guards, policies, or transitions in a different list order.  Lists
    without a stable semantic identifier remain ordered because order can be
    meaningful (for example, a required multi-hop actor path).
    """

    if isinstance(value, dict):
        return {key: _canonicalize_review(child) for key, child in value.items()}
    if not isinstance(value, list):
        return value
    children = [_canonicalize_review(child) for child in value]
    identifiers = (
        "obligation_id",
        "policy_id",
        "rule_id",
        "fault_id",
        "branch_id",
        "alternative_id",
        "transition_id",
        "place_id",
        "arc_id",
        "choice_id",
        "guard_id",
        "path_id",
        "fact_key",
        "phase",
        "name",
        "module_id",
    )
    if children and all(isinstance(child, dict) for child in children):
        key = next(
            (
                candidate
                for candidate in identifiers
                if all(candidate in child for child in children)
            ),
            None,
        )
        if key:
            return sorted(children, key=lambda child: str(child[key]))
    return children


def _assign(value: dict[str, Any], path: str, replacement: Any) -> None:
    pieces = path.split(".")
    cursor: Any = value
    for piece in pieces[:-1]:
        cursor = cursor[int(piece)] if isinstance(cursor, list) else cursor[piece]
    final = pieces[-1]
    if isinstance(cursor, list):
        cursor[int(final)] = replacement
    else:
        cursor[final] = replacement


def _numeric_pair(item: dict[str, Any]) -> bool:
    return all(
        isinstance(item[key], (int, float)) and not isinstance(item[key], bool)
        for key in ("left", "right")
    )


__all__ = [
    "adjudicate_v5_reviews",
    "compare_v5_submissions",
    "confirm_v5_adjudication",
    "export_neutral_v5_packet",
    "freeze_v5_process",
    "neutral_v5_review_template",
    "validate_v5_submission",
]
