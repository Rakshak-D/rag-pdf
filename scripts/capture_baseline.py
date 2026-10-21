#!/usr/bin/env python
"""Capture a deterministic, source-only research baseline manifest."""

from __future__ import annotations

import ast
import hashlib
import json
import platform
import subprocess
import argparse
from importlib import metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "fixtures" / "research-baseline.json"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> list[Path]:
    tracked = git("ls-files", "notebooks").splitlines()
    return [ROOT / rel for rel in tracked if Path(rel).suffix in {".py", ".ipynb"}]


def config_metadata(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Config")
    fields = {}
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            try:
                value = ast.literal_eval(node.value)
            except Exception:
                value = ast.unparse(node.value)
            fields[node.target.id] = value
    return {"field_names": sorted(fields), "literal_defaults": fields}


def notebook_metadata(path: Path) -> dict:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    cells = notebook.get("cells", [])
    return {
        "cell_count": len(cells),
        "cell_types": {kind: sum(cell.get("cell_type") == kind for cell in cells)
                        for kind in sorted({cell.get("cell_type") for cell in cells})},
        "code_output_counts": [len(cell.get("outputs", [])) for cell in cells
                                if cell.get("cell_type") == "code"],
        "source_lengths": [len("".join(cell.get("source", []))) for cell in cells],
        "nbformat": notebook.get("nbformat"),
        "nbformat_minor": notebook.get("nbformat_minor"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-outcome",
        default="not recorded by source-only capture",
        help="Observed result of the targeted test command, recorded as provenance.",
    )
    args = parser.parse_args()
    files = []
    for path in sorted(source_files(), key=lambda item: item.relative_to(ROOT).as_posix()):
        rel = path.relative_to(ROOT).as_posix()
        item = {"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)}
        if path.suffix == ".ipynb":
            item["notebook"] = notebook_metadata(path)
        else:
            item["ast_parse"] = True
        files.append(item)

    config_path = ROOT / "notebooks" / "config.py"
    manifest = {
        "schema_version": 1,
        "baseline": {
            "branch": git("branch", "--show-current"),
            "commit": git("rev-parse", "HEAD"),
        },
        "research_files": files,
        "configuration": config_metadata(config_path),
        "provenance": {
            "capture_command": "python scripts/capture_baseline.py",
            "test_command": "python -m pytest tests/unit tests/integration",
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "pytest": metadata.version("pytest") if "pytest" in metadata.packages_distributions() else "unavailable",
            "test_outcome": args.test_outcome,
            "machine_specific_paths_included": False,
            "secrets_or_environment_variables_included": False,
            "pdf_contents_or_model_weights_included": False,
            "notes": "Runtime versions may vary; research file hashes and structural metadata are the approval baseline.",
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT).as_posix()}")
    print(f"Baseline commit: {manifest['baseline']['commit']}")
    print(f"Research files: {len(files)}")


if __name__ == "__main__":
    main()
