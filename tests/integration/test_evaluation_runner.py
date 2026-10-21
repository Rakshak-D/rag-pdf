from tests.unit.test_evaluation_runner import setup_chain
from backend.experiments import BenchmarkCase, BenchmarkSuite, EvaluationRunner


def test_evaluation_runner_is_in_memory_and_preserves_report_identity(tmp_path):
    plan, query, result, formatter, generation = setup_chain(tmp_path)
    suite = BenchmarkSuite("integration", "v1", (BenchmarkCase("case-1", query, ("text",), text_k=5),))
    output = EvaluationRunner(plan, lambda *_: result, formatter, generation).run(suite)
    assert output.report.run_fingerprint == plan.run_fingerprint
    assert output.report.trace_ids == (output.traces[0].trace_id,)
    assert output.report.evaluation_fingerprint
    assert not list(tmp_path.rglob("*.json"))
