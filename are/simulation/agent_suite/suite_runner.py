import csv
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from are.simulation.config import ARE_SIMULATION_ROOT


class SuiteConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    pack_name: str
    family: str
    scenario_id: str
    model_profile: str
    model: str
    provider: str
    endpoint: str | None
    output_dir: str
    repeat_index: int
    export: bool
    log_level: str
    wait_for_user_input_timeout: float | None
    agent_max_iterations: int | None
    model_resolution: str
    a2a_enabled: bool
    a2a_app_prop: float
    a2a_policy: str
    a2a_app_agent: str
    a2a_model_profile: str | None
    a2a_model: str | None
    a2a_provider: str | None
    a2a_endpoint: str | None
    a2a_model_resolution: str | None


def load_suite_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise SuiteConfigError(f"Config file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise SuiteConfigError("Suite config must be a mapping at the top level")

    for required in ["scenario_sets", "model_profiles", "packs"]:
        if required not in config:
            raise SuiteConfigError(f"Missing required config section: {required}")
    if not isinstance(config["scenario_sets"], dict):
        raise SuiteConfigError("scenario_sets must be a mapping")
    if not isinstance(config["model_profiles"], dict):
        raise SuiteConfigError("model_profiles must be a mapping")
    if not isinstance(config["packs"], dict):
        raise SuiteConfigError("packs must be a mapping")
    return config


def _ensure_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SuiteConfigError(f"{name} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise SuiteConfigError(f"{name} must contain non-empty strings")
    return [item.strip() for item in value]


def _resolve_profile(
    profile_name: str,
    profiles: dict[str, Any],
    force_mock: bool,
    force_real: bool,
    enable_real_model_preflight: bool,
) -> tuple[str, str, str | None, str]:
    if profile_name not in profiles:
        raise SuiteConfigError(f"Unknown model_profile '{profile_name}'")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise SuiteConfigError(f"model_profile '{profile_name}' must be a mapping")

    model = str(profile.get("model", "")).strip()
    provider = str(profile.get("provider", "")).strip()
    endpoint = profile.get("endpoint", None)
    if endpoint is not None:
        endpoint = str(endpoint)

    resolution = "profile"
    if force_mock:
        model = "mock-model"
        provider = "mock"
        endpoint = None
        resolution = "forced_mock"
    if force_real and provider == "mock":
        fallback_profile = profiles.get("openai_fast", {})
        if isinstance(fallback_profile, dict):
            model = str(fallback_profile.get("model", "gpt-4o-mini"))
            provider = str(fallback_profile.get("provider", "llama-api"))
            endpoint = fallback_profile.get("endpoint", None)
            resolution = "force_real_from_mock_profile"

    if force_real:
        model, provider, endpoint, resolution = _resolve_real_model_with_fallback(
            model=model,
            provider=provider,
            endpoint=endpoint,
            resolution=resolution,
            enable_preflight=enable_real_model_preflight,
        )

    if not model:
        raise SuiteConfigError(f"model missing for profile '{profile_name}'")
    if not provider:
        raise SuiteConfigError(f"provider missing for profile '{profile_name}'")
    return model, provider, endpoint, resolution


def _resolve_a2a_block(pack_name: str, pack: dict[str, Any]) -> dict[str, Any]:
    a2a = pack.get("a2a", None)
    if a2a is None:
        return {
            "enabled": False,
            "app_prop": 0.0,
            "policy": "generic",
            "app_agent": "default_app_agent",
            "model_profile": None,
        }
    if not isinstance(a2a, dict):
        raise SuiteConfigError(f"packs.{pack_name}.a2a must be a mapping")

    enabled = bool(a2a.get("enabled", False))
    app_prop_raw = a2a.get("app_prop", 0.5 if enabled else 0.0)
    try:
        app_prop = float(app_prop_raw)
    except (TypeError, ValueError) as exc:
        raise SuiteConfigError(
            f"packs.{pack_name}.a2a.app_prop must be a number"
        ) from exc
    if app_prop < 0.0 or app_prop > 1.0:
        raise SuiteConfigError(
            f"packs.{pack_name}.a2a.app_prop must be in [0.0, 1.0]"
        )
    if not enabled:
        app_prop = 0.0

    policy = str(a2a.get("policy", "typed_experts" if enabled else "generic")).strip()
    if policy not in {"typed_experts", "generic"}:
        raise SuiteConfigError(
            f"packs.{pack_name}.a2a.policy must be one of: typed_experts, generic"
        )
    if not enabled:
        policy = "generic"

    app_agent = str(a2a.get("app_agent", "default_app_agent")).strip()
    if not app_agent:
        raise SuiteConfigError(f"packs.{pack_name}.a2a.app_agent cannot be empty")

    model_profile = a2a.get("model_profile", None)
    if model_profile is not None:
        model_profile = str(model_profile).strip()
        if not model_profile:
            model_profile = None

    return {
        "enabled": enabled,
        "app_prop": app_prop,
        "policy": policy,
        "app_agent": app_agent,
        "model_profile": model_profile,
    }


def _resolve_real_model_with_fallback(
    model: str,
    provider: str,
    endpoint: str | None,
    resolution: str,
    enable_preflight: bool,
) -> tuple[str, str, str | None, str]:
    provider_normalized = provider.strip()
    endpoint_resolved = endpoint
    if endpoint_resolved is None and provider_normalized in {"openai", "llama-api"}:
        endpoint_resolved = os.environ.get("OPENAI_BASE_URL") or os.environ.get(
            "LLAMA_API_BASE"
        )

    if provider_normalized not in {"openai", "llama-api"}:
        return model, provider_normalized, endpoint_resolved, resolution

    preferred_candidates = ["o4-mini", "gpt-4o-mini"]
    first_candidate = preferred_candidates[0]
    second_candidate = preferred_candidates[1]

    if not enable_preflight:
        return (
            first_candidate,
            provider_normalized,
            endpoint_resolved,
            "real_preflight_skipped_o4_mini",
        )

    ok_first, _ = _preflight_model_access(
        model=first_candidate,
        provider=provider_normalized,
        endpoint=endpoint_resolved,
    )
    if ok_first:
        return (
            first_candidate,
            provider_normalized,
            endpoint_resolved,
            "real_preflight_o4_mini",
        )

    ok_second, _ = _preflight_model_access(
        model=second_candidate,
        provider=provider_normalized,
        endpoint=endpoint_resolved,
    )
    if ok_second:
        return (
            second_candidate,
            provider_normalized,
            endpoint_resolved,
            "real_fallback_gpt_4o_mini",
        )

    return (
        second_candidate,
        provider_normalized,
        endpoint_resolved,
        "real_fallback_unverified",
    )


_PREFLIGHT_CACHE: dict[tuple[str, str, str | None], tuple[bool, str | None]] = {}


def _preflight_model_access(
    model: str,
    provider: str,
    endpoint: str | None,
) -> tuple[bool, str | None]:
    cache_key = (provider, model, endpoint)
    if cache_key in _PREFLIGHT_CACHE:
        return _PREFLIGHT_CACHE[cache_key]

    provider_normalized = provider.strip()
    if provider_normalized not in {"openai", "llama-api"}:
        result = (True, "skip_non_openai_provider")
        _PREFLIGHT_CACHE[cache_key] = result
        return result

    api_key = os.environ.get("LLAMA_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        result = (False, "missing_api_key")
        _PREFLIGHT_CACHE[cache_key] = result
        return result

    api_base = endpoint or os.environ.get("LLAMA_API_BASE") or os.environ.get(
        "OPENAI_BASE_URL"
    )

    try:
        from litellm import completion

        completion(
            model=model,
            custom_llm_provider="openai",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
            api_key=api_key,
            api_base=api_base,
            timeout=20,
        )
        result = (True, None)
    except Exception as exc:  # pragma: no cover - external API behavior
        result = (False, str(exc))

    _PREFLIGHT_CACHE[cache_key] = result
    return result


def expand_run_specs(
    config: dict[str, Any],
    force_mock: bool = False,
    force_real: bool = False,
    enable_real_model_preflight: bool = True,
) -> list[RunSpec]:
    defaults = config.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise SuiteConfigError("defaults must be a mapping")

    base_output = str(defaults.get("output_root", "outputs/agent_suite")).strip()
    export = bool(defaults.get("export", True))
    log_level = str(defaults.get("log_level", "INFO")).strip()
    default_repeats = int(defaults.get("repeats", 1))
    wait_for_user_input_timeout_raw = defaults.get("wait_for_user_input_timeout", None)
    wait_for_user_input_timeout = None
    if wait_for_user_input_timeout_raw is not None:
        try:
            wait_for_user_input_timeout = float(wait_for_user_input_timeout_raw)
        except (TypeError, ValueError) as exc:
            raise SuiteConfigError(
                "defaults.wait_for_user_input_timeout must be a number"
            ) from exc
    agent_max_iterations_raw = defaults.get("agent_max_iterations", None)
    agent_max_iterations = None
    if agent_max_iterations_raw is not None:
        try:
            agent_max_iterations = int(agent_max_iterations_raw)
        except (TypeError, ValueError) as exc:
            raise SuiteConfigError(
                "defaults.agent_max_iterations must be an integer"
            ) from exc
        if agent_max_iterations <= 0:
            raise SuiteConfigError("defaults.agent_max_iterations must be > 0")

    scenario_sets = config["scenario_sets"]
    model_profiles = config["model_profiles"]
    packs = config["packs"]

    run_specs: list[RunSpec] = []
    for pack_name in sorted(packs.keys()):
        pack = packs[pack_name]
        if not isinstance(pack, dict):
            raise SuiteConfigError(f"Pack '{pack_name}' must be a mapping")
        families = _ensure_list(pack.get("families"), f"packs.{pack_name}.families")
        referenced_sets = _ensure_list(
            pack.get("scenario_sets"), f"packs.{pack_name}.scenario_sets"
        )
        profile_name = str(pack.get("model_profile", "")).strip()
        if not profile_name:
            raise SuiteConfigError(f"packs.{pack_name}.model_profile is required")
        repeats = int(pack.get("repeats", default_repeats))
        if repeats <= 0:
            raise SuiteConfigError(f"packs.{pack_name}.repeats must be > 0")
        a2a = _resolve_a2a_block(pack_name, pack)

        model, provider, endpoint, model_resolution = _resolve_profile(
            profile_name=profile_name,
            profiles=model_profiles,
            force_mock=force_mock,
            force_real=force_real,
            enable_real_model_preflight=enable_real_model_preflight,
        )
        a2a_model_profile = None
        a2a_model = None
        a2a_provider = None
        a2a_endpoint = None
        a2a_model_resolution = None
        if a2a["enabled"]:
            a2a_model_profile = a2a["model_profile"]
            if a2a_model_profile is None:
                a2a_model = model
                a2a_provider = provider
                a2a_endpoint = endpoint
                a2a_model_resolution = "inherit_main_model"
            else:
                (
                    a2a_model,
                    a2a_provider,
                    a2a_endpoint,
                    a2a_model_resolution,
                ) = _resolve_profile(
                    profile_name=a2a_model_profile,
                    profiles=model_profiles,
                    force_mock=force_mock,
                    force_real=force_real,
                    enable_real_model_preflight=enable_real_model_preflight,
                )

        scenarios: list[str] = []
        for scenario_set_name in referenced_sets:
            if scenario_set_name not in scenario_sets:
                raise SuiteConfigError(
                    f"Unknown scenario_set '{scenario_set_name}' in pack '{pack_name}'"
                )
            scenarios.extend(
                _ensure_list(
                    scenario_sets[scenario_set_name],
                    f"scenario_sets.{scenario_set_name}",
                )
            )

        deduped_scenarios = list(dict.fromkeys(scenarios))
        for family in families:
            for scenario_id in deduped_scenarios:
                for repeat_index in range(repeats):
                    run_id = (
                        f"{pack_name}__{family}__{scenario_id}__r{repeat_index + 1}"
                    )
                    out_dir = Path(base_output) / run_id
                    run_specs.append(
                        RunSpec(
                            run_id=run_id,
                            pack_name=pack_name,
                            family=family,
                            scenario_id=scenario_id,
                            model_profile=profile_name,
                            model=model,
                            provider=provider,
                            endpoint=endpoint,
                            output_dir=str(out_dir),
                            repeat_index=repeat_index,
                            export=export,
                            log_level=log_level,
                            wait_for_user_input_timeout=wait_for_user_input_timeout,
                            agent_max_iterations=agent_max_iterations,
                            model_resolution=model_resolution,
                            a2a_enabled=a2a["enabled"],
                            a2a_app_prop=a2a["app_prop"],
                            a2a_policy=a2a["policy"],
                            a2a_app_agent=a2a["app_agent"],
                            a2a_model_profile=a2a_model_profile,
                            a2a_model=a2a_model,
                            a2a_provider=a2a_provider,
                            a2a_endpoint=a2a_endpoint,
                            a2a_model_resolution=a2a_model_resolution,
                        )
                    )
    return run_specs


def _build_command(spec: RunSpec, output_dir: str | None = None) -> list[str]:
    resolved_output_dir = output_dir or spec.output_dir
    command = [
        sys.executable,
        "-m",
        "are.simulation.main",
        "-s",
        spec.scenario_id,
        "-a",
        spec.family,
        "-m",
        spec.model,
        "-mp",
        spec.provider,
        "--output_dir",
        resolved_output_dir,
        "--log-level",
        spec.log_level,
    ]
    if spec.endpoint:
        command.extend(["--endpoint", spec.endpoint])
    if spec.export:
        command.append("-e")
    if spec.wait_for_user_input_timeout is not None:
        command.extend(
            ["--wait-for-user-input-timeout", str(spec.wait_for_user_input_timeout)]
        )
    if spec.agent_max_iterations is not None:
        command.extend(["--agent-max-iterations", str(spec.agent_max_iterations)])
    if spec.a2a_enabled:
        command.extend(["--a2a-app-prop", str(spec.a2a_app_prop)])
        command.extend(["--a2a-policy", spec.a2a_policy])
        command.extend(["--a2a-app-agent", spec.a2a_app_agent])
        if spec.a2a_model:
            command.extend(["--a2a-model", spec.a2a_model])
        if spec.a2a_provider:
            command.extend(["--a2a-model-provider", spec.a2a_provider])
        if spec.a2a_endpoint:
            command.extend(["--a2a-endpoint", spec.a2a_endpoint])
    return command


def _parse_output_jsonl(output_jsonl_path: Path) -> dict[str, Any]:
    if not output_jsonl_path.exists():
        return {"status": "missing_output_jsonl"}
    with open(output_jsonl_path, "r", encoding="utf-8") as handle:
        line = handle.readline().strip()
    if not line:
        return {"status": "empty_output_jsonl"}
    payload = json.loads(line)
    metadata = payload.get("metadata", {})
    telemetry = metadata.get("telemetry", {})
    if not isinstance(telemetry, dict):
        telemetry = {}
    result = {
        "task_id": payload.get("task_id"),
        "trace_id": payload.get("trace_id"),
        "score": payload.get("score"),
        "status": metadata.get("status"),
        "rationale": metadata.get("rationale"),
        "telemetry": telemetry,
    }
    return result


def _flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    flattened = dict(row)
    telemetry = flattened.pop("telemetry", {})
    if isinstance(telemetry, dict):
        for key, value in telemetry.items():
            flattened[f"telemetry_{key}"] = value
    return flattened


def _build_execution_env(spec: RunSpec) -> dict[str, str]:
    env = os.environ.copy()
    if spec.provider == "llama-api":
        if not env.get("LLAMA_API_KEY") and env.get("OPENAI_API_KEY"):
            env["LLAMA_API_KEY"] = env["OPENAI_API_KEY"]
        if not env.get("LLAMA_API_BASE"):
            if spec.endpoint:
                env["LLAMA_API_BASE"] = spec.endpoint
            elif env.get("OPENAI_BASE_URL"):
                env["LLAMA_API_BASE"] = env["OPENAI_BASE_URL"]

    if spec.a2a_provider == "llama-api":
        if not env.get("LLAMA_API_KEY") and env.get("OPENAI_API_KEY"):
            env["LLAMA_API_KEY"] = env["OPENAI_API_KEY"]
        if not env.get("LLAMA_API_BASE"):
            if spec.a2a_endpoint:
                env["LLAMA_API_BASE"] = spec.a2a_endpoint
            elif env.get("OPENAI_BASE_URL"):
                env["LLAMA_API_BASE"] = env["OPENAI_BASE_URL"]

    return env


def _derive_infra_flags(
    process: subprocess.CompletedProcess[str],
    parsed_row: dict[str, Any],
) -> dict[str, Any]:
    combined_logs = f"{process.stdout}\n{process.stderr}".lower()
    auth_issue_markers = [
        "auth error",
        "invalid api key",
        "unauthorized",
        "forbidden",
        "insufficient_quota",
        "llmengineexception: auth error",
        "error code: 401",
        "geography restrictions enabled",
    ]
    connectivity_issue_markers = [
        "apiconnectionerror",
        "openai.apiconnectionerror",
        "connection error.",
        "connection refused",
        "connection reset",
        "timed out",
        "read timeout",
        "name resolution",
        "network is unreachable",
        "max retries exceeded",
    ]

    has_auth_issue = any(marker in combined_logs for marker in auth_issue_markers)
    has_connectivity_issue = any(
        marker in combined_logs for marker in connectivity_issue_markers
    )
    telemetry = parsed_row.get("telemetry", {})
    llm_calls = 0
    if isinstance(telemetry, dict):
        maybe_calls = telemetry.get("llm_calls", 0)
        if isinstance(maybe_calls, (int, float)):
            llm_calls = int(maybe_calls)
    trace_exported = bool(parsed_row.get("trace_id"))
    llm_evidence = llm_calls > 0

    return {
        "infra_exit_ok": process.returncode == 0,
        "infra_auth_ok": not has_auth_issue,
        "infra_connectivity_ok": not has_connectivity_issue,
        "infra_llm_calls_positive": llm_evidence,
        "infra_trace_exported": trace_exported,
    }


def run_suite(
    run_specs: list[RunSpec],
    dry_run: bool = False,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    if repo_root is None:
        repo_root = ARE_SIMULATION_ROOT.parent

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suite_output_root = repo_root / "outputs" / "agent_suite_runs" / timestamp
    suite_output_root.mkdir(parents=True, exist_ok=True)

    manifest_rows = [asdict(spec) for spec in run_specs]
    manifest_path = suite_output_root / "suite_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest_rows, handle, indent=2)

    if dry_run:
        return {
            "mode": "dry_run",
            "suite_output_root": str(suite_output_root),
            "manifest_path": str(manifest_path),
            "runs": manifest_rows,
        }

    result_rows: list[dict[str, Any]] = []
    for spec in run_specs:
        output_dir = suite_output_root / spec.run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        command = _build_command(spec, output_dir=str(output_dir))
        process = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=_build_execution_env(spec),
        )

        stderr_path = output_dir / "stderr.log"
        stdout_path = output_dir / "stdout.log"
        with open(stdout_path, "w", encoding="utf-8") as handle:
            handle.write(process.stdout)
        with open(stderr_path, "w", encoding="utf-8") as handle:
            handle.write(process.stderr)

        row: dict[str, Any] = {
            "run_id": spec.run_id,
            "pack_name": spec.pack_name,
            "family": spec.family,
            "scenario_id": spec.scenario_id,
            "model_profile": spec.model_profile,
            "model": spec.model,
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "model_resolution": spec.model_resolution,
            "repeat_index": spec.repeat_index,
            "output_dir": str(output_dir),
            "configured_output_dir": spec.output_dir,
            "a2a_enabled": spec.a2a_enabled,
            "a2a_app_prop": spec.a2a_app_prop,
            "a2a_policy": spec.a2a_policy,
            "a2a_app_agent": spec.a2a_app_agent,
            "a2a_model_profile": spec.a2a_model_profile,
            "a2a_model": spec.a2a_model,
            "a2a_provider": spec.a2a_provider,
            "a2a_endpoint": spec.a2a_endpoint,
            "a2a_model_resolution": spec.a2a_model_resolution,
            "command": " ".join(command),
            "return_code": process.returncode,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "telemetry": {},
        }

        output_jsonl = output_dir / "output.jsonl"
        parsed = _parse_output_jsonl(output_jsonl)
        row.update(parsed)
        row.update(_derive_infra_flags(process, row))
        row["infra_pass"] = (
            bool(row["infra_exit_ok"])
            and bool(row["infra_auth_ok"])
            and bool(row["infra_connectivity_ok"])
            and bool(row["infra_trace_exported"])
            and bool(row["infra_llm_calls_positive"])
        )
        if process.returncode != 0:
            row["status"] = row.get("status", "command_failed")
        result_rows.append(row)

    json_path = suite_output_root / "suite_results.json"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(result_rows, handle, indent=2)

    flattened_rows = [_flatten_row(row) for row in result_rows]
    csv_path = suite_output_root / "suite_results.csv"
    field_names: list[str] = []
    for row in flattened_rows:
        for key in row.keys():
            if key not in field_names:
                field_names.append(key)
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names)
        writer.writeheader()
        for row in flattened_rows:
            writer.writerow(row)

    return {
        "mode": "run",
        "suite_output_root": str(suite_output_root),
        "manifest_path": str(manifest_path),
        "json_path": str(json_path),
        "csv_path": str(csv_path),
        "rows": result_rows,
    }
