import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CHECKER = Path(__file__).resolve().parents[1] / "check_ruff_baseline.py"
SOURCE_COMMIT = "c1cba315e8df4130f281fd95ba5794663eb30c9f"
RUFF_VERSION = "0.17.5"
BASE_PATH = "backend/legacy.py"
BASE_CODE = "E702"
BASE_MESSAGE = "Multiple statements on one line (semicolon)"
BASE_LINE = "first = 1; second = 2"


def source_fingerprint(line: str) -> str:
    normalized = " ".join(line.split())
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def diagnostic(
    path: str,
    code: str,
    message: str,
    row: int,
    column: int = 1,
) -> dict[str, object]:
    return {
        "filename": path,
        "code": code,
        "message": message,
        "location": {"row": row, "column": column},
        "end_location": {"row": row, "column": column + 1},
    }


def baseline_entry(
    path: str = BASE_PATH,
    code: str = BASE_CODE,
    message: str = BASE_MESSAGE,
    line: str = BASE_LINE,
    rows: tuple[int, ...] = (1,),
) -> dict[str, object]:
    return {
        "path": path,
        "code": code,
        "message": message,
        "fingerprint": source_fingerprint(line),
        "occurrences": len(rows),
        "locations": [
            {"row": row, "column": 1, "end_row": row, "end_column": 2}
            for row in rows
        ],
    }


def make_baseline(entries: list[dict[str, object]]) -> dict[str, object]:
    rule_counts: dict[str, int] = {}
    file_counts: dict[str, int] = {}
    for entry in entries:
        count = int(entry["occurrences"])
        rule = str(entry["code"])
        path = str(entry["path"])
        rule_counts[rule] = rule_counts.get(rule, 0) + count
        file_counts[path] = file_counts.get(path, 0) + count
    return {
        "schema_version": 1,
        "provenance": {
            "source_commit": SOURCE_COMMIT,
            "ruff_version": RUFF_VERSION,
            "target_version": "py310",
            "select": ["E4", "E7", "E9", "F"],
            "paths": ["backend", "scripts"],
        },
        "finding_count": sum(rule_counts.values()),
        "rule_counts": rule_counts,
        "file_counts": file_counts,
        "findings": entries,
    }


