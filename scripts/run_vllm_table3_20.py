from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "scripts" / "iclr_validation_runner.py"

TABLE3_SCENARIOS = [
    "scenario_full_season_hb_organic_residue_weed_establishment",
    "scenario_full_season_hb_organic_patch_weed_mechanical_capacity",
    "scenario_full_season_hb_micronutrient_deficiency_flowering_patch",
    "scenario_full_season_hb_local_soil_constraint_nutrition_patch",
    "scenario_full_season_heinong84_edge_low_fertility",
    "scenario_full_season_hb_cloudy_wet_low_radiation_biomass",
    "scenario_full_season_hb_poordrainage_wetjune_disease_trafficability",
    "scenario_full_season_hb_staggered_wetjune_canopy_disease",
    "scenario_full_season_hb_soy_after_soy_wetjune_disease",
    "scenario_full_season_hb_dryr5r6_hn58_std_waterlimit",
    "scenario_full_season_hb_drought_recovery_false_disease_signal",
    "scenario_full_season_hb_heinong58_resistant_biotic_water_budget_priority",
    "scenario_full_season_hb_hn60_high_fastdrain_dryr5r6",
    "scenario_full_season_hb_dryr5r6_insect_threshold_waterstress",
    "scenario_full_season_hb_dryer_capacity_batch_harvest_storage",
    "scenario_full_season_hb_grain_moisture_sensor_failure_harvest",
    "scenario_full_season_hb_laterain_shattering_drying_tradeoff",
    "scenario_full_season_hb_cool_august_lategrain_laterain",
    "scenario_full_season_hb_compacted_headland_stand_recovery",
    "scenario_full_season_hb_hn50_hn84_hn58_mixed_stress",
]


def _csv(items: list[str]) -> str:
    return ",".join(items)


def _env_default(*names: str, fallback: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return fallback


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Run Table 3's 20 L3 scenarios against a local/OpenAI-compatible "
            "vLLM endpoint using ARE's qwen-json action format by default. "
            "Defaults can be overridden by passing the same arguments accepted "
            "by scripts/iclr_validation_runner.py."
        )
    )
    parser.add_argument(
        "--phase",
        default="vllm_qwen_json_table3_20_detail_false",
        help="Runner phase label.",
    )
    parser.add_argument(
        "--output-root",
        default=(
            "validation_runs/vllm_qwen_json_table3_20_detail_false/"
            "phase5_paper_matrix"
        ),
    )
    parser.add_argument("--families", default="farm_baseline_react")
    parser.add_argument("--scenarios", default=_csv(TABLE3_SCENARIOS))
    parser.add_argument(
        "--model",
        default=_env_default(
            "VLLM_MODEL",
            "SERVED_MODEL_NAME",
            fallback="Qwen/Qwen2.5-7B-Instruct",
        ),
    )
    parser.add_argument("--provider", default="qwen-json")
    parser.add_argument("--model-family", default="vLLM")
    parser.add_argument(
        "--endpoint",
        default=_env_default(
            "VLLM_ENDPOINT",
            "VLLM_API_BASE",
            "OPENAI_BASE_URL",
            fallback="http://localhost:8000/v1",
        ),
    )
    parser.add_argument("--detail", default="false")
    parser.add_argument("--a2a", default="false")
    parser.add_argument("--cost-cap-dollars", type=float, default=200.0)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument("--cell-timeout-s", type=int, default=1200)
    parser.add_argument("--cell-timeout-grace-s", type=int, default=60)
    parser.add_argument("--agent-max-iterations", type=int, default=300)
    parser.add_argument("--wait-for-user-input-timeout", type=float, default=5.0)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the iclr_validation_runner.py command without running it.",
    )
    parser.add_argument(
        "--print-scenarios",
        action="store_true",
        help="Print the default Table 3 scenario list and exit.",
    )
    return parser.parse_known_args(argv)


def _runner_cmd(args: argparse.Namespace, passthrough: list[str]) -> list[str]:
    cmd = [
        sys.executable,
        str(RUNNER),
        "--phase",
        args.phase,
        "--output-root",
        args.output_root,
        "--families",
        args.families,
        "--scenarios",
        args.scenarios,
        "--repeats",
        str(args.repeats),
        "--model",
        args.model,
        "--provider",
        args.provider,
        "--model-family",
        args.model_family,
        "--endpoint",
        args.endpoint,
        "--detail",
        args.detail,
        "--a2a",
        args.a2a,
        "--cost-cap-dollars",
        str(args.cost_cap_dollars),
        "--max-concurrent",
        str(args.max_concurrent),
        "--cell-timeout-s",
        str(args.cell_timeout_s),
        "--cell-timeout-grace-s",
        str(args.cell_timeout_grace_s),
        "--agent-max-iterations",
        str(args.agent_max_iterations),
        "--wait-for-user-input-timeout",
        str(args.wait_for_user_input_timeout),
        "--log-level",
        args.log_level,
    ]
    return cmd + passthrough


def main(argv: list[str] | None = None) -> int:
    args, passthrough = parse_args(argv)
    if args.print_scenarios:
        for scenario in TABLE3_SCENARIOS:
            print(scenario)
        return 0

    env = os.environ.copy()
    api_key = env.get("VLLM_API_KEY") or env.get("QWEN_API_KEY") or "EMPTY"
    env.setdefault("QWEN_API_KEY", api_key)
    env["QWEN_API_BASE"] = args.endpoint
    env.setdefault("LLAMA_API_KEY", api_key)
    env["LLAMA_API_BASE"] = args.endpoint

    cmd = _runner_cmd(args, passthrough)
    print(" ".join(cmd), flush=True)
    print(f"QWEN_API_BASE={env['QWEN_API_BASE']}", flush=True)
    print("QWEN_API_KEY=<set>", flush=True)
    if args.dry_run:
        return 0
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
