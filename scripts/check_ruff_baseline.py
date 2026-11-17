"""Reject new Ruff findings against the reviewed product-base snapshot."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tokenize
from typing import Any


RUFF_VERSION = "0.17.5"
TARGET_VERSION = "py310"
SELECTED_RULES = ["E4", "E7", "E9", "F"]
SCOPE_PATHS = ["backend", "scripts"]
DEFAULT_BASELINE = Path(__file__).with_name("ruff-baseline.json")
FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

FindingKey = tuple[str, str, str, str]


class BaselineError(ValueError):
    """The recorded baseline is missing, malformed, or internally inconsistent."""


class RuffExecutionError(RuntimeError):
    """Ruff could not be run or returned an infrastructure error."""


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BaselineError(f"{label} must be a positive integer")
    return value


def _normalize_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BaselineError(f"{label} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BaselineError(f"{label} must stay inside the repository: {value!r}")
    if path.parts[0] not in SCOPE_PATHS:
        raise BaselineError(f"{label} is outside the configured Ruff scope: {value!r}")
    return path.as_posix()


def load_baseline(path: Path) -> tuple[Counter[FindingKey], dict[str, Any]]:
    try:
        baseline = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise BaselineError(f"baseline file does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BaselineError(f"invalid baseline file {path}: {exc}") from exc

    expected_top_keys = {
        "schema_version",
        "provenance",
        "finding_count",
        "rule_counts",
        "file_counts",
        "findings",
    }
    if not isinstance(baseline, dict) or set(baseline) != expected_top_keys:
        raise BaselineError("baseline has missing or unexpected top-level fields")
    schema_version = baseline["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise BaselineError("baseline schema_version must be 1")

    provenance = baseline["provenance"]
    provenance_keys = {
        "source_commit",
        "ruff_version",
        "target_version",
        "select",
        "paths",
    }
    if not isinstance(provenance, dict) or set(provenance) != provenance_keys:
        raise BaselineError("baseline provenance has missing or unexpected fields")
    if not isinstance(provenance["source_commit"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", provenance["source_commit"]
    ):
        raise BaselineError("baseline source_commit must be a full lowercase Git SHA")
    if any(
        provenance[key] != expected
        for key, expected in {
            "ruff_version": RUFF_VERSION,
            "target_version": TARGET_VERSION,
            "select": SELECTED_RULES,
            "paths": SCOPE_PATHS,
        }.items()
    ):
        raise BaselineError("baseline provenance does not match the verified CI lint scope")

    records = baseline["findings"]
    if not isinstance(records, list):
        raise BaselineError("baseline findings must be a list")

    expected_record_keys = {
        "path",
        "code",
        "message",
        "fingerprint",
        "occurrences",
        "locations",
    }
    expected_location_keys = {"row", "column", "end_row", "end_column"}
    counts: Counter[FindingKey] = Counter()
    reconstructed_rules: Counter[str] = Counter()
    reconstructed_files: Counter[str] = Counter()

    for index, record in enumerate(records):
        if not isinstance(record, dict) or set(record) != expected_record_keys:
            raise BaselineError(f"baseline finding {index} has missing or unexpected fields")
        relative_path = _normalize_relative_path(record["path"], f"finding {index} path")
        if record["path"] != relative_path:
            raise BaselineError(f"baseline finding {index} path is not normalized")
        code = record["code"]
        message = record["message"]
        fingerprint = record["fingerprint"]
        if not isinstance(code, str) or not re.fullmatch(r"(?:E[0-9]{3}|F[0-9]{3})", code):
            raise BaselineError(f"baseline finding {index} has an invalid Ruff rule code")
        if not code.startswith(("E4", "E7", "E9", "F")):
            raise BaselineError(f"baseline finding {index} is outside the configured rule families")
        if not isinstance(message, str) or not message:
            raise BaselineError(f"baseline finding {index} has an empty message")
        if not isinstance(fingerprint, str) or not FINGERPRINT_PATTERN.fullmatch(fingerprint):
            raise BaselineError(f"baseline finding {index} has an invalid source fingerprint")
        occurrences = _positive_int(record["occurrences"], f"finding {index} occurrences")
        locations = record["locations"]
        if not isinstance(locations, list) or len(locations) != occurrences:
            raise BaselineError(f"finding {index} occurrence count does not match its locations")
        seen_locations: set[tuple[int, int, int, int]] = set()
        for location_index, location in enumerate(locations):
            if not isinstance(location, dict) or set(location) != expected_location_keys:
                raise BaselineError(
                    f"finding {index} location {location_index} has missing or unexpected fields"
                )
            values = tuple(
                _positive_int(location[key], f"finding {index} location {key}")
                for key in ("row", "column", "end_row", "end_column")
            )
            row, column, end_row, end_column = values
            if end_row < row or (end_row == row and end_column <= column):
                raise BaselineError(f"finding {index} has an invalid source range")
            if values in seen_locations:
                raise BaselineError(f"finding {index} contains a duplicate source location")
            seen_locations.add(values)

        key = (relative_path, code, message, fingerprint)
        if key in counts:
            raise BaselineError(f"duplicate baseline finding record: {relative_path} {code}")
        counts[key] = occurrences
        reconstructed_rules[code] += occurrences
        reconstructed_files[relative_path] += occurrences

    finding_count = sum(counts.values())
    declared_count = baseline["finding_count"]
    if (
        isinstance(declared_count, bool)
        or not isinstance(declared_count, int)
        or declared_count != finding_count
    ):
        raise BaselineError("baseline finding_count does not match its occurrence records")
    rule_counts = baseline["rule_counts"]
    if not isinstance(rule_counts, dict) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in rule_counts.values()
    ) or rule_counts != dict(sorted(reconstructed_rules.items())):
        raise BaselineError("baseline rule_counts do not match its finding records")
    file_counts = baseline["file_counts"]
    if not isinstance(file_counts, dict) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in file_counts.values()
    ) or file_counts != dict(sorted(reconstructed_files.items())):
        raise BaselineError("baseline file_counts do not match its finding records")
    return counts, baseline


def _source_fingerprint(root: Path, relative_path: str, row: int) -> str:
    root = root.resolve()
    target = root.joinpath(*relative_path.split("/"))
    try:
        resolved_target = target.resolve(strict=True)
        resolved_target.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RuffExecutionError(
            f"Ruff diagnostic source path is missing or escapes the repository: {relative_path}"
        ) from exc
    try:
        with tokenize.open(resolved_target) as source_file:
            lines = source_file.read().splitlines()
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise RuffExecutionError(f"could not read Ruff source context for {relative_path}: {exc}") from exc
    if row < 1 or row > len(lines):
        raise RuffExecutionError(
            f"Ruff diagnostic location has no source line: {relative_path}:{row}"
        )
    normalized_context = " ".join(lines[row - 1].split())
    if not normalized_context:
        raise RuffExecutionError(
            f"Ruff diagnostic location has empty source context: {relative_path}:{row}"
        )
    digest = hashlib.sha256(normalized_context.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _diagnostic_path(root: Path, filename: Any) -> str:
    if not isinstance(filename, str) or not filename:
        raise RuffExecutionError("Ruff returned a diagnostic without a filename")
    candidate = Path(filename)
    if candidate.is_absolute():
        try:
            relative = candidate.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError) as exc:
            raise RuffExecutionError(
                f"Ruff returned a path outside the repository: {filename}"
            ) from exc
    else:
        relative = filename
    try:
        return _normalize_relative_path(relative, "Ruff diagnostic path")
    except BaselineError as exc:
        raise RuffExecutionError(str(exc)) from exc


def parse_ruff_diagnostics(output: str, root: Path) -> Counter[FindingKey]:
    try:
        diagnostics = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuffExecutionError(f"Ruff output parse failed: {exc}") from exc
    if not isinstance(diagnostics, list):
        raise RuffExecutionError("Ruff JSON output must be a list")

    counts: Counter[FindingKey] = Counter()
    for index, diagnostic_record in enumerate(diagnostics):
        if not isinstance(diagnostic_record, dict):
            raise RuffExecutionError(f"Ruff diagnostic {index} is not an object")
        code = diagnostic_record.get("code")
        message = diagnostic_record.get("message")
        location = diagnostic_record.get("location")
        end_location = diagnostic_record.get("end_location")
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"(?:E[0-9]{3}|F[0-9]{3})", code)
            or not isinstance(message, str)
            or not message
            or not isinstance(location, dict)
            or not isinstance(end_location, dict)
        ):
            raise RuffExecutionError(f"Ruff diagnostic {index} has an invalid shape")
        if not code.startswith(("E4", "E7", "E9", "F")):
            raise RuffExecutionError(f"Ruff diagnostic {index} is outside the selected rule families")
        row = location.get("row")
        column = location.get("column")
        end_row = end_location.get("row")
        end_column = end_location.get("column")
        coordinates = (row, column, end_row, end_column)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in coordinates
        ):
            raise RuffExecutionError(f"Ruff diagnostic {index} has invalid source coordinates")
        if end_row < row or (end_row == row and end_column <= column):
            raise RuffExecutionError(f"Ruff diagnostic {index} has an invalid source range")
        relative_path = _diagnostic_path(root, diagnostic_record.get("filename"))
        fingerprint = _source_fingerprint(root, relative_path, row)
        counts[(relative_path, code, message, fingerprint)] += 1
    return counts


def _run_process(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuffExecutionError(f"Ruff execution failed: {exc}") from exc


def run_ruff(root: Path, command: list[str]) -> Counter[FindingKey]:
    version_result = _run_process(command + ["--version"], root)
    if version_result.returncode != 0:
        raise RuffExecutionError(
            f"Ruff execution failed while checking version (exit {version_result.returncode}): "
            f"{version_result.stderr.strip()}"
        )
    if version_result.stdout.strip() != f"ruff {RUFF_VERSION}":
        raise RuffExecutionError(
            f"Ruff version mismatch: expected {RUFF_VERSION}, got {version_result.stdout.strip()!r}"
        )

    check_command = command + [
        "check",
        "--target-version",
        TARGET_VERSION,
        "--select",
        ",".join(SELECTED_RULES),
        "--output-format",
        "json",
        *SCOPE_PATHS,
    ]
    result = _run_process(check_command, root)
    if result.returncode not in (0, 1):
        raise RuffExecutionError(
            f"Ruff execution failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    current = parse_ruff_diagnostics(result.stdout, root)
    expected_return_code = 1 if sum(current.values()) else 0
    if result.returncode != expected_return_code:
        raise RuffExecutionError(
            "Ruff exit status does not agree with its parsed diagnostics "
            f"(exit {result.returncode}, diagnostics {sum(current.values())})"
        )
    return current


def compare_findings(
    baseline: Counter[FindingKey], current: Counter[FindingKey]
) -> tuple[Counter[FindingKey], Counter[FindingKey]]:
    return current - baseline, baseline - current


def _print_delta(label: str, findings: Counter[FindingKey]) -> None:
    for (path, code, message, _fingerprint), count in sorted(findings.items()):
        delta_word = "added" if label == "New or increased finding" else "removed"
        print(f"{label} ({count} occurrence(s) {delta_word}): {path}: {code}: {message}")


def check_baseline(root: Path, baseline_path: Path, ruff_command: list[str]) -> int:
    baseline, metadata = load_baseline(baseline_path)
    current = run_ruff(root, ruff_command)
    new_findings, removed_findings = compare_findings(baseline, current)
    baseline_total = sum(baseline.values())
    current_total = sum(current.values())
    new_total = sum(new_findings.values())
    removed_total = sum(removed_findings.values())

    print(f"Baseline source: {metadata['provenance']['source_commit']}")
    print(f"Ruff version: {metadata['provenance']['ruff_version']}")
    print(f"Recorded baseline debt: {baseline_total}")
    print(f"Current debt: {current_total}")
    print(f"New violations: {new_total}")
    print(f"Removed findings: {removed_total}")
    _print_delta("New or increased finding", new_findings)
    _print_delta("Removed finding; baseline reconciliation required", removed_findings)

    if new_total or removed_total:
        if not new_total and removed_total:
            print("Fail: update and review the baseline to reconcile lint reductions.")
        return 1
    print("Pass: no new Ruff findings; recorded baseline debt is unchanged.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--ruff-command",
        nargs="+",
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    if not root.is_dir():
        print(f"Ruff baseline check failed: repository root does not exist: {root}", file=sys.stderr)
        return 2
    for relative_path in SCOPE_PATHS:
        if not (root / relative_path).is_dir():
            print(
                f"Ruff baseline check failed: required scope path is missing: {relative_path}",
                file=sys.stderr,
            )
            return 2
    command = arguments.ruff_command or [sys.executable, "-m", "ruff"]
    try:
        return check_baseline(root, arguments.baseline.resolve(), command)
    except (BaselineError, RuffExecutionError) as exc:
        print(f"Ruff baseline check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
