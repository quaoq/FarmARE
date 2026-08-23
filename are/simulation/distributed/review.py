"""Neutral, digest-attested expert review workflow for Farm D-CORE."""

from __future__ import annotations

import copy
import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.petri import (
    ArcSpec,
    ArgumentConstraint,
    ChoiceGroupSpec,
    FarmPetriTemplateSpec,
    InformationPolicySpec,
    PetriNetSpec,
    PlaceSpec,
    ScoringClass,
    WorldBranchSpec,
    transition_dependencies,
    validate_petri_net,
)


def _require_utc(value: Any, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp") from error
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise ValueError(f"{label} must be UTC")
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    compile_native_petri_net,
)
from are.simulation.scenarios.scenario_dcore.wetjune_petri_v3 import (
    build_wetjune_template,
    expand_wetjune_template,
)

REVIEW_SCHEMA = "farm_dcore_independent_review_v1"
TEAM_REVIEW_SCHEMA = "farm_team_refinement_review_v1"


def submission_attestation_digest(payload: dict[str, Any]) -> str:
    """Digest a review while excluding the digest field itself.

    Reviewers compute this after completing every scientific field, then place
    the returned value in ``reviewer.digest_attestation``.  This prevents an
    accidentally edited worksheet from silently passing as the reviewed one.
    """

    canonical = copy.deepcopy(payload)
    reviewer = canonical.setdefault("reviewer", {})
    reviewer["digest_attestation"] = None
    return stable_digest(canonical)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], path))
        return result
    if isinstance(value, list):
        result = {}
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            result.update(_flatten(item, path))
        return result
    return {prefix: value}


def wetjune_review_template() -> FarmPetriTemplateSpec:
    return build_wetjune_template(compile_native_petri_net("farm_wetjune_recheck"))


