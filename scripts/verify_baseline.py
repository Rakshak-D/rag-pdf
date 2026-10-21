#!/usr/bin/env python
"""Verify research source hashes and notebook structure against the manifest."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "research-baseline.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def tracked_research_paths() -> set[str]:
    """Return the exact tracked Python/notebook file set under notebooks/."""
    paths = git("ls-files", "--", "notebooks").splitlines()
    return {
        Path(path).as_posix()
        for path in paths
        if Path(path).suffix.lower() in {".py", ".ipynb"}
    }


def compare_file_sets(
    tracked_paths: set[str],
    manifest_paths: set[str],
    existing_paths: set[str],
) -> dict[str, list[str]]:
    """Compare tracked, manifest, and on-disk research paths."""
    return {
        "removed": sorted(manifest_paths - tracked_paths),
        "added": sorted(tracked_paths - manifest_paths),
        "missing": sorted(manifest_paths - existing_paths),
    }


def main() -> int:
    if not MANIFEST.exists():
        print(f"Missing manifest: {MANIFEST}", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures = []
    if git("branch", "--show-current") != manifest["baseline"]["branch"]:
        failures.append("current branch differs from approved baseline branch")
    manifest_paths = {
        Path(item["path"]).as_posix()
        for item in manifest.get("research_files", [])
    }
    existing_paths = {
        path for path in manifest_paths
        if (ROOT / path).exists()
    }
    file_set = compare_file_sets(tracked_research_paths(), manifest_paths, existing_paths)
    for path in file_set["removed"]:
        failures.append(f"removed tracked research file: {path}")
    for path in file_set["added"]:
        failures.append(f"new tracked research file not in baseline: {path}")
    for item in manifest["research_files"]:
        path = ROOT / item["path"]
        if not path.exists():
            continue
        actual = sha256(path)
        if actual != item["sha256"]:
            failures.append(f"hash drift: {item['path']} expected {item['sha256']} got {actual}")
    for path in file_set["missing"]:
        failures.append(f"missing baseline research file on disk: {path}")
    if failures:
        print("Baseline verification failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"Baseline verified: {len(manifest['research_files'])} research files unchanged")
    print(f"Approved starting commit: {manifest['baseline']['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
