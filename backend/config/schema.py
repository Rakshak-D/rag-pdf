"""Pure, typed configuration representations for the workbench boundary."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from typing import Any, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from notebooks.config import Config


_DEFAULT_LEGACY_CONFIG = Config()
_T = TypeVar("_T", bound="_LegacyConfigBase")


def _approximately_one(value: float) -> bool:
    return abs(value - 1.0) <= 1e-6


class _LegacyConfigBase(BaseModel):
    """Shared behavior for the dynamically field-complete legacy schema."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @property
    def fusion_type(self) -> str:
        """Expose the research-derived fusion label without storing a second flag."""

        return "weighted_sum" if self.use_weighted_fusion else "rrf"

    @classmethod
    def from_legacy(cls: type[_T], config: Config) -> _T:
        """Copy a legacy ``Config`` without invoking its side-effecting validate()."""

        values = {field.name: getattr(config, field.name) for field in dataclass_fields(Config)}
        return cls(**values)

    def to_legacy(self) -> Config:
        """Reconstruct the research dataclass without calling ``Config.validate``."""

        values = self.model_dump(mode="python", exclude={"workbench_policy"})
        return Config(**values)

    def to_serializable_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-compatible configuration data."""

        return self.model_dump(mode="json")

    @model_validator(mode="after")
    def validate_cross_field_contracts(self) -> "_LegacyConfigBase":
        positive_fields = (
            "chunk_max_tokens",
            "chunk_min_text_len",
            "text_embed_batch_size",
            "image_embed_batch_size",
            "text_k",
            "image_k",
            "rerank_k",
            "rrf_k_constant",
            "max_text_chunks",
            "max_context_tokens",
            "max_images",
            "llm_max_tokens",
            "rouge_top_k_chunks",
            "top_k_accuracy_k",
            "img_min_width",
            "img_min_height",
        )
        for field_name in positive_fields:
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be greater than zero")

        if self.chunk_overlap_tokens < 0:
            raise ValueError("chunk_overlap_tokens must be non-negative")
        if self.chunk_overlap_tokens >= self.chunk_max_tokens:
            raise ValueError("chunk_overlap_tokens must be less than chunk_max_tokens")
        if self.chunk_max_vertical_gap < 0:
            raise ValueError("chunk_max_vertical_gap must be non-negative")

        if self.retrieval_mode not in {"semantic", "bm25", "hybrid"}:
            raise ValueError("retrieval_mode must be one of: semantic, bm25, hybrid")
        if self.retrieval_mode != "hybrid" and self.adaptive_weighting:
            raise ValueError("adaptive_weighting requires retrieval_mode='hybrid'")
        if self.retrieval_mode != "hybrid" and self.use_weighted_fusion:
            raise ValueError("use_weighted_fusion requires retrieval_mode='hybrid'")
        if self.text_k < self.rerank_k:
            raise ValueError("text_k must be greater than or equal to rerank_k")

        self._check_weight_pair("bm25_weight", "semantic_weight")
        for name in (
            "adaptive_weights_keyword",
            "adaptive_weights_semantic",
            "adaptive_weights_balanced_strong_bm25",
            "adaptive_weights_balanced_weak_bm25",
        ):
            self._check_tuple_weights(name)
        self._check_weight_group(
            "reranker weights",
            (self.reranker_ce_weight, self.reranker_fused_weight, self.reranker_boost_weight),
        )
        self._check_weight_group(
            "BM25 signal weights",
            (
                self.bm25_signal_max_weight,
                self.bm25_signal_std_weight,
                self.bm25_signal_spec_weight,
                self.bm25_signal_lex_weight,
            ),
        )

        for field_name in (
            "bm25_weight",
            "semantic_weight",
            "image_caption_image_weight",
            "reranker_ce_weight",
            "reranker_fused_weight",
            "reranker_boost_weight",
            "bm25_signal_max_weight",
            "bm25_signal_std_weight",
            "bm25_signal_spec_weight",
            "bm25_signal_lex_weight",
            "img_white_ratio_threshold",
        ):
            self._check_unit_interval(field_name)
        for name in (
            "adaptive_weights_keyword",
            "adaptive_weights_semantic",
            "adaptive_weights_balanced_strong_bm25",
            "adaptive_weights_balanced_weak_bm25",
        ):
            for value in getattr(self, name):
                if not 0.0 <= value <= 1.0:
                    raise ValueError(f"{name} values must be between 0 and 1")

        if not 0 <= self.percentile_cutoff <= 100:
            raise ValueError("percentile_cutoff must be between 0 and 100")
        if self.use_percentile_filtering and not self.use_filtering:
            raise ValueError("use_percentile_filtering requires use_filtering=True")
        if not 0 <= self.llm_temperature <= 2:
            raise ValueError("llm_temperature must be between 0 and 2")
        if self.llm_retries < 0:
            raise ValueError("llm_retries must be non-negative")
        if self.llm_retry_backoff_sec < 0:
            raise ValueError("llm_retry_backoff_sec must be non-negative")

        for field_name in (
            "text_distance_threshold",
            "image_distance_threshold",
            "bm25_strong_max_score_threshold",
            "bm25_strong_std_threshold",
            "bm25_weak_max_score_threshold",
            "img_variance_threshold",
            "cross_modal_max_boost",
            "cross_modal_boost_per_overlap",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.img_white_pixel_threshold < 0 or self.img_white_pixel_threshold > 255:
            raise ValueError("img_white_pixel_threshold must be between 0 and 255")
        if self.img_min_aspect_ratio <= 0:
            raise ValueError("img_min_aspect_ratio must be greater than zero")
        if self.img_max_aspect_ratio < self.img_min_aspect_ratio:
            raise ValueError("img_max_aspect_ratio must be >= img_min_aspect_ratio")

        for field_name in (
            "pdf_dir", "images_dir", "database_dir", "results_dir",
            "model_comparison_results_dir", "retrieval_results_dir",
            "text_embed_model", "image_embed_model", "image_embed_pretrained",
            "reranker_model", "llm_model", "tiktoken_encoding",
            "text_collection_name", "image_collection_name",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be non-empty")
        if not self.llm_models or any(not model.strip() for model in self.llm_models):
            raise ValueError("llm_models must contain at least one non-empty model identifier")
        if self.llm_model not in self.llm_models:
            raise ValueError("llm_model must be included in llm_models")
        if self.cross_modal_boost_per_overlap > self.cross_modal_max_boost:
            raise ValueError("cross_modal_boost_per_overlap must not exceed cross_modal_max_boost")
        return self

    def _check_weight_pair(self, first: str, second: str) -> None:
        self._check_weight_group(f"{first} + {second}", (getattr(self, first), getattr(self, second)))

    def _check_tuple_weights(self, field_name: str) -> None:
        value = getattr(self, field_name)
        if len(value) != 2:
            raise ValueError(f"{field_name} must contain exactly two weights")
        self._check_weight_group(field_name, value)

    @staticmethod
    def _check_weight_group(label: str, values: tuple[float, ...] | list[float]) -> None:
        if any(value < 0 for value in values):
            raise ValueError(f"{label} must not contain negative weights")
        total = sum(values)
        if not _approximately_one(total):
            raise ValueError(f"{label} must sum to 1.0; got {total:.6f}")

    def _check_unit_interval(self, field_name: str) -> None:  # type: ignore[override]
        value = getattr(self, field_name)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{field_name} must be between 0 and 1")


# Build the compatibility schema directly from the dataclass.  This makes a
# missing legacy field an import-time/test-visible error rather than a silent
# compatibility gap while retaining Pydantic's actual typed validation.
_legacy_fields = {
    field.name: (field.type, getattr(_DEFAULT_LEGACY_CONFIG, field.name))
    for field in dataclass_fields(Config)
}
LegacyConfigSchema = create_model(
    "LegacyConfigSchema",
    __base__=_LegacyConfigBase,
    **_legacy_fields,
)


class WorkbenchSafetyPolicy(BaseModel):
    """Explicit opt-in controls for destructive workbench operations."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    allow_destructive_index_reset: bool = False
    allow_collection_delete: bool = False


class ResolvedRunConfig(LegacyConfigSchema):
    """Validated legacy settings plus explicit workbench safety policy."""

    workbench_policy: WorkbenchSafetyPolicy = Field(default_factory=WorkbenchSafetyPolicy)

    @classmethod
    def from_legacy(
        cls,
        config: Config,
        *,
        workbench_policy: WorkbenchSafetyPolicy | None = None,
    ) -> "ResolvedRunConfig":
        values = {field.name: getattr(config, field.name) for field in dataclass_fields(Config)}
        values["workbench_policy"] = workbench_policy or WorkbenchSafetyPolicy()
        return cls(**values)

    def validate_for_workbench(self) -> None:
        """Apply the future workbench's non-destructive default before execution."""

        if self.reset_collections_on_start and not self.workbench_policy.allow_destructive_index_reset:
            raise ValueError(
                "reset_collections_on_start=True requires explicit "
                "workbench_policy.allow_destructive_index_reset=True"
            )
