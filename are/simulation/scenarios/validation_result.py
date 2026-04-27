# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

import polars as pl

from are.simulation.scenarios.config import MultiScenarioRunnerConfig

if TYPE_CHECKING:
    pass


class ScenarioMetadata(TypedDict):
    """Type definition for scenario metadata stored in MultiScenarioValidationResult.

    :param base_scenario_id: The base scenario identifier
    :type base_scenario_id: str
    :param run_number: The run number for multiple runs
    :type run_number: int | None
    :param config: The configuration name
    :type config: str | None
    :param has_a2a_augmentation: Whether agent-to-agent augmentation is enabled
    :type has_a2a_augmentation: bool
    """

    base_scenario_id: str
    run_number: int | None
    config: str | None
    has_a2a_augmentation: bool


@dataclass
class ScenarioValidationResult:
    # Flag indicating whether the scenario validation was successful
    # None indicates that the judge or run failed (an exception occurred)
    success: bool | None

    # Optional exception that occurred during validation, if any
    exception: Exception | None = None

    # Optional path to exported traces, if applicable
    export_path: str | None = None

    # Optional description of the rationale
    rationale: str | None = None

    # Duration of the run in seconds
    duration: float | None = None

    # Optional per-run telemetry emitted by the agent implementation
    telemetry: dict | None = None


