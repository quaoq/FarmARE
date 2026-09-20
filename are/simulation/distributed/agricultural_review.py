"""Blind, digest-bound agricultural review packets for authored specifications."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5


def _review_cases(process: FarmProcessSpecV5) -> list[dict[str, Any]]:
    transition_by_id = {
        item.transition_id: item for item in process.occurrence_net.transitions
    }
    cases = []
    for obligation in sorted(
        process.causal_obligations, key=lambda item: item.obligation_id
    ):
        targets = [
            transition_by_id[target]
            for target in obligation.target_transition_ids
            if target in transition_by_id
        ]
        for prerequisite in obligation.prerequisites:
            policies = [
                policy.model_dump(mode="json")
                for policy in sorted(
                    process.information_policies, key=lambda item: item.policy_id
                )
                if any(
                    target.actor_id == policy.actor_id and target.phase in policy.phases
                    for target in targets
                )
            ]
            case = {
                "obligation": obligation.model_dump(mode="json"),
                "prerequisite": prerequisite.model_dump(mode="json"),
                "target_transitions": [
                    target.model_dump(mode="json") for target in targets
                ],
                "applicable_information_policies": policies,
            }
            case["case_digest"] = stable_digest(case)
            cases.append(case)

    # Unload, drying and storage are governed by explicit occurrence-net
    # order rather than by an information fact.  Expose those genuine authored
    # dependencies to reviewers instead of inventing a synthetic sensor fact.
    incoming: dict[str, set[str]] = {}
    for arc in process.occurrence_net.arcs:
        incoming.setdefault(arc.target, set()).add(arc.source)
    transition_ids = set(transition_by_id)

    def predecessor_transitions(target_id: str) -> tuple[str, ...]:
        found: set[str] = set()
        frontier = list(incoming.get(target_id, ()))
        visited: set[str] = set()
        while frontier:
            node = frontier.pop()
            if node in visited:
                continue
            visited.add(node)
            if node in transition_ids:
                found.add(node)
                continue
            frontier.extend(incoming.get(node, ()))
        return tuple(sorted(found))

    postharvest_actions = {
        "TractorApp__unload_grain",
        "FarmWorldApp__dry_grain",
        "FarmWorldApp__store_grain",
    }
    for target in sorted(
        (
            item
            for item in process.occurrence_net.transitions
            if item.action in postharvest_actions
        ),
        key=lambda item: item.transition_id,
    ):
        predecessors = predecessor_transitions(target.transition_id)
        if not predecessors:
            raise ValueError(
                f"{target.transition_id} is a postharvest operation without an "
                "authored predecessor"
            )
        edges = [
            {
                "source_transition_id": predecessor,
                "target_transition_id": target.transition_id,
            }
            for predecessor in predecessors
        ]
        prerequisite = {
            "prerequisite_id": f"transition-order:{target.transition_id}",
            "kind": "transition_order",
            "fact_key": "workflow:predecessor_complete",
            "actor_id": target.actor_id,
            "scope": target.scope,
            "transition_edges": edges,
            "source": "authored_occurrence_net",
        }
        obligation = {
            "obligation_id": f"transition-order:{target.transition_id}",
            "target_transition_ids": [target.transition_id],
            "prerequisites": [prerequisite],
            "source": "authored_occurrence_net",
        }
        policies = [
            policy.model_dump(mode="json")
            for policy in sorted(
                process.information_policies, key=lambda item: item.policy_id
            )
            if target.actor_id == policy.actor_id and target.phase in policy.phases
        ]
        case = {
            "case_type": "transition_order_prerequisite",
            "obligation": obligation,
            "prerequisite": prerequisite,
            "target_transitions": [target.model_dump(mode="json")],
            "predecessor_transitions": [
                transition_by_id[item].model_dump(mode="json") for item in predecessors
            ],
            "applicable_information_policies": policies,
        }
        case["case_digest"] = stable_digest(case)
        cases.append(case)
    unique = {item["case_digest"]: item for item in cases}
    return [unique[key] for key in sorted(unique)]


def _packet(
    process: FarmProcessSpecV5, case: dict[str, Any], index: int
) -> dict[str, Any]:
    return {
        "packet_id": f"{process.scenario_id}:agricultural:{index + 1:02d}",
        "scenario_id": process.scenario_id,
        "process_digest": process.digest,
        "case": case,
        "authored_choices": process.metadata.get("authored_choices", {}),
        "questions": [
            "Are the agricultural facts, units, inclusive ridge scopes, and validity periods defensible?",
            "Are the decision phase and action window agronomically plausible?",
            "Do the listed prerequisites cover the evidence a competent operator needs?",
            "Are permitted responses and negative obligations safe and realistic?",
            "Identify any assumption that requires published support or revision.",
        ],
    }


def _case_tags(scenario_id: str, case: dict[str, Any]) -> set[str]:
    transitions = case["target_transitions"]
    prerequisite = case["prerequisite"]
    actions = {str(item.get("action", "")).split("__")[-1] for item in transitions}
    phases = {str(item.get("phase")) for item in transitions}
    transition_ids = " ".join(
        str(item.get("transition_id", "")) for item in transitions
    )
    tags = {f"phase:{phase}" for phase in phases}
    tags.add(f"mechanism:{str(prerequisite.get('fact_key', '')).split(':', 1)[0]}")
    tags.add(f"scope:{json.dumps(prerequisite.get('scope'), sort_keys=True)}")
    if "plant_seeds" in actions:
        tags.add("required:establishment")
    if "harvest" in actions:
        tags.add("required:harvest")
    if actions & {"apply_fungicide", "spray_pesticide"}:
        tags.add("required:treatment")
    if "irrigate" in actions:
        tags.add("required:irrigation")
    if actions & {"unload_grain", "dry_grain", "store_grain"}:
        tags.add("required:postharvest")
    if scenario_id == "farm_wetjune_recheck" and "reproduction" in phases:
        tags.add("required:wet_june_recheck")
    if scenario_id == "farm_disease_drought":
        if "irrigate" in actions:
            tags.add("required:drought_irrigation")
        if "apply_fungicide" in actions:
            tags.add("required:drought_treatment")
    if scenario_id == "farm_three_cultivar":
        if any(token in transition_ids for token in ("heihe", "heinong")):
            tags.add("required:cultivar_case")
        if prerequisite.get("scope") not in (None, [0, 63], (0, 63)):
            tags.add("required:cultivar_scope")
    if prerequisite.get("transition_edges"):
        tags.add("mechanism:transition_order")
    return tags


def _select_review_cases(
    process: FarmProcessSpecV5,
    cases: list[dict[str, Any]],
    count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tagged = [(case, _case_tags(process.scenario_id, case)) for case in cases]
    available = set().union(*(tags for _, tags in tagged))
    required = {"required:establishment", "required:harvest"}
    if "required:treatment" in available or "required:irrigation" in available:
        required.add(
            "required:treatment"
            if "required:treatment" in available
            else "required:irrigation"
        )
    has_postharvest = any(
        transition.action
        in {
            "TractorApp__unload_grain",
            "FarmWorldApp__dry_grain",
            "FarmWorldApp__store_grain",
        }
        for transition in process.occurrence_net.transitions
    )
    if has_postharvest:
        required.add("required:postharvest")
    required.update(
        tag
        for tag in available
        if tag
        in {
            "required:wet_june_recheck",
            "required:drought_irrigation",
            "required:drought_treatment",
            "required:cultivar_case",
            "required:cultivar_scope",
        }
    )
    missing_source = required - available
    if missing_source:
        raise ValueError(
            f"{process.scenario_id} cannot cover required agricultural cases: "
            f"{sorted(missing_source)}"
        )
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    remaining = list(tagged)
    while remaining and len(selected) < count:

        def score(item: tuple[dict[str, Any], set[str]]) -> tuple[int, int, str]:
            case, tags = item
            required_gain = len((tags & required) - covered)
            diversity_gain = len(
                {
                    tag
                    for tag in tags - covered
                    if tag.startswith(("phase:", "mechanism:", "scope:"))
                }
            )
            return (-required_gain, -diversity_gain, str(case["case_digest"]))

        chosen = min(remaining, key=score)
        remaining.remove(chosen)
        selected.append(chosen[0])
        covered.update(chosen[1])
    missing = required - covered
    if missing:
        raise ValueError(
            f"{process.scenario_id} agricultural packet coverage shortfall: "
            f"{sorted(missing)}"
        )
    return selected, {
        "required_tags": sorted(required),
        "covered_tags": sorted(covered),
        "selection_method": "deterministic_stratified_greedy_set_cover_v1",
    }


def build_agricultural_review_packets(
    process_paths: tuple[Path, ...], output_dir: Path, *, per_scenario: int = 8
) -> dict[str, Any]:
    processes = [
        FarmProcessSpecV5.model_validate_json(path.read_text(encoding="utf-8"))
        for path in process_paths
    ]
    scenario_ids = [item.scenario_id for item in processes]
    if len(processes) != 3 or len(set(scenario_ids)) != 3:
        raise ValueError(
            "agricultural review requires one base specification per scenario"
        )
    packets = []
    shortfalls = {}
    coverage = {}
    for process in sorted(processes, key=lambda item: item.scenario_id):
        cases = _review_cases(process)
        shortfalls[process.scenario_id] = max(0, per_scenario - len(cases))
        if len(cases) < per_scenario:
            raise ValueError(
                f"{process.scenario_id} has only {len(cases)} unique "
                f"obligation/prerequisite cases; requested {per_scenario}"
            )
        selected, coverage[process.scenario_id] = _select_review_cases(
            process, cases, per_scenario
        )
        packets.extend(
            _packet(process, case, index) for index, case in enumerate(selected)
        )
    content_digests = [
        stable_digest(
            {key: value for key, value in packet.items() if key != "packet_id"}
        )
        for packet in packets
    ]
    if len(content_digests) != len(set(content_digests)):
        raise ValueError("agricultural review packet content contains duplicates")
    envelope = {
        "schema_version": "dcore_agricultural_review_packet_v1",
        "reviewer_instructions": (
            "Review independently without consulting the other reviewer. Assess the "
            "authored scientific contract, not model outcomes. Record unknown when "
            "the packet does not justify a determination."
        ),
        "per_scenario": per_scenario,
        "packets": packets,
        "shortfalls": shortfalls,
        "coverage": coverage,
    }
    envelope["packet_digest"] = stable_digest(envelope)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "packets.json").write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    template = {
        "schema_version": "dcore_agricultural_review_submission_v1",
        "packet_digest": envelope["packet_digest"],
        "reviewer_id": None,
        "reviewer_role": "agricultural_reviewer",
        "independent_submission": True,
        "submitted_at": None,
        "reviews": [
            {
                "packet_id": item["packet_id"],
                "determination": None,
                "requested_changes": [],
                "supporting_sources": [],
                "rationale": "",
            }
            for item in packets
        ],
    }
    for name in ("reviewer_a.json", "reviewer_b.json"):
        (output_dir / name).write_text(
            json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    manifest = {
        "schema_version": "dcore_agricultural_review_inventory_v1",
        "packet_digest": envelope["packet_digest"],
        "packet_count": len(packets),
        "process_digests": sorted(item.digest for item in processes),
        "review_complete": False,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def validate_agricultural_reviews(
    packets_path: Path, submissions: tuple[Path, Path]
) -> dict[str, Any]:
    """Validate two complete, independent, digest-bound human submissions."""

    packets = json.loads(packets_path.read_text(encoding="utf-8"))
    packet_digest = packets.get("packet_digest")
    packet_ids = {item["packet_id"] for item in packets.get("packets", ())}
    if len(packet_ids) != 24:
        raise ValueError("the frozen agricultural review requires 24 packets")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in submissions]
    reviewer_ids: list[str] = []
    for payload in payloads:
        if payload.get("packet_digest") != packet_digest:
            raise ValueError("agricultural review packet digest mismatch")
        reviewer_id = payload.get("reviewer_id")
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError("agricultural reviewer identity is missing")
        reviewer_ids.append(reviewer_id.strip())
        if payload.get("independent_submission") is not True:
            raise ValueError("agricultural review must be independently submitted")
        if not payload.get("submitted_at"):
            raise ValueError("agricultural review submission time is missing")
        reviews = payload.get("reviews") or []
        if {item.get("packet_id") for item in reviews} != packet_ids:
            raise ValueError("agricultural review packet coverage is incomplete")
        for item in reviews:
            if item.get("determination") not in {"approve", "revise", "unknown"}:
                raise ValueError("agricultural review determination is invalid")
            if not str(item.get("rationale") or "").strip():
                raise ValueError("agricultural review rationale is missing")
    if reviewer_ids[0] == reviewer_ids[1]:
        raise ValueError("agricultural reviews require two distinct reviewers")
    return {
        "schema_version": "dcore_agricultural_review_validation_v1",
        "packet_digest": packet_digest,
        "review_complete": True,
        "reviewer_count": 2,
        "reviewer_submission_digests": [stable_digest(item) for item in payloads],
        "determination_counts": {
            label: sum(
                review.get("determination") == label
                for payload in payloads
                for review in payload["reviews"]
            )
            for label in ("approve", "revise", "unknown")
        },
        "confirmation_permitted": all(
            review.get("determination") == "approve"
            for payload in payloads
            for review in payload["reviews"]
        ),
    }


__all__ = ["build_agricultural_review_packets", "validate_agricultural_reviews"]
