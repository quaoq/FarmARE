#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from are.simulation.agent_suite.suite_runner import (
    expand_run_specs,
    load_suite_config,
    run_suite,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run architecture-comparison suites for FarmARE agents."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to suite YAML config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print expanded run plan without executing runs.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force mock model profile for all runs (no API key needed).",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Force non-mock profiles where available.",
    )
    parser.add_argument(
        "--family",
        action="append",
        default=[],
        help="Optional filter. Repeat to select specific families only.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Optional filter. Repeat to select specific scenarios only.",
    )
    args = parser.parse_args()

    if args.mock and args.real:
        raise ValueError("Use either --mock or --real, not both.")

    config = load_suite_config(args.config)
    run_specs = expand_run_specs(
        config=config,
        force_mock=args.mock,
        force_real=args.real,
        enable_real_model_preflight=args.real and not args.dry_run,
    )
    if args.family:
        selected_families = {item.strip() for item in args.family if item.strip()}
        run_specs = [spec for spec in run_specs if spec.family in selected_families]
    if args.scenario:
        selected_scenarios = {item.strip() for item in args.scenario if item.strip()}
        run_specs = [spec for spec in run_specs if spec.scenario_id in selected_scenarios]

    summary = run_suite(run_specs=run_specs, dry_run=args.dry_run, repo_root=Path.cwd())
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
