"""Small offline validation-study CLI; no execution or provider dependencies."""

from __future__ import annotations

import json
from functools import wraps
from pathlib import Path

import click

from are.simulation.distributed.diagnostic_validation import (
    evaluate_validation_variants,
)
from are.simulation.distributed.models import DistributedTrace
from are.simulation.distributed.scientific_v5 import FarmProcessSpecV5
from are.simulation.distributed.validation_study import (
    agreement,
    file_digest,
    freeze_plan,
    load_plan,
    predict_packet,
    sample_episodes,
    score_predictions,
    write_json,
)

INPUT = click.Path(exists=True, dir_okay=False, path_type=Path)
OUTPUT = click.Path(dir_okay=False, path_type=Path)
DIRECTORY = click.Path(exists=True, file_okay=False, path_type=Path)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, OSError, KeyError) as error:
            raise click.ClickException(str(error)) from error

    return wrapped


@click.group()
def validation():
    """Plan and evaluate independent diagnosis validation using saved traces."""


@validation.command("plan")
@click.option("--manifest", required=True, type=INPUT)
@click.option("--process", "processes", required=True, multiple=True, type=INPUT)
@click.option("--alternative", "alternatives", multiple=True, type=INPUT)
@click.option("--episodes", default=120, type=click.IntRange(1, 200))
@click.option("--max-per-run", default=2, type=click.IntRange(1, 10))
@click.option("--seed", default=2027, type=int)
@click.option(
    "--fixture-only",
    is_flag=True,
    help="Permit draft specs and sample only non-LLM fixtures; never paper evidence.",
)
@click.option("--output", required=True, type=OUTPUT)
@checked
def plan_command(
    manifest, processes, alternatives, episodes, max_per_run, seed, fixture_only, output
):
    """Bind sampling choices and all sensitivity variants before outcomes."""
    plan = freeze_plan(
        manifest,
        processes,
        output,
        alternatives=alternatives,
        episodes=episodes,
        max_per_run=max_per_run,
        seed=seed,
        fixture_only=fixture_only,
    )
    click.echo(json.dumps(plan.model_dump(mode="json"), indent=2))


@validation.command("sample")
@click.option("--plan", "plan_path", required=True, type=INPUT)
@click.option("--manifest", required=True, type=INPUT)
@click.option("--results", required=True, type=DIRECTORY)
@click.option(
    "--output", required=True, type=click.Path(file_okay=False, path_type=Path)
)
@checked
def sample_command(plan_path, manifest, results, output):
    """Inventory all v5 traces and create separate public/private packets."""
    plan = load_plan(plan_path)
    if file_digest(manifest) != plan.manifest_sha256:
        raise ValueError("experiment manifest differs from the precommitted plan")
    click.echo(json.dumps(sample_episodes(plan, results, output), indent=2))


@validation.command("compare")
@click.option("--packet", required=True, type=INPUT)
@click.option("--left", required=True, type=INPUT)
@click.option("--right", required=True, type=INPUT)
@click.option("--output", required=True, type=OUTPUT)
@checked
def compare_command(packet, left, right, output):
    """Measure independent agreement and list disagreements for adjudication."""
    write_json(output, agreement(json.loads(packet.read_text()), left, right))


def _private_output(packet_dir, output):
    if output.resolve().is_relative_to((packet_dir / "public").resolve()):
        raise ValueError("keep evaluator outputs outside the blinded public packet")


@validation.command("predict")
@click.option("--packet-dir", required=True, type=DIRECTORY)
@click.option("--output", required=True, type=OUTPUT)
@checked
def predict_command(packet_dir, output):
    """Record D-CORE and flat-baseline predictions without human labels."""
    _private_output(packet_dir, output)
    write_json(output, predict_packet(packet_dir))


@validation.command("score")
@click.option("--packet-dir", required=True, type=DIRECTORY)
@click.option("--left", required=True, type=INPUT)
@click.option("--right", required=True, type=INPUT)
@click.option("--adjudicated", required=True, type=INPUT)
@click.option("--predictions", required=True, type=INPUT)
@click.option("--output", required=True, type=OUTPUT)
@checked
def score_command(packet_dir, left, right, adjudicated, predictions, output):
    """Score locked predictions against complete, independently adjudicated labels."""
    _private_output(packet_dir, output)
    write_json(
        output, score_predictions(packet_dir, adjudicated, predictions, left, right)
    )


@validation.command("evaluate")
@click.option("--plan", "plan_path", required=True, type=INPUT)
@click.option("--process", "process_path", required=True, type=INPUT)
@click.option("--trace", "trace_path", required=True, type=INPUT)
@click.option("--alternative", "alternative_paths", multiple=True, type=INPUT)
@click.option("--output", required=True, type=OUTPUT)
@checked
def evaluate_command(plan_path, process_path, trace_path, alternative_paths, output):
    """Run reduced diagnostics and every predeclared alternative on saved evidence."""
    plan = load_plan(plan_path)
    process = FarmProcessSpecV5.model_validate_json(process_path.read_text())
    alternatives = [
        FarmProcessSpecV5.model_validate_json(p.read_text()) for p in alternative_paths
    ]
    expected = plan.sensitivity_families.get(process.process_id, ())
    if process.digest not in plan.process_digests or sorted(
        p.digest for p in alternatives
    ) != sorted(expected):
        raise ValueError(
            "supply the planned process and every sensitivity variant in its family"
        )
    trace = DistributedTrace.model_validate_json(trace_path.read_text())
    write_json(
        output, evaluate_validation_variants(process, trace, alternatives=alternatives)
    )
