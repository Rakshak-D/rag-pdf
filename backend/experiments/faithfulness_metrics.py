"""Offline lexical term-overlap heuristic used for audited M14."""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Callable, Iterable
from typing import Literal


MetricStatus = Literal["computed", "skipped", "failed", "unavailable"]

# Kept identical to the research fallback list. It intentionally is not the
# BM25 domain-stopword list.
FALLBACK_STOPWORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "aren't", "as", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "can",
    "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't",
    "doing", "don't", "down", "during", "each", "few", "for", "from",
    "further", "get", "got", "had", "hadn't", "has", "hasn't", "have",
    "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't",
    "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't",
    "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only",
    "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own",
    "same", "shan't", "she", "she'd", "she'll", "she's", "should",
    "shouldn't", "so", "some", "such", "than", "that", "that's", "the",
    "their", "theirs", "them", "themselves", "then", "there", "there's",
    "these", "they", "they'd", "they'll", "they're", "they've", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't",
    "what", "what's", "when", "when's", "where", "where's", "which", "while",
    "who", "who's", "whom", "why", "why's", "will", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your",
    "yours", "yourself", "yourselves",
})


@dataclass(frozen=True)
class FaithfulnessResult:
    status: MetricStatus
    value: float | None = None
    reason_code: str | None = None


def _regex_sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]


def _local_sentences(text: str) -> list[str]:
    try:
        import nltk

        try:
            sentences = nltk.sent_tokenize(text)
        except Exception:
            sentences = _regex_sentences(text)
    except Exception:
        sentences = _regex_sentences(text)
    return [sentence.strip() for sentence in sentences if isinstance(sentence, str) and sentence.strip()]


def _local_stopwords() -> set[str]:
    try:
        from nltk.corpus import stopwords

        return set(stopwords.words("english"))
    except Exception:
        return set(FALLBACK_STOPWORDS)


class FaithfulnessMetricAdapter:
    """Compute the legacy context term-overlap heuristic without models."""

    def __init__(self, *, stopwords: Iterable[str] | None = None, stopword_loader: Callable[[], Iterable[str]] | None = None, sentence_splitter: Callable[[str], Iterable[str]] | None = None) -> None:
        self._stopwords = set(stopwords) if stopwords is not None else None
        self._stopword_loader = stopword_loader or _local_stopwords
        self._sentence_splitter = sentence_splitter or _local_sentences

    @property
    def stopwords(self) -> set[str]:
        if self._stopwords is None:
            self._stopwords = set(self._stopword_loader())
        return self._stopwords

    def score(self, context: str, response: str) -> FaithfulnessResult:
        if not isinstance(context, str) or not isinstance(response, str) or not context.strip() or not response.strip():
            return FaithfulnessResult("skipped", reason_code="missing_text")
        try:
            context_terms = {term for term in re.findall(r"\b[A-Za-z]{3,}\b", context.lower()) if term not in self.stopwords}
            if not context_terms:
                return FaithfulnessResult("computed", value=0.0)
            sentences = list(self._sentence_splitter(response))
            grounded = 0
            countable = 0
            for sentence in sentences:
                if not isinstance(sentence, str):
                    return FaithfulnessResult("failed", reason_code="faithfulness_invalid_sentence")
                sentence_terms = {term for term in re.findall(r"\b[A-Za-z]{3,}\b", sentence.lower()) if term not in self.stopwords}
                if not sentence_terms:
                    continue
                countable += 1
                if len(sentence_terms & context_terms) / len(sentence_terms) >= 0.30:
                    grounded += 1
            value = (grounded / countable) * 100.0 if countable else 0.0
            if not 0.0 <= value <= 100.0:
                return FaithfulnessResult("failed", reason_code="faithfulness_invalid_score")
            return FaithfulnessResult("computed", value=value)
        except Exception:
            return FaithfulnessResult("failed", reason_code="faithfulness_failed")
