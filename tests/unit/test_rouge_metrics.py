from __future__ import annotations

from backend.experiments.rouge_metrics import RougeScorerAdapter, select_reference


def test_reference_selection_matches_audited_threshold_and_chunk_cap():
    assert select_reference("x" * 21, "context", top_k_chunks=5, max_text_chunks=2).kind == "ground_truth"
    selected = select_reference("x" * 20, "one\n\ntwo\n\nthree", top_k_chunks=5, max_text_chunks=2)
    assert selected.kind == "context_pseudoreference"
    assert selected.text == "one\n\ntwo"
    assert select_reference(None, "", top_k_chunks=5, max_text_chunks=2).kind == "no_reference"


def test_rouge_scores_match_stemmed_scorer():
    from rouge_score.rouge_scorer import RougeScorer

    reference = "The cats were running quickly."
    response = "The cat runs quickly."
    expected = RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True).score(reference, response)
    result = RougeScorerAdapter().score(reference, response, "ground_truth")
    assert result.status == "computed"
    assert result.values == tuple(expected[name].fmeasure for name in ("rouge1", "rouge2", "rougeL"))


def test_rouge_zero_is_computed():
    result = RougeScorerAdapter().score("completely different reference", "unrelated response", "ground_truth")
    assert result.status == "computed"
    assert result.values is not None and result.values[0] == 0.0
