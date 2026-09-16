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
                    target.actor_id == policy.actor_id
                    and target.phase in policy.phases
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


def build_agricultural_review_packets(
    process_paths: tuple[Path, ...], output_dir: Path, *, per_scenario: int = 8
) -> dict[str, Any]:
    processes = [
        FarmProcessSpecV5.model_validate_json(path.read_text(encoding="utf-8"))
        for path in process_paths
    ]
    scenario_ids = [item.scenario_id for item in processes]
    if len(processes) != 3 or len(set(scenario_ids)) != 3:
        raise ValueError("agricultural review requires one base specification per scenario")
    packets = []
    shortfalls = {}
    for process in sorted(processes, key=lambda item: item.scenario_id):
        cases = _review_cases(process)
        shortfalls[process.scenario_id] = max(0, per_scenario - len(cases))
        if len(cases) < per_scenario:
            raise ValueError(
                f"{process.scenario_id} has only {len(cases)} unique "
                f"obligation/prerequisite cases; requested {per_scenario}"
            )
        packets.extend(
            _packet(process, case, index)
            for index, case in enumerate(cases[:per_scenario])
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
