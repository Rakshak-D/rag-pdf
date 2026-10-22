"""Cache-only, lazy adapter for the audited BERTScore F1 metric."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable, Literal


MetricStatus = Literal["computed", "skipped", "failed", "unavailable"]
MODEL_ID = "roberta-large"
MODEL_LAYERS = 17
EXECUTION_BATCH_SIZE = 8


@dataclass(frozen=True)
class CachedBertModel:
    path: str
    model_identifier: str
    model_type: str = MODEL_ID
    num_layers: int = MODEL_LAYERS


@dataclass(frozen=True)
class BertScoreResult:
    status: MetricStatus
    value: float | None = None
    reason_code: str | None = None
    model_identifier: str | None = None


def _revision_identifier(path: Path) -> str:
    name = path.name
    if len(name) == 40 and all(char in "0123456789abcdef" for char in name.lower()):
        return f"{MODEL_ID}@{name.lower()}"
    return MODEL_ID


def resolve_cached_model() -> CachedBertModel | None:
    """Resolve the legacy model from the existing local HF cache only."""

    try:
        from bert_score.utils import lang2model, model2layers
        from huggingface_hub import snapshot_download

        model_id = lang2model["en"]
        layers = model2layers[model_id]
        local_path = snapshot_download(repo_id=model_id, local_files_only=True)
    except ImportError:
        raise
    except Exception:
        return None
    path = Path(local_path)
    if not path.is_dir():
        return None
    # Avoid passing an incomplete local directory to transformers, which could
    # otherwise attempt a remote lookup for a missing file.
    required = (path / "config.json",)
    has_tokenizer = any((path / name).is_file() for name in ("tokenizer.json", "vocab.json", "spiece.model"))
    has_weights = any((path / name).is_file() for name in ("pytorch_model.bin", "model.safetensors", "pytorch_model.bin.index.json", "model.safetensors.index.json")) or any(path.glob("*.safetensors"))
    if not all(item.is_file() for item in required) or not has_tokenizer or not has_weights:
        return None
    return CachedBertModel(str(path), _revision_identifier(path), model_type=model_id, num_layers=layers)


class BertScoreAdapter:
    """Lazily score one response/reference pair with cached BERTScore."""

    def __init__(
        self,
        *,
        model_resolver: Callable[[], CachedBertModel | None] | None = None,
        scorer_factory: Callable[..., Any] | None = None,
        device_selector: Callable[[], str] | None = None,
        batch_size: int = EXECUTION_BATCH_SIZE,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        self._resolver = model_resolver or resolve_cached_model
        self._factory = scorer_factory
        self._device_selector = device_selector or self._default_device
        self.batch_size = batch_size
        self._scorer: Any = None
        self._model: CachedBertModel | None = None
        self._load_attempted = False
        self._load_result: BertScoreResult | None = None

    @staticmethod
    def _default_device() -> str:
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _load(self) -> BertScoreResult | None:
        if self._load_attempted:
            return self._load_result
        self._load_attempted = True
        try:
            model = self._resolver()
        except ImportError:
            self._load_result = BertScoreResult("unavailable", reason_code="bertscore_dependency_unavailable")
            return self._load_result
        except Exception:
            self._load_result = BertScoreResult("unavailable", reason_code="bertscore_model_not_cached")
            return self._load_result
        if model is None:
            self._load_result = BertScoreResult("unavailable", reason_code="bertscore_model_not_cached")
            return self._load_result
        if not isinstance(model, CachedBertModel):
            self._load_result = BertScoreResult("failed", reason_code="bertscore_model_load_failed")
            return self._load_result
        self._model = model
        model_identifier = model.model_identifier or MODEL_ID
        if self._factory is None:
            try:
                from bert_score.scorer import BERTScorer

                self._factory = BERTScorer
            except Exception:
                self._load_result = BertScoreResult("unavailable", reason_code="bertscore_dependency_unavailable", model_identifier=model_identifier)
                return self._load_result
        try:
            self._scorer = self._factory(
                model_type=model.path,
                num_layers=model.num_layers,
                batch_size=self.batch_size,
                idf=False,
                device=self._device_selector(),
                lang="en",
                rescale_with_baseline=False,
            )
        except Exception:
            self._load_result = BertScoreResult("failed", reason_code="bertscore_model_load_failed", model_identifier=model_identifier)
            return self._load_result
        return None

    @staticmethod
    def _extract_f1(value: object) -> object:
        if not isinstance(value, (tuple, list)) or len(value) != 3:
            return None
        f1 = value[2]
        try:
            if len(f1) != 1:
                return None
            item = f1[0]
            return item.item() if hasattr(item, "item") else item
        except Exception:
            return None

    def score(self, response: str, reference: str) -> BertScoreResult:
        if not isinstance(response, str) or not response.strip() or not isinstance(reference, str) or not reference.strip():
            return BertScoreResult("skipped", reason_code="missing_text")
        load_result = self._load()
        if load_result is not None:
            return load_result
        assert self._model is not None
        try:
            output = self._scorer.score([response.strip()], [reference.strip()], batch_size=self.batch_size, verbose=False)
            value = self._extract_f1(output)
        except Exception:
            return BertScoreResult("failed", reason_code="bertscore_failed", model_identifier=self._model.model_identifier)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return BertScoreResult("failed", reason_code="bertscore_invalid_score", model_identifier=self._model.model_identifier)
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0.0 or numeric > 1.0:
            return BertScoreResult("failed", reason_code="bertscore_invalid_score", model_identifier=self._model.model_identifier)
        return BertScoreResult("computed", value=numeric, model_identifier=self._model.model_identifier)
