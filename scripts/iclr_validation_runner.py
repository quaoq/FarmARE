"""
ICLR validation-sweep runner.

Drives `are.simulation.main` over a (family, scenario, repeat) cell list,
captures per-cell results into a CSV, and tracks cumulative LLM dollar
cost against a hard ceiling. Designed to run all four spend phases of
the ICLR validation plan with a single tool.

Usage:
    python scripts/iclr_validation_runner.py \\
        --phase phase2_smoke \\
        --output-root validation_runs/iclr_sweep_<ts>/phase2_smoke \\
        --families farm_baseline_react \\
        --scenarios scenario_farm_world_irrigation,scenario_full_season_balanced \\
        --repeats 1 \\
        --model gpt-4o-mini \\
        --cost-cap-dollars 1.0 \\
        --max-concurrent 2

    python scripts/iclr_validation_runner.py --config validation_runs/iclr.json

For Qwen, use ``--provider qwen``. The runner maps it to ARE's
OpenAI-compatible ``llama-api`` provider inside the subprocess and reads
``QWEN_API_KEY`` plus optional ``QWEN_API_BASE`` / ``DASHSCOPE_API_BASE``.

The runner enforces:
  - per-cell wall-clock timeout (``--cell-timeout-s``, default 300s), with
    graceful SIGINT shutdown before hard termination
  - cumulative cost ceiling (aborts at 80% of cap)
  - per-cell ``--agent-max-iterations`` (default 200) + ``--wait-for-user-input-timeout`` (default 5)
  - subprocess-level isolation (each cell is its own process)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_BIN = str(REPO_ROOT / ".venv312" / "bin" / "python")

# Costs ($/1M tokens) for the EU OpenAI endpoint via litellm. Conservative
# estimates rounded up so the cost guard tends to over-estimate.
MODEL_COSTS = {
    "gpt-4o-mini": {"input_per_mtok": 0.15, "output_per_mtok": 0.60},
    "o4-mini": {"input_per_mtok": 1.10, "output_per_mtok": 4.40},
    "gpt-4o": {"input_per_mtok": 2.50, "output_per_mtok": 10.00},
    "deepseek-chat": {"input_per_mtok": 0.27, "output_per_mtok": 1.10},
    "deepseek-reasoner": {"input_per_mtok": 0.55, "output_per_mtok": 2.19},
    "qwen-plus": {"input_per_mtok": 0.40, "output_per_mtok": 1.20},
    "qwen-max": {"input_per_mtok": 2.40, "output_per_mtok": 9.60},
}

RUNNER_DEFAULTS = {
    "phase": None,
    "output_root": None,
    "families": None,
    "scenarios": None,
    "repeats": 1,
    "model": "gpt-4o-mini",
    "provider": "llama-api",
    "model_family": None,
    "endpoint": None,
    "log_level": "INFO",
    "cost_cap_dollars": 10.0,
    "max_concurrent": 4,
    "cell_timeout_s": 300,
    "cell_timeout_grace_s": 60,
    "agent_max_iterations": 200,
    "wait_for_user_input_timeout": 5.0,
    "scenario_kwargs": None,
    "init_kwargs": None,
    "detail": None,
    "a2a": False,
    "a2a_app_prop": 0.5,
    "a2a_policy": "typed_experts",
    "a2a_app_agent": "default_app_agent",
    "a2a_model": None,
    "a2a_provider": None,
    "a2a_endpoint": None,
}


@dataclass
class CellSpec:
    family: str
    scenario: str
    repeat: int


def _build_cell_command(
    cell: CellSpec,
    model: str,
    provider: str,
    output_dir: Path,
    oracle: bool = False,
    log_level: str = "INFO",
    endpoint: str | None = None,
    scenario_creation_kwargs: str | None = None,
    scenario_initialization_kwargs: str | None = None,
    agent_max_iterations: int = 200,
    wait_for_user_input_timeout: float = 5.0,
    a2a_enabled: bool = False,
    a2a_app_prop: float = 0.5,
    a2a_policy: str = "typed_experts",
    a2a_app_agent: str = "default_app_agent",
    a2a_model: str | None = None,
    a2a_provider: str | None = None,
    a2a_endpoint: str | None = None,
) -> list[str]:
    cmd = [
        PYTHON_BIN,
        "-m",
        "are.simulation.main",
        "-s",
        cell.scenario,
        "-a",
        cell.family,
        "-mp",
        provider,
        "-m",
        model,
        "--log-level",
        log_level,
        "--output_dir",
        str(output_dir),
        "-e",
        "-w",
        str(wait_for_user_input_timeout),
        "--agent-max-iterations",
        str(agent_max_iterations),
    ]
    if endpoint:
        cmd.extend(["--endpoint", endpoint])
    if scenario_creation_kwargs:
        cmd.extend(["--scenario_kwargs", scenario_creation_kwargs])
    if scenario_initialization_kwargs:
        cmd.extend(["--kwargs", scenario_initialization_kwargs])
    if a2a_enabled:
        cmd.extend(["--a2a-app-prop", str(a2a_app_prop)])
        cmd.extend(["--a2a-policy", a2a_policy])
        cmd.extend(["--a2a-app-agent", a2a_app_agent])
        if a2a_model:
            cmd.extend(["--a2a-model", a2a_model])
        if a2a_provider:
            cmd.extend(["--a2a-model-provider", a2a_provider])
        if a2a_endpoint:
            cmd.extend(["--a2a-endpoint", a2a_endpoint])
    if oracle:
        cmd.append("-o")
    return cmd


def _wait_for_cell_process(
    proc: subprocess.Popen,
    timeout_s: int,
    timeout_grace_s: int,
    stderr_h,
    cmd: list[str],
) -> tuple[int, bool, bool]:
    """Wait for a cell process, asking it to stop cleanly before killing it."""
    try:
        return proc.wait(timeout=timeout_s), False, False
    except subprocess.TimeoutExpired:
        stderr_h.write(
            f"\nTimed out after {timeout_s}s; sending SIGINT for graceful shutdown: "
            f"{shlex.join(cmd)}\n"
        )
        stderr_h.flush()
        try:
            proc.send_signal(signal.SIGINT)
            return proc.wait(timeout=timeout_grace_s), True, False
        except subprocess.TimeoutExpired:
            stderr_h.write(
                f"\nGraceful shutdown did not finish within {timeout_grace_s}s; "
                "terminating process.\n"
            )
            stderr_h.flush()
            proc.terminate()
            try:
                return proc.wait(timeout=10), True, True
            except subprocess.TimeoutExpired:
                stderr_h.write("\nProcess did not terminate; killing process.\n")
                stderr_h.flush()
                proc.kill()
                return proc.wait(), True, True


def _load_env_file(env: dict[str, str]) -> None:
    """Merge repo-local .env into env without overriding shell exports."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env.setdefault(key.strip(), val.strip().strip("'\""))


