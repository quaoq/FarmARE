from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "iclr_validation_runner.py"
RETRIEVED_CONTEXT_ENV_VAR = "FARM_ARE_RETRIEVED_CONTEXT_PATH"
FARMING_GROUPS = {"establishment", "management", "harvest"}
GROUPED_RETRIEVAL_QUOTAS = {
    "establishment": 1,
    "management": 2,
    "harvest": 1,
}

DEFAULT_SCENARIOS = [
    "scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery",
    "scenario_full_season_hb_coldspring_planting_window_heihe50",
    "scenario_full_season_hb_wetcold_high_residue_establishment",
    "scenario_full_season_heinong84_staggered_planting",
    "scenario_full_season_hb_fertilizer_quota_edge_lowfertility",
    "scenario_full_season_hb_insect_after_fungicide_budget_conflict",
    "scenario_full_season_hb_two_dry_patches_one_irrigation",
    "scenario_full_season_hb_wetjune_shortwindow_trafficability",
    "scenario_full_season_hb_storage_capacity_limit_batching",
    "scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence",
]

DIRECT_GROUPS: dict[str, object] = {
    "detail_false": False,
    "detail_true": True,
    "l2_human_same": "l2_human_same",
    "l2_human_differ": "l2_human_differ",
    "l2_textsim_differ": "l2_textsim_differ",
    "l2_textsim_grouped_differ": "l2_textsim_grouped_differ",
}

PATHSIM_GROUPS = {
    "l2_pathsim_differ",
    "l2_pathsim_grouped_differ",
    "l3_pathsim_same",
    "l3_pathsim_differ",
}

L3_TEXTSIM_GROUPS = {
    "l3_textsim_differ",
}

GROUP_ALIASES = {
    "l3_textdiff_differ": "l3_textsim_differ",
}


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(
        description=(
            "Run the L2/L3 skill-library retrieval experiments. Direct groups "
            "run in one pass; path-sim groups first consume DETAIL=FALSE "
            "candidate traces, build a per-scenario retrieved-context map, "
            "then rerun the target scenarios with that map."
        )
    )
    parser.add_argument(
        "--groups",
        default=",".join([*DIRECT_GROUPS, *sorted(PATHSIM_GROUPS), *sorted(L3_TEXTSIM_GROUPS)]),
        help=(
            "Comma-separated subset of detail_false,detail_true,l2_human_same,"
            "l2_human_differ,l2_textsim_differ,l2_textsim_grouped_differ,"
            "l2_pathsim_differ,"
            "l2_pathsim_grouped_differ,"
            "l3_pathsim_same,l3_pathsim_differ,l3_textsim_differ."
        ),
    )
    parser.add_argument("--scenarios", default=",".join(DEFAULT_SCENARIOS))
    parser.add_argument(
        "--reference-scenarios",
        default=None,
        help=(
            "Comma-separated L3 oracle-workflow reference pool for l3_pathsim_* "
            "and l3_textsim_differ retrieval. Defaults to --scenarios for "
            "backward compatibility."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "validation_runs" / f"l3_skill_retrieval_{stamp}",
    )
    parser.add_argument("--families", default="farm_baseline_react")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default="qwen3.6-flash-2026-04-16")
    parser.add_argument("--provider", default="qwen")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--cost-cap-dollars", type=float, default=10.0)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument("--cell-timeout-s", type=int, default=1800)
    parser.add_argument("--cell-timeout-grace-s", type=int, default=120)
    parser.add_argument("--agent-max-iterations", type=int, default=300)
    parser.add_argument("--wait-for-user-input-timeout", type=float, default=5.0)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--pathsim-top-k", type=int, default=4)
    parser.add_argument(
        "--candidate-output-root",
        type=Path,
        default=None,
        help=(
            "Existing DETAIL=FALSE runner output to use for path-sim retrieval. "
            "If omitted and a path-sim group is requested, this script first "
            "runs a candidate pass under <output-root>/candidate_detail_false."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print runner commands. Path-sim context generation is skipped unless an existing candidate root is supplied.",
    )
    parser.add_argument(
        "--preview-context-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory where the exact per-scenario prompt briefing "
            "for each requested context mode is written before the agent run."
        ),
    )
    return parser.parse_args()


def build_runner_command(
    args: argparse.Namespace,
    *,
    group: str,
    scenario_kwargs: dict[str, Any],
    output_dir: Path,
) -> list[str]:
    cmd = [
        sys.executable,
        str(RUNNER),
        "--phase",
        f"l3_skill_{group}",
        "--output-root",
        str(output_dir),
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
        "--scenario-kwargs",
        json.dumps(scenario_kwargs, ensure_ascii=False, separators=(",", ":")),
    ]
    if args.endpoint:
        cmd.extend(["--endpoint", args.endpoint])
    return cmd


def _run_or_print(cmd: list[str], *, env: dict[str, str], cwd: Path, dry_run: bool) -> int:
    print("\n" + " ".join(cmd), flush=True)
    if RETRIEVED_CONTEXT_ENV_VAR in env:
        print(f"{RETRIEVED_CONTEXT_ENV_VAR}={env[RETRIEVED_CONTEXT_ENV_VAR]}", flush=True)
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=cwd, env=env).returncode


