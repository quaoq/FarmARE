#!/usr/bin/env python3
"""Verify pinned comparator checkouts and write a credential-free local config."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def revision(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--who-when-root", required=True, type=Path)
    parser.add_argument("--who-when-python", required=True, type=Path)
    parser.add_argument("--agentrx-root", required=True, type=Path)
    parser.add_argument("--agentrx-python", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("AAMAS/comparators/adapter_config.local.json"),
    )
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    locks = json.loads(
        (repository / "AAMAS/comparators/source_lock.json").read_text(encoding="utf-8")
    )["methods"]
    bridge = (repository / "AAMAS/comparators/upstream_bridge.py").resolve()
    roots = {
        "who_when_all_at_once": args.who_when_root.resolve(),
        "agentrx": args.agentrx_root.resolve(),
    }
    pythons = {
        "who_when_all_at_once": args.who_when_python.resolve(),
        "agentrx": args.agentrx_python.resolve(),
    }
    for method, root in roots.items():
        expected = locks[method]["revision"]
        actual = revision(root)
        if actual != expected:
            raise SystemExit(
                f"{method} revision mismatch: expected {expected}, received {actual}"
            )
        if not pythons[method].is_file():
            raise SystemExit(f"missing isolated interpreter: {pythons[method]}")
    template = json.loads(
        (repository / "AAMAS/comparators/adapter_config.template.json").read_text(
            encoding="utf-8"
        )
    )
    adapters = template["adapters"]
    adapters["who_when_all_at_once"]["command"] = [
        str(pythons["who_when_all_at_once"]),
        str(bridge),
        "who_when_all_at_once",
    ]
    for method in ("agentrx", "agentrx_reviewed_constraints"):
        adapters[method]["command"] = [
            str(pythons["agentrx"]),
            str(bridge),
            method,
        ]
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
