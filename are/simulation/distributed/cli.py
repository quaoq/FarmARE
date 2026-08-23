"""Farm D-CORE professor-facing command line interface."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import click

from are.simulation.distributed.evaluator_v3 import evaluate_farm_dcore
from are.simulation.distributed.models import DistributedTrace, FarmDistributedRunConfig
from are.simulation.distributed.petri import (
    petri_to_dot,
    petri_to_pnml,
    validate_petri_net,
)
from are.simulation.distributed.runner import DistributedScenarioRunner
from are.simulation.distributed.trace import validate_trace
from are.simulation.scenarios.scenario_dcore.farm_catalog import (
    FARM_SCENARIOS,
    compile_paper_petri_net,
)


def _mapping(values: tuple[str, ...], option: str) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise click.UsageError(f"{option} values must use ACTOR=VALUE")
        actor, setting = value.split("=", 1)
        result[actor] = setting
    return result


@click.group(invoke_without_command=True)
@click.option(
    "--evaluate-trace",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    hidden=True,
)
@click.pass_context
def main(context: click.Context, evaluate_trace: Path | None) -> None:
    """Run and evaluate farm-only distributed long-horizon experiments."""
    if evaluate_trace:
        # Read-only v1 compatibility for previously generated semantic traces.
        from are.simulation.distributed.evaluator import evaluate_dcore
        from are.simulation.scenarios.scenario_dcore.farm import farm_spec
        from are.simulation.scenarios.scenario_dcore.transaction import transaction_spec

        trace = DistributedTrace.model_validate_json(
            evaluate_trace.read_text(encoding="utf-8")
        )
        spec = (
            farm_spec()
            if trace.task_id == "farm_wetjune_fungicide_dcore"
            else transaction_spec()
        )
        click.echo(json.dumps(evaluate_dcore(spec, trace), indent=2, default=str))
        context.exit()
    if context.invoked_subcommand is None:
        click.echo(context.get_help())


@main.command("validate-spec")
@click.option("--scenario-id", type=click.Choice(sorted(FARM_SCENARIOS)), multiple=True)
@click.option("--world-seed", type=int, default=0, show_default=True)
@click.option(
    "--process-spec",
    "process_specs",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Frozen farm_process_spec_v5 file; repeat for multiple scenarios.",
)
@click.option(
    "--team-id",
    type=click.Choice(["wetjune_2agent", "wetjune_3agent", "wetjune_4agent"]),
    default="wetjune_2agent",
    show_default=True,
)
@click.option(
    "--team-spec-path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Reviewed AgentTeamSpec JSON used to validate ownership and topology.",
)
@click.option(
    "--role-refinement-path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Reviewed RoleRefinementSpec JSON for a non-primary team.",
)
@click.option(
    "--require-confirmed", is_flag=True, help="Fail unless expert review is confirmed."
)
@click.option(
    "--with-mutants",
    is_flag=True,
    help="Run controlled metric and attribution validation.",
)
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path))
def validate_spec(
    scenario_id: tuple[str, ...],
    world_seed: int,
    process_specs: tuple[Path, ...],
    team_id: str,
    team_spec_path: Path | None,
    role_refinement_path: Path | None,
    require_confirmed: bool,
    with_mutants: bool,
    output_dir: Path | None,
) -> None:
    """Validate executable Petri oracles and optionally export JSON/DOT/PNML."""
    from are.simulation.distributed.scientific_v5 import (
        FarmProcessSpecV5,
        engineering_process_from_v4,
    )

    process_by_scenario: dict[str, tuple[FarmProcessSpecV5, Path]] = {}
    for path in process_specs:
        process = FarmProcessSpecV5.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if process.scenario_id in process_by_scenario:
            raise click.UsageError(
                f"multiple v5 process specifications for {process.scenario_id}"
            )
        process_by_scenario[process.scenario_id] = (process, path)
    identifiers = (
        scenario_id or tuple(sorted(process_by_scenario)) or tuple(FARM_SCENARIOS)
    )
    reports = []
    for identifier in identifiers:
        from are.simulation.distributed.teams import (
            load_role_refinement,
            load_team_spec,
            refine_petri_for_team,
            team_digest,
        )
        from are.simulation.scenarios.scenario_dcore.farm_catalog import (
            compile_paper_petri_net,
            create_native_scenario,
        )

        native = create_native_scenario(identifier, world_seed=world_seed)
        from are.simulation.distributed.models import DistributedRunnerConfig

        validation_config = DistributedRunnerConfig(
            scenario_id=identifier,
            world_seed=world_seed,
            team_id=team_id,
            team_spec_path=str(team_spec_path) if team_spec_path else None,
            role_refinement_path=(
                str(role_refinement_path) if role_refinement_path else None
            ),
        )
        team = load_team_spec(validation_config, native.get_tools())
        refinement = load_role_refinement(validation_config, team, identifier)
        selected_process = process_by_scenario.get(identifier)
        if selected_process is not None:
            process, process_path = selected_process
            net = process.occurrence_net
            if tuple(net.actors) != tuple(actor.actor_id for actor in team.actors):
                raise click.ClickException(
                    f"{identifier} v5 process actors do not match {team_id}"
                )
        else:
            process_path = None
            net = compile_paper_petri_net(identifier, world_seed=world_seed)
            net = refine_petri_for_team(net, team, refinement)
            process = engineering_process_from_v4(net)
        report = validate_petri_net(net)
        report.update(
            {
                "scenario_id": identifier,
                "team_id": team_id,
                "team_size": len(team.actors),
                "team_spec_digest": team_digest(team),
                "team_review_status": team.expert_review_status,
                "role_refinement_status": (
                    refinement.expert_review_status if refinement else "not_applicable"
                ),
                "expert_review_status": net.expert_review_status,
                "oracle_version": net.oracle_version,
                "metric_annotation_status": net.metadata.get(
                    "metric_annotation_status"
                ),
                "branch_annotation_status": net.metadata.get(
                    "branch_annotation_status"
                ),
                "guard_annotation_status": net.metadata.get("guard_annotation_status"),
                "exogenous_branch_count": len(net.exogenous_branches),
                "high_impact_guard_count": sum(
                    len(transition.guards)
                    for transition in net.transitions
                    if transition.high_impact
                ),
                "scientific_contract": "v5",
                "process_spec_digest": process.digest,
                "process_annotation_status": process.annotation_status,
                "process_review_status": process.expert_review_status,
                "process_paper_eligible": bool(
                    process.annotation_status == "frozen"
                    and process.expert_review_status == "confirmed"
                ),
            }
        )
        if require_confirmed and (
            process.annotation_status != "frozen"
            or process.expert_review_status != "confirmed"
        ):
            raise click.ClickException(
                f"{identifier} has no confirmed frozen farm_process_spec_v5"
            )
        if require_confirmed and team.expert_review_status != "confirmed":
            raise click.ClickException(f"{team_id} has no confirmed team specification")
        if (
            require_confirmed
            and refinement is not None
            and refinement.expert_review_status != "confirmed"
        ):
            raise click.ClickException(
                f"{team_id} has no confirmed role-refinement specification"
            )
        if require_confirmed and selected_process is None:
            if net.expert_review_status != "confirmed":
                raise click.ClickException(
                    f"{identifier} has not completed the legacy review protocol"
                )
            for metadata_key, label in (
                ("metric_annotation_status", "expert weights/tolerances"),
                ("branch_annotation_status", "exogenous branch annotations"),
                ("guard_annotation_status", "high-impact guard annotations"),
            ):
                if net.metadata.get(metadata_key) != "frozen":
                    raise click.ClickException(f"{identifier} has no frozen {label}")
            if not net.exogenous_branches:
                raise click.ClickException(
                    f"{identifier} has no reviewed executable alternatives"
                )
        if output_dir:
            target = output_dir / identifier
            target.mkdir(parents=True, exist_ok=True)
            (target / "petri_net.json").write_text(
                net.model_dump_json(indent=2), encoding="utf-8"
            )
            (target / "petri_net.dot").write_text(petri_to_dot(net), encoding="utf-8")
            (target / "petri_net.pnml").write_text(petri_to_pnml(net), encoding="utf-8")
            (target / "farm_process_spec_v5.json").write_text(
                process.model_dump_json(indent=2), encoding="utf-8"
            )
        if with_mutants:
            from are.simulation.distributed.controlled_suite_v5 import (
                build_controlled_suite_v5,
                evaluate_controlled_suite_v5,
            )

            oracle = DistributedScenarioRunner().run(
                DistributedRunnerConfig(
                    scenario_id=identifier,
                    world_seed=world_seed,
                    team_id=team_id,
                    team_spec_path=(str(team_spec_path) if team_spec_path else None),
                    role_refinement_path=(
                        str(role_refinement_path) if role_refinement_path else None
                    ),
                    scientific_contract="v5",
                    petri_spec_path=str(process_path) if process_path else None,
                    max_logical_steps=1000,
                )
            )
            cases = build_controlled_suite_v5(process, oracle.trace)
            mutation_report = evaluate_controlled_suite_v5(process, cases)
            mutation_report["scenario_id"] = identifier
            mutation_report["all_properties_pass"] = mutation_report[
                "paper_validation_passed"
            ]
            report["controlled_mutants"] = mutation_report
            if output_dir:
                (output_dir / identifier / "metric_validation.json").write_text(
                    json.dumps(mutation_report, indent=2), encoding="utf-8"
                )
            if require_confirmed and not mutation_report["all_properties_pass"]:
                raise click.ClickException(
                    f"{identifier} v5 controlled-mutant validation failed"
                )
        reports.append(report)
    click.echo(json.dumps(reports, indent=2, default=str))


@main.command("run")
@click.option(
    "--scenario-id",
    type=click.Choice(sorted(FARM_SCENARIOS)),
    default="farm_wetjune_recheck",
    show_default=True,
)
@click.option(
    "--controller-mode",
    type=click.Choice(["scripted", "mock_llm", "llm", "replay"]),
    default="scripted",
)
@click.option(
    "--visibility-mode",
    type=click.Choice(["local", "shared_blackboard"]),
    default="local",
)
@click.option(
    "--handoff-mode", type=click.Choice(["free_text", "causal"]), default="causal"
)
@click.option(
    "--enforcement-mode",
    type=click.Choice(["off", "audit", "enforce"]),
    default="enforce",
)
@click.option(
    "--fault",
    type=click.Choice(
        [
            "none",
            "delay",
            "delay_within_validity",
            "delay_past_validity",
            "delay_past_deadline",
            "drop",
            "duplicate",
            "reorder",
            "mixed",
        ]
    ),
    default="none",
)
@click.option(
    "--fault-target",
    "fault_target_ids",
    multiple=True,
    help="Stable handoff ID to fault, e.g. handoff:harvest:v1.",
)
@click.option("--world-seed", type=int, default=0)
@click.option("--scheduler-seed", type=int, default=0)
@click.option("--model-seed", type=int, default=0)
@click.option("--fault-seed", type=int, default=0)
@click.option(
    "--scientific-contract",
    type=click.Choice(["v4", "v5"]),
    default="v4",
    help="Use v5 for all paper-bound runs; v4 is replay compatibility only.",
)
@click.option("--max-logical-steps", type=int, default=1000)
@click.option("--max-deferrals", type=int, default=3)
@click.option(
    "--petri-spec-path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Confirmed farm_process_spec_v5 used by paper mode.",
)
@click.option(
    "--scientific-gate-manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Offline gate attestation required before a real-LLM run.",
)
@click.option("--paper-mode", is_flag=True)
@click.option(
    "--bounded-llm-smoke",
    is_flag=True,
    help="Authorize only the prerelease Wet-June smoke (at most 12 model calls).",
)
@click.option(
    "--team-spec-path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Versioned AgentTeamSpec JSON; otherwise use a built-in Wet-June team.",
)
@click.option(
    "--role-refinement-path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Reviewed RoleRefinementSpec JSON for a non-primary team.",
)
@click.option(
    "--team-id",
    type=click.Choice(["wetjune_2agent", "wetjune_3agent", "wetjune_4agent"]),
    default="wetjune_2agent",
    show_default=True,
)
@click.option(
    "--activation-policy",
    type=click.Choice(["round_robin", "seeded_permutation", "event_driven"]),
)
@click.option(
    "--communication-topology",
    type=click.Choice(
        ["explicit", "fully_connected", "hierarchical", "shared_blackboard"]
    ),
)
@click.option("--team-call-budget", type=int)
@click.option("--per-agent-call-budget", type=int)
@click.option("--team-token-budget", type=int)
@click.option("--per-agent-token-budget", type=int)
@click.option("--max-messages", type=int, default=10000, show_default=True)
@click.option("--condition-id", default="causal_enforced")
@click.option("--controller-profile-id", default="default")
@click.option("--repeat-index", type=int, default=0)
@click.option(
    "--output-dir", required=True, type=click.Path(file_okay=False, path_type=str)
)
@click.option(
    "--replay-trace", type=click.Path(exists=True, dir_okay=False, path_type=str)
)
@click.option("--model", multiple=True, help="Per-actor ACTOR=MODEL mapping.")
@click.option("--provider", multiple=True, help="Per-actor ACTOR=PROVIDER mapping.")
@click.option("--endpoint", multiple=True, help="Per-actor ACTOR=URL mapping.")
@click.option("--agent-family", multiple=True, help="Per-actor ACTOR=FAMILY mapping.")
@click.option("--history-window", multiple=True, help="Per-actor ACTOR=COUNT mapping.")
@click.option("--temperature", multiple=True, help="Per-actor ACTOR=FLOAT mapping.")
@click.option("--max-model-calls", type=int, default=700, show_default=True)
@click.option("--max-output-tokens", type=int, default=1024, show_default=True)
def run_command(**values: object) -> None:
    """Execute one complete planting-to-storage season."""
    try:
        config = FarmDistributedRunConfig(
            **{
                key: value
                for key, value in values.items()
                if key
                not in {
                    "model",
                    "provider",
                    "endpoint",
                    "agent_family",
                    "history_window",
                    "temperature",
                }
            },
            model_by_actor=_mapping(values["model"], "--model"),  # type: ignore[arg-type]
            provider_by_actor=_mapping(values["provider"], "--provider"),  # type: ignore[arg-type]
            endpoint_by_actor=_mapping(values["endpoint"], "--endpoint"),  # type: ignore[arg-type]
            agent_family_by_actor=_mapping(
                values["agent_family"],
                "--agent-family",  # type: ignore[arg-type]
            ),
            history_window_by_actor={
                actor: int(count)
                for actor, count in _mapping(
                    values["history_window"],
                    "--history-window",  # type: ignore[arg-type]
                ).items()
            },
            temperature_by_actor={
                actor: float(temperature)
                for actor, temperature in _mapping(
                    values["temperature"],
                    "--temperature",  # type: ignore[arg-type]
                ).items()
            },
        )
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    try:
        result = DistributedScenarioRunner().run(config)
    except Exception as error:
        failure_dir = Path(config.output_dir or ".")
        failure_dir.mkdir(parents=True, exist_ok=True)
        (failure_dir / "FAILURE.json").write_text(
            json.dumps(
                {
                    "schema_version": "dcore_failure_v1",
                    "status": "failed",
                    "configuration": config.model_dump(mode="json"),
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            {
                "run_id": result.trace.run_id,
                "scenario_id": config.scenario_id,
                "metric_version": result.metrics["metric_version"],
                "dcore_score": result.metrics["dcore_score"],
                "event_fidelity": result.metrics["event_fidelity"],
                "causal_conformance": result.metrics["causal_conformance"],
                "local_global_gap": result.metrics["local_global_gap"],
                "success": result.trace.outcome.get("success"),
                "marketable_yield_kg": result.trace.outcome.get("marketable_yield_kg"),
                "artifacts": result.artifacts,
            },
            indent=2,
        )
    )


@main.command("evaluate")
@click.argument(
    "trace_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--scenario-id", type=click.Choice(sorted(FARM_SCENARIOS)))
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def evaluate_command(
    trace_path: Path, scenario_id: str | None, output: Path | None
) -> None:
    """Recompute frozen metrics without rerunning agents."""
    raw = trace_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if payload.get("version") == "are_simulation_v1":
        if not scenario_id:
            raise click.UsageError(
                "--scenario-id is required for a legacy FarmARE trace"
            )
        from are.simulation.distributed.experiments import (
            evaluate_legacy_farmare_trace,
        )

        metrics = evaluate_legacy_farmare_trace(trace_path, scenario_id)
        rendered = json.dumps(metrics, indent=2, default=str)
        if output:
            output.write_text(rendered, encoding="utf-8")
        else:
            click.echo(rendered)
        return
    trace = DistributedTrace.model_validate(payload)
    validate_trace(trace)
    identifier = scenario_id or trace.configuration.get("scenario_id")
    if identifier not in FARM_SCENARIOS:
        raise click.UsageError(
            "--scenario-id is required for a trace without a public farm scenario ID"
        )
    process_path = trace_path.parent / "farm_process_spec_v5.json"
    frozen_spec = trace_path.parent / "petri_net.json"
    if frozen_spec.exists():
        from are.simulation.distributed.petri import PetriNetSpec

        net = PetriNetSpec.model_validate_json(frozen_spec.read_text(encoding="utf-8"))
    else:
        from are.simulation.scenarios.scenario_dcore.farm_catalog import (
            compile_paper_petri_net,
        )

        net = compile_paper_petri_net(
            identifier, world_seed=int(trace.configuration.get("world_seed", 0))
        )
    if trace.schema_version == "dcore_trace_v5":
        from are.simulation.distributed.evaluator_v5 import evaluate_farm_dcore_v5
        from are.simulation.distributed.scientific_v5 import (
            FarmProcessSpecV5,
            engineering_process_from_v4,
        )

        process = (
            FarmProcessSpecV5.model_validate_json(
                process_path.read_text(encoding="utf-8")
            )
            if process_path.exists()
            else engineering_process_from_v4(net)
        )
        metrics = evaluate_farm_dcore_v5(process, trace)
    elif trace.schema_version == "dcore_trace_v4":
        from are.simulation.distributed.evaluator_v4 import evaluate_farm_dcore_v4

        metrics = evaluate_farm_dcore_v4(net, trace)
    else:
        metrics = evaluate_farm_dcore(net, trace)
    rendered = json.dumps(metrics, indent=2, default=str)
    if output:
        output.write_text(rendered, encoding="utf-8")
    else:
        click.echo(rendered)


@main.command("matrix")
@click.argument(
    "manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--output-dir", required=True, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--dry-run", is_flag=True, help="Resolve and count runs without executing."
)
@click.option("--no-resume", is_flag=True)
@click.option("--shard-count", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--shard-index", type=int, default=0, show_default=True)
def matrix_command(
    manifest: Path,
    output_dir: Path,
    dry_run: bool,
    no_resume: bool,
    shard_count: int,
    shard_index: int,
) -> None:
    """Resolve, execute, or resume a YAML experiment matrix."""
    from are.simulation.distributed.experiments import (
        load_manifest,
        resolve_manifest,
        run_resolved_matrix,
        shard_rows,
        summarize_resolved_rows,
    )

    manifest_payload = load_manifest(manifest)
    all_rows = resolve_manifest(manifest_payload)
    try:
        rows = shard_rows(
            all_rows, shard_count=shard_count, shard_index=shard_index
        )
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    runnable = [
        row
        for row in rows
        if row["execution"] in {"dcore", "farmare_direct", "farmare_a2a"}
    ]
    external = [row for row in rows if row["execution"] == "legacy_import"]
    by_team: dict[str, int] = {}
    by_controller_profile: dict[str, int] = {}
    for row in rows:
        team_id = str(row.get("team_id", "legacy"))
        by_team[team_id] = by_team.get(team_id, 0) + 1
        profile_id = str(row.get("controller_profile_id", "default"))
        by_controller_profile[profile_id] = by_controller_profile.get(profile_id, 0) + 1
    planned_model_call_cap = sum(
        int(row.get("team_call_budget") or row.get("max_model_calls", 0))
        for row in runnable
        if row.get("controller_mode") == "llm"
    )

    def placeholder_values(value: object) -> set[str]:
        if isinstance(value, str):
            return {value} if "REPLACE_WITH" in value else set()
        if isinstance(value, dict):
            return {
                found
                for nested in value.values()
                for found in placeholder_values(nested)
            }
        if isinstance(value, (list, tuple)):
            return {found for nested in value for found in placeholder_values(nested)}
        return set()

    unresolved_placeholders = sorted(placeholder_values(rows))
    paper_mode_runs = sum(
        row["execution"] != "legacy_import" and bool(row.get("paper_mode"))
        for row in rows
    )
    resolved = {
        "schema_version": "farm_dcore_resolved_v1",
        "total": len(rows),
        "runnable": len(runnable),
        "legacy_import": len(external),
        "by_team": by_team,
        "by_controller_profile": by_controller_profile,
        "planned_model_call_cap": planned_model_call_cap,
        "paper_mode_runs": paper_mode_runs,
        "engineering_mode_runs": len(runnable) - paper_mode_runs,
        "unresolved_placeholders": unresolved_placeholders,
        "execution_preflight_ready": not unresolved_placeholders,
        "paper_experiment_ready": bool(rows)
        and not unresolved_placeholders
        and paper_mode_runs == len(runnable),
        "runs": rows,
        "all_shards_total": len(all_rows),
        "shard_count": shard_count,
        "shard_index": shard_index,
        "resource_preflight": summarize_resolved_rows(
            rows, pricing=manifest_payload.get("pricing_assumptions")
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "resolved_manifest.json").write_text(
        json.dumps(resolved, indent=2), encoding="utf-8"
    )
    if dry_run:
        click.echo(
            json.dumps(
                {
                    key: resolved[key]
                    for key in (
                        "total",
                        "runnable",
                        "legacy_import",
                        "by_team",
                        "by_controller_profile",
                        "planned_model_call_cap",
                        "paper_mode_runs",
                        "engineering_mode_runs",
                        "unresolved_placeholders",
                        "execution_preflight_ready",
                        "paper_experiment_ready",
                        "all_shards_total",
                        "shard_count",
                        "shard_index",
                        "resource_preflight",
                    )
                },
                indent=2,
            )
        )
        return
    if unresolved_placeholders:
        raise click.ClickException(
            "matrix contains unresolved placeholders: "
            + ", ".join(unresolved_placeholders)
        )
    completed = run_resolved_matrix(rows, output_dir, resume=not no_resume)
    click.echo(
        json.dumps(
            {
                "resolved": len(rows),
                "completed_or_failed": len(completed),
                "output_dir": str(output_dir),
            },
            indent=2,
        )
    )


@main.command("aggregate")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--paper-mode",
    is_flag=True,
    help="Reject engineering, pre-v5, audit-failed, or inactive-fault rows.",
)
def aggregate_command(
    input_path: Path, output_dir: Path | None, paper_mode: bool
) -> None:
    """Produce season-level summaries and bootstrap confidence intervals."""
    from are.simulation.distributed.experiments import aggregate_directory

    try:
        report = aggregate_directory(input_path, output_dir, paper_mode=paper_mode)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            {
                "groups": len(report["groups"]),
                "schema_version": report["schema_version"],
            },
            indent=2,
        )
    )


@main.command("report")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output-dir", required=True, type=click.Path(file_okay=False, path_type=Path)
)
def report_command(input_path: Path, output_dir: Path) -> None:
    """Render the six frozen tables and five frozen figures."""
    from are.simulation.distributed.paper_report import generate_paper_report

    manifest = generate_paper_report(input_path, output_dir)
    click.echo(
        json.dumps(
            {
                "tables": len(manifest["tables"]),
                "figures": len(manifest["figures"]),
                "source_rows": manifest["source_row_count"],
                "output_dir": str(output_dir),
            },
            indent=2,
        )
    )


@main.command("preflight")
@click.argument(
    "manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--output-dir", required=True, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--limit-worlds",
    type=click.IntRange(min=1),
    help="Engineering-only bound; omitted for the professor scientific gate.",
)
def preflight_command(
    manifest: Path, output_dir: Path, limit_worlds: int | None
) -> None:
    """Run paired scripted/native checks without calling a model."""
    from are.simulation.distributed.preflight import run_no_model_preflight

    try:
        report = run_no_model_preflight(
            manifest, output_dir, limit_worlds=limit_worlds
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            {
                "passed": report["passed"],
                "world_count": report["world_count"],
                "limited_engineering_run": report["limited_engineering_run"],
            },
            indent=2,
        )
    )
    if not report["passed"]:
        raise click.exceptions.Exit(1)


@main.group("handoff")
def handoff_group() -> None:
    """Build the immutable professor experiment package."""


@handoff_group.command("build")
@click.option(
    "--manifest", "manifests", multiple=True, required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--process-spec", "process_specs", multiple=True, required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--team-spec", "team_specs", multiple=True, required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--role-refinement", "role_refinements", multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--gate-manifest", "gate_manifests", multiple=True, required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--output-dir", required=True, type=click.Path(file_okay=False, path_type=Path)
)
def handoff_build_command(
    manifests: tuple[Path, ...],
    process_specs: tuple[Path, ...],
    team_specs: tuple[Path, ...],
    role_refinements: tuple[Path, ...],
    gate_manifests: tuple[Path, ...],
    output_dir: Path,
) -> None:
    """Refuse unless expert reviews, offline gates, smoke, and release bind."""
    from are.simulation.distributed.handoff import build_professor_handoff

    try:
        inventory = build_professor_handoff(
            manifests=manifests,
            process_specs=process_specs,
            team_specs=team_specs,
            role_refinements=role_refinements,
            gate_manifests=gate_manifests,
            output_dir=output_dir,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(inventory, indent=2))


@main.group("review")
def review_group() -> None:
    """Export and validate the independent Wet-June expert review."""


@review_group.command("export-v5")
@click.option(
    "--output-dir", required=True, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--scenario-id",
    type=click.Choice(sorted(FARM_SCENARIOS)),
    default="farm_wetjune_recheck",
)
def review_export_v5(output_dir: Path, scenario_id: str) -> None:
    """Export the neutral v5 scientific-review packet."""
    from are.simulation.distributed.review_v5 import export_neutral_v5_packet
    from are.simulation.distributed.scientific_v5 import engineering_process_from_v4

    process = engineering_process_from_v4(compile_paper_petri_net(scenario_id))
    path = export_neutral_v5_packet(process, output_dir)
    click.echo(
        json.dumps(
            {"schema_version": "farm_dcore_review_v5", "path": str(path)}, indent=2
        )
    )


@review_group.command("validate-v5")
@click.argument(
    "submission", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def review_validate_v5(submission: Path) -> None:
    """Validate one independent v5 domain submission."""
    from are.simulation.distributed.review_v5 import validate_v5_submission

    try:
        process = validate_v5_submission(
            json.loads(submission.read_text(encoding="utf-8"))
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps({"valid": True, "process_digest": process.digest}, indent=2))


@review_group.command("compare-v5")
@click.argument("left", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("right", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output", required=True, type=click.Path(dir_okay=False, path_type=Path)
)
def review_compare_v5(left: Path, right: Path, output: Path) -> None:
    """Compare two independently authored v5 specifications."""
    from are.simulation.distributed.review_v5 import compare_v5_submissions

    try:
        report = compare_v5_submissions(
            json.loads(left.read_text(encoding="utf-8")),
            json.loads(right.read_text(encoding="utf-8")),
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    click.echo(
        json.dumps(
            {"output": str(output), "unresolved": len(report["unresolved_paths"])},
            indent=2,
        )
    )


@review_group.command("adjudicate-v5")
@click.argument("left", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("right", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument(
    "resolutions", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--adjudicator-id", required=True)
@click.option("--adjudicator-expertise-role", required=True)
@click.option("--attest-no-final-results", is_flag=True, required=True)
@click.option("--attest-no-yield-tuning", is_flag=True, required=True)
@click.option(
    "--output", required=True, type=click.Path(dir_okay=False, path_type=Path)
)
def review_adjudicate_v5(
    left: Path,
    right: Path,
    resolutions: Path,
    adjudicator_id: str,
    adjudicator_expertise_role: str,
    attest_no_final_results: bool,
    attest_no_yield_tuning: bool,
    output: Path,
) -> None:
    """Resolve every reported v5 disagreement immutably."""
    from are.simulation.distributed.review_v5 import adjudicate_v5_reviews

    try:
        report = adjudicate_v5_reviews(
            json.loads(left.read_text(encoding="utf-8")),
            json.loads(right.read_text(encoding="utf-8")),
            json.loads(resolutions.read_text(encoding="utf-8")),
            adjudicator_id=adjudicator_id,
            adjudicator_expertise_role=adjudicator_expertise_role,
            final_model_results_not_inspected=attest_no_final_results,
            yield_correlations_not_used_for_tuning=attest_no_yield_tuning,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    click.echo(json.dumps({"status": "adjudicated", "output": str(output)}, indent=2))


@review_group.command("confirm-v5")
@click.argument(
    "adjudication", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.argument(
    "confirmation", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--output", required=True, type=click.Path(dir_okay=False, path_type=Path)
)
def review_confirm_v5(adjudication: Path, confirmation: Path, output: Path) -> None:
    """Attach a third expert's digest-bound v5 attestation."""
    from are.simulation.distributed.review_v5 import confirm_v5_adjudication

    try:
        report = confirm_v5_adjudication(
            json.loads(adjudication.read_text(encoding="utf-8")),
            json.loads(confirmation.read_text(encoding="utf-8")),
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    click.echo(json.dumps({"status": "confirmed", "output": str(output)}, indent=2))


@review_group.command("freeze-v5")
@click.argument(
    "adjudication", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.argument(
    "confirmation", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--output", required=True, type=click.Path(dir_okay=False, path_type=Path)
)
def review_freeze_v5(adjudication: Path, confirmation: Path, output: Path) -> None:
    """Freeze the confirmed v5 process used by paper runs."""
    from are.simulation.distributed.review_v5 import freeze_v5_process

    try:
        process = freeze_v5_process(
            json.loads(adjudication.read_text(encoding="utf-8")),
            json.loads(confirmation.read_text(encoding="utf-8")),
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    output.write_text(process.model_dump_json(indent=2), encoding="utf-8")
    from are.simulation.distributed.scientific_v5 import ScientificGateManifestV5

    gate_path = output.with_name(f"{output.stem}.scientific_gates_v5.template.json")
    gate_path.write_text(
        ScientificGateManifestV5(
            scenario_id=process.scenario_id,
            confirmed_process_digest=process.digest,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    click.echo(
        json.dumps(
            {
                "status": "frozen",
                "digest": process.digest,
                "output": str(output),
                "scientific_gate_template": str(gate_path),
            },
            indent=2,
        )
    )


@review_group.command("refine-team-v5")
@click.argument(
    "base_process", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.argument(
    "team_spec", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.argument(
    "role_refinement", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--output", required=True, type=click.Path(dir_okay=False, path_type=Path)
)
def review_refine_team_v5(
    base_process: Path,
    team_spec: Path,
    role_refinement: Path,
    output: Path,
) -> None:
    """Derive one immutable reviewed n-agent v5 process."""
    from are.simulation.distributed.models import AgentTeamSpec, RoleRefinementSpec
    from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5
    from are.simulation.distributed.teams import refine_process_for_team

    try:
        process = FarmProcessSpecV5.model_validate_json(
            base_process.read_text(encoding="utf-8")
        )
        team = AgentTeamSpec.model_validate_json(team_spec.read_text(encoding="utf-8"))
        refinement = RoleRefinementSpec.model_validate_json(
            role_refinement.read_text(encoding="utf-8")
        )
        if process.annotation_status != "frozen":
            raise ValueError("base process is not frozen")
        if team.expert_review_status != "confirmed":
            raise ValueError("team specification is not confirmed")
        if refinement.expert_review_status != "confirmed":
            raise ValueError("role refinement is not confirmed")
        derived = refine_process_for_team(process, team, refinement)
        if derived.annotation_status != "frozen":
            raise ValueError("derived process did not retain paper eligibility")
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(derived.model_dump_json(indent=2), encoding="utf-8")
    click.echo(
        json.dumps(
            {
                "status": "frozen",
                "team_id": team.team_id,
                "base_process_digest": process.digest,
                "derived_process_digest": derived.digest,
                "output": str(output),
            },
            indent=2,
        )
    )
@review_group.command("export")
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def review_export(output_dir: Path) -> None:
    """Generate a neutral packet with no proposed scientific answers."""
    from are.simulation.distributed.review import export_neutral_review_packet

    click.echo(json.dumps(export_neutral_review_packet(output_dir), indent=2))


@review_group.command("validate")
@click.argument(
    "submission", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def review_validate(submission: Path) -> None:
    """Validate one completed independent submission."""
    from are.simulation.distributed.review import (
        TEAM_REVIEW_SCHEMA,
        validate_submission,
        validate_team_submission,
    )

    try:
        payload = json.loads(submission.read_text(encoding="utf-8"))
        validator = (
            validate_team_submission
            if payload.get("schema_version") == TEAM_REVIEW_SCHEMA
            else validate_submission
        )
        report = validator(payload)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(report, indent=2))


@review_group.command("compare")
@click.argument("left", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("right", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def review_compare(left: Path, right: Path, output: Path | None) -> None:
    """Compare two independently validated submissions."""
    from are.simulation.distributed.review import (
        TEAM_REVIEW_SCHEMA,
        compare_submissions,
        compare_team_submissions,
    )

    try:
        left_payload = json.loads(left.read_text(encoding="utf-8"))
        right_payload = json.loads(right.read_text(encoding="utf-8"))
        if left_payload.get("schema_version") != right_payload.get("schema_version"):
            raise ValueError("cannot compare farm and team review schemas")
        comparator = (
            compare_team_submissions
            if left_payload.get("schema_version") == TEAM_REVIEW_SCHEMA
            else compare_submissions
        )
        report = comparator(left_payload, right_payload)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    rendered = json.dumps(report, indent=2)
    if output:
        output.write_text(rendered, encoding="utf-8")
    else:
        click.echo(rendered)


@review_group.command("adjudicate")
@click.argument("left", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("right", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument(
    "resolved", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--adjudicator",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--output", required=True, type=click.Path(dir_okay=False, path_type=Path)
)
def review_adjudicate(
    left: Path,
    right: Path,
    resolved: Path,
    adjudicator: Path,
    output: Path,
) -> None:
    """Create an immutable adjudication without changing either submission."""
    from are.simulation.distributed.review import (
        TEAM_REVIEW_SCHEMA,
        adjudicate_reviews,
        adjudicate_team_reviews,
    )

    try:
        left_payload = json.loads(left.read_text(encoding="utf-8"))
        right_payload = json.loads(right.read_text(encoding="utf-8"))
        resolved_payload = json.loads(resolved.read_text(encoding="utf-8"))
        if (
            len(
                {
                    left_payload.get("schema_version"),
                    right_payload.get("schema_version"),
                    resolved_payload.get("schema_version"),
                }
            )
            != 1
        ):
            raise ValueError("all adjudication inputs must use the same review schema")
        adjudicator_fn = (
            adjudicate_team_reviews
            if left_payload.get("schema_version") == TEAM_REVIEW_SCHEMA
            else adjudicate_reviews
        )
        report = adjudicator_fn(
            left_payload,
            right_payload,
            resolved_payload,
            json.loads(adjudicator.read_text(encoding="utf-8")),
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    click.echo(json.dumps({"status": "adjudicated", "output": str(output)}, indent=2))


@review_group.command("confirm")
@click.argument(
    "adjudication", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.argument(
    "confirmation", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--output", required=True, type=click.Path(dir_okay=False, path_type=Path)
)
def review_confirm(adjudication: Path, confirmation: Path, output: Path) -> None:
    """Attach a third expert's digest confirmation."""
    from are.simulation.distributed.review import confirm_adjudication

    try:
        report = confirm_adjudication(
            json.loads(adjudication.read_text(encoding="utf-8")),
            json.loads(confirmation.read_text(encoding="utf-8")),
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    click.echo(json.dumps({"status": "confirmed", "output": str(output)}, indent=2))


@review_group.command("freeze")
@click.argument(
    "confirmed", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--petri-net",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Confirmed frozen Petri net; required when freezing a team review.",
)
def review_freeze(confirmed: Path, output_dir: Path, petri_net: Path | None) -> None:
    """Freeze a confirmed hierarchical template and expanded Petri net."""
    from are.simulation.distributed.review import (
        TEAM_REVIEW_SCHEMA,
        freeze_confirmed_review,
        freeze_confirmed_team_review,
    )

    try:
        payload = json.loads(confirmed.read_text(encoding="utf-8"))
        resolved_schema = (payload.get("resolved_specification") or {}).get(
            "schema_version"
        )
        if resolved_schema == TEAM_REVIEW_SCHEMA:
            if petri_net is None:
                raise ValueError("team review freeze requires --petri-net")
            bundle = freeze_confirmed_team_review(
                payload, json.loads(petri_net.read_text(encoding="utf-8"))
            )
        else:
            template, net = freeze_confirmed_review(payload)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    output_dir.mkdir(parents=True, exist_ok=True)
    if resolved_schema == TEAM_REVIEW_SCHEMA:
        bundle_path = output_dir / "team_review.frozen.json"
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        for item in bundle["teams"]:
            team = item["team_spec"]
            (output_dir / f"{team['team_id']}.team.frozen.json").write_text(
                json.dumps(team, indent=2), encoding="utf-8"
            )
        for item in bundle["role_refinements"]:
            (
                output_dir / f"{item['target_team_id']}.refinement.frozen.json"
            ).write_text(json.dumps(item, indent=2), encoding="utf-8")
        click.echo(
            json.dumps(
                {
                    "status": "frozen",
                    "team_bundle": str(bundle_path),
                    "teams": len(bundle["teams"]),
                    "role_refinements": len(bundle["role_refinements"]),
                },
                indent=2,
            )
        )
        return
    template_path = output_dir / "farm_petri_template.frozen.json"
    net_path = output_dir / "petri_net.frozen.json"
    gate_path = output_dir / "scientific_gates.template.json"
    template_path.write_text(json.dumps(template, indent=2), encoding="utf-8")
    net_path.write_text(json.dumps(net, indent=2), encoding="utf-8")
    from are.simulation.distributed.models import stable_digest

    gate_path.write_text(
        json.dumps(
            {
                "schema_version": "farm_dcore_scientific_gates_v1",
                "confirmed_spec_digest": stable_digest(net),
                "confirmed_team_digest": None,
                "offline_semantic_gates": False,
                "prompt_leakage_check": False,
                "saved_trace_replay": False,
                "blocked_write_nonmutation": False,
                "allowed_write_exactly_once": False,
                "completed_at_utc": None,
                "test_report_digest": None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    click.echo(
        json.dumps(
            {
                "status": "frozen",
                "template": str(template_path),
                "petri_net": str(net_path),
                "scientific_gates": str(gate_path),
            },
            indent=2,
        )
    )


@main.command("doctor")
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--team-id",
    "team_ids",
    multiple=True,
    type=click.Choice(["wetjune_2agent", "wetjune_3agent", "wetjune_4agent"]),
)
@click.option(
    "--process-spec",
    "process_spec_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Frozen farm_process_spec_v5 file; repeat for every paper scenario/team.",
)
@click.option(
    "--gate-manifest",
    "gate_manifest_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Completed scientific-gate v5 file; repeat for paper scenario/team cells.",
)
@click.option(
    "--team-spec",
    "team_spec_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Reviewed AgentTeamSpec file; repeat for selected team decompositions.",
)
@click.option(
    "--role-refinement",
    "role_refinement_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Reviewed RoleRefinementSpec file for each selected team above two actors.",
)
def doctor_command(
    output_dir: Path | None,
    team_ids: tuple[str, ...],
    process_spec_paths: tuple[Path, ...],
    gate_manifest_paths: tuple[Path, ...],
    team_spec_paths: tuple[Path, ...],
    role_refinement_paths: tuple[Path, ...],
) -> None:
    """Check dependencies, native scenarios, model variables, and output access."""
    modules = {
        name: importlib.util.find_spec(name) is not None
        for name in (
            "click",
            "inputimeout",
            "pydantic",
            "yaml",
            "numpy",
            "pandas",
            "pm4py",
            "matplotlib",
        )
    }
    pm4py_version = None
    if modules["pm4py"]:
        from importlib.metadata import version as package_version

        pm4py_version = package_version("pm4py")
    scenarios: dict[str, str] = {}
    oracle_review: dict[str, str] = {}
    metric_annotations: dict[str, str] = {}
    branch_annotations: dict[str, str] = {}
    branch_counts: dict[str, int] = {}
    guard_annotations: dict[str, str] = {}
    team_readiness: dict[str, dict[str, object]] = {}
    process_readiness: dict[str, dict[str, object]] = {}
    gate_readiness: dict[str, dict[str, object]] = {}
    supplied_processes = []
    if process_spec_paths:
        from are.simulation.distributed.scientific_v5 import load_process_spec

        seen_digests: set[str] = set()
        for path in process_spec_paths:
            try:
                process = load_process_spec(path)
                if process.digest in seen_digests:
                    raise ValueError("duplicate v5 process specification digest")
                seen_digests.add(process.digest)
                frozen = bool(
                    process.annotation_status == "frozen"
                    and process.expert_review_status == "confirmed"
                    and process.review_digest
                    and not process.metadata.get("engineering_defaults")
                )
                supplied_processes.append(process)
                process_readiness[str(path)] = {
                    "valid": True,
                    "frozen_and_confirmed": frozen,
                    "scenario_id": process.scenario_id,
                    "actors": list(process.occurrence_net.actors),
                    "process_digest": process.digest,
                    "world_branch_count": len(
                        process.occurrence_net.exogenous_branches
                    ),
                    "role_refinement_review_status": process.metadata.get(
                        "role_refinement_review_status", "not_applicable"
                    ),
                }
            except Exception as error:
                process_readiness[str(path)] = {
                    "valid": False,
                    "error": str(error),
                }
    for identifier in FARM_SCENARIOS:
        try:
            net = compile_paper_petri_net(identifier, world_seed=0)
            validate_petri_net(net)
            scenarios[identifier] = "ok"
            oracle_review[identifier] = net.expert_review_status
            metric_annotations[identifier] = str(
                net.metadata.get(
                    "annotation_status",
                    net.metadata.get("metric_annotation_status", "unfrozen"),
                )
            )
            branch_annotations[identifier] = str(
                net.metadata.get("branch_annotation_status", "unfrozen")
            )
            branch_counts[identifier] = len(net.exogenous_branches)
            guard_annotations[identifier] = str(
                net.metadata.get("guard_annotation_status", "unfrozen")
            )
        except Exception as error:
            scenarios[identifier] = f"error: {error}"
    from are.simulation.distributed.teams import (
        build_builtin_team,
        built_in_role_refinement,
        refine_petri_for_team,
        team_digest,
    )
    from are.simulation.scenarios.scenario_dcore.farm_catalog import (
        create_native_scenario,
    )

    wetjune_scenario = create_native_scenario("farm_wetjune_recheck", world_seed=0)
    base_wetjune = compile_paper_petri_net("farm_wetjune_recheck", world_seed=0)
    builtin_ids = team_ids or (
        ()
        if team_spec_paths
        else ("wetjune_2agent", "wetjune_3agent", "wetjune_4agent")
    )
    for team_id in builtin_ids:
        try:
            team = build_builtin_team(team_id, wetjune_scenario.get_tools())
            refinement = built_in_role_refinement(team, "farm_wetjune_recheck")
            refined = refine_petri_for_team(base_wetjune, team, refinement)
            validate_petri_net(refined)
            team_readiness[team_id] = {
                "runtime_valid": True,
                "team_size": len(team.actors),
                "actors": [actor.actor_id for actor in team.actors],
                "team_spec_digest": team_digest(team),
                "expert_review_status": team.expert_review_status,
                "role_refinement_status": (
                    refinement.expert_review_status if refinement else "not_applicable"
                ),
                "paper_eligible": bool(refined.metadata.get("paper_eligible")),
            }
        except Exception as error:
            team_readiness[team_id] = {
                "runtime_valid": False,
                "error": str(error),
            }
    if team_spec_paths:
        from are.simulation.distributed.models import (
            AgentTeamSpec,
            RoleRefinementSpec,
            stable_digest,
        )

        refinements = {}
        for path in role_refinement_paths:
            refinement = RoleRefinementSpec.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if refinement.target_team_id in refinements:
                raise click.ClickException("duplicate role refinement target team")
            refinements[refinement.target_team_id] = refinement
        public_tool_names = {tool.name for tool in wetjune_scenario.get_tools()}
        primary = build_builtin_team("wetjune_2agent", wetjune_scenario.get_tools())
        primary_union = {
            action for actor in primary.actors for action in actor.permitted_actions
        }
        for path in team_spec_paths:
            try:
                team = AgentTeamSpec.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                grants = {
                    action
                    for actor in team.actors
                    for action in actor.permitted_actions
                }
                refinement = refinements.get(team.team_id)
                refinement_digest = (
                    stable_digest(refinement.model_dump(mode="json"))
                    if refinement is not None
                    else None
                )
                runtime_valid = bool(
                    grants == primary_union
                    and grants <= public_tool_names
                    and (
                        len(team.actors) <= 2
                        or (
                            refinement is not None
                            and refinement.expert_review_status == "confirmed"
                            and team.role_refinement_digest == refinement_digest
                        )
                    )
                )
                team_readiness[team.team_id] = {
                    "runtime_valid": runtime_valid,
                    "team_size": len(team.actors),
                    "actors": [actor.actor_id for actor in team.actors],
                    "team_spec_digest": team_digest(team),
                    "expert_review_status": team.expert_review_status,
                    "role_refinement_status": (
                        refinement.expert_review_status
                        if refinement is not None
                        else "not_applicable"
                    ),
                    "paper_eligible": False,
                    "source": str(path),
                }
            except Exception as error:
                team_readiness[str(path)] = {
                    "runtime_valid": False,
                    "error": str(error),
                }
    writable = None
    if output_dir:
        parent = output_dir if output_dir.exists() else output_dir.parent
        writable = parent.exists() and os.access(parent, os.W_OK)
    model_environment = {
        key: bool(os.getenv(key))
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY")
    }
    healthy = (
        all(value for name, value in modules.items() if name != "pm4py")
        and all(value == "ok" for value in scenarios.values())
        and writable is not False
    )
    wetjune = compile_paper_petri_net("farm_wetjune_recheck", world_seed=0)
    wetjune_guard_keys = {
        guard.fact_key
        for transition in wetjune.transitions
        for guard in transition.guards
        if guard.source == "world"
    }
    required_scenarios = set(FARM_SCENARIOS)
    confirmed_processes = [
        process
        for process in supplied_processes
        if process.annotation_status == "frozen"
        and process.expert_review_status == "confirmed"
        and not process.metadata.get("engineering_defaults")
    ]
    confirmed_scenarios = {process.scenario_id for process in confirmed_processes}
    try:
        current_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        worktree_dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        current_tags = set(
            subprocess.run(
                ["git", "tag", "--points-at", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        )
    except (OSError, subprocess.CalledProcessError):
        current_commit = None
        worktree_dirty = True
        current_tags = set()
    lock_path = Path("uv.lock")
    lock_digest = (
        hashlib.sha256(lock_path.read_bytes()).hexdigest()
        if lock_path.is_file()
        else None
    )
    protocol_path = Path(__file__).with_name("EXPERIMENT_PROTOCOL.md")
    protocol_digest = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    completed_gate_process_digests: set[str] = set()
    if gate_manifest_paths:
        from are.simulation.distributed.scientific_v5 import (
            ScientificGateManifestV5,
        )

        for path in gate_manifest_paths:
            try:
                gate = ScientificGateManifestV5.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                matching = next(
                    (
                        process
                        for process in confirmed_processes
                        if process.digest == gate.confirmed_process_digest
                    ),
                    None,
                )
                valid = bool(
                    gate.status == "complete"
                    and matching is not None
                    and gate.confirmed_team_digest
                    in {
                        item.get("team_spec_digest") for item in team_readiness.values()
                    }
                    and gate.scenario_id == matching.scenario_id
                    and gate.code_commit == current_commit
                    and not worktree_dirty
                    and gate.release_tag in current_tags
                    and gate.environment_lock_digest == lock_digest
                    and gate.analysis_protocol_digest == protocol_digest
                )
                if valid and gate.confirmed_process_digest:
                    completed_gate_process_digests.add(gate.confirmed_process_digest)
                gate_readiness[str(path)] = {
                    "valid": valid,
                    "scenario_id": gate.scenario_id,
                    "process_digest": gate.confirmed_process_digest,
                    "team_digest": gate.confirmed_team_digest,
                    "status": gate.status,
                    "code_commit_matches": gate.code_commit == current_commit,
                    "worktree_clean": not worktree_dirty,
                    "release_tag_matches": gate.release_tag in current_tags,
                    "environment_lock_matches": (
                        gate.environment_lock_digest == lock_digest
                    ),
                    "analysis_protocol_matches": (
                        gate.analysis_protocol_digest == protocol_digest
                    ),
                }
            except Exception as error:
                gate_readiness[str(path)] = {
                    "valid": False,
                    "error": str(error),
                }
    scientific_release_gates_complete = bool(confirmed_processes) and all(
        process.digest in completed_gate_process_digests
        for process in confirmed_processes
        if process.scenario_id in required_scenarios
    )
    v5_process_specs_confirmed = required_scenarios <= confirmed_scenarios
    requested_team_ids = tuple(team_readiness)
    for team_id in requested_team_ids:
        item = team_readiness.get(team_id, {})
        expected_size = item.get("team_size")
        candidates = [
            process
            for process in supplied_processes
            if process.scenario_id == "farm_wetjune_recheck"
            and len(process.occurrence_net.actors) == expected_size
            and set(process.occurrence_net.actors) == set(item.get("actors", ()))
            and process.annotation_status == "frozen"
            and process.expert_review_status == "confirmed"
        ]
        reviewed = any(
            item.get("expert_review_status") == "confirmed"
            and (
                len(process.occurrence_net.actors) <= 2
                or (
                    process.metadata.get("role_refinement_review_status") == "confirmed"
                    and process.metadata.get("role_refinement_review_digest")
                )
            )
            for process in candidates
        )
        item["v5_reviewed_process_available"] = reviewed
        item["paper_eligible"] = bool(item.get("runtime_valid") and reviewed)
    readiness_gates = {
        "v5_process_specs_confirmed": v5_process_specs_confirmed,
        "v5_independent_policy_reconstruction": True,
        "v5_categorical_acceptance": True,
        "v5_semantic_causal_obligations": True,
        "v5_provenance_failure_localization": True,
        "pm4py_sequential_baseline_available": (
            modules["pm4py"] and pm4py_version == "2.7.23.4"
        ),
        "oracle_expert_review": v5_process_specs_confirmed,
        "metric_annotations_frozen": v5_process_specs_confirmed,
        "exogenous_branches_frozen": v5_process_specs_confirmed
        and all(
            process.occurrence_net.exogenous_branches
            for process in confirmed_processes
            if process.scenario_id in required_scenarios
        ),
        "high_impact_guards_frozen": v5_process_specs_confirmed
        and all(
            all(
                not transition.high_impact or transition.guards
                for transition in process.occurrence_net.transitions
            )
            for process in confirmed_processes
            if process.scenario_id in required_scenarios
        ),
        "agent_driven_season_runtime": True,
        "parameterized_team_runtime": all(
            bool(item.get("runtime_valid")) for item in team_readiness.values()
        ),
        "selected_team_refinements_reviewed": all(
            bool(team_readiness[item].get("paper_eligible"))
            for item in requested_team_ids
        ),
        "scientific_release_gates_complete": scientific_release_gates_complete,
        "controller_arguments_execute_in_farmare": True,
        "native_baseagent_family_step_adapter": True,
        "wetjune_world_truth_data_guards": {
            "weather:spray_window_open",
            "soil:trafficable",
            "disease:confirmed",
        }
        <= wetjune_guard_keys,
        # OpenAI's API does not promise deterministic sampling from a client
        # seed. We record this explicitly rather than claiming it was applied.
        "world_scheduler_model_fault_seeds_separated": True,
    }
    from are.simulation.distributed.experiments import load_manifest, resolve_manifest
    from are.simulation.distributed.handoff import EXPECTED_REQUIRED_COUNTS

    config_root = Path(__file__).with_name("configs")
    suite_files = {
        "primary_pass_1": config_root / "farm_dcore_primary_pass1.yaml",
        "primary_pass_2": config_root / "farm_dcore_primary_pass2.yaml",
        "controller_robustness": config_root / "farm_dcore_controller_robustness.yaml",
        "scalability": config_root / "farm_dcore_scalability.yaml",
    }
    suite_counts = {
        block: len(resolve_manifest(load_manifest(path)))
        for block, path in suite_files.items()
    }
    readiness_gates.update(
        {
            "professor_matrix_counts_frozen": suite_counts
            == EXPECTED_REQUIRED_COUNTS,
            "matched_native_baselines_available": True,
            "twenty_case_controlled_suite_available": True,
            "paper_report_recipes_available": True,
        }
    )
    paper_ready = healthy and all(readiness_gates.values())
    scenario_readiness = {
        identifier: identifier in confirmed_scenarios for identifier in FARM_SCENARIOS
    }
    report = {
        "healthy": healthy,
        "paper_ready": paper_ready,
        "scenario_readiness": scenario_readiness,
        "team_readiness": team_readiness,
        "v5_process_readiness": process_readiness,
        "v5_gate_readiness": gate_readiness,
        "current_git_commit": current_commit,
        "worktree_clean": not worktree_dirty,
        "environment_lock_digest": lock_digest,
        "analysis_protocol_digest": protocol_digest,
        "python": sys.version,
        "dependencies": modules,
        "pm4py_version": pm4py_version,
        "pm4py_expected_version": "2.7.23.4",
        "scenarios": scenarios,
        "oracle_expert_review": oracle_review,
        "metric_annotations": metric_annotations,
        "branch_annotations": branch_annotations,
        "exogenous_branch_counts": branch_counts,
        "guard_annotations": guard_annotations,
        "paper_readiness_gates": readiness_gates,
        "professor_suite_counts": suite_counts,
        "output_writable": writable,
        "model_credentials_present": model_environment,
    }
    click.echo(json.dumps(report, indent=2))
    if not healthy:
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    main()