def _review_targets(
    template: FarmPetriTemplateSpec,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return compact template-level dependency and tolerance targets."""

    base = compile_native_petri_net("farm_wetjune_recheck")
    transition_to_template = {
        transition_id: item.template_id
        for item in template.transition_templates
        for transition_id in item.expansion.source_transition_ids
    }
    expanded = expand_wetjune_template(template, base)
    dependencies = tuple(
        sorted(
            {
                f"{transition_to_template[source]}->{transition_to_template[target]}"
                for source, target in transition_dependencies(expanded)
                if transition_to_template[source] != transition_to_template[target]
            }
        )
    )
    by_id = {item.transition_id: item for item in base.transitions}
    numeric = tuple(
        item.template_id
        for item in template.transition_templates
        if any(
            by_id[source].arguments
            for source in item.expansion.source_transition_ids
            if source in by_id
        )
    )
    timed = tuple(
        item.template_id
        for item in template.transition_templates
        if any(
            by_id[source].window_start is not None
            or by_id[source].window_end is not None
            for source in item.expansion.source_transition_ids
            if source in by_id
        )
    )
    scoped = tuple(
        item.template_id
        for item in template.transition_templates
        if any(
            by_id[source].scope is not None
            for source in item.expansion.source_transition_ids
            if source in by_id
        )
    )
    return dependencies, numeric, timed, scoped


def _csv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _module_dot(template: FarmPetriTemplateSpec) -> str:
    lines = ["digraph wetjune_review {", "  rankdir=LR;"]
    for module in template.modules:
        lines.append(f'  "{module.module_id}" [label="{module.label}"];')
    for left, right in zip(template.modules, template.modules[1:]):
        lines.append(f'  "{left.module_id}" -> "{right.module_id}" [style=dashed];')
    lines.append("}")
    return "\n".join(lines)


def _module_pnml(template: FarmPetriTemplateSpec) -> str:
    transitions = "".join(
        f'<transition id="{item.module_id}"><name><text>{item.label}</text></name></transition>'
        for item in template.modules
    )
    places = "".join(
        f'<place id="module-place-{index}"/>'
        for index in range(len(template.modules) - 1)
    )
    arcs = "".join(
        (
            f'<arc id="module-out-{index}" source="{left.module_id}" '
            f'target="module-place-{index}"/>'
            f'<arc id="module-in-{index}" source="module-place-{index}" '
            f'target="{right.module_id}"/>'
        )
        for index, (left, right) in enumerate(
            zip(template.modules, template.modules[1:])
        )
    )
    return (
        '<?xml version="1.0"?><pnml><net id="wetjune-neutral">'
        f"{places}{transitions}{arcs}</net></pnml>"
    )


def neutral_submission_template(template: FarmPetriTemplateSpec) -> dict[str, Any]:
    dependencies, numeric, timed, scoped = _review_targets(template)
    return {
        "schema_version": REVIEW_SCHEMA,
        "scenario_id": template.scenario_id,
        "template_id": template.template_id,
        "template_digest": stable_digest(template.model_dump(mode="json")),
        "reviewer": {
            "reviewer_id": None,
            "expertise_role": None,
            "reviewed_at_utc": None,
            "digest_attestation": None,
        },
        "no_final_model_results_seen": None,
        "module_annotations": {
            item.module_id: {"weight_budget": None, "required": None}
            for item in template.modules
        },
        "transition_annotations": {
            item.template_id: {
                "scoring_class": None,
                "within_module_weight": None,
                "required": None,
            }
            for item in template.transition_templates
        },
        "fact_annotations": {
            item.fact_key: {
                "accepted": None,
                "valid_for_seconds": None,
                "notes": None,
            }
            for item in template.fact_definitions
        },
        "world_branches": [],
        "information_policies": {},
        "choice_groups": [],
        "numeric_acceptance_ranges": {
            template_id: {"status": None, "constraints": {}} for template_id in numeric
        },
        "time_windows": {
            template_id: {"status": None, "window": None} for template_id in timed
        },
        "scope_acceptance": {
            template_id: {"status": None, "acceptance": None} for template_id in scoped
        },
        "direct_dependency_decisions": {
            dependency: {"decision": None, "rationale": None}
            for dependency in dependencies
        },
        "notes": None,
    }


def export_neutral_review_packet(output: Path) -> dict[str, str]:
    template = wetjune_review_template()
    output.mkdir(parents=True, exist_ok=True)
    packet = neutral_submission_template(template)
    from are.simulation.distributed.teams import (
        FOUR_AGENT_TEAM_ID,
        PRIMARY_TEAM_ID,
        THREE_AGENT_TEAM_ID,
        build_builtin_team,
        team_digest,
    )
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        create_native_scenario,
    )

    scenario = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    teams = [
        build_builtin_team(team_id, scenario.get_tools())
        for team_id in (
            PRIMARY_TEAM_ID,
            THREE_AGENT_TEAM_ID,
            FOUR_AGENT_TEAM_ID,
        )
    ]
    files = {
        "instructions": output / "README.md",
        "modules": output / "modules.csv",
        "transitions": output / "transition_templates.csv",
        "facts": output / "fact_definitions.csv",
        "policies": output / "policy_questions.csv",
        "dependencies": output / "template_dependencies.csv",
        "submission": output / "reviewer_submission.template.json",
        "dot": output / "module_skeleton.dot",
        "pnml": output / "module_skeleton.pnml",
        "teams": output / "team_decompositions.engineering.json",
        "team_review": output / "team_refinement_review.template.json",
    }
    files["instructions"].write_text(
        "# Neutral Wet-June expert packet\n\n"
        "Specify each field independently. The packet deliberately omits proposed "
        "weights, guards, tolerances, and correct policy answers. Do not inspect "
        "another reviewer submission, agent results, or yield correlations. "
        "Set `no_final_model_results_seen=true`, record the template digest, and "
        "attest the completed submission digest.\n"
        "The team-decomposition file records the fixed experimental treatments, "
        "not an expert-approved oracle. Review the primary two-agent team with "
        "the Wet-June specification; review each 3/4-agent role refinement "
        "separately before using it in paper tables.\n",
        encoding="utf-8",
    )
    files["modules"].write_text(
        _csv(
            [
                {
                    "module_id": item.module_id,
                    "label": item.label,
                    "phase": item.phase,
                    "weight_budget": "",
                    "required": "",
                }
                for item in template.modules
            ],
            ["module_id", "label", "phase", "weight_budget", "required"],
        ),
        encoding="utf-8",
    )
    files["transitions"].write_text(
        _csv(
            [
                {
                    "template_id": item.template_id,
                    "module_id": item.module_id,
                    "label": item.label,
                    "actor": item.actor_id,
                    "kind": item.kind.value,
                    "action_pattern": item.action_pattern,
                    "instances": item.expansion.expected_count,
                    "scoring_class": "",
                    "within_module_weight": "",
                    "required": "",
                }
                for item in template.transition_templates
            ],
            [
                "template_id",
                "module_id",
                "label",
                "actor",
                "kind",
                "action_pattern",
                "instances",
                "scoring_class",
                "within_module_weight",
                "required",
            ],
        ),
        encoding="utf-8",
    )
    files["facts"].write_text(
        _csv(
            [
                {
                    "fact_key": item.fact_key,
                    "type": item.value_type,
                    "units": item.units or "",
                    "scope": item.scope_kind,
                    "observation_actions": "|".join(item.observation_actions),
                    "truth_source": item.truth_source,
                    "accepted": "",
                    "valid_for_seconds": "",
                    "notes": "",
                }
                for item in template.fact_definitions
            ],
            [
                "fact_key",
                "type",
                "units",
                "scope",
                "observation_actions",
                "truth_source",
                "accepted",
                "valid_for_seconds",
                "notes",
            ],
        ),
        encoding="utf-8",
    )
    files["policies"].write_text(
        _csv(
            [
                {
                    "policy_id": item.policy_id,
                    "actor": item.actor_id,
                    "phases": "|".join(item.phases),
                    "candidate_actions": "|".join(item.action_patterns),
                    "required_fact_keys": "",
                    "true_open": "",
                    "false_open": "",
                    "unknown_open": "",
                    "closed": "",
                }
                for item in template.information_policies
            ],
            [
                "policy_id",
                "actor",
                "phases",
                "candidate_actions",
                "required_fact_keys",
                "true_open",
                "false_open",
                "unknown_open",
                "closed",
            ],
        ),
        encoding="utf-8",
    )
    dependencies, _, _, _ = _review_targets(template)
    files["dependencies"].write_text(
        _csv(
            [
                {
                    "dependency": dependency,
                    "decision": "",
                    "rationale": "",
                }
                for dependency in dependencies
            ],
            ["dependency", "decision", "rationale"],
        ),
        encoding="utf-8",
    )
    files["submission"].write_text(json.dumps(packet, indent=2), encoding="utf-8")
    files["dot"].write_text(_module_dot(template), encoding="utf-8")
    files["pnml"].write_text(_module_pnml(template), encoding="utf-8")
    files["teams"].write_text(
        json.dumps(
            {
                "schema_version": "farm_team_packet_v1",
                "paper_status": "engineering_unreviewed",
                "teams": [
                    {
                        "team_spec": team.model_dump(mode="json"),
                        "team_spec_digest": team_digest(team),
                    }
                    for team in teams
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    files["team_review"].write_text(
        json.dumps(
            {
                "schema_version": TEAM_REVIEW_SCHEMA,
                "reviewer": {
                    "reviewer_id": None,
                    "expertise_role": None,
                    "reviewed_at_utc": None,
                    "no_final_model_results_seen": None,
                    "no_yield_correlation_tuning": None,
                    "digest_attestation": None,
                },
                "team_decisions": {
                    team.team_id: {
                        "team_spec_digest": team_digest(team),
                        "role_partition_accepted": None,
                        "aggregate_capability_preserved": None,
                        "tool_ownership_accepted": None,
                        "observation_ownership_accepted": None,
                        "time_authority_accepted": None,
                        "topology_accepted": None,
                        "transition_owner_overrides": {},
                        "communication_paths": (
                            {}
                            if team.team_id == PRIMARY_TEAM_ID
                            else {
                                "agronomic_evidence": [],
                                **(
                                    {"resource_readiness": []}
                                    if team.team_id == FOUR_AGENT_TEAM_ID
                                    else {}
                                ),
                            }
                        ),
                        "module_weight_budgets": (
                            {}
                            if team.team_id == PRIMARY_TEAM_ID
                            else {module.module_id: None for module in template.modules}
                        ),
                        "intended_concurrency": [],
                        "rationale": None,
                    }
                    for team in teams
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {key: str(path) for key, path in files.items()}


def validate_team_submission(
    payload: dict[str, Any], *, complete: bool = True
) -> dict[str, Any]:
    """Validate one independent review of the fixed team decompositions."""

    from are.simulation.distributed.teams import (
        FOUR_AGENT_TEAM_ID,
        PRIMARY_TEAM_ID,
        THREE_AGENT_TEAM_ID,
        build_builtin_team,
        team_digest,
        topology_edges,
    )
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        create_native_scenario,
    )

    scenario = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    teams = {
        team_id: build_builtin_team(team_id, scenario.get_tools())
        for team_id in (PRIMARY_TEAM_ID, THREE_AGENT_TEAM_ID, FOUR_AGENT_TEAM_ID)
    }
    errors: list[str] = []
    if payload.get("schema_version") != TEAM_REVIEW_SCHEMA:
        errors.append("wrong schema_version")
    reviewer = payload.get("reviewer") or {}
    for key in ("reviewer_id", "expertise_role", "reviewed_at_utc"):
        if complete and not reviewer.get(key):
            errors.append(f"missing reviewer.{key}")
    if complete and reviewer.get("reviewed_at_utc"):
        try:
            _require_utc(reviewer["reviewed_at_utc"], "reviewer.reviewed_at_utc")
        except ValueError as error:
            errors.append(str(error))
    if complete and reviewer.get("no_final_model_results_seen") is not True:
        errors.append("reviewer must attest that final model results were not seen")
    if complete and reviewer.get("no_yield_correlation_tuning") is not True:
        errors.append("reviewer must attest that yield correlations were not used")
    expected_attestation = submission_attestation_digest(payload)
    if complete and reviewer.get("digest_attestation") != expected_attestation:
        errors.append(
            "reviewer.digest_attestation mismatch; expected "
            f"{expected_attestation} for the completed submission"
        )

    decisions = payload.get("team_decisions") or {}
    expected_modules = {
        module.module_id for module in wetjune_review_template().modules
    }
    acceptance_fields = (
        "role_partition_accepted",
        "aggregate_capability_preserved",
        "tool_ownership_accepted",
        "observation_ownership_accepted",
        "time_authority_accepted",
        "topology_accepted",
    )
    for team_id, team in teams.items():
        row = decisions.get(team_id) or {}
        if row.get("team_spec_digest") != team_digest(team):
            errors.append(f"team digest mismatch for {team_id}")
        for field in acceptance_fields:
            if complete and not isinstance(row.get(field), bool):
                errors.append(f"{team_id}.{field} is incomplete")
        if complete and not row.get("rationale"):
            errors.append(f"{team_id} needs a reviewer rationale")
        owners = row.get("transition_owner_overrides") or {}
        known_actors = {actor.actor_id for actor in team.actors}
        unknown_owners = set(owners.values()) - known_actors
        if unknown_owners:
            errors.append(
                f"{team_id} assigns transitions to unknown actors: "
                f"{sorted(unknown_owners)}"
            )
        paths = row.get("communication_paths") or {}
        expected_path_names = (
            set()
            if team_id == PRIMARY_TEAM_ID
            else {
                "agronomic_evidence",
                *(("resource_readiness",) if team_id == FOUR_AGENT_TEAM_ID else ()),
            }
        )
        if complete and not expected_path_names <= set(paths):
            errors.append(f"{team_id} is missing required evidence paths")
        edges = set(topology_edges(team))
        for path_id, path in paths.items():
            if complete and (not isinstance(path, list) or len(path) < 2):
                errors.append(f"{team_id}.{path_id} needs at least two actors")
                continue
            if not isinstance(path, list):
                continue
            if not set(path) <= known_actors:
                errors.append(f"{team_id}.{path_id} contains unknown actors")
            if any(edge not in edges for edge in zip(path, path[1:])):
                errors.append(f"{team_id}.{path_id} violates the declared topology")
        budgets = row.get("module_weight_budgets") or {}
        if team_id != PRIMARY_TEAM_ID:
            if complete and set(budgets) != expected_modules:
                errors.append(f"{team_id} must allocate every module budget")
            for module_id, value in budgets.items():
                if module_id not in expected_modules:
                    errors.append(f"{team_id} has unknown module {module_id}")
                if complete and (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or float(value) <= 0
                ):
                    errors.append(f"{team_id}.{module_id} has an invalid budget")
        concurrency = row.get("intended_concurrency") or []
        if not isinstance(concurrency, list) or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or not set(pair) <= known_actors
            for pair in concurrency
        ):
            errors.append(f"{team_id} has invalid intended-concurrency pairs")

    report = {
        "valid": not errors,
        "errors": errors,
        "submission_digest": stable_digest(payload),
        "attestation_digest": expected_attestation,
        "team_count": len(teams),
    }
    if errors:
        raise ValueError("; ".join(errors))
    return report


def compare_team_submissions(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, Any]:
    """Compare two independently attested team/refinement reviews."""

    validate_team_submission(left)
    validate_team_submission(right)
    left_id = (left.get("reviewer") or {}).get("reviewer_id")
    right_id = (right.get("reviewer") or {}).get("reviewer_id")
    if left_id == right_id:
        raise ValueError("independent submissions must use different reviewer IDs")
    left_values = _flatten(left.get("team_decisions") or {})
    right_values = _flatten(right.get("team_decisions") or {})
    paths = sorted(set(left_values) | set(right_values))
    differences = [
        {
            "path": path,
            "left": left_values.get(path),
            "right": right_values.get(path),
            "kind": (
                "numerical"
                if isinstance(left_values.get(path), (int, float))
                and not isinstance(left_values.get(path), bool)
                and isinstance(right_values.get(path), (int, float))
                and not isinstance(right_values.get(path), bool)
                else "categorical"
            ),
        }
        for path in paths
        if left_values.get(path) != right_values.get(path)
    ]
    total = len(paths)
    return {
        "schema_version": "farm_team_review_comparison_v1",
        "left_digest": stable_digest(left),
        "right_digest": stable_digest(right),
        "left_reviewer_id": left_id,
        "right_reviewer_id": right_id,
        "agreement_count": total - len(differences),
        "field_count": total,
        "agreement_rate": (total - len(differences)) / total if total else 1.0,
        "unresolved_items": differences,
    }


def adjudicate_team_reviews(
    left: dict[str, Any],
    right: dict[str, Any],
    resolved: dict[str, Any],
    adjudicator: dict[str, Any],
) -> dict[str, Any]:
    comparison = compare_team_submissions(left, right)
    validate_team_submission(resolved)
    if not all(
        adjudicator.get(key)
        for key in ("reviewer_id", "expertise_role", "reviewed_at_utc")
    ):
        raise ValueError(
            "adjudicator identity, expertise, and UTC timestamp are required"
        )
    if adjudicator.get("no_final_model_results_seen") is not True:
        raise ValueError("adjudicator must not inspect final model results")
    if adjudicator.get("no_yield_correlation_tuning") is not True:
        raise ValueError("adjudicator must not tune from yield correlations")
    _require_utc(adjudicator["reviewed_at_utc"], "adjudicator.reviewed_at_utc")
    resolutions = adjudicator.get("resolutions") or {}
    missing = {item["path"] for item in comparison["unresolved_items"]} - set(
        resolutions
    )
    if missing:
        raise ValueError(
            "adjudication is missing resolutions for: " + ", ".join(sorted(missing))
        )
    return {
        "schema_version": "farm_team_adjudication_v1",
        "status": "adjudicated",
        "source_comparison": comparison,
        "adjudicator": adjudicator,
        "resolution_count": len(resolutions),
        "resolved_specification": resolved,
        "resolved_specification_digest": stable_digest(resolved),
        "unresolved_disagreements": [],
    }


def freeze_confirmed_team_review(
    confirmed: dict[str, Any], frozen_petri: dict[str, Any]
) -> dict[str, Any]:
    """Create immutable confirmed team specs and role refinements."""

    from are.simulation.distributed.models import AgentTeamSpec, RoleRefinementSpec
    from are.simulation.distributed.petri import PetriNetSpec, validate_petri_net
    from are.simulation.distributed.teams import (
        FOUR_AGENT_TEAM_ID,
        PRIMARY_TEAM_ID,
        THREE_AGENT_TEAM_ID,
        build_builtin_team,
        built_in_role_refinement,
        refine_petri_for_team,
        team_digest,
    )
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        create_native_scenario,
    )

    if confirmed.get("status") != "confirmed":
        raise ValueError("only a third-expert-confirmed team review can be frozen")
    resolved = dict(confirmed["resolved_specification"])
    validate_team_submission(resolved)
    base_net = PetriNetSpec.model_validate(frozen_petri)
    validate_petri_net(base_net)
    if base_net.expert_review_status != "confirmed":
        raise ValueError("team freeze requires a confirmed farm Petri specification")
    confirmation_digest = stable_digest(confirmed)
    base_digest = stable_digest(base_net.model_dump(mode="json"))
    scenario = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    frozen_teams: list[dict[str, Any]] = []
    frozen_refinements: list[dict[str, Any]] = []
    occurrence_nets: list[dict[str, Any]] = []
    for team_id in (PRIMARY_TEAM_ID, THREE_AGENT_TEAM_ID, FOUR_AGENT_TEAM_ID):
        draft_team = build_builtin_team(team_id, scenario.get_tools())
        row = resolved["team_decisions"][team_id]
        if not all(
            row[field] is True
            for field in (
                "role_partition_accepted",
                "aggregate_capability_preserved",
                "tool_ownership_accepted",
                "observation_ownership_accepted",
                "time_authority_accepted",
                "topology_accepted",
            )
        ):
            raise ValueError(f"cannot freeze rejected team decomposition {team_id}")
        refinement: RoleRefinementSpec | None = None
        if team_id != PRIMARY_TEAM_ID:
            draft = built_in_role_refinement(draft_team, base_net.scenario_id)
            assert draft is not None
            refinement = draft.model_copy(
                update={
                    "transition_owner_overrides": row["transition_owner_overrides"],
                    "communication_paths": {
                        key: tuple(path)
                        for key, path in row["communication_paths"].items()
                    },
                    "module_weight_budgets": row["module_weight_budgets"],
                    "intended_concurrency": tuple(
                        tuple(pair) for pair in row["intended_concurrency"]
                    ),
                    "reviewer_rationale": row["rationale"],
                    "confirmation_digest": confirmation_digest,
                    "expert_review_status": "confirmed",
                }
            )
        refinement_digest = (
            stable_digest(refinement.model_dump(mode="json")) if refinement else None
        )
        team = AgentTeamSpec.model_validate(
            {
                **draft_team.model_dump(mode="python"),
                "petri_spec_digest": base_digest,
                "role_refinement_digest": refinement_digest,
                "expert_review_status": "confirmed",
                "metadata": {
                    **draft_team.metadata,
                    "confirmed_team_review_digest": confirmation_digest,
                },
            }
        )
        occurrence = refine_petri_for_team(base_net, team, refinement)
        validate_petri_net(occurrence)
        frozen_teams.append(
            {
                "team_spec": team.model_dump(mode="json"),
                "team_spec_digest": team_digest(team),
            }
        )
        if refinement:
            frozen_refinements.append(refinement.model_dump(mode="json"))
        occurrence_nets.append(occurrence.model_dump(mode="json"))
    return {
        "schema_version": "farm_team_review_freeze_v1",
        "status": "confirmed",
        "confirmed_review_digest": confirmation_digest,
        "base_petri_spec_digest": base_digest,
        "teams": frozen_teams,
        "role_refinements": frozen_refinements,
        "occurrence_nets": occurrence_nets,
    }


def validate_submission(
    payload: dict[str, Any], *, complete: bool = True
) -> dict[str, Any]:
    template = wetjune_review_template()
    expected_policy_ids = {item.policy_id for item in template.information_policies}
    errors = []
    if payload.get("schema_version") != REVIEW_SCHEMA:
        errors.append("wrong schema_version")
    if payload.get("template_digest") != stable_digest(
        template.model_dump(mode="json")
    ):
        errors.append("template digest mismatch")
    reviewer = payload.get("reviewer") or {}
    for key in ("reviewer_id", "expertise_role", "reviewed_at_utc"):
        if complete and not reviewer.get(key):
            errors.append(f"missing reviewer.{key}")
    expected_attestation = submission_attestation_digest(payload)
    if complete and reviewer.get("digest_attestation") != expected_attestation:
        errors.append(
            "reviewer.digest_attestation mismatch; expected "
            f"{expected_attestation} for the completed submission"
        )
    if complete and payload.get("no_final_model_results_seen") is not True:
        errors.append("reviewer must attest that final model results were not seen")
    modules = payload.get("module_annotations") or {}
    for module in template.modules:
        row = modules.get(module.module_id) or {}
        if complete and (
            not isinstance(row.get("weight_budget"), (int, float))
            or float(row["weight_budget"]) <= 0
        ):
            errors.append(f"invalid module weight for {module.module_id}")
        if complete and not isinstance(row.get("required"), bool):
            errors.append(f"missing module requiredness for {module.module_id}")
    transitions = payload.get("transition_annotations") or {}
    for item in template.transition_templates:
        row = transitions.get(item.template_id) or {}
        if complete and row.get("scoring_class") not in {
            value.value for value in ScoringClass
        }:
            errors.append(f"invalid scoring class for {item.template_id}")
        if complete and (
            not isinstance(row.get("within_module_weight"), (int, float))
            or float(row["within_module_weight"]) <= 0
        ):
            errors.append(f"invalid transition weight for {item.template_id}")
        if complete and not isinstance(row.get("required"), bool):
            errors.append(f"missing transition requiredness for {item.template_id}")
    facts = payload.get("fact_annotations") or {}
    for item in template.fact_definitions:
        row = facts.get(item.fact_key) or {}
        if complete and not isinstance(row.get("accepted"), bool):
            errors.append(f"missing fact decision for {item.fact_key}")
        if (
            complete
            and row.get("accepted") is True
            and not isinstance(row.get("valid_for_seconds"), (int, float))
            and "no expiry" not in str(row.get("notes", "")).lower()
        ):
            errors.append(
                f"fact {item.fact_key} needs a validity duration or 'no expiry' note"
            )
    if complete and not payload.get("world_branches"):
        errors.append("at least one reviewed world branch is required")
    if complete and not payload.get("information_policies"):
        errors.append("reviewed information policies are required")
    else:
        policies = payload.get("information_policies") or {}
        if complete and not expected_policy_ids <= set(policies):
            errors.append("review is missing a required information policy")
        known_facts = {item.fact_key for item in template.fact_definitions}
        for policy_id, raw in policies.items():
            try:
                policy = InformationPolicySpec.model_validate(raw)
            except Exception as error:
                errors.append(f"invalid information policy {policy_id}: {error}")
                continue
            if complete and policy.deadline_world_time is None:
                errors.append(f"policy {policy_id} has no reviewed deadline")
            if not set(policy.requirement_fact_keys) <= known_facts:
                errors.append(f"policy {policy_id} references undefined facts")
            rejected = {
                key
                for key in policy.requirement_fact_keys
                if (facts.get(key) or {}).get("accepted") is not True
            }
            if rejected:
                errors.append(
                    f"policy {policy_id} uses unaccepted facts: {sorted(rejected)}"
                )
    branches = []
    for index, raw in enumerate(payload.get("world_branches") or []):
        try:
            branches.append(WorldBranchSpec.model_validate(raw))
        except Exception as error:
            errors.append(f"invalid world branch {index}: {error}")
    known_facts = {item.fact_key for item in template.fact_definitions}
    for branch in branches:
        if not set(branch.commitment_fact_keys) <= known_facts:
            errors.append(f"branch {branch.branch_id} references undefined facts")
        rejected = {
            key
            for key in branch.commitment_fact_keys
            if (facts.get(key) or {}).get("accepted") is not True
        }
        if rejected:
            errors.append(
                f"branch {branch.branch_id} uses unaccepted facts: {sorted(rejected)}"
            )
        if not any(item.default for item in branch.alternatives):
            errors.append(
                f"branch {branch.branch_id} needs a default for exhaustive resolution"
            )
        guard_signatures = [
            stable_digest([guard.model_dump(mode="json") for guard in item.guards])
            for item in branch.alternatives
            if not item.default
        ]
        if len(guard_signatures) != len(set(guard_signatures)):
            errors.append(f"branch {branch.branch_id} has duplicate branch guards")
    choices = []
    for index, raw in enumerate(payload.get("choice_groups") or []):
        try:
            choices.append(ChoiceGroupSpec.model_validate(raw))
        except Exception as error:
            errors.append(f"invalid choice group {index}: {error}")
    if complete:
        covered_policies = {item.policy_id for item in choices}
        missing_choices = expected_policy_ids - covered_policies
        if missing_choices:
            errors.append(
                "review is missing choice groups for policies: "
                + ", ".join(sorted(missing_choices))
            )
    dependencies, numeric, timed, scoped = _review_targets(template)
    dependency_rows = payload.get("direct_dependency_decisions") or {}
    for dependency in dependencies:
        row = dependency_rows.get(dependency) or {}
        if complete and row.get("decision") not in {"retain", "remove"}:
            errors.append(f"dependency {dependency} has no retain/remove decision")
        if complete and row.get("decision") == "remove" and not row.get("rationale"):
            errors.append(f"removed dependency {dependency} needs a rationale")
    for field, targets in (
        ("numeric_acceptance_ranges", numeric),
        ("time_windows", timed),
        ("scope_acceptance", scoped),
    ):
        rows = payload.get(field) or {}
        for template_id in targets:
            if complete and (rows.get(template_id) or {}).get("status") not in {
                "accepted",
                "revised",
                "not_applicable",
            }:
                errors.append(f"{field}.{template_id} is incomplete")
    report = {
        "valid": not errors,
        "errors": errors,
        "submission_digest": stable_digest(payload),
        "attestation_digest": expected_attestation,
        "template_digest": stable_digest(template.model_dump(mode="json")),
    }
    if errors:
        raise ValueError("; ".join(errors))
    return report


def compare_submissions(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    validate_submission(left)
    validate_submission(right)
    fields = (
        "module_annotations",
        "transition_annotations",
        "fact_annotations",
        "world_branches",
        "information_policies",
        "choice_groups",
        "numeric_acceptance_ranges",
        "time_windows",
        "scope_acceptance",
        "direct_dependency_decisions",
    )
    left_id = (left.get("reviewer") or {}).get("reviewer_id")
    right_id = (right.get("reviewer") or {}).get("reviewer_id")
    if left_id == right_id:
        raise ValueError("independent submissions must use different reviewer IDs")
    left_values = _flatten({field: left.get(field) for field in fields})
    right_values = _flatten({field: right.get(field) for field in fields})
    paths = sorted(set(left_values) | set(right_values))
    differences = [
        {
            "path": path,
            "left": left_values.get(path),
            "right": right_values.get(path),
            "kind": (
                "numerical"
                if isinstance(left_values.get(path), (int, float))
                and not isinstance(left_values.get(path), bool)
                and isinstance(right_values.get(path), (int, float))
                and not isinstance(right_values.get(path), bool)
                else (
                    "dependency"
                    if path.startswith("direct_dependency_decisions")
                    else "categorical"
                )
            ),
        }
        for path in paths
        if left_values.get(path) != right_values.get(path)
    ]
    disagreements = sorted({item["path"].split(".", 1)[0] for item in differences})
    return {
        "schema_version": "farm_dcore_review_comparison_v1",
        "left_digest": stable_digest(left),
        "right_digest": stable_digest(right),
        "left_reviewer_id": left_id,
        "right_reviewer_id": right_id,
        "agreement_count": len(fields) - len(disagreements),
        "field_count": len(fields),
        "agreement_rate": (len(fields) - len(disagreements)) / len(fields),
        "disagreement_fields": disagreements,
        "unresolved_items": differences,
        "categorical_disagreement_count": sum(
            item["kind"] == "categorical" for item in differences
        ),
        "numerical_disagreement_count": sum(
            item["kind"] == "numerical" for item in differences
        ),
        "dependency_conflict_count": sum(
            item["kind"] == "dependency" for item in differences
        ),
    }


def adjudicate_reviews(
    left: dict[str, Any],
    right: dict[str, Any],
    resolved: dict[str, Any],
    adjudicator: dict[str, Any],
) -> dict[str, Any]:
    comparison = compare_submissions(left, right)
    validate_submission(resolved)
    if not all(
        adjudicator.get(key)
        for key in ("reviewer_id", "expertise_role", "reviewed_at_utc")
    ):
        raise ValueError(
            "adjudicator identity, expertise, and UTC timestamp are required"
        )
    if adjudicator.get("no_final_model_results_seen") is not True:
        raise ValueError("adjudicator must not inspect final model results")
    resolutions = adjudicator.get("resolutions") or {}
    unresolved_paths = {item["path"] for item in comparison["unresolved_items"]} - set(
        resolutions
    )
    if unresolved_paths:
        raise ValueError(
            "adjudication is missing resolutions for: "
            + ", ".join(sorted(unresolved_paths))
        )
    return {
        "schema_version": "farm_dcore_adjudication_v1",
        "status": "adjudicated",
        "source_comparison": comparison,
        "adjudicator": adjudicator,
        "resolution_count": len(resolutions),
        "resolved_specification": resolved,
        "resolved_specification_digest": stable_digest(resolved),
        "unresolved_disagreements": [],
    }


def confirm_adjudication(
    adjudication: dict[str, Any], confirmation: dict[str, Any]
) -> dict[str, Any]:
    if adjudication.get("status") != "adjudicated":
        raise ValueError("input has not been adjudicated")
    expected = adjudication.get("resolved_specification_digest")
    if confirmation.get("confirmed_digest") != expected:
        raise ValueError("third-expert confirmation digest mismatch")
    if not all(
        confirmation.get(key)
        for key in ("reviewer_id", "expertise_role", "confirmed_at_utc")
    ):
        raise ValueError("third-expert identity, expertise, and timestamp are required")
    _require_utc(confirmation["confirmed_at_utc"], "third_expert.confirmed_at_utc")
    source = adjudication.get("source_comparison", {})
    prior_reviewers = {
        source.get("left_reviewer_id"),
        source.get("right_reviewer_id"),
        (adjudication.get("adjudicator") or {}).get("reviewer_id"),
    }
    if confirmation.get("reviewer_id") in prior_reviewers:
        raise ValueError("third-expert confirmer must be independent")
    if confirmation.get("no_final_model_results_seen") is not True:
        raise ValueError("third expert must not inspect final model results")
    if (
        adjudication.get("schema_version") == "farm_team_adjudication_v1"
        and confirmation.get("no_yield_correlation_tuning") is not True
    ):
        raise ValueError("third expert must not tune from yield correlations")
    canonical_confirmation = copy.deepcopy(confirmation)
    canonical_confirmation["digest_attestation"] = None
    expected_attestation = stable_digest(canonical_confirmation)
    if confirmation.get("digest_attestation") != expected_attestation:
        raise ValueError("third-expert confirmation attestation mismatch")
    return {
        **adjudication,
        "schema_version": "farm_dcore_confirmation_v1",
        "status": "confirmed",
        "third_expert_confirmation": confirmation,
    }


def freeze_confirmed_review(
    confirmed: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if confirmed.get("status") != "confirmed":
        raise ValueError("only a third-expert-confirmed review can be frozen")
    resolved = dict(confirmed["resolved_specification"])
    validate_submission(resolved)
    base = wetjune_review_template()
    modules = tuple(
        item.model_copy(
            update={
                "weight_budget": float(
                    resolved["module_annotations"][item.module_id]["weight_budget"]
                ),
                "required": bool(
                    resolved["module_annotations"][item.module_id]["required"]
                ),
            }
        )
        for item in base.modules
    )
    transitions = tuple(
        item.model_copy(
            update={
                "scoring_class": ScoringClass(
                    resolved["transition_annotations"][item.template_id][
                        "scoring_class"
                    ]
                ),
                "within_module_weight": float(
                    resolved["transition_annotations"][item.template_id][
                        "within_module_weight"
                    ]
                ),
                "required": bool(
                    resolved["transition_annotations"][item.template_id]["required"]
                ),
            }
        )
        for item in base.transition_templates
    )
    policies = tuple(
        InformationPolicySpec.model_validate(value)
        for value in resolved["information_policies"].values()
    )
    facts = tuple(
        item.model_copy(
            update={
                "engineering_valid_for": resolved["fact_annotations"][item.fact_key][
                    "valid_for_seconds"
                ],
                "paper_status": "frozen",
            }
        )
        for item in base.fact_definitions
        if resolved["fact_annotations"][item.fact_key]["accepted"] is True
    )
    template = base.model_copy(
        update={
            "modules": modules,
            "transition_templates": transitions,
            "world_branches": tuple(
                WorldBranchSpec.model_validate(value)
                for value in resolved["world_branches"]
            ),
            "information_policies": policies,
            "fact_definitions": facts,
            "choice_groups": tuple(
                ChoiceGroupSpec.model_validate(value)
                for value in resolved.get("choice_groups", [])
            ),
            "expert_review_status": "confirmed",
            "annotation_status": "frozen",
            "metadata": {
                **base.metadata,
                "paper_eligible": True,
                "confirmed_review_digest": stable_digest(confirmed),
            },
        }
    )
    expanded = expand_wetjune_template(
        template, compile_native_petri_net("farm_wetjune_recheck")
    )
    annotated_transitions = []
    for transition in expanded.transitions:
        numeric = resolved["numeric_acceptance_ranges"].get(transition.template_id, {})
        constraints = numeric.get("constraints", {})
        arguments = tuple(
            ArgumentConstraint(
                name=item.name,
                expected=item.expected,
                tolerance=(constraints.get(item.name) or {}).get(
                    "tolerance", item.tolerance
                ),
                critical=(constraints.get(item.name) or {}).get(
                    "critical", item.critical
                ),
                weight=(constraints.get(item.name) or {}).get("weight", item.weight),
            )
            for item in transition.arguments
        )
        timing = resolved["time_windows"].get(transition.template_id, {})
        window = timing.get("window") or {}
        scope = resolved["scope_acceptance"].get(transition.template_id, {})
        acceptance = scope.get("acceptance") or {}
        annotated_transitions.append(
            transition.model_copy(
                update={
                    "arguments": arguments,
                    "window_start": window.get(
                        "start_world_time", transition.window_start
                    ),
                    "window_end": window.get("end_world_time", transition.window_end),
                    "scope_iou_threshold": acceptance.get(
                        "minimum_iou", transition.scope_iou_threshold
                    ),
                }
            )
        )
    expanded = expanded.model_copy(update={"transitions": tuple(annotated_transitions)})
    transition_map = {item.transition_id: item for item in expanded.transitions}
    retained_dependencies = []
    for source, target in sorted(transition_dependencies(expanded)):
        key = f"{transition_map[source].template_id}->{transition_map[target].template_id}"
        if (
            transition_map[source].template_id == transition_map[target].template_id
            or resolved["direct_dependency_decisions"][key]["decision"] == "retain"
        ):
            retained_dependencies.append((source, target))
    predecessors = {item.transition_id: set() for item in expanded.transitions}
    successors = {item.transition_id: set() for item in expanded.transitions}
    for source, target in retained_dependencies:
        predecessors[target].add(source)
        successors[source].add(target)
    places: list[PlaceSpec] = []
    arcs: list[ArcSpec] = []
    for transition_id in sorted(transition_map):
        transition_required = transition_map[transition_id].required
        if not predecessors[transition_id] and (
            transition_required or successors[transition_id]
        ):
            place_id = f"review-initial:{transition_id}"
            places.append(PlaceSpec(place_id=place_id, initially_marked=True))
            arcs.append(
                ArcSpec(
                    arc_id=f"review-arc-initial:{transition_id}",
                    source=place_id,
                    target=transition_id,
                )
            )
        if not successors[transition_id] and (
            transition_required or predecessors[transition_id]
        ):
            place_id = f"review-terminal:{transition_id}"
            places.append(PlaceSpec(place_id=place_id, terminal=True))
            arcs.append(
                ArcSpec(
                    arc_id=f"review-arc-terminal:{transition_id}",
                    source=transition_id,
                    target=place_id,
                )
            )
    for index, (source, target) in enumerate(retained_dependencies):
        place_id = f"review-dependency:{index:04d}"
        places.append(PlaceSpec(place_id=place_id))
        arcs.extend(
            (
                ArcSpec(
                    arc_id=f"review-dependency-in:{index:04d}",
                    source=source,
                    target=place_id,
                ),
                ArcSpec(
                    arc_id=f"review-dependency-out:{index:04d}",
                    source=place_id,
                    target=target,
                ),
            )
        )
    net = expanded.model_copy(
        update={
            "places": tuple(places),
            "arcs": tuple(arcs),
            "exogenous_branches": template.world_branches,
            "choice_groups": template.choice_groups,
            "expert_review_status": "confirmed",
            "metadata": {
                **expanded.metadata,
                "paper_eligible": True,
                "confirmed_review_digest": stable_digest(confirmed),
                "reviewed_dependency_count": len(retained_dependencies),
                "reviewed_acceptance_annotations": True,
            },
        }
    )
    net = PetriNetSpec.model_validate(net.model_dump(mode="json"))
    validate_petri_net(net)
    return template.model_dump(mode="json"), net.model_dump(mode="json")
