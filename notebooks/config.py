from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple, List


def _detect_project_root() -> Path:
    """
    Determine the project's root directory reliably across execution contexts.

    When running as a script, __file__ points to the config module location.
    In notebook environments, we fall back to the current working directory.
    The function prefers a parent directory containing a 'data' folder,
    which is the conventional layout for this pipeline.
    """
    try:
        base = Path(__file__).resolve().parent
    except NameError:
        base = Path.cwd()

    # Check if parent contains data directory (standard project structure)
    candidate = base.parent if (base.parent / "data").exists() else base
    if (candidate / "data").exists():
        return candidate
    return base


PROJECT_ROOT = _detect_project_root()


@dataclass
class Config:
    """
    Central configuration for the multimodal RAG pipeline.

    This class consolidates all tunable parameters for document processing,
    embedding, retrieval, and generation. Centralizing configuration ensures
    reproducible experiments and simplifies switching between the six
    evaluation configurations shown in the ablation study:
    Dense-only, BM25-only, Hybrid Static, Hybrid Adaptive, Hybrid with Reranker,
    and Full Pipeline.
    """
    pdf_dir: str = str(PROJECT_ROOT / "data" / "pdf")
    images_dir: str = str(PROJECT_ROOT / "data" / "images_pymupdf")
    database_dir: str = str(PROJECT_ROOT / "data" / "database")
    results_dir: str = str(PROJECT_ROOT / "data" / "results")
    model_comparison_results_dir: str = str(PROJECT_ROOT / "data" / "model_comparison")
    retrieval_results_dir: str = str(PROJECT_ROOT / "data" / "retrieval_results")

    # ── Notebook execution safety / reproducibility ──────────────────────────
    # When True, delete and recreate Chroma collections at startup.
    # This avoids "Nothing found on disk" after interrupted runs, but forces
    # rebuilding the index each time.
    reset_collections_on_start: bool = True

    # If True, prints verbose metric details (default False keeps notebook output clean).
    metrics_verbose: bool = True
    # LLM reliability controls (used by `LocalLLM.generate_response`).
    llm_retries: int = 2
    llm_retry_backoff_sec: float = 1.0
    llm_fail_fast: bool = False

    text_embed_model: str = "BAAI/bge-large-en-v1.5"
    image_embed_model: str = "ViT-L-14"
    image_embed_pretrained: str = "laion2b_s32b_b82k"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-12-v2"

    # ── LLM settings ──────────────────────────────────────────────────────────
    llm_model: str = "qwen3.5:2b"
    llm_models: List[str] = field(default_factory=lambda: ["gemma4:e2b"])

    tiktoken_encoding: str = "cl100k_base"

    chunk_max_tokens: int = 384
    chunk_overlap_tokens: int = 128
    chunk_max_vertical_gap: int = 60
    chunk_min_text_len: int = 20

    text_embed_batch_size: int = 64
    image_embed_batch_size: int = 8

    text_collection_name: str = "vector_text"
    image_collection_name: str = "vector_image"

    text_k: int = 10
    image_k: int = 5
    rerank_k: int = 5
    rrf_k_constant: int = 20

    bm25_weight: float = 0.5
    semantic_weight: float = 0.5
    adaptive_weights_keyword: Tuple[float, float] = (0.7, 0.3)
    adaptive_weights_semantic: Tuple[float, float] = (0.2, 0.8)
    adaptive_weights_balanced_strong_bm25: Tuple[float, float] = (0.5, 0.5)
    adaptive_weights_balanced_weak_bm25: Tuple[float, float] = (0.3, 0.7)
    bm25_strong_max_score_threshold: float = 10.0
    bm25_strong_std_threshold: float = 2.0
    bm25_weak_max_score_threshold: float = 3.0

    image_caption_image_weight: float = 0.8

    max_text_chunks: int = 3
    max_context_tokens: int = 1024
    max_images: int = 1
    text_distance_threshold: float = 1.0
    image_distance_threshold: float = 0.75
    use_filtering: bool = True
    use_percentile_filtering: bool = True
    percentile_cutoff: int = 80

    llm_temperature: float = 0.25
    llm_max_tokens: int = 384
    llm_think_mode: bool = False

    retrieval_mode: str = "hybrid"  # Retrieval strategy: "semantic", "bm25", or "hybrid"

    use_reranker: bool = True
    adaptive_weighting: bool = True
    use_weighted_fusion: bool = False  # When True, hybrid uses normalized weighted sum; when False, uses Reciprocal Rank Fusion

    rouge_top_k_chunks: int = 5
    top_k_accuracy_k: int = 5

    # ── Reranker scoring weights ───────────────────────────────────────────────
    # final_rank_score = (ce_weight * ce_norm) + (fused_weight * fused_norm) + (boost_weight * boost_norm)
    # Must sum to 1.0. ce = CrossEncoder score, fused = hybrid fusion score, boost = cross-modal boost.
    reranker_ce_weight: float = 0.65  # CrossEncoder relevance
    reranker_fused_weight: float = 0.30  # Hybrid fusion score contribution
    reranker_boost_weight: float = 0.05  # Cross-modal image-text boost contribution

    # ── Cross-modal boost ──────────────────────────────────────────────────────
    # When a retrieved text chunk shares image IDs with retrieved images, its distance
    # is reduced by (boost_per_overlap * number_of_overlapping_images), capped at max_boost.
    use_cross_modal_boost: bool = True
    cross_modal_max_boost: float = 0.3  # Maximum distance reduction allowed
    cross_modal_boost_per_overlap: float = 0.1  # Boost added per overlapping image ID

    # ── BM25 adaptive signal strength weights ─────────────────────────────────
    # bm25_signal_strength = (max_w * max_signal) + (std_w * std_signal) +
    #                        (spec_w * specificity_signal) + (lex_w * lexical_query_signal)
    # Must sum to 1.0.
    bm25_signal_max_weight: float = 0.60  # Weight for BM25 max-score signal
    bm25_signal_std_weight: float = 0.40  # Weight for BM25 score std-dev signal
    bm25_signal_spec_weight: float = 0.0  # Unused; kept for backward compatibility
    bm25_signal_lex_weight: float = 0.0  # Unused; kept for backward compatibility

    # ── Image quality filter thresholds ───────────────────────────────────────
    # These control which images are kept vs discarded during PDF loading.
    img_variance_threshold: int = 10  # Minimum pixel std-dev; below = flat/junk image
    img_white_pixel_threshold: int = 240  # Pixel value above which it counts as "white"
    img_white_ratio_threshold: float = 0.95  # Fraction of white pixels that marks image as blank
    img_min_width: int = 50  # Minimum image width in pixels
    img_min_height: int = 50  # Minimum image height in pixels
    img_min_aspect_ratio: float = 0.1  # Minimum width/height ratio (filters tall slivers)
    img_max_aspect_ratio: float = 10.0  # Maximum width/height ratio (filters wide banners)

    @property
    def fusion_type(self) -> str:
        """
        Return the active fusion strategy name for reporting and experiment tracking.

        Weighted sum fusion combines normalized BM25 and semantic scores using
        configurable weights. Reciprocal Rank Fusion combines rank positions
        using the standard 1/(k+rank) formulation without score scaling.
        """
        return "weighted_sum" if self.use_weighted_fusion else "rrf"

    def validate(self) -> None:
        if self.retrieval_mode not in {"semantic", "bm25", "hybrid"}:
            raise ValueError("retrieval_mode must be one of 'semantic', 'bm25', or 'hybrid'.")
        if self.retrieval_mode != "hybrid" and self.adaptive_weighting:
            raise ValueError("adaptive_weighting requires retrieval_mode='hybrid'.")
        if self.retrieval_mode != "hybrid" and self.use_weighted_fusion:
            raise ValueError("use_weighted_fusion requires retrieval_mode='hybrid'.")
        if abs((self.bm25_weight + self.semantic_weight) - 1.0) > 1e-6:
            raise ValueError("bm25_weight and semantic_weight must sum to 1.0.")
        if not (0.0 <= self.image_caption_image_weight <= 1.0):
            raise ValueError("image_caption_image_weight must be between 0 and 1.")
        if self.text_k < self.rerank_k:
            raise ValueError("text_k must be greater than or equal to rerank_k.")
        if not (0 <= self.percentile_cutoff <= 100):
            raise ValueError("percentile_cutoff must be between 0 and 100.")
        if self.use_percentile_filtering and not self.use_filtering:
            raise ValueError("use_percentile_filtering requires use_filtering=True.")
        for directory in [self.pdf_dir, self.images_dir, self.database_dir,
                          self.results_dir, self.model_comparison_results_dir,
                          self.retrieval_results_dir]:
            Path(directory).mkdir(parents=True, exist_ok=True)
        if not self.llm_models:
            raise ValueError("llm_models list cannot be empty.")
        reranker_total = self.reranker_ce_weight + self.reranker_fused_weight + self.reranker_boost_weight
        if abs(reranker_total - 1.0) > 1e-6:
            raise ValueError(
                f"reranker_ce_weight + reranker_fused_weight + reranker_boost_weight must sum to 1.0, got {reranker_total:.4f}."
            )
        bm25_signal_total = (self.bm25_signal_max_weight + self.bm25_signal_std_weight +
                             self.bm25_signal_spec_weight + self.bm25_signal_lex_weight)
        if abs(bm25_signal_total - 1.0) > 1e-6:
            raise ValueError(
                f"BM25 signal weights must sum to 1.0, got {bm25_signal_total:.4f}."
            )


cfg = Config()