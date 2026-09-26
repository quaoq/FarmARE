#!/usr/bin/env python3
"""Pinned-source JSON bridges for the Who&When and AgentRx comparators.

This file intentionally uses only the Python standard library. Run it with the
interpreter from the comparator's isolated environment. It reads one
``dcore_comparator_request_v1`` from stdin and emits one
``comparator_result_v1`` to stdout. Provider and upstream diagnostic output is
captured so stdout remains machine-readable.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

WHO_WHEN_REVISION = "f4d2b6da464a826580e59b3a0eae15ea2d642d7c"
AGENTRX_REVISION = "7a18c79708e7671be15124460f4f7296107c2a55"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _request() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if value.get("schema_version") != "dcore_comparator_request_v1":
        raise ValueError("unsupported comparator request schema")
    packet = value.get("packet")
    if not isinstance(packet, dict) or not packet.get("packet_digest"):
        raise ValueError("request is missing its diagnostic packet or digest")
    return value


def _result(
    request: dict[str, Any],
    *,
    revision: str,
    witnesses: list[dict[str, Any]],
    provider_requests: int,
    provider_tokens: int | None,
    prompt_digest: str,
    model_settings: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "comparator_result_v1",
        "method": request["method"],
        "capability": request["capability"],
        "status": "ok",
        "packet_digest": request["packet"]["packet_digest"],
        "witnesses": witnesses,
        "source_revision": revision,
        "provider_requests": provider_requests,
        "provider_tokens": provider_tokens,
        "prompt_digest": prompt_digest,
        "model_settings": model_settings,
        "adapter_metadata": metadata,
    }


def _decision_steps(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return list(packet.get("decisions") or packet.get("local_contexts") or ())


def shared_evidence_view(packet: dict[str, Any]) -> dict[str, Any]:
    """Documented normalized evidence supplied to both external methods."""

    return {
        "facts": [
            {
                key: fact.get(key)
                for key in (
                    "version_id",
                    "fact_key",
                    "value",
                    "scope",
                    "world_time",
                    "valid_until",
                    "visible_to",
                    "source_event_id",
                )
            }
            for fact in packet.get("fact_versions", ())
        ],
        "messages": list(packet.get("messages", ())),
        "receipts": list(packet.get("receipts", ())),
        "prompt_inclusion": [
            {
                "decision_id": decision.get("decision_id"),
                "actor_id": decision.get("actor_id"),
                "acquired_fact_ids": decision.get("knowledge_snapshot", {}).get(
                    "item_ids", ()
                ),
                "prompt_fact_ids": decision.get("prompt_item_ids", ()),
                "prompt_message_ids": decision.get("prompt_message_ids", ()),
            }
            for decision in _decision_steps(packet)
        ],
        "cutoff": {
            "decision_id": packet.get("prefix_decision_id"),
            "event_index": packet.get("cutoff_event_index"),
            "world_time": packet.get("cutoff_world_time"),
        },
    }


def who_when_dataset(packet: dict[str, Any]) -> dict[str, Any]:
    """Project a diagnostic packet into the upstream hand-crafted format."""

    history = []
    for index, decision in enumerate(_decision_steps(packet)):
        history.append(
            {
                "role": str(decision.get("actor_id") or "unknown_actor"),
                "content": json.dumps(
                    {
                        "step": index,
                        "decision_id": decision.get("decision_id"),
                        "logical_time": decision.get("logical_time"),
                        "proposal": decision.get("proposed_intent"),
                        "knowledge_snapshot": decision.get("knowledge_snapshot"),
                        "prompt_item_ids": decision.get("prompt_item_ids", ()),
                        "prompt_message_ids": decision.get("prompt_message_ids", ()),
                    },
                    sort_keys=True,
                ),
            }
        )
    return {
        "question": packet["public_task_contract"],
        # The upstream method expects a known answer. Post-hoc packets may expose
        # the permitted terminal outcome; prefix packets deliberately leave it empty.
        "ground_truth": json.dumps(packet.get("outcome"), sort_keys=True)
        if packet.get("outcome") is not None
        else "",
        "history": history,
        "shared_evidence_view": shared_evidence_view(packet),
    }


def _witness(
    request: dict[str, Any],
    *,
    actor_id: str,
    step_number: int,
    mechanism: str,
    explanation: str,
    method: str,
) -> dict[str, Any]:
    decisions = _decision_steps(request["packet"])
    valid_step = 0 <= step_number < len(decisions)
    if valid_step:
        normalized = step_number
        decision = decisions[step_number]
        decision_id = str(decision.get("decision_id") or f"step-{step_number}")
        decision_time = decision.get("logical_time")
    else:
        normalized = step_number
        decision_id = "unknown"
        decision_time = None
    witness_id = _digest(
        [request["method"], request["packet"]["packet_digest"], normalized, actor_id]
    )[:24]
    return {
        "schema_version": "diagnostic_witness_v2",
        "witness_id": witness_id,
        "decision_id": decision_id,
        "obligation_id": f"{method}:posthoc_attribution",
        "prerequisite_id": f"{method}:reported_step_{normalized}",
        "actor_id": actor_id or "unknown",
        "mechanism": mechanism,
        "supporting_event_ids": [],
        "fact_version_ids": [],
        "root_support_group": f"{method}:{decision_id}",
        "determination": "supported" if valid_step else "unresolved",
        "decision_time": decision_time,
        "explanation": explanation,
    }


def run_who_when(request: dict[str, Any]) -> dict[str, Any]:
    root = Path(os.environ["DCORE_WHO_WHEN_ROOT"]).resolve()
    automated = root / "Automated_FA"
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if revision != WHO_WHEN_REVISION:
        raise ValueError(f"Who&When checkout revision mismatch: {revision}")

    model = os.environ.get("DCORE_WHO_WHEN_MODEL", "gpt-4o")
    provider = os.environ.get("DCORE_WHO_WHEN_PROVIDER", "openai").lower()
    max_tokens = int(os.environ.get("DCORE_WHO_WHEN_MAX_TOKENS", "1024"))

    dataset = who_when_dataset(request["packet"])
    prompt_digest = _digest(dataset)
    with tempfile.TemporaryDirectory(prefix="dcore-who-when-") as directory:
        input_dir = Path(directory) / "input"
        input_dir.mkdir()
        (input_dir / "0.json").write_text(json.dumps(dataset), encoding="utf-8")
        sys.path.insert(0, str(automated))
        from Lib.utils import all_at_once  # type: ignore[import-not-found]

        if provider == "azure":
            from openai import AzureOpenAI  # type: ignore[import-not-found]

            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
            api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
            api_version = os.environ.get(
                "AZURE_OPENAI_API_VERSION", "2024-08-01-preview"
            )
            if not endpoint or not api_key:
                raise ValueError("Who&When Azure mode requires endpoint and API key")
            client = AzureOpenAI(
                api_key=api_key,
                api_version=api_version,
                azure_endpoint=endpoint,
            )
        elif provider == "openai":
            from openai import OpenAI  # type: ignore[import-not-found]

            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                raise ValueError("Who&When OpenAI mode requires OPENAI_API_KEY")
            base_url = os.environ.get("OPENAI_BASE_URL") or None
            client = OpenAI(api_key=api_key, base_url=base_url)
            api_version = None
        else:
            raise ValueError("DCORE_WHO_WHEN_PROVIDER must be openai or azure")
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            all_at_once(client, str(input_dir), True, model, max_tokens)
    output = capture.getvalue()
    agent = re.search(r"Agent Name:\s*(.+)", output, re.IGNORECASE)
    step = re.search(r"Step Number:\s*(\d+)", output, re.IGNORECASE)
    reason = re.search(
        r"Reason for Mistake:\s*(.+?)(?:\n={3,}|\Z)",
        output,
        re.IGNORECASE | re.DOTALL,
    )
    witnesses = []
    if agent is not None and step is not None:
        witnesses.append(
            _witness(
                request,
                actor_id=agent.group(1).strip(),
                step_number=int(step.group(1)),
                mechanism="unresolved_evidence",
                explanation=reason.group(1).strip() if reason else "",
                method="who_when_all_at_once",
            )
        )
    return _result(
        request,
        revision=WHO_WHEN_REVISION,
        witnesses=witnesses,
        provider_requests=1,
        provider_tokens=None,
        prompt_digest=prompt_digest,
        model_settings={
            "model": model,
            "provider": provider,
            "api_version": api_version,
            "max_tokens": max_tokens,
            "method": "all_at_once",
        },
        metadata={
            "upstream_entrypoint": "Automated_FA.Lib.utils.all_at_once",
            "upstream_output_retained_by_caller": False,
            "usage_status": "upstream_does_not_report_tokens",
            "raw_output": output,
            "raw_output_status": "attribution"
            if witnesses
            else "abstention_or_invalid",
        },
    )


def agentrx_markdown(
    packet: dict[str, Any], *, reviewed_constraints: bool = False
) -> str:
    briefing = str(packet["public_task_contract"]).replace("\n", "\\n")
    requirements = json.dumps(packet.get("requirements", ()), sort_keys=True)
    evidence = json.dumps(shared_evidence_view(packet), sort_keys=True)
    lines = [
        "# User Properties",
        f"- **scenario_name**: {packet['run_id']}",
        f"- **first_turn_prompt**: {briefing}",
        f"- **shared_evidence_view_json**: {evidence}",
        f"- **reviewed_constraints_json**: {requirements if reviewed_constraints else '[]'}",
        "",
        "# Conversation",
    ]
    for index, decision in enumerate(_decision_steps(packet)):
        role = re.sub(r"\W+", "_", str(decision.get("actor_id") or "unknown"))
        content = json.dumps(
            {
                "decision_id": decision.get("decision_id"),
                "logical_time": decision.get("logical_time"),
                "proposal": decision.get("proposed_intent"),
                "knowledge_snapshot": decision.get("knowledge_snapshot"),
            },
            sort_keys=True,
        )
        lines.extend(
            (f"## ***{role}*** #{index}", content, '<hr style="border:5px solid">')
        )
    return "\n".join(lines) + "\n"


def _agentrx_mechanism(failure_case: int) -> str:
    return {
        1: "transition_order_failure",
        4: "failure_to_use_available_evidence",
        5: "transition_order_failure",
        6: "missing_observation",
        9: "native_execution_failure",
    }.get(failure_case, "unresolved_evidence")


def run_agentrx(
    request: dict[str, Any], *, reviewed_constraints: bool
) -> dict[str, Any]:
    root = Path(os.environ["DCORE_AGENTRX_ROOT"]).resolve()
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if revision != AGENTRX_REVISION:
        raise ValueError(f"AgentRx checkout revision mismatch: {revision}")
    endpoint = os.environ.get("DCORE_AGENTRX_ENDPOINT", "azure")
    model = os.environ.get("AGENT_VERIFY_MODEL_NAME", "gpt-5")
    markdown = agentrx_markdown(
        request["packet"], reviewed_constraints=reviewed_constraints
    )
    prompt_digest = _digest(
        {
            "trajectory": markdown,
            "requirements": request["packet"].get("requirements", ())
            if reviewed_constraints
            else None,
        }
    )

    with tempfile.TemporaryDirectory(prefix="dcore-agentrx-") as directory:
        directory_path = Path(directory)
        trajectory = directory_path / "trajectory.md"
        trajectory.write_text(markdown, encoding="utf-8")
        run_dir = directory_path / "run"
        command = [
            sys.executable,
            str(root / "run.py"),
            str(trajectory),
            "--domain",
            "magentic",
            "--endpoint",
            endpoint,
            "--run-dir",
            str(run_dir),
            "--dynamic-mode",
            "oneshot",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=float(os.environ.get("DCORE_AGENTRX_TIMEOUT_SECONDS", "900")),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"AgentRx exited {completed.returncode}: {completed.stderr[-2000:]}"
            )
        result_path = run_dir / "judge_output" / "runs" / "run1.json"
        output = json.loads(result_path.read_text(encoding="utf-8"))
        rows = output.get("detailed_results", output)
        if not isinstance(rows, list):
            rows = []
        failures = rows[0].get("failures") or () if rows else ()
        witnesses = []
        for failure in failures:
            raw_step = failure.get("step_number")
            try:
                step_number = int(raw_step) - 1
            except (TypeError, ValueError):
                step_number = -1
            decisions = _decision_steps(request["packet"])
            actor = (
                str(decisions[step_number].get("actor_id"))
                if 0 <= step_number < len(decisions)
                else str(failure.get("actor_id") or "unknown")
            )
            witnesses.append(
                _witness(
                    request,
                    actor_id=actor,
                    step_number=step_number,
                    mechanism=_agentrx_mechanism(int(failure.get("failure_case", 10))),
                    explanation=str(failure.get("description") or ""),
                    method=request["method"],
                )
            )
        summary = output.get("summary") or {}
        judge_tokens = int(summary.get("total_tokens") or 0)
        telemetry_files = tuple(run_dir.glob("checker_results/**/telemetry_*.json"))
        checker_calls = 0
        checker_tokens = 0
        for path in telemetry_files:
            telemetry = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(telemetry, list):
                checker_calls += len(telemetry)
                for row in telemetry:
                    checker_tokens += int(
                        row.get("total_tokens") or row.get("tokens_used") or 0
                    )
        provider_requests = 1 + checker_calls
        provider_tokens = judge_tokens + checker_tokens
    return _result(
        request,
        revision=AGENTRX_REVISION,
        witnesses=witnesses,
        provider_requests=provider_requests,
        provider_tokens=provider_tokens,
        prompt_digest=prompt_digest,
        model_settings={
            "model": model,
            "endpoint": endpoint,
            "dynamic_mode": "oneshot",
            "reviewed_constraints": reviewed_constraints,
        },
        metadata={
            "upstream_entrypoint": "run.py",
            "pipeline": "ir,static,dynamic,check,judge,report",
            "requirements_digest": _digest(request["packet"].get("requirements", ()))
            if reviewed_constraints
            else None,
            "usage_status": "reported_by_upstream_telemetry",
            "raw_output": output,
            "raw_output_status": "failure_attributions" if witnesses else "no_error",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "method",
        choices=("who_when_all_at_once", "agentrx", "agentrx_reviewed_constraints"),
    )
    args = parser.parse_args()
    request = _request()
    if request.get("method") != args.method or request.get("capability") != "diagnosis":
        raise ValueError("bridge method or capability does not match request")
    if args.method == "who_when_all_at_once":
        output = run_who_when(request)
    else:
        output = run_agentrx(
            request, reviewed_constraints=args.method == "agentrx_reviewed_constraints"
        )
    json.dump(output, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
