"""Behavior-preserving engine adapters that remain free of model side effects."""

from .ingestion import (
    PdfExtractionResult,
    bbox_chunker,
    build_chunk,
    build_image_objects,
    extract_pdfs,
    get_overlapping_images,
    is_caption_block,
    is_extreme_aspect_ratio,
    is_low_variance,
    is_mostly_white,
    merge_bbox,
    stable_chunk_id,
    vertical_gap,
)

__all__ = [
    "PdfExtractionResult",
    "bbox_chunker",
    "build_chunk",
    "build_image_objects",
    "extract_pdfs",
    "get_overlapping_images",
    "is_caption_block",
    "is_extreme_aspect_ratio",
    "is_low_variance",
    "is_mostly_white",
    "merge_bbox",
    "stable_chunk_id",
    "vertical_gap",
]
