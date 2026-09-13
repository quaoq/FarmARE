"""Package a development candidate without claiming professor-handover readiness.

Run from the repository root. Original artifacts are never modified. Portable
exports disclose path rebasing and carry both original and exported hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".md",
    ".xml",
    ".yaml",
    ".yml",
    ".tex",
    ".csv",
    ".log",
    ".txt",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def known_credentials() -> list[bytes]:
    # Compare locally; no credential value, prefix or digest is printed/exported.
    from dotenv import dotenv_values

    values = dotenv_values(ROOT / ".env")
    return [
        str(value).encode()
        for key, value in values.items()
        if value
        and any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        and len(str(value)) >= 12
    ]


def package(
    paths: list[Path], destination: Path, *, portable: bool, anonymous: bool = False
) -> dict:
    if destination.exists():
        raise FileExistsError(destination)
    secrets = known_credentials()
    entries = []
    with zipfile.ZipFile(
        destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(set(paths)):
            name = path.relative_to(ROOT).as_posix()
            if (
                path.is_symlink()
                or ".git" in path.parts
                or path.name.startswith(".env")
            ):
                raise ValueError(f"forbidden archive entry: {name}")
            original = path.read_bytes()
            if any(secret in original for secret in secrets):
                raise ValueError(
                    f"credential found in archive input: {name}; no credential value disclosed"
                )
            exported = original
            if portable and path.suffix in TEXT_SUFFIXES:
                content = original.decode("utf-8")
                content = content.replace(str(ROOT), ".")
                content = re.sub(r'/Users/[^/\s"<>]+', "<local-home>", content)
                exported = content.encode()
            if anonymous and b"panosmichelakis.com" in exported:
                raise ValueError(
                    f"personal publication-profile link in anonymous input: {name}"
                )
            item = zipfile.ZipInfo(name, date_time=(2026, 9, 13, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, exported)
            entries.append(
                {
                    "path": name,
                    "original_sha256": digest(original),
                    "exported_sha256": digest(exported),
                    "path_rebased": exported != original,
                }
            )
        manifest = {
            "schema_version": "dcore_development_archive_v1",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "source_worktree_dirty": bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=ROOT, text=True
                ).strip()
            ),
            "professor_handover_ready": False,
            "paper_execution_enabled": False,
            "original_artifacts_unchanged": True,
            "portable_export": portable,
            "anonymous_candidate": anonymous,
            "entries": entries,
        }
        archive.writestr("ARCHIVE_MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
    # Reopen the actual archive and verify every payload rather than trusting writes.
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("archive CRC verification failed")
        for entry in entries:
            if digest(archive.read(entry["path"])) != entry["exported_sha256"]:
                raise ValueError("archive content hash mismatch")
    if anonymous and destination.stat().st_size > 25_000_000:
        raise ValueError("anonymous candidate exceeds the 25 MB limit")
    return {
        "archive": destination.name,
        "sha256": digest(destination.read_bytes()),
        "size_bytes": destination.stat().st_size,
        "entry_count": len(entries),
        "path_rebased_entries": sum(e["path_rebased"] for e in entries),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--include-historical-raw", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    source = [
        ROOT / name
        for name in names
        if (ROOT / name).is_file()
        and not name.startswith("results/")
        and not Path(name).name.startswith(".env")
    ]
    raw_roots = [ROOT / "results/aamas_handover"]
    if args.include_historical_raw:
        raw_roots += [
            ROOT / "results/aamas_pilot_20260913",
            ROOT / "results/aamas_followup_20260913",
        ]
    if any(not path.is_dir() for path in raw_roots):
        raise FileNotFoundError("a requested raw-artifact root is missing")
    raw = [
        p
        for raw_root in raw_roots
        for p in raw_root.rglob("*")
        if p.is_file() and p.suffix not in {".sqlite-shm", ".sqlite-wal"}
    ]
    # The private raw archive preserves bytes. Its portable companion is explicitly
    # a path-rebased export; it is never called an unchanged raw trace.
    records = [
        package(
            source, args.output_dir / "implementation_candidate.zip", portable=True
        ),
        package(raw, args.output_dir / "development_raw_originals.zip", portable=False),
        package(
            raw, args.output_dir / "development_portable_exports.zip", portable=True
        ),
    ]
    anonymous = [
        p
        for p in source
        if (
            p.relative_to(ROOT).as_posix().startswith("are/")
            and p.suffix in {".py", ".json", ".yaml", ".yml", ".tiktoken"}
            and "tangyan5_table_fitted" not in p.parts
        )
        or p.name
        in {
            "LICENSE",
            "TIKTOKEN_LICENSE",
            "requirements.txt",
            "requirements-dev.txt",
            "requirements-gui.txt",
            "README.md",
            "uv.lock",
            "pyproject.toml",
            ".python-version",
        }
    ]
    anonymous += [
        p
        for p in (ROOT / "AAMAS/manuscript").rglob("*")
        if p.is_file()
        and p.name not in {"SOURCES_VERIFIED.md"}
        and p.suffix in {".tex", ".bib", ".cls", ".bst", ".pdf", ".csv", ".json"}
    ]
    anonymous += list((ROOT / "build_hooks").glob("*.py"))
    anonymous += list(
        (ROOT / "AAMAS/handover_development/interventions").glob("*.yaml")
    )
    anonymous += [
        ROOT / "AAMAS/manuscript/AI_ASSISTANCE.md",
        ROOT / "AAMAS/HANDOVER_STATUS.md",
    ]
    records.append(
        package(
            anonymous,
            args.output_dir / "anonymous_supplement_candidate.zip",
            portable=True,
            anonymous=True,
        )
    )
    report = {
        "schema_version": "dcore_candidate_archive_verification_v1",
        "readiness_certified": False,
        "archives": records,
        "limits": [
            "Raw originals retain historical machine-path provenance and are private, not the anonymous supplement.",
            "Portable exports disclose rebased paths and original hashes; scientific observations are not revised.",
            "The anonymous candidate excludes unrelated legacy tangyan5_table_fitted source with a hardcoded personal machine path.",
            "No archive bypasses the failed handover/scientific gates.",
        ],
    }
    (args.output_dir / "verification.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
