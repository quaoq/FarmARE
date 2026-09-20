"""Prospective study declarations that contain no observed D-CORE output."""

from __future__ import annotations

from typing import Any

from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.repair_study import REPAIR_STUDY_CONDITIONS

MECHANISM_ALLOCATION = (
    ("missing_observation", 3),
    ("failed_delivery", 3),
    ("context_omission", 3),
    ("expired_evidence", 3),
    ("incorrect_scope", 3),
    ("valid_no_repair", 3),
    ("late_or_infeasible", 2),
)


def future_repair_checkpoint_manifest() -> dict[str, Any]:
    """Return the frozen 60-slot design; source decisions and labels stay pending."""

    checkpoints = []
    scenarios = (
        "farm_wetjune_recheck",
        "farm_disease_drought",
        "farm_three_cultivar",
    )
    for scenario in scenarios:
        ordinal = 0
        for mechanism, count in MECHANISM_ALLOCATION:
            for within_mechanism in range(count):
                ordinal += 1
                checkpoint_id = f"{scenario}:future:{ordinal:02d}"
                checkpoints.append(
                    {
                        "checkpoint_id": checkpoint_id,
                        "scenario_id": scenario,
                        "allocation": mechanism,
                        "within_mechanism_index": within_mechanism,
                        "source_selection_status": "pending_blind_selection",
                        "label_status": "pending_independent_annotation",
                        "dcore_output_opened": False,
                    }
                )
    payload = {
        "schema_version": "dcore_future_checkpoint_manifest_v1",
        "campaign_id": "professor_full_repair_study_pending",
        "execution_allowed": False,
        "checkpoint_count": len(checkpoints),
        "checkpoints_per_scenario": 20,
        "conditions": list(REPAIR_STUDY_CONDITIONS),
        "suffix_repetitions": 3,
        "assigned_suffixes_after_selection": len(checkpoints)
        * len(REPAIR_STUDY_CONDITIONS)
        * 3,
        "label_freeze_rule": (
            "Independent labels and source checkpoint identities must be frozen "
            "before any D-CORE prediction is opened."
        ),
        "checkpoints": checkpoints,
    }
    return {**payload, "design_digest": stable_digest(payload)}


__all__ = ["future_repair_checkpoint_manifest"]
