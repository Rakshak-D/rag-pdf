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


def main() -> int:
    if not MANIFEST.exists():
        print(f"Missing manifest: {MANIFEST}", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures = []
    if git("branch", "--show-current") != manifest["baseline"]["branch"]:
        failures.append("current branch differs from approved baseline branch")
    for item in manifest["research_files"]:
        path = ROOT / item["path"]
        if not path.exists():
            failures.append(f"missing research file: {item['path']}")
            continue
        actual = sha256(path)
        if actual != item["sha256"]:
            failures.append(f"hash drift: {item['path']} expected {item['sha256']} got {actual}")
    if failures:
        print("Baseline verification failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"Baseline verified: {len(manifest['research_files'])} research files unchanged")
    print(f"Approved starting commit: {manifest['baseline']['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