class RuffBaselineCliTests(unittest.TestCase):
    def run_checker(
        self,
        diagnostics: list[dict[str, object]] | str,
        entries: list[dict[str, object]],
        files: dict[str, str],
        *,
        ruff_exit: int = 1,
        baseline_text: str | None = None,
        omit_baseline: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="ruff-baseline-test-") as temporary:
            temporary_path = Path(temporary)
            root = temporary_path / "repo"
            root.mkdir()
            (root / "backend").mkdir()
            (root / "scripts").mkdir()
            for relative, contents in files.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents, encoding="utf-8")

            baseline_path = temporary_path / "baseline.json"
            if not omit_baseline:
                if baseline_text is not None:
                    baseline_path.write_text(baseline_text, encoding="utf-8")
                else:
                    baseline_path.write_text(
                        json.dumps(make_baseline(entries)), encoding="utf-8"
                    )

            diagnostics_path = temporary_path / "ruff-output.json"
            serialized_diagnostics = (
                diagnostics if isinstance(diagnostics, str) else json.dumps(diagnostics)
            )
            diagnostics_path.write_text(serialized_diagnostics, encoding="utf-8")
            runner_path = temporary_path / "fake_ruff.py"
            runner_path.write_text(
                "import os, pathlib, sys\n"
                "if '--version' in sys.argv:\n"
                "    print('ruff 0.17.5')\n"
                "    raise SystemExit(0)\n"
                "print(pathlib.Path(os.environ['RUFF_FAKE_OUTPUT']).read_text(encoding='utf-8'))\n"
                "raise SystemExit(int(os.environ['RUFF_FAKE_EXIT']))\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["RUFF_FAKE_OUTPUT"] = str(diagnostics_path)
            environment["RUFF_FAKE_EXIT"] = str(ruff_exit)
            command = [
                sys.executable,
                str(CHECKER),
                "--root",
                str(root),
                "--baseline",
                str(baseline_path),
                "--ruff-command",
                sys.executable,
                str(runner_path),
            ]
            return subprocess.run(
                command,
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_unchanged_baseline_passes_after_source_line_moves(self) -> None:
        result = self.run_checker(
            [diagnostic(BASE_PATH, BASE_CODE, BASE_MESSAGE, row=3, column=11)],
            [baseline_entry()],
            {BASE_PATH: "# inserted line\n\n" + BASE_LINE + "\n"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Current debt: 1", result.stdout)
        self.assertIn("New violations: 0", result.stdout)

    def test_new_f821_finding_fails(self) -> None:
        result = self.run_checker(
            [
                diagnostic(BASE_PATH, BASE_CODE, BASE_MESSAGE, row=1, column=11),
                diagnostic(
                    "backend/new.py",
                    "F821",
                    "Undefined name `missing_name`",
                    row=1,
                ),
            ],
            [baseline_entry()],
            {
                BASE_PATH: BASE_LINE + "\n",
                "backend/new.py": "value = missing_name\n",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("F821", result.stdout)
        self.assertIn("New violations: 1", result.stdout)

    def test_new_e702_finding_fails_even_when_rule_already_exists(self) -> None:
        result = self.run_checker(
            [
                diagnostic(BASE_PATH, BASE_CODE, BASE_MESSAGE, row=1, column=11),
                diagnostic(
                    "backend/new.py",
                    "E702",
                    BASE_MESSAGE,
                    row=1,
                    column=11,
                ),
            ],
            [baseline_entry()],
            {
                BASE_PATH: BASE_LINE + "\n",
                "backend/new.py": "left = 1; right = 2\n",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E702", result.stdout)
        self.assertIn("New violations: 1", result.stdout)

    def test_increased_occurrence_count_fails(self) -> None:
        result = self.run_checker(
            [
                diagnostic(BASE_PATH, BASE_CODE, BASE_MESSAGE, row=1, column=11),
                diagnostic(BASE_PATH, BASE_CODE, BASE_MESSAGE, row=2, column=11),
            ],
            [baseline_entry()],
            {BASE_PATH: BASE_LINE + "\n" + BASE_LINE + "\n"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("occurrence(s) added", result.stdout)
        self.assertIn("New violations: 1", result.stdout)

    def test_malformed_and_missing_baseline_fail(self) -> None:
        cases = (
            ("{not-json", False, "invalid baseline"),
            (None, True, "baseline file does not exist"),
        )
        for contents, omit, expected in cases:
            with self.subTest(contents=contents, omit=omit):
                result = self.run_checker(
                    [],
                    [],
                    {},
                    ruff_exit=0,
                    baseline_text=contents,
                    omit_baseline=omit,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, (result.stdout + result.stderr).lower())

    def test_duplicate_baseline_records_fail(self) -> None:
        entry = baseline_entry()
        result = self.run_checker(
            [],
            [entry, dict(entry)],
            {BASE_PATH: BASE_LINE + "\n"},
            ruff_exit=0,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", (result.stdout + result.stderr).lower())

    def test_inconsistent_baseline_summary_fails(self) -> None:
        baseline = make_baseline([baseline_entry()])
        baseline["finding_count"] = 2
        result = self.run_checker(
            [],
            [],
            {BASE_PATH: BASE_LINE + "\n"},
            ruff_exit=0,
            baseline_text=json.dumps(baseline),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("finding_count", (result.stdout + result.stderr).lower())

    def test_ruff_execution_failure_fails(self) -> None:
        result = self.run_checker(
            [],
            [],
            {},
            ruff_exit=2,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ruff execution failed", (result.stdout + result.stderr).lower())

    def test_malformed_ruff_diagnostics_fail(self) -> None:
        result = self.run_checker(
            "not-json",
            [],
            {},
            ruff_exit=1,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ruff output parse failed", (result.stdout + result.stderr).lower())

    def test_removed_finding_requires_baseline_reconciliation(self) -> None:
        result = self.run_checker(
            [diagnostic(BASE_PATH, BASE_CODE, BASE_MESSAGE, row=1, column=11)],
            [baseline_entry(rows=(1, 2))],
            {BASE_PATH: BASE_LINE + "\n" + BASE_LINE + "\n"},
            ruff_exit=1,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("removed findings: 1", result.stdout.lower())
        self.assertIn("baseline reconciliation", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
