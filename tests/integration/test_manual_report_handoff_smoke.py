from __future__ import annotations

from scripts.manual_report_handoff_smoke import SmokeError, run_smoke


def test_manual_report_handoff_build_preflight_and_runtime_sequence():
    try:
        outcomes = run_smoke(timeout=45)
    except SmokeError as exc:
        raise AssertionError(f"manual handoff smoke failed at {exc.code}") from None

    assert any(item.startswith("fresh-only synthetic Chroma build: passed") for item in outcomes)
    assert "explicit report handoff preflight: passed without storage mutation" in outcomes
    assert "ordinary runtime refuses missing selected report: passed" in outcomes
    assert "ordinary runtime authentication, plan identity, and report-only readiness: passed" in outcomes
    assert "manifest change after preflight rejected by ordinary runtime: passed" in outcomes
    assert "post-preflight report replacement, failed, and partial outcomes: passed" in outcomes
    assert "temporary-only runtime configuration and cleanup: passed" in outcomes