@dataclass
class MultiScenarioValidationResult:
    run_config: MultiScenarioRunnerConfig

    # Dictionary mapping (base_scenario_id, run_number) tuples to their respective validation results
    # Key format: (scenario_id, None) for single runs, (scenario_id, run_number) for multiple runs
    scenario_results: dict[tuple[str, int | None], ScenarioValidationResult] = field(
        default_factory=dict
    )

    # Duration of the entire validation run in seconds
    duration: float = 0.0

    # Counts of different scenario outcomes
    successful_count: int = 0
    failed_count: int = 0
    exception_count: int = 0
    no_validation_count: int = 0

    def to_polars(self, extra_columns: dict[str, str] | None = None) -> pl.DataFrame:
        """Convert this MultiScenarioValidationResult to a polars DataFrame.

        :param extra_columns: Additional columns to add to each row (e.g., phase_name, config, etc.)
        :type extra_columns: dict[str, str] | None
        :returns: Polars DataFrame with one row per scenario run
        :rtype: pl.DataFrame
        """

        rows = []

        for scenario_key, scenario_result in self.scenario_results.items():
            base_scenario_id, run_number = scenario_key

            # Convert success to numeric (1.0 for True, 0.0 for False, None for exception)
            success_numeric = (
                1.0
                if scenario_result.success is True
                else 0.0
                if scenario_result.success is False
                else None
            )

            # Determine status
            if scenario_result.success is True:
                status = "success"
            elif scenario_result.success is False:
                status = "failed"
            elif scenario_result.exception is not None:
                status = "exception"
            else:
                status = "no_validation"

            row = {
                "base_scenario_id": base_scenario_id,
                "run_number": run_number,
                "success_numeric": success_numeric,
                "success_bool": scenario_result.success,
                "status": status,
                "has_exception": scenario_result.exception is not None,
                "exception_type": (
                    type(scenario_result.exception).__name__
                    if scenario_result.exception
                    else None
                ),
                "exception_message": (
                    str(scenario_result.exception)
                    if scenario_result.exception
                    else None
                ),
                "rationale": scenario_result.rationale,
                "export_path": scenario_result.export_path,
                "model": self.run_config.model,
                "model_provider": self.run_config.model_provider,
                "agent": self.run_config.agent,
                "run_duration": scenario_result.duration,
                "job_duration": self.duration,
            }

            # Add any extra columns provided (cast all values to string to ensure consistent schema)
            if extra_columns:
                row.update({k: str(v) for k, v in extra_columns.items()})
            rows.append(row)

        # Define explicit schema to avoid inference issues when mixing None and string values
        base_schema = {
            "base_scenario_id": pl.Utf8,
            "run_number": pl.Int64,
            "success_numeric": pl.Float64,
            "success_bool": pl.Boolean,
            "status": pl.Utf8,
            "has_exception": pl.Boolean,
            "exception_type": pl.Utf8,
            "exception_message": pl.Utf8,
            "rationale": pl.Utf8,
            "export_path": pl.Utf8,
            "model": pl.Utf8,
            "model_provider": pl.Utf8,
            "agent": pl.Utf8,
            "run_duration": pl.Float64,
            "job_duration": pl.Float64,
        }

        # Add schema for extra columns (assume string type for simplicity)
        if extra_columns:
            for col_name in extra_columns.keys():
                if col_name not in base_schema:
                    base_schema[col_name] = pl.Utf8

        return pl.DataFrame(rows, schema=base_schema)

    def add_result(
        self,
        result: ScenarioValidationResult,
        scenario_id: str,
        run_number: int | None = None,
    ) -> None:
        """Add a scenario result using tuple key format.

        Args:
            scenario: The scenario object
            result: The scenario validation result
        """

        # Create tuple key
        result_key = (scenario_id, run_number)

        # Store the result
        self.scenario_results[result_key] = result

        # Update counts
        if result.success is True:
            self.successful_count += 1
        elif result.success is False:
            self.failed_count += 1
        else:  # result.success is None
            # Distinguish between actual exceptions and cases with no validation
            if result.exception is not None:
                self.exception_count += 1
            else:
                self.no_validation_count += 1

    def success_rate(self):
        total_count = (
            self.successful_count
            + self.failed_count
            + self.exception_count
            + self.no_validation_count
        )
        return (self.successful_count / total_count * 100) if total_count > 0 else 0.0

    def description(self) -> str:
        """
        Generate a detailed description of the validation results.

        Returns:
            A formatted string with the validation results and metadata
        """
        # Import here to avoid circular dependency
        from are.simulation.benchmark.report_stats import generate_validation_report

        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        description = f"""
Dataset generated on: {current_time}

"""

        # Add model information if config is provided
        description += f"""Model information:
- Model: {self.run_config.model}
- Provider: {self.run_config.model_provider or "N/A"}
- Agent: {self.run_config.agent or "N/A"}

"""

        # Add results summary
        total_scenario_runs = len(self.scenario_results)

        # Calculate unique base scenarios for the breakdown
        unique_base_scenarios = set()
        runs_per_scenario = {}
        for scenario_key in self.scenario_results.keys():
            # Extract base scenario ID from the tuple key
            base_id, run_number = scenario_key
            unique_base_scenarios.add(base_id)
            runs_per_scenario[base_id] = runs_per_scenario.get(base_id, 0) + 1

        # Create breakdown text if we have multiple runs
        breakdown_text = ""
        if len(unique_base_scenarios) > 0 and any(
            count > 1 for count in runs_per_scenario.values()
        ):
            # Find the most common number of runs per scenario
            run_counts = list(runs_per_scenario.values())
            if len(set(run_counts)) == 1:
                # All scenarios have the same number of runs
                breakdown_text = f" ({len(unique_base_scenarios)} base scenarios × {run_counts[0]} runs each)"
            else:
                # Mixed number of runs
                breakdown_text = (
                    f" ({len(unique_base_scenarios)} base scenarios with varying runs)"
                )

        description += f"""Results summary:
- Total scenario runs: {total_scenario_runs}{breakdown_text}
- Duration: {self.duration:.2f} seconds
- Successful scenario runs: {self.successful_count}
- Failed scenario runs: {self.failed_count}
- Exception scenario runs: {self.exception_count}
- No validation scenario runs: {self.no_validation_count}
- Success rate: {self.success_rate():.2f}%

"""

        # Use polars-based reporting for detailed metrics
        df = self.to_polars()
        if not df.is_empty():
            report = generate_validation_report(
                df, self.run_config.model, self.run_config.model_provider or "unknown"
            )
            description += "=== Stats ===\n"
            description += report
        else:
            description += "=== Stats ===\n"
            description += "No results available for stats.\n"

        return description
