"""Load selected existing source definitions without importing heavy dependencies.

This is intentionally a test-only adapter. It parses the checked-in research
source and executes only named top-level functions, with explicit lightweight
globals supplied by the test. It does not copy or reimplement production logic.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SOURCE = ROOT / "notebooks" / "rag_pipeline.py"


def load_functions(*names: str, globals_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = PIPELINE_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PIPELINE_SOURCE))
    wanted = set(names)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    found = {node.name for node in nodes}
    missing = wanted - found
    if missing:
        raise AssertionError(f"Expected source functions were not found: {sorted(missing)}")

    namespace: Dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "Tuple": Tuple,
        "hashlib": hashlib,
        "json": json,
    }
    if globals_override:
        namespace.update(globals_override)
    code = compile(ast.Module(body=nodes, type_ignores=[]), str(PIPELINE_SOURCE), "exec")
    exec(code, namespace)
    return {name: namespace[name] for name in names}
