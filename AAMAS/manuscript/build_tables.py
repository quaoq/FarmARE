"""Run from the repository root: python AAMAS/manuscript/build_tables.py."""

from pathlib import Path

from are.simulation.distributed.paper_report import (
    generate_paper_report,
    generate_pending_result_tables,
    generate_pending_study_tables,
)

repository = Path(__file__).resolve().parents[2]
configs = repository / "are/simulation/distributed/configs"
generate_pending_study_tables(
    [
        configs / name
        for name in [
            "farm_dcore_primary_pass1.yaml",
            "farm_dcore_primary_pass2.yaml",
            "farm_dcore_live_verification.yaml",
            "farm_dcore_reserve.yaml",
        ]
    ],
    Path(__file__).resolve().parent / "tables",
)
generate_pending_result_tables(Path(__file__).resolve().parent / "tables")

# Exercise every final result panel with zero actual observations. This is an
# empty study, not synthetic observations or copied pilot values.
empty_source = Path(__file__).resolve().parent / "tables" / "empty_study.jsonl"
empty_source.write_text("")
generate_paper_report(empty_source, empty_source.parent / "pending_results")
