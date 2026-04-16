from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


def _detect_project_root() -> Path:
    """
    Resolve the project root in both notebook and script execution modes.
    """
    notebook_dir = Path(__file__).resolve().parent
    project_root = notebook_dir.parent
    if (project_root / "data").exists():
        return project_root
    return notebook_dir


PROJECT_ROOT = _detect_project_root()


@dataclass
class Config:
    """
    Central configuration for the notebook-based RAG pipeline.

    Keeping experiment settings here makes the notebooks easier to read and
    makes parameter changes easier to track during research iterations.
    """
    pdf_dir: str = str(PROJECT_ROOT / "data" / "pdf")
    images_dir: str = str(PROJECT_ROOT / "data" / "images_pymupdf")
    database_dir: str = str(PROJECT_ROOT / "data" / "database")
    results_dir: str = str(PROJECT_ROOT / "data" / "results")
    retrieval_results_dir: str = str(PROJECT_ROOT / "data" / "retrieval_results")

    text_embed_model: str = "BAAI/bge-large-en-v1.5"
    image_embed_model: str = "ViT-L-14"
    image_embed_pretrained: str = "laion2b_s32b_b82k"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-12-v2"
    llm_model: str = "gemma4:e2b"
    tiktoken_encoding: str = "cl100k_base"

    chunk_max_tokens: int = 384
    chunk_overlap_tokens: int = 96
    chunk_max_vertical_gap: int = 60
    chunk_min_text_len: int = 20

    text_embed_batch_size: int = 32
    image_embed_batch_size: int = 8

    text_collection_name: str = "vector_text"
    image_collection_name: str = "vector_image"

    text_k: int = 10
    image_k: int = 5
    rerank_k: int = 5
    rrf_k_constant: int = 20

    bm25_weight: float = 0.4
    semantic_weight: float = 0.6
    adaptive_weights_keyword: Tuple[float, float] = (0.7, 0.3)
    adaptive_weights_semantic: Tuple[float, float] = (0.2, 0.8)
    adaptive_weights_balanced_strong_bm25: Tuple[float, float] = (0.5, 0.5)
    adaptive_weights_balanced_weak_bm25: Tuple[float, float] = (0.3, 0.7)
    bm25_strong_max_score_threshold: float = 10.0
    bm25_strong_std_threshold: float = 2.0
    bm25_weak_max_score_threshold: float = 3.0

    image_caption_image_weight: float = 0.7

    max_text_chunks: int = 5
    max_images: int = 1
    text_distance_threshold: float = 0.65
    image_distance_threshold: float = 0.75
    percentile_cutoff: int = 50

    llm_temperature: float = 0.4
    llm_max_tokens: int = 1000

    use_hybrid: bool = True
    use_reranker: bool = True
    adaptive_weighting: bool = True
    score_fusion: bool = True

    rouge_top_k_chunks: int = 5
    top_k_accuracy_k: int = 5

    def validate(self) -> None:
        if abs((self.bm25_weight + self.semantic_weight) - 1.0) > 1e-6:
            raise ValueError("bm25_weight and semantic_weight must sum to 1.0.")
        if not (0.0 <= self.image_caption_image_weight <= 1.0):
            raise ValueError("image_caption_image_weight must be between 0 and 1.")
        if self.text_k < self.rerank_k:
            raise ValueError("text_k must be greater than or equal to rerank_k.")
        if not Path(self.pdf_dir).is_dir():
            raise FileNotFoundError(f"PDF directory not found: {self.pdf_dir}")


cfg = Config()
