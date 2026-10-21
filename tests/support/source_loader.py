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


def load_class_methods(
    class_name: str,
    *method_names: str,
    globals_override: Optional[Dict[str, Any]] = None,
) -> type:
    """Compile selected methods from an existing research class only."""
    source = PIPELINE_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PIPELINE_SOURCE))
    source_class = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name),
        None,
    )
    if source_class is None:
        raise AssertionError(f"Expected source class was not found: {class_name}")
    wanted = set(method_names)
    nodes = [
        node for node in source_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
    ]
    found = {node.name for node in nodes}
    missing = wanted - found
    if missing:
        raise AssertionError(f"Expected class methods were not found: {sorted(missing)}")

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
    synthetic = ast.ClassDef(
        name="SelectedSourceMethods",
        bases=[],
        keywords=[],
        body=nodes,
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[synthetic], type_ignores=[]))
    exec(compile(module, str(PIPELINE_SOURCE), "exec"), namespace)
    return namespace["SelectedSourceMethods"]


def load_assignment(name: str, globals_override: Optional[Dict[str, Any]] = None) -> Any:
    """Evaluate one top-level assignment from the research source."""
    source = PIPELINE_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PIPELINE_SOURCE))
    assignment = next(
        (
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
        ),
        None,
    )
    if assignment is None:
        raise AssertionError(f"Expected source assignment was not found: {name}")
    namespace: Dict[str, Any] = {}
    if globals_override:
        namespace.update(globals_override)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[assignment], type_ignores=[])),
                 str(PIPELINE_SOURCE), "exec"), namespace)
    return namespace[name]
