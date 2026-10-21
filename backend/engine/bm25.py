"""Offline BM25 indexing and querying compatible with the research pipeline."""
from __future__ import annotations

import re
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi
from nltk.stem import PorterStemmer

try:
    from nltk.corpus import stopwords
    from nltk.tokenize import word_tokenize
except Exception:  # pragma: no cover - optional NLTK data is handled below
    stopwords = None
    word_tokenize = None

FALLBACK_STOPWORDS = {"a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "few", "for", "from", "further", "get", "got", "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's", "will", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves"}

class BM25Error(ValueError):
    """Invalid corpus or query for BM25."""

def safe_word_tokenize(text: str) -> list[str]:
    try:
        return word_tokenize(text) if word_tokenize else re.findall(r"[A-Za-z0-9]+", text)
    except LookupError:
        return re.findall(r"[A-Za-z0-9]+", text)

def get_stopword_set() -> set[str]:
    try:
        return set(stopwords.words("english")) if stopwords else set(FALLBACK_STOPWORDS)
    except LookupError:
        return set(FALLBACK_STOPWORDS)

class BM25Index:
    """An in-memory, explicitly supplied BM25 corpus."""
    def __init__(self, documents: Sequence[str], doc_ids: Sequence[str], metadatas: Sequence[Mapping[str, Any]], enable_preprocessing: bool = True):
        if not (len(documents) == len(doc_ids) == len(metadatas)):
            raise BM25Error("documents, doc_ids, and metadatas must have equal lengths")
        if len(set(doc_ids)) != len(doc_ids) or any(not isinstance(i, str) or not i for i in doc_ids):
            raise BM25Error("doc_ids must be non-empty and unique")
        if any(not isinstance(d, str) or not d.strip() for d in documents):
            raise BM25Error("documents must contain non-empty text")
        if any(not isinstance(m, Mapping) for m in metadatas):
            raise BM25Error("metadatas must contain mappings")
        self.documents, self.doc_ids, self.metadatas = list(documents), list(doc_ids), [dict(m) for m in metadatas]
        self.stemmer = PorterStemmer(); self.stop_words = get_stopword_set()
        self.stop_words.update({"et", "al", "using", "used", "shown", "show", "via", "thus", "therefore", "hence"})
        self.enable_preprocessing = enable_preprocessing
        self.processed_docs = [self._preprocess(d) for d in self.documents] if enable_preprocessing else [safe_word_tokenize(d.lower()) for d in self.documents]
        self.corpus_tokens = set().union(*self.processed_docs) if self.processed_docs else set()
        self.bm25 = BM25Okapi(self.processed_docs) if self.processed_docs else None

    def _preprocess(self, text: str) -> list[str]:
        text = text.lower()
        text = re.sub(r"([a-z])(\d)", r"\1 \2", text); text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
        result = []
        for token in safe_word_tokenize(text):
            if token.isdigit() or (any(c.isdigit() for c in token) and any(c.isalpha() for c in token)):
                result.append(token)
            elif token not in self.stop_words and len(token) >= 3:
                result.append(self.stemmer.stem(token))
        return result

    def _expand_query(self, tokens: list[str]) -> list[str]:
        expanded = list(dict.fromkeys(tokens))
        seen = set(expanded)
        for token in tokens:
            for variant in (token + "s", token + "es", token + "ed", token + "ing", token[:-1] if token.endswith("s") else None, token[:-2] if token.endswith("es") else None):
                if variant and variant in self.corpus_tokens and variant not in seen:
                    expanded.append(variant)
                    seen.add(variant)
        return expanded

    def query(self, query: str, top_k: int = 10) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip(): raise BM25Error("query must be a non-empty string")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0: raise BM25Error("top_k must be a positive integer")
        tokens = self._preprocess(query) if self.enable_preprocessing else safe_word_tokenize(query.lower())
        if not tokens:
            tokens = [token for token in safe_word_tokenize(query.lower()) if token not in self.stop_words and len(token) > 2]
        tokens = self._expand_query(tokens)
        if self.bm25 is None: scores = np.zeros(0, dtype=float)
        else: scores = np.asarray(self.bm25.get_scores(tokens), dtype=float)
        positive = np.where(scores > 0)[0]
        used_overlap = False
        if len(positive) == 0:
            used_overlap = True; qset = set(tokens)
            scores = np.asarray([len(qset.intersection(doc)) for doc in self.processed_docs], dtype=float)
            positive = np.where(scores > 0)[0]
        order = positive[np.argsort(scores[positive])[::-1]][:top_k] if len(positive) else np.array([], dtype=int)
        top_scores = [float(scores[i]) for i in order]
        stats = {"query_length_tokens": len(tokens), "expanded_tokens": len(tokens), "max_score": max(top_scores) if top_scores else 0.0, "mean_score": statistics.mean(top_scores) if top_scores else 0.0, "min_score": min(top_scores) if top_scores else 0.0, "std_score": statistics.stdev(top_scores) if len(top_scores) > 1 else 0.0, "non_zero_docs": int(len(positive)) if not used_overlap else len(order), "corpus_coverage": float(len(positive) / len(scores)) if len(scores) and not used_overlap else float(len(order) / len(scores)) if len(scores) else 0.0, "used_overlap_fallback": used_overlap}
        return {"documents": [[self.documents[i] for i in order]], "metadatas": [[self.metadatas[i] for i in order]], "ids": [[self.doc_ids[i] for i in order]], "scores": top_scores, "query_tokens": tokens, "bm25_stats": stats}