def _canonical_group(group: str) -> str:
    return GROUP_ALIASES.get(group, group)


def _write_prompt_context_previews(
    args: argparse.Namespace,
    *,
    group: str,
    scenarios: list[str],
    detailed_briefing: Any,
    context_path: Path | None = None,
) -> None:
    if args.preview_context_dir is None:
        return
    from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_context_briefings import (
        RETRIEVED_CONTEXT_ENV_VAR as BRIEFING_RETRIEVED_CONTEXT_ENV_VAR,
        build_l3_context_briefing,
    )

    out_dir = args.preview_context_dir / group
    out_dir.mkdir(parents=True, exist_ok=True)
    old_context_path = os.environ.get(BRIEFING_RETRIEVED_CONTEXT_ENV_VAR)
    if context_path is not None:
        os.environ[BRIEFING_RETRIEVED_CONTEXT_ENV_VAR] = str(context_path.resolve())
    try:
        index_lines = [
            f"# Prompt context preview: {group}",
            "",
            f"- detailed_briefing: `{detailed_briefing}`",
            f"- scenarios: {len(scenarios)}",
            "",
        ]
        for scenario_id in scenarios:
            slug, spec = _l3_spec_for_retrieval(scenario_id)
            if spec is None:
                raise KeyError(f"No L3 ScenarioSpec found for {scenario_id!r}")
            text = build_l3_context_briefing(spec, detailed_briefing)
            path = out_dir / f"{scenario_id}.md"
            path.write_text(text + "\n", encoding="utf-8")
            index_lines.append(f"- `{scenario_id}` -> `{path.name}`")
            if slug:
                index_lines[-1] += f" ({slug})"
        (out_dir / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
        print(f"[context-preview] {group}: wrote {len(scenarios)} file(s) to {out_dir}", flush=True)
    finally:
        if old_context_path is None:
            os.environ.pop(BRIEFING_RETRIEVED_CONTEXT_ENV_VAR, None)
        else:
            os.environ[BRIEFING_RETRIEVED_CONTEXT_ENV_VAR] = old_context_path


def _load_candidate_agent_workflow(candidate_root: Path, scenario_id: str) -> dict[str, Any]:
    rows_path = candidate_root / "results.csv"
    if not rows_path.is_file():
        raise FileNotFoundError(f"Candidate results.csv not found: {rows_path}")
    matches: list[Path] = []
    with rows_path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            if row.get("scenario") == scenario_id and row.get("return_code") == "0":
                cell = _resolve_candidate_path(candidate_root, row.get("cell_dir") or "")
                matches.append(cell)
    if not matches:
        raise FileNotFoundError(f"No successful candidate cell for {scenario_id} in {rows_path}")
    output_jsonl = matches[0] / "output.jsonl"
    payload = json.loads(output_jsonl.read_text(encoding="utf-8").splitlines()[0])
    rationale = ((payload.get("metadata") or {}).get("rationale") or "")
    match = re.search(r"workflow_agent=([^,\n]+)", rationale)
    if match:
        workflow_path = Path(match.group(1))
        if not workflow_path.is_file():
            raise FileNotFoundError(f"workflow_agent path does not exist: {workflow_path}")
        return json.loads(workflow_path.read_text(encoding="utf-8"))

    trace_path = _resolve_candidate_path(candidate_root, str(payload.get("trace_id") or ""))
    if not trace_path.is_file():
        raise ValueError(f"No workflow_agent path or trace_id found in {output_jsonl}")
    return _workflow_from_trace_json(trace_path)


def _resolve_candidate_path(candidate_root: Path, raw_path: str) -> Path:
    """Resolve paths from a candidate run that may have been produced in another repo.

    `iclr_validation_runner.py` records `cell_dir` and `trace_id` exactly as the
    run saw them. Older runs often used paths relative to their own repo root
    (for example `validation_runs/...`). When this harness is executed from
    `FarmARE_physics` against a candidate root from sibling checkout `FarmARE`,
    resolving those paths against this repo root is wrong. Try the common bases
    and return the first existing candidate.
    """
    path = Path(raw_path)
    if path.is_absolute():
        return path
    candidates = [
        REPO_ROOT / path,
        candidate_root / path,
        candidate_root.parent / path,
        candidate_root.parent.parent / path,
        Path.cwd() / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _workflow_from_trace_json(trace_path: Path) -> dict[str, Any]:
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    workflow: dict[str, Any] = {}
    for idx, event in enumerate(trace.get("completed_events") or []):
        action = event.get("action") or {}
        app = action.get("app")
        fn = action.get("function")
        if not app or not fn:
            continue
        args = {
            item.get("name"): item.get("value")
            for item in action.get("args") or []
            if isinstance(item, dict) and item.get("name")
        }
        workflow[f"step{len(workflow)}"] = {
            "name": f"step{len(workflow)}",
            "content": (event.get("metadata") or {}).get("return_value"),
            "op_type": action.get("operation_type") or "WRITE",
            "tool_name": f"{app}__{fn}",
            "tool_args": args,
            "depends_on": [],
            "time": event.get("event_time"),
        }
    return workflow


def _load_registry():
    from are.simulation.scenarios.utils.registry import registry

    return registry


def _oracle_workflow(scenario_id: str) -> dict[str, Any]:
    from are.simulation.scenarios.workflow_validation import workflow_from_event_log
    from are.simulation.environment import Environment, EnvironmentConfig

    scenario_cls = _load_registry().get_scenario(scenario_id)
    scenario = scenario_cls()
    scenario.initialize()
    env_config = EnvironmentConfig(
        oracle_mode=True,
        queue_based_loop=True,
        time_increment_in_seconds=getattr(scenario, "time_increment_in_seconds", 1),
        exit_when_no_events=True,
    )
    if getattr(scenario, "start_time", None) and scenario.start_time > 0:
        env_config.start_time = scenario.start_time
    env = Environment(config=env_config)
    env.run(scenario, wait_for_end=False)
    env.join()
    events = env.event_log.list_view() if getattr(env, "event_log", None) is not None else []
    failed = [event for event in events if event.failed()]
    if failed:
        raise RuntimeError(
            f"Oracle workflow run failed for {scenario_id}: "
            f"{[getattr(event.metadata, 'exception', None) for event in failed[:5]]}"
        )
    return workflow_from_event_log(events)


def _metric_score(oracle_wf: dict[str, Any], agent_wf: dict[str, Any]) -> float:
    from are.simulation.scenarios.fos.spatiotemporal import compute_farm_fos

    report = compute_farm_fos(oracle_workflow=oracle_wf, agent_workflow=agent_wf)
    return float(report.farm_fos if report.farm_fos is not None else 0.0)


def _ordered_workflow_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        workflow.values(),
        key=lambda step: (
            step.get("time") is None,
            step.get("time") or 0,
            step.get("name") or "",
        ),
    )


def _workflow_from_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for idx, step in enumerate(steps):
        copied = dict(step)
        copied["name"] = f"step{idx}"
        out[copied["name"]] = copied
    return out


def _best_local_metric_score(
    oracle_wf: dict[str, Any], agent_wf: dict[str, Any]
) -> tuple[float, int, int]:
    """Score a short L2 oracle against the best local window in a full L3 path."""
    agent_steps = _ordered_workflow_steps(agent_wf)
    oracle_len = max(1, len(_ordered_workflow_steps(oracle_wf)))
    if len(agent_steps) <= oracle_len:
        return _metric_score(oracle_wf, agent_wf), 0, len(agent_steps)

    window_sizes = sorted(
        {
            min(len(agent_steps), max(oracle_len, 8)),
            min(len(agent_steps), max(int(oracle_len * 1.5), 12)),
            min(len(agent_steps), max(oracle_len * 2, 16)),
            min(len(agent_steps), max(oracle_len * 3, 24)),
        }
    )
    stride = max(1, min(8, oracle_len // 2))
    best: tuple[float, int, int] = (-1.0, 0, window_sizes[0])
    for size in window_sizes:
        starts = list(range(0, max(1, len(agent_steps) - size + 1), stride))
        final_start = max(0, len(agent_steps) - size)
        if final_start not in starts:
            starts.append(final_start)
        for start in starts:
            window = _workflow_from_steps(agent_steps[start : start + size])
            score = _metric_score(oracle_wf, window)
            if score > best[0]:
                best = (score, start, size)
    return best


def _scenario_slug_map() -> dict[str, str]:
    from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_context_briefings import (
        _SCENARIO_LIBRARY_SLUGS,
    )

    return dict(_SCENARIO_LIBRARY_SLUGS)


def _l2_scenarios_by_source() -> dict[str, list[str]]:
    base = (
        REPO_ROOT
        / "are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits"
    )
    out: dict[str, list[str]] = {}
    for slug in set(_scenario_slug_map().values()):
        ids: list[str] = []
        for path in sorted((base / slug).glob("scenario_l2*.py")):
            text = path.read_text(encoding="utf-8")
            match = re.search(r"SCENARIO_ID\s*=\s*[\"']([^\"']+)[\"']", text)
            if match:
                ids.append(match.group(1))
        out[slug] = ids
    return out


def _l2_farming_groups_by_source() -> dict[tuple[str, str], str]:
    """Map (source_slug, source_l2_scenario_id) to explicit skill group."""
    base = (
        REPO_ROOT
        / "are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/knowledge_library_pilot"
    )
    out: dict[tuple[str, str], str] = {}
    for path in sorted(base.glob("*/library_same_l3.json")):
        source_slug = path.parent.name
        payload = json.loads(path.read_text(encoding="utf-8"))
        for skill in payload.get("skills", []):
            if not isinstance(skill, dict):
                continue
            source_l2 = str(skill.get("source_l2_scenario_id") or "").strip()
            if not source_l2:
                continue
            group = str(skill.get("farming_group") or "").strip()
            if group not in FARMING_GROUPS:
                skill_id = str(skill.get("skill_id") or "unknown")
                raise ValueError(
                    f"Atomic skill {source_slug}/{skill_id} must define "
                    f"farming_group as one of {sorted(FARMING_GROUPS)}; got {group!r}"
                )
            out[(source_slug, source_l2)] = group
    return out


def _l2_skill_cards_by_source_l2() -> dict[tuple[str, str], dict[str, Any]]:
    """Map (source_slug, source_l2_scenario_id) to the atomic skill card."""
    base = (
        REPO_ROOT
        / "are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/knowledge_library_pilot"
    )
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(base.glob("*/library_same_l3.json")):
        source_slug = path.parent.name
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_l3 = str(payload.get("source_l3_scenario_id") or "")
        for skill in payload.get("skills", []):
            if not isinstance(skill, dict):
                continue
            source_l2 = str(skill.get("source_l2_scenario_id") or "").strip()
            if not source_l2:
                continue
            oracle_events = skill.get("oracle_events") or []
            if not oracle_events:
                skill_id = str(skill.get("skill_id") or "unknown")
                raise ValueError(
                    f"Atomic skill {source_slug}/{skill_id} has empty oracle_events; "
                    "regenerate or repair the skill card before using it as context."
                )
            out[(source_slug, source_l2)] = {
                "source_slug": source_slug,
                "source_l3_scenario_id": source_l3,
                "skill": skill,
            }
    return out


def _compact_args(args: dict[str, Any]) -> str:
    if not args:
        return ""
    keys = [
        "start_ridge",
        "end_ridge",
        "start",
        "end",
        "seed_type",
        "seed_spacing_cm",
        "depth_cm",
        "liters_per_ridge",
        "nutrient_amount",
        "water_mm",
        "hours",
        "target_moisture_pct",
        "days",
    ]
    picked = {k: args[k] for k in keys if k in args}
    if not picked:
        picked = {k: args[k] for k in sorted(args)[:4]}
    return json.dumps(picked, ensure_ascii=False, separators=(",", ":"))


def _workflow_summary(workflow: dict[str, Any], *, max_steps: int = 60) -> str:
    steps = sorted(workflow.values(), key=lambda x: (x.get("time") is None, x.get("time") or 0, x.get("name") or ""))
    lines: list[str] = []
    for step in steps:
        tool = str(step.get("tool_name") or "")
        op_type = str(step.get("op_type") or "")
        if op_type == "READ" and len(lines) > 20:
            continue
        args = _compact_args(step.get("tool_args") or {})
        lines.append(f"{len(lines)+1}. {tool}({args})")
        if len(lines) >= max_steps:
            lines.append(f"... truncated, total_workflow_steps={len(steps)}")
            break
    return "\n".join(lines)


def _l2_farming_group(
    source_slug: str, l2_id: str, group_by_l2: dict[tuple[str, str], str]
) -> str:
    group = group_by_l2.get((source_slug, l2_id))
    if group not in FARMING_GROUPS:
        raise KeyError(
            f"L2 {source_slug}/{l2_id} has no explicit farming_group in its "
            "atomic skill card. Add source_l2_scenario_id and farming_group to "
            "the corresponding library_same_l3.json entry."
        )
    return group


def _l2_group_priority(group: str, workflow: dict[str, Any]) -> int:
    tools = {
        str(step.get("tool_name") or "").lower()
        for step in workflow.values()
    }
    tool_text = " ".join(sorted(tools))
    if group == "management" and any(
        token in tool_text
        for token in (
            "apply_fertigation",
            "irrigate",
            "apply_herbicide",
            "apply_fungicide",
            "apply_insecticide",
            "apply_pesticide",
            "pesticide_manual",
        )
    ):
        return 2
    if group == "management" and any(token in tool_text for token in ("inspect", "survey", "sensor")):
        return 1
    return 0


def _simple_text_relevance(target: str, source_slug: str, l2_id: str, workflow: dict[str, Any]) -> float:
    return _textsim_score(
        target,
        _l2_candidate_text(source_slug=source_slug, l2_id=l2_id, workflow=workflow),
    )


def _tokens(text: str) -> Counter[str]:
    stop = {
        "scenario",
        "full",
        "season",
        "hb",
        "l2",
        "l3",
        "the",
        "and",
        "or",
        "to",
        "of",
        "in",
        "with",
    }
    return Counter(
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 1 and token not in stop
    )


def _textsim_score(query_text: str, candidate_text: str) -> float:
    query = _tokens(query_text)
    candidate = _tokens(candidate_text)
    if not query or not candidate:
        return 0.0
    overlap = sum(min(query[token], candidate[token]) for token in query.keys() & candidate.keys())
    return overlap / ((sum(query.values()) ** 0.5) * (sum(candidate.values()) ** 0.5))


def _target_text_for_retrieval(scenario_id: str) -> str:
    slug, spec = _l3_spec_for_retrieval(scenario_id)
    parts = [scenario_id, slug or ""]
    if spec is not None:
        for name in (
            "profile_name",
            "description",
            "briefing_text",
            "primary_seed",
            "cultivar",
            "primary_metric",
        ):
            parts.append(str(getattr(spec, name, "") or ""))
    return " ".join(parts)


def _l3_spec_for_retrieval(scenario_id: str) -> tuple[str | None, Any | None]:
    from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_catalog import (
        SPECS,
        get_spec,
    )

    slug = _scenario_slug_map().get(scenario_id)
    if slug:
        try:
            return slug, get_spec(slug)
        except KeyError:
            pass
    for candidate_slug, spec in SPECS.items():
        if getattr(spec, "scenario_id", None) == scenario_id:
            return candidate_slug, spec
    try:
        scenario_cls = _load_registry().get_scenario(scenario_id)
        scenario = scenario_cls()
        return slug, SimpleNamespace(
            scenario_id=scenario_id,
            profile_name=getattr(scenario, "profile_name", ""),
            description=getattr(scenario, "description", ""),
            briefing_text=getattr(scenario, "briefing_text", ""),
            primary_seed=getattr(scenario, "primary_seed", ""),
            cultivar=getattr(scenario, "cultivar", ""),
            primary_metric=getattr(scenario, "primary_metric", ""),
            detailed_briefing_text=getattr(scenario, "detailed_briefing_text", None),
        )
    except Exception:
        pass
    return None, None


def _l3_reference_text(scenario_id: str, workflow: dict[str, Any]) -> str:
    return " ".join(
        [
            _target_text_for_retrieval(scenario_id),
            _workflow_summary(workflow, max_steps=90),
        ]
    )


def _skill_text_by_l2() -> dict[tuple[str, str], str]:
    base = (
        REPO_ROOT
        / "are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/knowledge_library_pilot"
    )
    out: dict[tuple[str, str], str] = {}
    for path in sorted(base.glob("*/library_same_l3.json")):
        source_slug = path.parent.name
        payload = json.loads(path.read_text(encoding="utf-8"))
        for skill in payload.get("skills", []):
            if not isinstance(skill, dict):
                continue
            skill_id = str(skill.get("skill_id") or "")
            source_l2 = str(skill.get("source_l2_scenario_id") or "")
            text = " ".join(
                [
                    skill_id,
                    source_l2,
                    str(skill.get("task_type") or ""),
                    str(skill.get("crop_stage") or ""),
                    str(skill.get("belief") or ""),
                    json.dumps(skill.get("evidence_chain") or [], ensure_ascii=False),
                    json.dumps(skill.get("constraints") or [], ensure_ascii=False),
                    json.dumps(skill.get("success_checks") or [], ensure_ascii=False),
                ]
            )
            if source_l2:
                out[(source_slug, source_l2)] = text
            if skill_id:
                out[(source_slug, skill_id)] = text
    return out


def _l2_candidate_text(
    *, source_slug: str, l2_id: str, workflow: dict[str, Any], skill_text_by_l2: dict[tuple[str, str], str] | None = None
) -> str:
    tools = " ".join(
        str(step.get("tool_name") or "").lower()
        for step in workflow.values()
    )
    skill_text = ""
    if skill_text_by_l2 is not None:
        skill_text = skill_text_by_l2.get((source_slug, l2_id), "")
        if not skill_text:
            for (slug, skill_id), text in skill_text_by_l2.items():
                if slug == source_slug and skill_id and skill_id in l2_id:
                    skill_text = text
                    break
    return f"{source_slug} {l2_id} {tools} {skill_text}"


def _format_jsonish(value: Any, *, max_len: int = 900) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _advance_time_seconds(args: dict[str, Any]) -> int:
    try:
        return max(
            0,
            int(args.get("seconds", 0) or 0)
            + int(args.get("minutes", 0) or 0) * 60
            + int(args.get("hours", 0) or 0) * 3600
            + int(args.get("days", 0) or 0) * 86400,
        )
    except (TypeError, ValueError):
        return 0


def _advance_time_args(total_seconds: int) -> dict[str, int]:
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    args: dict[str, int] = {}
    if days:
        args["days"] = days
    if hours:
        args["hours"] = hours
    if minutes:
        args["minutes"] = minutes
    if seconds or not args:
        args["seconds"] = seconds
    return args


def _compact_oracle_events(events: list[Any]) -> list[Any]:
    compacted: list[Any] = []
    for event in events:
        if not isinstance(event, dict):
            compacted.append(event)
            continue
        if event.get("tool") != "SystemApp.advance_time":
            compacted.append(event)
            continue
        args = event.get("args")
        if not isinstance(args, dict):
            compacted.append(event)
            continue
        if (
            compacted
            and isinstance(compacted[-1], dict)
            and compacted[-1].get("tool") == "SystemApp.advance_time"
            and isinstance(compacted[-1].get("args"), dict)
        ):
            previous = compacted[-1]
            total_seconds = _advance_time_seconds(previous["args"]) + _advance_time_seconds(args)
            previous["args"] = _advance_time_args(total_seconds)
            continue
        copied = dict(event)
        copied["args"] = dict(args)
        compacted.append(copied)
    return compacted


def _render_l2_skill_card(
    *,
    card: dict[str, Any],
    score_label: str,
    score: float,
    window_start: int | None = None,
    window_size: int | None = None,
) -> str:
    skill = card["skill"]
    oracle_events = skill.get("oracle_events") or []
    if not oracle_events:
        source = card.get("source_slug", "")
        skill_id = skill.get("skill_id", "unknown")
        raise ValueError(
            f"Atomic skill {source}/{skill_id} has empty oracle_events; "
            "regenerate or repair the skill card before using it as context."
        )
    oracle_events = _compact_oracle_events(oracle_events)
    source = card.get("source_slug", "")
    source_l3 = card.get("source_l3_scenario_id", "")
    source_l2 = skill.get("source_l2_scenario_id") or ""
    header = f"### Skill: {skill.get('skill_id', 'unknown')} ({score_label}={score:.4f})"
    lines = [
        header,
        f"- source_l3: `{source_l3}`",
        f"- source_library: `{source}`",
        f"- source_l2: `{source_l2}`",
        f"- farming_group: `{skill.get('farming_group', '')}`",
        f"- task_type: `{skill.get('task_type', '')}`; stage: `{skill.get('crop_stage', '')}`",
    ]
    if window_start is not None and window_size is not None:
        lines.append(f"- matched_candidate_window: start_step={window_start}, size={window_size}")
    lines.extend(
        [
            f"- belief: {skill.get('belief', '')}",
            f"- evidence_chain: {_format_jsonish(skill.get('evidence_chain', []), max_len=700)}",
            f"- constraints: {_format_jsonish(skill.get('constraints', []), max_len=900)}",
            f"- success_checks: {_format_jsonish(skill.get('success_checks', []), max_len=700)}",
            f"- oracle_event_template: {_format_jsonish(oracle_events, max_len=1400)}",
            "- transfer_note: This is an example from a different L3. Use the "
            "skill structure, evidence chain, and agronomic constraints; do not "
            "blindly copy source-L3 dates, ridges, or rates unless target tools "
            "confirm them.",
        ]
    )
    return "\n".join(lines)


def _render_l2_pathsim_context(
    *,
    title: str,
    note: str,
    ranked: list[tuple[float, int, int, str, str, str]],
    skill_cards_by_l2: dict[tuple[str, str], dict[str, Any]],
    top_k: int | None = None,
) -> str:
    selected = ranked if top_k is None else ranked[:top_k]
    parts = [title, "", note, ""]
    for score, window_start, window_size, source_slug, l2_id, group in selected:
        card = skill_cards_by_l2.get((source_slug, l2_id))
        if card is None:
            raise KeyError(
                f"Pathsim selected {source_slug}/{l2_id}, but no matching atomic "
                "skill card exists. Add source_l2_scenario_id to library_same_l3.json."
            )
        parts.append(
            _render_l2_skill_card(
                card=card,
                score_label="pathsim_score",
                score=score,
                window_start=window_start,
                window_size=window_size,
            )
        )
        parts.append("")
    return "\n".join(parts).strip()


def _build_l2_pathsim_contexts(candidate_root: Path, scenarios: list[str], top_k: int) -> dict[str, str]:
    slug_by_scenario = _scenario_slug_map()
    l2_by_source = _l2_scenarios_by_source()
    l2_pool: list[tuple[str, str]] = [
        (slug, sid)
        for slug, scenario_ids in l2_by_source.items()
        for sid in scenario_ids
    ]
    group_by_l2 = _l2_farming_groups_by_source()
    skill_cards_by_l2 = _l2_skill_cards_by_source_l2()
    oracle_cache: dict[str, dict[str, Any]] = {}
    contexts: dict[str, str] = {}
    for target in scenarios:
        target_slug = slug_by_scenario.get(target)
        candidate_wf = _load_candidate_agent_workflow(candidate_root, target)
        ranked: list[tuple[float, int, int, str, str, str]] = []
        for source_slug, l2_id in l2_pool:
            if source_slug == target_slug:
                continue
            if l2_id not in oracle_cache:
                oracle_cache[l2_id] = _oracle_workflow(l2_id)
            score, window_start, window_size = _best_local_metric_score(
                oracle_cache[l2_id], candidate_wf
            )
            group = _l2_farming_group(source_slug, l2_id, group_by_l2)
            ranked.append((score, window_start, window_size, source_slug, l2_id, group))
        ranked.sort(reverse=True)
        contexts[target] = _render_l2_pathsim_context(
            title="# L2_PATHSIM_DIFFER retrieved atomic workflow context",
            note="These L2 examples were selected by comparing each cross-L3 L2 oracle workflow against the best local window of the target DETAIL=FALSE candidate workflow. Use them as correction examples; do not copy source-specific ridges, dates, or rates without target evidence.",
            ranked=ranked,
            skill_cards_by_l2=skill_cards_by_l2,
            top_k=top_k,
        )
    return contexts


def _build_l2_pathsim_grouped_contexts(candidate_root: Path, scenarios: list[str]) -> dict[str, str]:
    slug_by_scenario = _scenario_slug_map()
    l2_by_source = _l2_scenarios_by_source()
    l2_pool: list[tuple[str, str]] = [
        (slug, sid)
        for slug, scenario_ids in l2_by_source.items()
        for sid in scenario_ids
    ]
    group_by_l2 = _l2_farming_groups_by_source()
    skill_cards_by_l2 = _l2_skill_cards_by_source_l2()
    oracle_cache: dict[str, dict[str, Any]] = {}
    contexts: dict[str, str] = {}
    for target in scenarios:
        target_slug = slug_by_scenario.get(target)
        candidate_wf = _load_candidate_agent_workflow(candidate_root, target)
        by_group: dict[str, list[tuple[float, int, int, str, str, str]]] = {
            "establishment": [],
            "management": [],
            "harvest": [],
        }
        for source_slug, l2_id in l2_pool:
            if source_slug == target_slug:
                continue
            if l2_id not in oracle_cache:
                oracle_cache[l2_id] = _oracle_workflow(l2_id)
            score, window_start, window_size = _best_local_metric_score(
                oracle_cache[l2_id], candidate_wf
            )
            group = _l2_farming_group(source_slug, l2_id, group_by_l2)
            by_group.setdefault(group, []).append(
                (score, window_start, window_size, source_slug, l2_id, group)
            )

        selected: list[tuple[float, int, int, str, str, str]] = []
        for group in ("establishment", "management", "harvest"):
            ranked_group = sorted(by_group.get(group, []), reverse=True)
            selected.extend(ranked_group[: GROUPED_RETRIEVAL_QUOTAS[group]])
        contexts[target] = _render_l2_pathsim_context(
            title="# L2_PATHSIM_GROUPED_DIFFER retrieved atomic workflow context",
            note="These cross-L3 L2 examples were selected with path-based grouped search: one establishment skill, up to two management skills, and one harvest skill when available. Establishment combines field preparation and initial planting because those actions often form one establishment L2; replanting is treated as in-season management because it is an emergence-recovery decision after diagnosis. This condition uses path similarity only. Treat source ridges, dates, and rates as examples, not target answers.",
            ranked=selected,
            skill_cards_by_l2=skill_cards_by_l2,
            top_k=None,
        )
    return contexts


def _build_l3_pathsim_contexts(
    candidate_root: Path,
    target_scenarios: list[str],
    reference_scenarios: list[str],
    top_k: int,
    *,
    same: bool,
) -> dict[str, str]:
    oracle_cache: dict[str, dict[str, Any]] = {}
    contexts: dict[str, str] = {}
    for target in target_scenarios:
        candidate_wf = _load_candidate_agent_workflow(candidate_root, target)
        ranked: list[tuple[float, str]] = []
        for source in reference_scenarios:
            if not same and source == target:
                continue
            if source not in oracle_cache:
                oracle_cache[source] = _oracle_workflow(source)
            ranked.append((_metric_score(oracle_cache[source], candidate_wf), source))
        if not ranked:
            mode = "l3_pathsim_same" if same else "l3_pathsim_differ"
            raise ValueError(
                f"{mode} found no reference scenarios for target {target!r}. "
                "Pass --reference-scenarios with at least one usable L3 reference scenario."
            )
        ranked.sort(reverse=True)
        title = "L3_PATHSIM_SAME" if same else "L3_PATHSIM_DIFFER"
        parts = [
            f"# {title} retrieved full-workflow context",
            "",
            "These L3 reference workflows were selected by comparing the target DETAIL=FALSE candidate workflow against the L3 oracle-workflow reference pool. This is a full-workflow self-correction baseline, not an L2 atomic-skill library condition.",
            "",
        ]
        for score, source in ranked[:top_k]:
            parts.extend(
                [
                    f"## Retrieved L3: `{source}`",
                    f"- pathsim_score: {score:.4f}",
                    "```text",
                    _workflow_summary(oracle_cache[source], max_steps=90),
                    "```",
                    "",
                ]
            )
        contexts[target] = "\n".join(parts).strip()
    return contexts


def _build_l3_textsim_contexts(
    target_scenarios: list[str],
    reference_scenarios: list[str],
    top_k: int,
) -> dict[str, str]:
    oracle_cache: dict[str, dict[str, Any]] = {}
    reference_text_cache: dict[str, str] = {}
    contexts: dict[str, str] = {}
    for target in target_scenarios:
        query = _target_text_for_retrieval(target)
        ranked: list[tuple[float, str]] = []
        for source in reference_scenarios:
            if source == target:
                continue
            if source not in oracle_cache:
                oracle_cache[source] = _oracle_workflow(source)
            if source not in reference_text_cache:
                reference_text_cache[source] = _l3_reference_text(source, oracle_cache[source])
            ranked.append((_textsim_score(query, reference_text_cache[source]), source))
        if not ranked:
            raise ValueError(
                f"l3_textsim_differ found no reference scenarios for target {target!r}. "
                "Pass --reference-scenarios with at least one L3 reference scenario "
                "different from the target."
            )
        ranked.sort(reverse=True)
        parts = [
            "# L3_TEXTSIM_DIFFER retrieved full-workflow context",
            "",
            "These L3 reference workflows were selected by text similarity between the target L3 description and cross-L3 oracle-workflow references. This is a full-workflow retrieval baseline, not an L2 atomic-skill library condition. Treat source ridges, dates, and rates as examples only; do not copy them into the target task without target evidence.",
            "",
        ]
        for score, source in ranked[:top_k]:
            parts.extend(
                [
                    f"## Retrieved L3: `{source}`",
                    f"- textsim_score: {score:.4f}",
                    "```text",
                    _workflow_summary(oracle_cache[source], max_steps=90),
                    "```",
                    "",
                ]
            )
        contexts[target] = "\n".join(parts).strip()
    return contexts


def _write_context_map(path: Path, *, mode: str, contexts: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": mode, "contexts": contexts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def main() -> int:
    args = parse_args()
    raw_groups = _csv(args.groups)
    groups: list[str] = []
    for group in raw_groups:
        canonical = _canonical_group(group)
        if canonical not in groups:
            groups.append(canonical)
    known = set(DIRECT_GROUPS) | PATHSIM_GROUPS | L3_TEXTSIM_GROUPS
    unknown = [group for group in groups if group not in known]
    if unknown:
        raise SystemExit(f"Unknown group(s): {', '.join(unknown)}")

    scenarios = _csv(args.scenarios)
    reference_scenarios = _csv(args.reference_scenarios) if args.reference_scenarios else scenarios
    args.output_root.mkdir(parents=True, exist_ok=True)

    pathsim_requested = any(group in PATHSIM_GROUPS for group in groups)
    candidate_root = args.candidate_output_root
    if pathsim_requested and candidate_root is None:
        candidate_root = args.output_root / "candidate_detail_false"
        cmd = build_runner_command(
            args,
            group="candidate_detail_false",
            scenario_kwargs={"detailed_briefing": False},
            output_dir=candidate_root,
        )
        rc = _run_or_print(cmd, env=os.environ.copy(), cwd=REPO_ROOT, dry_run=args.dry_run)
        if rc != 0:
            return rc

    for group in groups:
        if group in DIRECT_GROUPS:
            if group == "detail_false" and pathsim_requested and candidate_root == args.output_root / "candidate_detail_false":
                continue
            _write_prompt_context_previews(
                args,
                group=group,
                scenarios=scenarios,
                detailed_briefing=DIRECT_GROUPS[group],
            )
            cmd = build_runner_command(
                args,
                group=group,
                scenario_kwargs={"detailed_briefing": DIRECT_GROUPS[group]},
                output_dir=args.output_root / group,
            )
            rc = _run_or_print(cmd, env=os.environ.copy(), cwd=REPO_ROOT, dry_run=args.dry_run)
            if rc != 0:
                return rc
            continue

        if group in PATHSIM_GROUPS and args.dry_run and args.candidate_output_root is None:
            print(
                f"\n[DRY-RUN] {group}: context map will be generated after "
                "candidate_detail_false has produced results.csv and workflows.",
                flush=True,
            )
            continue
        if group in PATHSIM_GROUPS and candidate_root is None:
            raise RuntimeError(f"{group} requires a candidate output root")
        if group == "l2_pathsim_differ":
            contexts = _build_l2_pathsim_contexts(candidate_root, scenarios, args.pathsim_top_k)
        elif group == "l2_pathsim_grouped_differ":
            contexts = _build_l2_pathsim_grouped_contexts(candidate_root, scenarios)
        elif group == "l3_pathsim_same":
            contexts = _build_l3_pathsim_contexts(
                candidate_root,
                scenarios,
                reference_scenarios,
                args.pathsim_top_k,
                same=True,
            )
        elif group == "l3_pathsim_differ":
            contexts = _build_l3_pathsim_contexts(
                candidate_root,
                scenarios,
                reference_scenarios,
                args.pathsim_top_k,
                same=False,
            )
        elif group == "l3_textsim_differ":
            contexts = _build_l3_textsim_contexts(
                scenarios,
                reference_scenarios,
                args.pathsim_top_k,
            )
        else:  # pragma: no cover
            raise RuntimeError(group)
        context_path = _write_context_map(
            args.output_root / "retrieved_contexts" / f"{group}.json",
            mode=group,
            contexts=contexts,
        )
        _write_prompt_context_previews(
            args,
            group=group,
            scenarios=scenarios,
            detailed_briefing=group,
            context_path=context_path,
        )
        env = os.environ.copy()
        env[RETRIEVED_CONTEXT_ENV_VAR] = str(context_path.resolve())
        cmd = build_runner_command(
            args,
            group=group,
            scenario_kwargs={"detailed_briefing": group},
            output_dir=args.output_root / group,
        )
        rc = _run_or_print(cmd, env=env, cwd=REPO_ROOT, dry_run=args.dry_run)
        if rc != 0:
            return rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
