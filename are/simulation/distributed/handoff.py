"""Build the immutable professor handoff after every scientific gate passes."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from are.simulation.distributed.experiments import load_manifest, resolve_manifest
from are.simulation.distributed.models import AgentTeamSpec, RoleRefinementSpec
from are.simulation.distributed.review_policy import (
    frozen_specification,
    validate_review_bundle,
)
from are.simulation.distributed.scientific_v5 import (
    FarmProcessSpecV5,
    ScientificGateManifestV5,
)
from are.simulation.scenarios.scenario_dcore.farm_catalog import FARM_SCENARIOS

EXPECTED_REQUIRED_COUNTS = {
    "primary_pass_1": 480,
    "primary_pass_2": 450,
    "live_verification": 300,
    "reserve": 15,
}


def _placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if "REPLACE_WITH" in value else set()
    if isinstance(value, dict):
        return {item for nested in value.values() for item in _placeholders(nested)}
    if isinstance(value, (list, tuple)):
        return {item for nested in value for item in _placeholders(nested)}
    return set()


def build_professor_handoff(
    *,
    manifests: tuple[Path, ...],
    process_specs: tuple[Path, ...],
    team_specs: tuple[Path, ...],
    role_refinements: tuple[Path, ...],
    gate_manifests: tuple[Path, ...],
    output_dir: Path,
    stage: str = "release",
) -> dict[str, Any]:
    if stage not in {"review", "release"}:
        raise ValueError("handoff stage must be review or release")
    errors: list[str] = []
    resolved: dict[str, list[dict[str, Any]]] = {}
    manifest_payloads: dict[Path, dict[str, Any]] = {}
    for path in manifests:
        try:
            payload = load_manifest(path)
            manifest_payloads[path] = payload
            block = str(payload.get("analysis_block"))
            rows = resolve_manifest(payload)
            resolved[block] = rows
            expected = EXPECTED_REQUIRED_COUNTS.get(block)
            if expected is not None and len(rows) != expected:
                errors.append(f"{block}: expected {expected} rows, found {len(rows)}")
        except Exception as error:
            errors.append(f"{path.name}: {error}")
    missing_blocks = set(EXPECTED_REQUIRED_COUNTS) - set(resolved)
    if missing_blocks:
        errors.append(f"missing required experiment blocks: {sorted(missing_blocks)}")
    processes = []
    process_paths: dict[str, Path] = {}
    for path in process_specs:
        try:
            process = FarmProcessSpecV5.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            processes.append(process)
            process_paths[process.digest] = path
            if not frozen_specification(process):
                errors.append(
                    f"{path.name}: process is not complete, frozen and on an accepted review route"
                )
        except Exception as error:
            errors.append(f"{path.name}: invalid process specification: {error}")
    covered = {item.scenario_id for item in processes}
    if set(FARM_SCENARIOS) - covered:
        errors.append(
            f"missing frozen scenarios: {sorted(set(FARM_SCENARIOS) - covered)}"
        )
    teams = []
    team_paths: dict[str, Path] = {}
    for path in team_specs:
        try:
            team = AgentTeamSpec.model_validate_json(path.read_text(encoding="utf-8"))
            teams.append(team)
            team_paths[team.team_id] = path
            if team.expert_review_status not in {"confirmed", "author_defined"}:
                errors.append(f"{path.name}: team decomposition is unconfirmed")
        except Exception as error:
            errors.append(f"{path.name}: invalid team specification: {error}")
    refinement_paths: dict[str, Path] = {}
    refinements = {}
    for path in role_refinements:
        try:
            refinement = RoleRefinementSpec.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            refinement_paths[refinement.target_team_id] = path
            refinements[refinement.target_team_id] = refinement
            if refinement.expert_review_status not in {"confirmed", "author_defined"}:
                errors.append(f"{path.name}: role refinement is unconfirmed")
        except Exception as error:
            errors.append(f"{path.name}: invalid role refinement: {error}")
    process_digests = {item.digest for item in processes}
    completed_gate_digests = set()
    gate_paths_by_process: dict[str, Path] = {}
    sensitivity_paths: dict[Path, Path] = {}
    repository_root = Path(__file__).parents[3]
    release_subject_paths = {
        "repair_catalogue": repository_root
        / "AAMAS/handover_development/repair_catalogue_v2.json",
        "comparator_lock": repository_root / "AAMAS/comparators/source_lock.json",
        "analysis_contract": repository_root
        / "AAMAS/handover_development/analysis_contract_v2.json",
    }
    release_subject_digests = {
        name: hashlib.sha256(subject.read_bytes()).hexdigest()
        for name, subject in release_subject_paths.items()
        if subject.is_file()
    }
    experiment_manifest_digests = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in manifests
    }
    agricultural_manifest_path = (
        repository_root / "AAMAS/agricultural_review_packets/validation.json"
    )
    agricultural_review_digest = (
        hashlib.sha256(agricultural_manifest_path.read_bytes()).hexdigest()
        if agricultural_manifest_path.is_file()
        else None
    )
    if stage == "release" and agricultural_manifest_path.is_file():
        agricultural_status = json.loads(
            agricultural_manifest_path.read_text(encoding="utf-8")
        )
        if agricultural_status.get("review_complete") is not True:
            errors.append("agricultural review validation is incomplete")
    for path in gate_manifests:
        try:
            gate = ScientificGateManifestV5.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            process = next(
                (p for p in processes if p.digest == gate.confirmed_process_digest),
                None,
            )
            from are.simulation.distributed.models import stable_digest

            team = next(
                (
                    t
                    for t in teams
                    if stable_digest(t.model_dump(mode="json"))
                    == gate.confirmed_team_digest
                ),
                None,
            )
            if process is None or team is None:
                raise ValueError("gate must bind supplied process and team artifacts")
            protocol_digest = hashlib.sha256(
                Path(__file__).with_name("EXPERIMENT_PROTOCOL.md").read_bytes()
            ).hexdigest()
            if gate.analysis_protocol_digest != protocol_digest:
                raise ValueError("gate approval binds a different analysis protocol")
            lock_digest = hashlib.sha256(
                (Path(__file__).parents[3] / "uv.lock").read_bytes()
            ).hexdigest()
            if gate.environment_lock_digest != lock_digest:
                raise ValueError("gate binds a different dependency lock")
            additional_subjects = {}
            if stage == "release":
                required_release_subjects = {
                    "repair_catalogue": gate.repair_catalogue_digest,
                    "comparator_lock": gate.comparator_lock_digest,
                    "analysis_contract": gate.analysis_contract_digest,
                    **{
                        f"experiment_manifest:{name}": digest
                        for name, digest in sorted(
                            gate.experiment_manifest_digests.items()
                        )
                    },
                    "agricultural_review": gate.agricultural_review_digest,
                }
                expected_release_subjects = {
                    **release_subject_digests,
                    **{
                        f"experiment_manifest:{name}": digest
                        for name, digest in sorted(experiment_manifest_digests.items())
                    },
                    "agricultural_review": agricultural_review_digest,
                }
                if required_release_subjects != expected_release_subjects or any(
                    value is None for value in required_release_subjects.values()
                ):
                    raise ValueError(
                        "release approval does not bind the current repair catalogue, "
                        "comparators, analysis contract, manifests and agricultural review"
                    )
                additional_subjects = {
                    key: str(value)
                    for key, value in required_release_subjects.items()
                }
            validate_review_bundle(
                process,
                team,
                refinements.get(team.team_id),
                protocol_digest,
                gate.review_attestation,
                stage=stage,
                additional_subject_digests=additional_subjects,
            )
            sensitivity_path = gate.verify_sensitivity(path)
            if sensitivity_path is not None:
                sensitivity_paths[path] = sensitivity_path
            # This handover protocol requires progression evidence for every
            # retained scenario, including transfer scenarios. Historical gate
            # files remain readable but do not waive this package requirement.
            if (
                gate.status
                not in (
                    {"complete"}
                    if stage == "release"
                    else {"offline_complete", "complete"}
                )
                or not gate.bounded_real_llm_smoke
            ):
                errors.append(f"{path.name}: complete scientific gate is missing")
            elif gate.confirmed_process_digest not in process_digests:
                errors.append(f"{path.name}: gate/process digest mismatch")
            else:
                completed_gate_digests.add(gate.confirmed_process_digest)
                gate_paths_by_process[str(gate.confirmed_process_digest)] = path
        except Exception as error:
            errors.append(f"{path.name}: invalid scientific gate: {error}")
    required_process_digests = {
        item.digest for item in processes if item.scenario_id in FARM_SCENARIOS
    }
    if not required_process_digests <= completed_gate_digests:
        errors.append("not every selected scenario has a completed scientific gate")
    process_by_scenario_size = {
        (item.scenario_id, len(item.occurrence_net.actors)): item for item in processes
    }
    for path, payload in manifest_payloads.items():
        team_ids = payload.get("team_ids") or [
            payload.get("primary_team_id", "wetjune_2agent")
        ]
        payload["team_spec_by_id"] = {
            team_id: f"../specifications/{team_paths[team_id].name}"
            for team_id in team_ids
            if team_id in team_paths
        }
        payload["role_refinement_by_team_id"] = {
            team_id: f"../specifications/{refinement_paths[team_id].name}"
            for team_id in team_ids
            if team_id in refinement_paths
        }
        if len(team_ids) == 1:
            team = next((item for item in teams if item.team_id == team_ids[0]), None)
            team_size = len(team.actors) if team is not None else 2
            selected = {
                scenario: process_by_scenario_size.get((scenario, team_size))
                for scenario in payload.get("scenarios", ())
            }
            payload["petri_spec_by_scenario"] = {
                scenario: f"../specifications/{process_paths[process.digest].name}"
                for scenario, process in selected.items()
                if process is not None
            }
            payload["scientific_gate_by_scenario"] = {
                scenario: f"../gates/{gate_paths_by_process[process.digest].name}"
                for scenario, process in selected.items()
                if process is not None and process.digest in gate_paths_by_process
            }
        else:
            selected_by_team = {}
            for team_id in team_ids:
                team = next((item for item in teams if item.team_id == team_id), None)
                if team is not None:
                    selected_by_team[team_id] = process_by_scenario_size.get(
                        ("farm_wetjune_recheck", len(team.actors))
                    )
            payload["petri_spec_by_team_id"] = {
                team_id: f"../specifications/{process_paths[process.digest].name}"
                for team_id, process in selected_by_team.items()
                if process is not None
            }
            payload["scientific_gate_by_team_id"] = {
                team_id: f"../gates/{gate_paths_by_process[process.digest].name}"
                for team_id, process in selected_by_team.items()
                if process is not None and process.digest in gate_paths_by_process
            }
        payload["paper_mode"] = stage == "release"
        payload["handoff_stage"] = stage
        placeholders = _placeholders(payload)
        if placeholders:
            errors.append(f"{path.name}: unresolved non-review placeholders")
        else:
            try:
                final_rows = resolve_manifest(payload)
                block = str(payload.get("analysis_block"))
                expected = EXPECTED_REQUIRED_COUNTS.get(block)
                if expected is not None and len(final_rows) != expected:
                    errors.append(
                        f"{path.name}: review resolution changed the frozen run count"
                    )
            except Exception as error:
                errors.append(
                    f"{path.name}: resolved paper manifest is invalid: {error}"
                )
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        tags = subprocess.run(
            ["git", "tag", "--points-at", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if dirty:
            errors.append("worktree is dirty; handoff must bind a committed release")
        if not tags:
            errors.append("current commit has no release tag")
    except (OSError, subprocess.CalledProcessError) as error:
        commit, tags = None, []
        errors.append(f"cannot resolve release commit/tag: {error}")
    if errors:
        raise ValueError("professor handoff refused:\n- " + "\n- ".join(errors))
    output_dir.mkdir(parents=True, exist_ok=False)
    for directory in ("manifests", "specifications", "gates", "protocol"):
        (output_dir / directory).mkdir()
    for path, payload in manifest_payloads.items():
        (output_dir / "manifests" / path.name).write_text(
            yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
        )
    for path in (*process_specs, *team_specs, *role_refinements):
        shutil.copy2(path, output_dir / "specifications" / path.name)
    for path in gate_manifests:
        shutil.copy2(path, output_dir / "gates" / path.name)
        if path in sensitivity_paths:
            gate_payload = json.loads(path.read_text(encoding="utf-8"))
            evidence_name = (
                f"sensitivity-{gate_payload['scenario_sensitivity_report_digest']}.json"
            )
            shutil.copy2(sensitivity_paths[path], output_dir / "gates" / evidence_name)
            gate_payload["scenario_sensitivity_report_path"] = evidence_name
            (output_dir / "gates" / path.name).write_text(
                json.dumps(gate_payload, indent=2), encoding="utf-8"
            )
    repository = Path(__file__).parents[3]
    for source in (
        repository / "uv.lock",
        repository / "AAMAS/professor_review/RUNBOOK.md",
        Path(__file__).with_name("EXPERIMENT_PROTOCOL.md"),
        Path(__file__).with_name("SCIENTIFIC_CONTRACT_V5.md"),
    ):
        shutil.copy2(source, output_dir / "protocol" / source.name)
    primary_process_arguments = " ".join(
        f"--process-spec specifications/{process_paths[item.digest].name}"
        for item in sorted(processes, key=lambda process: process.scenario_id)
        if item.scenario_id == "farm_wetjune_recheck"
        and len(item.occurrence_net.actors) == 2
    )
    primary_process = next(
        process
        for process in processes
        if process.scenario_id == "farm_wetjune_recheck"
        and len(process.occurrence_net.actors) == 2
    )
    primary_gate_path = gate_paths_by_process[primary_process.digest]
    primary_gate = ScientificGateManifestV5.model_validate_json(
        primary_gate_path.read_text()
    )
    primary_team = next(
        team
        for team in teams
        if stable_digest(team.model_dump(mode="json"))
        == primary_gate.confirmed_team_digest
    )
    review_arguments = (
        f"--review-stage {stage} "
        f"--team-spec-path specifications/{team_paths[primary_team.team_id].name} "
        f"--review-attestation gates/{primary_gate_path.name}"
    )
    commands = {
        "preflight": "are-dcore preflight manifests/farm_dcore_primary_pass1.yaml --output-dir preflight",
        "pass1": "are-dcore matrix manifests/farm_dcore_primary_pass1.yaml --output-dir results/pass1",
        "pass1_integrity": "are-dcore aggregate results/pass1 --output-dir analysis/pass1",
        "pass2": "are-dcore matrix manifests/farm_dcore_primary_pass2.yaml --output-dir results/pass2",
        "live_verification": "are-dcore matrix manifests/farm_dcore_live_verification.yaml --output-dir results/live-verification",
        "reserve": (
            "DECLARED BUT DISABLED: activate only through a prospectively versioned "
            "manifest before results are opened"
        ),
        "merge_and_aggregate": "are-dcore aggregate results --output-dir analysis/merged --paper-mode",
        "scientific_validation": (
            f"are-dcore validate-spec {primary_process_arguments} "
            f"--require-confirmed {review_arguments} --with-mutants --output-dir analysis/merged"
        ),
        "report": "are-dcore report analysis/merged --output-dir paper_outputs",
    }
    (output_dir / "COMMANDS.json").write_text(
        json.dumps(commands, indent=2), encoding="utf-8"
    )
    inventory = {
        "schema_version": "farm_dcore_professor_handoff_v1",
        "stage": stage,
        "paper_execution_enabled": stage == "release",
        "professor_approval_pending": stage == "review",
        "commit": commit,
        "release_tags": tags,
        "required_run_count": sum(EXPECTED_REQUIRED_COUNTS.values()),
        "llm_seasons": 1200,
        "scripted_oracles": 30,
        "unused_reserve_seasons": 15,
        "independent_annotation_decisions": 120,
        "repair_checkpoints": 60,
        "maximum_repair_suffix_executions": 900,
        "agricultural_review_packets": 24,
        "process_digests": sorted(process_digests),
        "team_ids": sorted(item.team_id for item in teams),
        "files": sorted(
            str(path.relative_to(output_dir))
            for path in output_dir.rglob("*")
            if path.is_file()
        ),
    }
    inventory["inventory_digest"] = hashlib.sha256(
        json.dumps(inventory, sort_keys=True).encode()
    ).hexdigest()
    (output_dir / "HANDOFF_MANIFEST.json").write_text(
        json.dumps(inventory, indent=2), encoding="utf-8"
    )
    return inventory


__all__ = ["EXPECTED_REQUIRED_COUNTS", "build_professor_handoff"]
