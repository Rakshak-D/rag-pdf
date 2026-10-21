from __future__ import annotations

from scripts.verify_baseline import compare_file_sets


def test_file_set_comparison_reports_new_tracked_research_file():
    result = compare_file_sets(
        {"notebooks/config.py", "notebooks/new_stage.ipynb"},
        {"notebooks/config.py"},
        {"notebooks/config.py"},
    )

    assert result == {
        "added": ["notebooks/new_stage.ipynb"],
        "missing": [],
        "removed": [],
    }


def test_file_set_comparison_reports_removed_manifest_file():
    result = compare_file_sets(
        {"notebooks/config.py"},
        {"notebooks/config.py", "notebooks/old_stage.ipynb"},
        {"notebooks/config.py", "notebooks/old_stage.ipynb"},
    )

    assert result == {
        "added": [],
        "missing": [],
        "removed": ["notebooks/old_stage.ipynb"],
    }


def test_file_set_comparison_is_platform_independent_and_sorted():
    result = compare_file_sets(
        {"notebooks/z.py", "notebooks/a.ipynb"},
        {"notebooks/m.ipynb"},
        {"notebooks/m.ipynb"},
    )

    assert result["added"] == ["notebooks/a.ipynb", "notebooks/z.py"]
    assert result["removed"] == ["notebooks/m.ipynb"]


def test_file_set_comparison_reports_manifest_file_missing_on_disk():
    result = compare_file_sets(
        {"notebooks/config.py", "notebooks/missing.ipynb"},
        {"notebooks/config.py", "notebooks/missing.ipynb"},
        {"notebooks/config.py"},
    )

    assert result["added"] == []
    assert result["removed"] == []
    assert result["missing"] == ["notebooks/missing.ipynb"]
