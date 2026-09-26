"""Manifest-driven diagnosis comparisons over prospectively sampled prefixes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from are.simulation.distributed.evaluation_adapters import (
    build_diagnostic_packet,
    run_adapters,
)
from are.simulation.distributed.journal import DurableRunJournal, load_journal
from are.simulation.distributed.models import stable_digest
from are.simulation.distributed.native_season import NativeDistributedSeasonRunner
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_diagnostic_comparison_study(
    selection_manifest: str | Path,
    methods: tuple[str, ...],
    output_dir: str | Path,
) -> dict[str, Any]:
    """Run every method on each selected decision prefix with durable denominators."""

    if not methods or len(methods) != len(set(methods)):
        raise ValueError("diagnostic study methods must be non-empty and unique")
    manifest_path = Path(selection_manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = list(manifest.get("selected") or ())
    if not selected:
        raise ValueError("diagnostic selection manifest contains no decisions")
    episode_ids = [str(item.get("episode_id")) for item in selected]
    if any(not item for item in episode_ids) or len(set(episode_ids)) != len(
        episode_ids
    ):
        raise ValueError("diagnostic selection requires unique episode IDs")

    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    packet_root = root / "packets"
    packet_root.mkdir(exist_ok=True)
    ledger_path = root / "diagnostic_study_ledger_v1.jsonl"
    manifest_digest = _file_digest(manifest_path)
    existing = load_journal(ledger_path) if ledger_path.is_file() else []
    campaign = next(
        (item for item in existing if item.get("kind") == "campaign_started"), None
    )
    if (
        campaign
        and campaign.get("payload", {}).get("manifest_digest") != manifest_digest
    ):
        raise ValueError("diagnostic output belongs to another selection manifest")
    completed = {
        str(item.get("payload", {}).get("assignment_id")): dict(
            item.get("payload", {}).get("row") or {}
        )
        for item in existing
        if item.get("kind") == "assignment_terminal"
    }
    planned = {
        str(item.get("payload", {}).get("assignment_id"))
        for item in existing
        if item.get("kind") == "assignment_planned"
    }
    journal = DurableRunJournal(ledger_path)
    if journal.sequence == 0:
        journal.append(
            "campaign_started",
            {"manifest_digest": manifest_digest, "methods": methods},
        )
    assignments = [
        {
            "episode_id": episode_id,
            "method": method,
            "assignment_id": stable_digest([manifest_digest, episode_id, method])[:24],
        }
        for episode_id in episode_ids
        for method in methods
    ]
    for assignment in assignments:
        if assignment["assignment_id"] not in planned:
            journal.append("assignment_planned", assignment)
            planned.add(assignment["assignment_id"])

    selected_by_id = {str(item["episode_id"]): item for item in selected}
    packet_by_episode: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for assignment in assignments:
        assignment_id = assignment["assignment_id"]
        if assignment_id in completed:
            rows.append(completed[assignment_id])
            continue
        episode_id = assignment["episode_id"]
        method = assignment["method"]
        selection = selected_by_id[episode_id]
        row: dict[str, Any] = {
            "schema_version": "diagnostic_study_assignment_v1",
            **assignment,
            "campaign_id": stable_digest([manifest_digest, "diagnosis"])[:24],
            "checkpoint_id": episode_id,
            "decision_id": str(selection.get("decision_id") or ""),
        }
        try:
            trace_path = Path(str(selection["trace"])).resolve()
            process_path = Path(str(selection["process"])).resolve()
            if _file_digest(trace_path) != selection.get("trace_sha256"):
                raise ValueError("selected trace digest changed")
            if _file_digest(process_path) != selection.get("process_sha256"):
                raise ValueError("selected process digest changed")
            if trace_path.parent != process_path.parent:
                raise ValueError("selected trace and process are from different runs")
            packet = packet_by_episode.get(episode_id)
            if packet is None:
                process = FarmProcessSpecV5.model_validate_json(
                    process_path.read_text(encoding="utf-8")
                )
                packet = build_diagnostic_packet(
                    trace_path.parent,
                    public_task_contract=NativeDistributedSeasonRunner._task_briefing(
                        process.scenario_id, process
                    ),
                    prefix_decision_id=row["decision_id"],
                    include_outcome=False,
                    campaign_id=row["campaign_id"],
                    checkpoint_id=episode_id,
                )
                packet_by_episode[episode_id] = packet
                (packet_root / f"{episode_id}.json").write_text(
                    json.dumps(packet.model_dump(mode="json"), indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
            result = run_adapters(packet, (method,))[0]
            row.update(
                {
                    "packet_digest": packet.packet_digest,
                    "scenario_id": packet.scenario_id,
                    "run_id": packet.run_id,
                    "terminal_status": "completed",
                    "result": result.model_dump(mode="json"),
                }
            )
        except Exception as error:
            row.update(
                {
                    "terminal_status": "method_failure",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
        rows.append(row)
        journal.append(
            "assignment_terminal", {"assignment_id": assignment_id, "row": row}
        )
    result = {
        "schema_version": "diagnostic_comparison_study_v1",
        "selection_manifest_digest": manifest_digest,
        "episodes": len(selected),
        "methods": methods,
        "assigned": len(assignments),
        "completed": sum(row["terminal_status"] == "completed" for row in rows),
        "failed": sum(row["terminal_status"] != "completed" for row in rows),
        "assignments": rows,
    }
    (root / "diagnostic_study_results_v1.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


__all__ = ["run_diagnostic_comparison_study"]
