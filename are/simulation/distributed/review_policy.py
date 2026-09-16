"""One review contract for author-defined and independent-review artifacts.

An attestation records a human declaration; it is not an identity-verification
service. Tools must never manufacture a professor's approval. Approval is bound
to content digests, so any scientific revision requires another human review.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from are.simulation.distributed.models import stable_digest


def frozen_specification(process: Any) -> bool:
    return bool(
        process.annotation_status == "frozen"
        and process.expert_review_status in {"confirmed", "author_defined"}
        and process.review_digest
        and not process.metadata.get("engineering_defaults")
    )


def validate_review_attestation(
    attestation: dict[str, Any], expected: dict[str, str]
) -> None:
    if attestation.get("route") != "author_defined_professor_approved":
        raise ValueError(
            "author-defined specifications require the professor approval route"
        )
    if attestation.get("approved") is not True:
        raise ValueError("actual professor approval is pending")
    if (
        not str(attestation.get("reviewer_name", "")).strip()
        or attestation.get("reviewer_role") != "professor"
    ):
        raise ValueError("professor approval must identify the human reviewer")
    if not str(attestation.get("statement", "")).strip():
        raise ValueError("professor approval requires an explicit review statement")
    try:
        signed = datetime.fromisoformat(attestation["signed_at"].replace("Z", "+00:00"))
        if signed.tzinfo is None:
            raise ValueError("timezone missing")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("approval requires an ISO timestamp with timezone") from error
    if attestation.get("subject_digests") != expected:
        raise ValueError(
            "professor approval does not bind the exact specification, team, refinement and protocol"
        )
    if any(
        len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
        for value in expected.values()
    ):
        raise ValueError("review subjects must use SHA-256 content digests")


def validate_review_bundle(
    process,
    team,
    refinement,
    protocol_digest,
    attestation=None,
    *,
    stage="release",
    additional_subject_digests: dict[str, str] | None = None,
) -> str:
    if stage not in {"review", "release"}:
        raise ValueError("review stage must be review or release")
    if not frozen_specification(process):
        raise ValueError("a complete frozen specification is required")
    artifacts = [process, team, *([refinement] if refinement is not None else [])]
    if any(
        a.expert_review_status not in {"confirmed", "author_defined"} for a in artifacts
    ):
        raise ValueError("team or refinement authorship/review is incomplete")
    if all(a.expert_review_status == "confirmed" for a in artifacts):
        return "independent_review_confirmed"
    if stage == "review":
        return "author_defined_professor_approval_pending"
    expected = {
        "process": process.digest,
        "team": stable_digest(team.model_dump(mode="json")),
        "protocol": protocol_digest,
    }
    if refinement is not None:
        expected["refinement"] = stable_digest(refinement.model_dump(mode="json"))
    expected.update(additional_subject_digests or {})
    validate_review_attestation(attestation or {}, expected)
    return "author_defined_professor_approved"


def trace_review_approved(process, trace) -> bool:
    from are.simulation.distributed.models import AgentTeamSpec, RoleRefinementSpec

    raw_team = trace.configuration.get("team_spec")
    if (
        process.expert_review_status == "confirmed"
        and (not raw_team or raw_team.get("expert_review_status") == "confirmed")
        and not trace.configuration.get("review_attestation")
    ):
        return frozen_specification(process)
    try:
        configuration = trace.configuration
        team = AgentTeamSpec.model_validate(configuration["team_spec"])
        raw_refinement = configuration.get("role_refinement")
        refinement = (
            RoleRefinementSpec.model_validate(raw_refinement)
            if raw_refinement
            else None
        )
        validate_review_bundle(
            process,
            team,
            refinement,
            configuration["review_protocol_digest"],
            configuration.get("review_attestation"),
        )
        return True
    except (KeyError, TypeError, ValueError):
        return False
