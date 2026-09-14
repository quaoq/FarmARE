"""Read-only action diagnosis and local-memory replay; no model or native writes."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, deque
from pathlib import Path
from types import SimpleNamespace

from are.simulation.distributed.controllers import FarmAREBaseAgentController
from are.simulation.distributed.experiments import execution_source_digest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT / "AAMAS/handover_validation/live_confirmation_v2.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.summary.read_text())
    rows = []
    for run in source["runs"]:
        trace_path = ROOT / run["artifact_dir"] / "trace.dcore_trace_v5.json"
        raw = trace_path.read_bytes()
        trace = json.loads(raw)
        actors = {}
        for actor in trace["actors"]:
            c = object.__new__(FarmAREBaseAgentController)
            c.base_agent = SimpleNamespace(
                agent_id=actor,
                append_agent_log=lambda _: None,
                make_timestamp=lambda: 0,
            )
            c.recent_failures = deque(maxlen=8)
            c.accepted_write_receipts = deque(maxlen=32)
            c.accepted_field_work = {}
            native = [
                e
                for e in trace["events"]
                if e["kind"] == "action"
                and e["actor_id"] == actor
                and e["payload"].get("execution_receipt")
            ]
            for event in native:
                receipt = event["payload"]["execution_receipt"]
                c.observe(
                    {
                        "selected_action": event["action"],
                        "arguments": event["args"],
                        "intent_id": receipt["intent_id"],
                        "intent_kind": "act",
                        "executed": receipt["status"] == "accepted",
                        "execution_receipt": receipt,
                        "result_world_time": event["world_time"],
                        "error": receipt.get("error"),
                    }
                )
            decisions = [
                d["proposed_intent"]
                for d in trace["decisions"]
                if d["actor_id"] == actor
            ]
            planted = sorted(
                ridge
                for (action, ridge) in c.accepted_field_work
                if action == "TractorApp__plant_seeds"
            )
            actors[actor] = {
                "action_counts": dict(
                    Counter(d["action"] or d["kind"] for d in decisions)
                ),
                "native_error_counts": dict(
                    Counter(
                        e["payload"]["tool_error"]
                        for e in native
                        if e["payload"].get("tool_error")
                    )
                ),
                "historical_accepted_planting_ridges": planted,
                "replayed_historical_field_work": c._field_work_memory(),
            }
        assert (
            hashlib.sha256(trace_path.read_bytes()).hexdigest()
            == hashlib.sha256(raw).hexdigest()
        )
        rows.append(
            {
                "scenario": run["scenario"],
                "world_seed": run["world_seed"],
                "trace_sha256": hashlib.sha256(raw).hexdigest(),
                "actors": actors,
            }
        )
    report = {
        "paper_evidence": False,
        "provider_calls": 0,
        "native_writes": 0,
        "execution_source_digest": execution_source_digest(),
        "interpretation": "Replay reconstructs only historical accepted local field work. It does not rerun an agent, infer current state, or establish improved progression.",
        "runs": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps({"runs": len(rows), "provider_calls": 0, "output": str(args.output)})
    )


if __name__ == "__main__":
    main()