def _prepare_provider_env(
    env: dict[str, str], provider: str, endpoint: str | None
) -> tuple[str, str | None]:
    """Prepare subprocess env for the selected ARE model provider.

    The ARE framework already has a native `deepseek` provider that reads
    DEEPSEEK_API_KEY / DEEPSEEK_API_BASE. Keep `llama-api` compatibility for
    existing OpenAI-compatible runs, but do not force every provider through it.
    Returns the provider and endpoint passed to are.simulation.main.
    """
    if provider == "llama-api":
        if env.get("OPENAI_BASE_URL"):
            env["OPENAI_API_BASE"] = env["OPENAI_BASE_URL"]
        if env.get("OPENAI_API_KEY") and not env.get("LLAMA_API_KEY"):
            env["LLAMA_API_KEY"] = env["OPENAI_API_KEY"]
        if endpoint:
            env["LLAMA_API_BASE"] = endpoint
            return "llama-api", endpoint
        if env.get("OPENAI_BASE_URL") and not env.get("LLAMA_API_BASE"):
            env["LLAMA_API_BASE"] = env["OPENAI_BASE_URL"]
        return "llama-api", endpoint

    if provider == "openai":
        if not env.get("OPENAI_API_KEY"):
            raise RuntimeError("Missing OPENAI_API_KEY for --provider openai")
        # ARE's current `openai` provider path goes through HuggingFaceLLMEngine,
        # which passes HF_INFERENCE_TOKEN/HF_TOKEN as the provider api_key.
        # Keep the provider as "openai", but expose the OpenAI key where that
        # engine actually reads it.
        env.setdefault("HF_INFERENCE_TOKEN", env["OPENAI_API_KEY"])
        return "openai", endpoint

    if provider == "deepseek":
        if not env.get("DEEPSEEK_API_KEY"):
            raise RuntimeError("Missing DEEPSEEK_API_KEY for --provider deepseek")
        resolved_endpoint = endpoint or env.get("DEEPSEEK_API_BASE")
        if resolved_endpoint is None:
            resolved_endpoint = "https://api.deepseek.com/v1"
        env["DEEPSEEK_API_BASE"] = resolved_endpoint
        return "deepseek", resolved_endpoint

    if provider == "qwen":
        qwen_key = env.get("QWEN_API_KEY") or env.get("DASHSCOPE_API_KEY")
        if not qwen_key:
            raise RuntimeError(
                "Missing QWEN_API_KEY or DASHSCOPE_API_KEY for --provider qwen"
            )
        resolved_endpoint = (
            endpoint
            or env.get("QWEN_API_BASE")
            or env.get("DASHSCOPE_API_BASE")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        env["LLAMA_API_KEY"] = qwen_key
        env["LLAMA_API_BASE"] = resolved_endpoint
        return "llama-api", resolved_endpoint

    return provider, endpoint


def _resolve_model_family(provider: str, model: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    provider_l = provider.lower()
    model_l = model.lower()
    if provider_l == "qwen" or "qwen" in model_l:
        return "Qwen"
    if provider_l == "deepseek" or "deepseek" in model_l:
        return "DeepSeek"
    if provider_l in {"llama-api", "openai"} or model_l.startswith(("gpt-", "o")):
        return "GPT"
    return provider


def _classify_level(scenario_id: str) -> str:
    if scenario_id.startswith("scenario_full_season_"):
        return "Level 3"
    if scenario_id.startswith("scenario_physics_"):
        return "Level 2"
    if "physics_action_tick" in scenario_id:
        return "Level 1"
    return "unknown"


def _serialize_json_arg(value: object, field_name: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"))
    raise ValueError(f"Config field {field_name!r} must be a string or object")


def _load_runner_config(config_path: Path | None) -> dict:
    if config_path is None:
        return {}
    suffix = config_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        raise ValueError(
            f"Unsupported config file type {suffix!r}; use .json"
        )
    if not isinstance(payload, dict):
        raise ValueError("Runner config must be a JSON object")
    raw_config = payload.get("iclr_validation_runner", payload)
    if not isinstance(raw_config, dict):
        raise ValueError("Config section 'iclr_validation_runner' must be an object")
    config = {str(key).replace("-", "_"): value for key, value in raw_config.items()}
    unknown = sorted(set(config) - set(RUNNER_DEFAULTS))
    if unknown:
        raise ValueError(f"Unknown runner config field(s): {', '.join(unknown)}")
    if "output_root" in config and config["output_root"] is not None:
        config["output_root"] = Path(str(config["output_root"]))
    for field_name in (
        "repeats",
        "max_concurrent",
        "cell_timeout_s",
        "cell_timeout_grace_s",
        "agent_max_iterations",
    ):
        if field_name in config and config[field_name] is not None:
            config[field_name] = int(config[field_name])
    for field_name in (
        "cost_cap_dollars",
        "wait_for_user_input_timeout",
        "a2a_app_prop",
    ):
        if field_name in config and config[field_name] is not None:
            config[field_name] = float(config[field_name])
    for field_name in ("scenario_kwargs", "init_kwargs"):
        if field_name in config:
            config[field_name] = _serialize_json_arg(config[field_name], field_name)
    for field_name in ("detail", "a2a"):
        if field_name in config:
            config[field_name] = _str_to_bool(config[field_name])
    return config


def _str_to_bool(value: str | bool | None) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got {value!r}")


def _merge_detail_into_creation_kwargs(
    scenario_kwargs: str | None, detail: bool | None
) -> tuple[str | None, bool | None]:
    if detail is None:
        return scenario_kwargs, None
    try:
        payload = json.loads(scenario_kwargs or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"--scenario-kwargs must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("--scenario-kwargs must decode to a JSON object")
    payload["detailed_briefing"] = detail
    return json.dumps(payload, separators=(",", ":")), detail


def _parse_combined(rationale: str) -> float | None:
    """Extract workflow_combined score from rationale freeform text."""
    m = re.search(r"combined=([0-9.]+)", rationale or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_fos(rationale: str) -> dict[str, float]:
    """Extract fos / outcome / decision / efficiency scores."""
    out = {}
    for key in ("outcome", "decision", "efficiency", "fos"):
        m = re.search(rf"\b{key}=([0-9.]+)", rationale or "")
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    return out


def _load_fos_report(scenario_id: str, output_dir: Path) -> dict | None:
    """Load the structured FOS JSON report if present.

    In real-LLM mode `env.dump_dir` is None so the validator writes to
    ``<cwd>/fos_exports/fos/fos_<scenario>.json``. We pick it up there and
    copy a snapshot into the cell dir so concurrent cells don't overwrite
    each other.
    """
    # First check the cell-local path (oracle mode writes here directly).
    paths = list(output_dir.glob(f"fos/fos_{scenario_id}.json"))
    if not paths:
        # Fall back to repo-root cwd default.
        repo_default = REPO_ROOT / "fos_exports" / "fos" / f"fos_{scenario_id}.json"
        if repo_default.exists():
            # Copy into cell dir so this cell's report is preserved.
            dst_dir = output_dir / "fos"
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f"fos_{scenario_id}.json"
            try:
                dst.write_text(repo_default.read_text())
            except Exception:
                pass
            paths = [dst]
    if not paths:
        return None
    try:
        return json.loads(paths[0].read_text())
    except Exception:
        return None


def _estimate_cell_cost_dollars(
    output_jsonl_path: Path, model: str, n_calls_override: int | None = None
) -> tuple[float, int, int]:
    """Estimate $ cost of a cell by parsing usage info from the output trace.

    The are.simulation.main exporter does not currently emit per-call usage
    metrics, so we approximate from the raw stdout/stderr length combined
    with a per-tool-call token estimate. Conservative: assume 3K input + 1K
    output per recorded LLM call (one per tool selection).
    """
    if n_calls_override is not None:
        n_calls = n_calls_override
    elif not output_jsonl_path.exists():
        return (0.0, 0, 0)
    else:
        # Count agent-tool events as a proxy for LLM calls.
        try:
            line = output_jsonl_path.open().readline()
            d = json.loads(line)
            # Look for agent log entries via metadata if present.
            meta = d.get("metadata", {})
            rationale = meta.get("rationale", "") or ""
            # Heuristic: count tool calls from "Agent (N):" line in rationale logs.
            # If unavailable, fall back to a flat estimate of 15 calls.
            n_calls_match = re.search(r"Agent\s*\((\d+)\)", rationale)
            n_calls = int(n_calls_match.group(1)) if n_calls_match else 15
        except Exception:
            n_calls = 15
    cost_per_call = (
        MODEL_COSTS.get(model, MODEL_COSTS["gpt-4o-mini"])["input_per_mtok"] * 3.0e-3
        + MODEL_COSTS.get(model, MODEL_COSTS["gpt-4o-mini"])["output_per_mtok"] * 1.0e-3
    )
    return (n_calls * cost_per_call, n_calls, 0)


def _split_csv_or_list(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = []
        for item in value:
            items.extend(str(item).split(","))
    else:
        raise ValueError(f"Expected comma-separated string or list, got {value!r}")
    return [item.strip() for item in items if item.strip()]


def _build_arg_parser(defaults: dict) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=defaults.get("config"),
        help="Optional JSON config file. CLI arguments override config values.",
    )
    parser.add_argument("--phase", default=defaults.get("phase"))
    parser.add_argument("--output-root", type=Path, default=defaults.get("output_root"))
    parser.add_argument(
        "--families",
        default=defaults.get("families"),
        help="Comma-separated family names, or a list in --config.",
    )
    parser.add_argument(
        "--scenarios",
        default=defaults.get("scenarios"),
        help="Comma-separated scenario IDs, or a list in --config.",
    )
    parser.add_argument("--repeats", type=int, default=defaults.get("repeats"))
    parser.add_argument("--model", default=defaults.get("model"))
    parser.add_argument(
        "--provider",
        default=defaults.get("provider"),
        help=(
            "Model provider: openai, llama-api/openai-compatible, deepseek, or qwen "
            "(qwen is mapped to llama-api in the subprocess)."
        ),
    )
    parser.add_argument(
        "--model-family",
        default=defaults.get("model_family"),
        help="Optional reporting label, e.g. GPT, DeepSeek, Qwen.",
    )
    parser.add_argument(
        "--endpoint",
        default=defaults.get("endpoint"),
        help="Optional provider endpoint override passed to are.simulation.main.",
    )
    parser.add_argument("--log-level", default=defaults.get("log_level"))
    parser.add_argument(
        "--cost-cap-dollars", type=float, default=defaults.get("cost_cap_dollars")
    )
    parser.add_argument(
        "--max-concurrent", type=int, default=defaults.get("max_concurrent")
    )
    parser.add_argument(
        "--cell-timeout-s", type=int, default=defaults.get("cell_timeout_s")
    )
    parser.add_argument(
        "--cell-timeout-grace-s",
        type=int,
        default=defaults.get("cell_timeout_grace_s"),
    )
    parser.add_argument(
        "--agent-max-iterations",
        type=int,
        default=defaults.get("agent_max_iterations"),
    )
    parser.add_argument(
        "--wait-for-user-input-timeout",
        type=float,
        default=defaults.get("wait_for_user_input_timeout"),
    )
    parser.add_argument(
        "--scenario-kwargs",
        default=defaults.get("scenario_kwargs"),
        help=(
            "JSON object passed to are.simulation.main --scenario_kwargs "
            "(scenario constructor fields)."
        ),
    )
    parser.add_argument(
        "--init-kwargs",
        default=defaults.get("init_kwargs"),
        help=(
            "JSON object passed to are.simulation.main --kwargs "
            "(scenario.initialize/init_and_populate_apps)."
        ),
    )
    parser.add_argument(
        "--detail",
        type=_str_to_bool,
        default=defaults.get("detail"),
        help="Set detailed_briefing in --kwargs, e.g. --detail true.",
    )
    parser.add_argument("--a2a", type=_str_to_bool, default=defaults.get("a2a"))
    parser.add_argument(
        "--a2a-app-prop", type=float, default=defaults.get("a2a_app_prop")
    )
    parser.add_argument("--a2a-policy", default=defaults.get("a2a_policy"))
    parser.add_argument("--a2a-app-agent", default=defaults.get("a2a_app_agent"))
    parser.add_argument("--a2a-model", default=defaults.get("a2a_model"))
    parser.add_argument("--a2a-provider", default=defaults.get("a2a_provider"))
    parser.add_argument("--a2a-endpoint", default=defaults.get("a2a_endpoint"))
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    bootstrap_parser = argparse.ArgumentParser(add_help=False)
    bootstrap_parser.add_argument("--config", type=Path, default=None)
    bootstrap_args, _ = bootstrap_parser.parse_known_args(argv)

    defaults = dict(RUNNER_DEFAULTS)
    config_defaults = _load_runner_config(bootstrap_args.config)
    defaults.update(config_defaults)
    defaults["config"] = bootstrap_args.config

    parser = _build_arg_parser(defaults)
    args = parser.parse_args(argv)
    missing = [
        name
        for name in ("phase", "output_root", "families", "scenarios")
        if getattr(args, name) in (None, "")
    ]
    if missing:
        parser.error(
            "Missing required argument(s): "
            + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
            + " (provide on CLI or in --config)"
        )
    return args


def run_cell(
    cell: CellSpec,
    output_root: Path,
    model: str,
    provider: str,
    endpoint: str | None,
    timeout_s: int,
    log_level: str,
    scenario_creation_kwargs: str | None,
    scenario_initialization_kwargs: str | None,
    detail_enabled: bool | None,
    model_family: str,
    agent_max_iterations: int,
    wait_for_user_input_timeout: float,
    a2a_enabled: bool,
    a2a_app_prop: float,
    a2a_policy: str,
    a2a_app_agent: str,
    a2a_model: str | None,
    a2a_provider: str | None,
    a2a_endpoint: str | None,
    timeout_grace_s: int = 60,
) -> dict:
    """Run one cell as a subprocess; capture output and parse FOS / workflow."""
    cell_dir = output_root / f"{cell.family}__{cell.scenario}__r{cell.repeat}"
    cell_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    _load_env_file(env)
    try:
        command_provider, resolved_endpoint = _prepare_provider_env(
            env, provider, endpoint
        )
    except Exception as exc:
        (cell_dir / "stdout.log").write_text("", encoding="utf-8")
        (cell_dir / "stderr.log").write_text(f"{exc}\n", encoding="utf-8")
        return {
            "family": cell.family,
            "scenario": cell.scenario,
            "repeat": cell.repeat,
            "provider": provider,
            "command_provider": None,
            "model": model,
            "model_family": model_family,
            "endpoint": endpoint,
            "level": _classify_level(cell.scenario),
            "a2a_enabled": a2a_enabled,
            "detail_enabled": detail_enabled,
            "wall_s": 0.0,
            "return_code": -3,
            "estimated_cost_dollars": 0.0,
            "exception": str(exc),
            "cell_dir": str(cell_dir),
            "stdout_path": str(cell_dir / "stdout.log"),
            "stderr_path": str(cell_dir / "stderr.log"),
        }
    # Direct FOS export to this cell's dir so concurrent cells don't race.
    env["FOS_EXPORT_DIR"] = str(cell_dir)

    cmd = _build_cell_command(
        cell,
        model,
        command_provider,
        cell_dir,
        oracle=False,
        log_level=log_level,
        endpoint=resolved_endpoint,
        scenario_creation_kwargs=scenario_creation_kwargs,
        scenario_initialization_kwargs=scenario_initialization_kwargs,
        agent_max_iterations=agent_max_iterations,
        wait_for_user_input_timeout=wait_for_user_input_timeout,
        a2a_enabled=a2a_enabled,
        a2a_app_prop=a2a_app_prop,
        a2a_policy=a2a_policy,
        a2a_app_agent=a2a_app_agent,
        a2a_model=a2a_model,
        a2a_provider=a2a_provider,
        a2a_endpoint=a2a_endpoint,
    )
    t0 = time.time()
    stdout_path = cell_dir / "stdout.log"
    stderr_path = cell_dir / "stderr.log"
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_h,
        stderr_path.open("w", encoding="utf-8") as stderr_h,
    ):
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=stdout_h,
            stderr=stderr_h,
            text=True,
            env=env,
        )
        return_code, timed_out, force_killed = _wait_for_cell_process(
            proc, timeout_s, timeout_grace_s, stderr_h, cmd
        )
        wall_s = time.time() - t0

    out_jsonl = cell_dir / "output.jsonl"
    workflow_combined: float | None = None
    fos_metrics: dict[str, float] = {}
    fos_report: dict | None = None
    rationale = ""
    score = None
    status = None
    telemetry: dict = {}
    if out_jsonl.exists():
        try:
            d = json.loads(out_jsonl.open().readline())
            score = d.get("score")
            metadata = d.get("metadata") or {}
            status = metadata.get("status")
            maybe_telemetry = metadata.get("telemetry") or {}
            if isinstance(maybe_telemetry, dict):
                telemetry = maybe_telemetry
            rationale = metadata.get("rationale", "") or ""
            workflow_combined = _parse_combined(rationale)
            fos_metrics = _parse_fos(rationale)
        except Exception:
            pass
        fos_report = _load_fos_report(cell.scenario, cell_dir)

    llm_calls = telemetry.get("llm_calls")
    if not isinstance(llm_calls, (int, float)):
        llm_calls = None
    cost, n_calls, _ = _estimate_cell_cost_dollars(
        out_jsonl, model, int(llm_calls) if llm_calls is not None else None
    )

    safety = None
    crop_loss = None
    gates_matched = None
    gates_total = None
    if fos_report is not None:
        ob = fos_report.get("outcome_breakdown", {})
        safety = ob.get("safety_violations")
        crop_loss = ob.get("crop_loss_count")
        gd = fos_report.get("decision_breakdown") or []
        gates_total = len(gd)
        gates_matched = sum(1 for g in gd if g.get("matched"))

    return {
        "family": cell.family,
        "scenario": cell.scenario,
        "level": _classify_level(cell.scenario),
        "repeat": cell.repeat,
        "provider": provider,
        "command_provider": command_provider,
        "model": model,
        "model_family": model_family,
        "endpoint": resolved_endpoint,
        "a2a_enabled": a2a_enabled,
        "a2a_app_prop": a2a_app_prop if a2a_enabled else 0.0,
        "a2a_policy": a2a_policy if a2a_enabled else "off",
        "a2a_app_agent": a2a_app_agent if a2a_enabled else "",
        "detail_enabled": detail_enabled,
        "scenario_kwargs": scenario_creation_kwargs or "",
        "scenario_initialization_kwargs": scenario_initialization_kwargs or "",
        "wall_s": round(wall_s, 2),
        "return_code": return_code,
        "timed_out": timed_out,
        "force_killed": force_killed,
        "timeout_grace_s": timeout_grace_s if timed_out else "",
        "score": score,
        "status": status,
        "workflow_combined": workflow_combined,
        "fos": fos_metrics.get("fos"),
        "outcome": fos_metrics.get("outcome"),
        "decision": fos_metrics.get("decision"),
        "efficiency": fos_metrics.get("efficiency"),
        "reasoning_steps": int(llm_calls) if llm_calls is not None else n_calls,
        "llm_calls": int(llm_calls) if llm_calls is not None else "",
        "tool_calls": telemetry.get("tool_calls", ""),
        "planned_steps": telemetry.get("planned_steps", ""),
        "executed_steps": telemetry.get("executed_steps", ""),
        "skill_hits": telemetry.get("skill_hits", ""),
        "reflection_signals": telemetry.get("reflection_signals", ""),
        "replan_signals": telemetry.get("replan_signals", ""),
        "delegation_signals": telemetry.get("delegation_signals", ""),
        "verification_signals": telemetry.get("verification_signals", ""),
        "error_count": telemetry.get("error_count", ""),
        "safety_violations": safety,
        "crop_loss": crop_loss,
        "gates_matched": gates_matched,
        "gates_total": gates_total,
        "estimated_cost_dollars": round(cost, 4),
        "cell_dir": str(cell_dir),
        "stdout_path": str(cell_dir / "stdout.log"),
        "stderr_path": str(cell_dir / "stderr.log"),
    }


def main() -> int:
    args = _parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    scenario_creation_kwargs, detail_enabled = _merge_detail_into_creation_kwargs(
        args.scenario_kwargs, args.detail
    )
    model_family = _resolve_model_family(args.provider, args.model, args.model_family)
    families = _split_csv_or_list(args.families)
    scenarios = _split_csv_or_list(args.scenarios)
    cells = [
        CellSpec(family=f, scenario=s, repeat=r)
        for f in families
        for s in scenarios
        for r in range(1, args.repeats + 1)
    ]
    total_cells = len(cells)
    print(
        f"=== {args.phase}: {total_cells} cells, "
        f"provider={args.provider}, model={args.model}, family={model_family}, "
        f"a2a={bool(args.a2a)}, detail={detail_enabled}, "
        f"cap=${args.cost_cap_dollars:.2f} ==="
    )

    rows: list[dict] = []
    cumulative_cost = 0.0
    aborted = False
    cap_threshold = args.cost_cap_dollars * 0.80
    started = time.time()

    with ThreadPoolExecutor(max_workers=args.max_concurrent) as pool:
        futures = {
            pool.submit(
                run_cell,
                cell,
                args.output_root,
                args.model,
                args.provider,
                args.endpoint,
                args.cell_timeout_s,
                args.log_level,
                scenario_creation_kwargs,
                args.init_kwargs,
                detail_enabled,
                model_family,
                args.agent_max_iterations,
                args.wait_for_user_input_timeout,
                bool(args.a2a),
                args.a2a_app_prop,
                args.a2a_policy,
                args.a2a_app_agent,
                args.a2a_model,
                args.a2a_provider,
                args.a2a_endpoint,
                args.cell_timeout_grace_s,
            ): cell
            for cell in cells
        }
        completed = 0
        for fut in as_completed(futures):
            cell = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:  # pragma: no cover
                row = {
                    "family": cell.family,
                    "scenario": cell.scenario,
                    "repeat": cell.repeat,
                    "level": _classify_level(cell.scenario),
                    "provider": args.provider,
                    "model_family": model_family,
                    "model": args.model,
                    "a2a_enabled": bool(args.a2a),
                    "detail_enabled": detail_enabled,
                    "wall_s": None,
                    "return_code": -2,
                    "estimated_cost_dollars": 0.0,
                    "exception": repr(exc),
                }
            cumulative_cost += float(row.get("estimated_cost_dollars") or 0.0)
            rows.append(row)
            completed += 1
            stamp = f"[{completed}/{total_cells}] cum=${cumulative_cost:.3f}"
            fos = row.get("fos")
            wf = row.get("workflow_combined")
            rc = row.get("return_code")
            print(
                f"  {stamp}  rc={rc}  wall={row.get('wall_s')}s  "
                f"wf={wf}  fos={fos}  "
                f"{row.get('provider', args.provider)}:{row.get('model', args.model)}  "
                f"{row['family']}__{row['scenario']}_r{row['repeat']}"
            )
            if cumulative_cost >= cap_threshold and not aborted:
                aborted = True
                print(
                    f"  WARN: cumulative cost ${cumulative_cost:.3f} >= 80% of cap "
                    f"${args.cost_cap_dollars:.2f}; cancelling remaining cells"
                )
                for f, c in futures.items():
                    if not f.done():
                        f.cancel()

    # Write CSV.
    csv_path = args.output_root / "results.csv"
    if rows:
        fieldnames = sorted({k for row in rows for k in row.keys()})
        with csv_path.open("w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"  wrote {csv_path} ({len(rows)} rows)")

    # Summary.
    success = sum(1 for r in rows if r.get("return_code") == 0)
    duration = time.time() - started
    print(
        f"\n  SUMMARY: {success}/{len(rows)} succeeded, "
        f"cum_cost=${cumulative_cost:.3f}, duration={duration:.0f}s"
    )
    return 0 if not aborted else 2


if __name__ == "__main__":
    sys.exit(main())
