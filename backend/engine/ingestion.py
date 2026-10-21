"""PDF extraction and layout-aware chunking without model initialization.

The extraction and chunking functions in this module are a deliberately small
port of the corresponding definitions in ``notebooks/rag_pipeline.py``.  The
research source remains protected; parity tests compare this module with
selected definitions loaded from that source without importing the full
pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import logging
import traceback

import numpy as np
import pymupdf
import tiktoken
from langchain_core.documents import Document

from backend.config.paths import resolve_owned_path
from backend.config.schema import ResolvedRunConfig
from backend.experiments.run_plan import RunPlan
from backend.engine.manifest import (
    DocumentManifest,
    ManifestMismatchError,
    ManifestValidationError,
    build_document_manifest,
    stage_document_snapshot,
)


LOGGER = logging.getLogger(__name__)


@dataclass
class PdfExtractionResult:
    """Page results plus explicit file-level success/failure reporting."""

    pages: List[Dict[str, Any]]
    successful_files: List[str]
    failed_files: List[Dict[str, str]]
    manifest_hash: str = ""


def _validated_config(config: ResolvedRunConfig) -> ResolvedRunConfig:
    if not isinstance(config, ResolvedRunConfig):
        raise TypeError("expected a ResolvedRunConfig")
    return ResolvedRunConfig.model_validate(config.model_dump(mode="python"))


def _execution_config(config_or_plan: ResolvedRunConfig | RunPlan) -> ResolvedRunConfig:
    if isinstance(config_or_plan, RunPlan):
        config_or_plan.verify_integrity()
        config = config_or_plan.resolved_config()
    else:
        config = _validated_config(config_or_plan)
    config.validate_for_workbench()
    return config


def _release_pixmap(pix: Any) -> None:
    """Release a Pixmap across the PyMuPDF APIs supported by the project pin."""

    close = getattr(pix, "close", None)
    if callable(close):
        close()


def is_low_variance(pix: Any, threshold: int) -> bool:
    """Match the research low-variance image filter with an explicit threshold."""

    try:
        if pix is None or pix.samples is None:
            return True
        samples = np.frombuffer(pix.samples, dtype=np.uint8)
        if len(samples) == 0:
            return True
        if pix.n >= 3:
            samples = samples.reshape(-1, pix.n)[:, :3].mean(axis=1)
        return samples.std() < threshold
    except Exception as exc:
        LOGGER.debug("is_low_variance failed: %s", exc)
        return True


def is_mostly_white(pix: Any, threshold: int, ratio: float) -> bool:
    """Match the research mostly-white image filter with explicit thresholds."""

    try:
        if pix is None or pix.samples is None:
            return True
        samples = np.frombuffer(pix.samples, dtype=np.uint8)
        if len(samples) == 0:
            return True
        if pix.n >= 3:
            samples = samples.reshape(-1, pix.n)[:, :3].mean(axis=1)
        white_pixels = np.sum(samples > threshold)
        return (white_pixels / len(samples)) > ratio
    except Exception as exc:
        LOGGER.debug("is_mostly_white failed: %s", exc)
        return True


def is_extreme_aspect_ratio(pix: Any, min_ratio: float, max_ratio: float) -> bool:
    """Match the research aspect-ratio image filter with explicit bounds."""

    try:
        if pix is None:
            return True
        aspect_ratio = pix.width / max(pix.height, 1)
        return aspect_ratio < min_ratio or aspect_ratio > max_ratio
    except Exception as exc:
        LOGGER.debug("is_extreme_aspect_ratio failed: %s", exc)
        return True


def _extract_pdf_pages(
    config: ResolvedRunConfig,
    input_dir: str | Path,
    storage_root: str | Path,
    manifest_hash: str,
) -> PdfExtractionResult:
    """Extract pages from an already verified, staged PDF directory."""

    source_root = Path(input_dir)
    if not source_root.is_dir():
        raise NotADirectoryError(f"{source_root} is an invalid directory.")

    owned_root = Path(storage_root).expanduser().resolve(strict=False)
    images_root = resolve_owned_path(owned_root, config.images_dir)
    pdf_files = sorted(source_root.rglob("*.pdf"))
    if not pdf_files:
        return PdfExtractionResult(
            pages=[], successful_files=[], failed_files=[], manifest_hash=manifest_hash
        )

    owned_root.mkdir(parents=True, exist_ok=True)
    images_root.mkdir(parents=True, exist_ok=True)
    all_pages: List[Dict[str, Any]] = []
    successful_files: List[str] = []
    failed_files: List[Dict[str, str]] = []

    for pdf_path in pdf_files:
        pdf = None
        try:
            image_dir = resolve_owned_path(images_root, pdf_path.stem)
            image_dir.mkdir(parents=True, exist_ok=True)
            pdf = pymupdf.open(filename=pdf_path, filetype="pdf")

            for page_num, page in enumerate(pdf, start=1):
                text_blocks: List[Dict[str, Any]] = []
                page_images: List[Dict[str, Any]] = []
                seen_xrefs = set()
                images = page.get_images(full=True)

                for img_index, img in enumerate(images):
                    pix = None
                    converted_pix = None
                    try:
                        if img[1] != 0:  # Preserve research soft-mask behavior.
                            continue
                        xref = img[0]
                        if xref in seen_xrefs:
                            continue
                        seen_xrefs.add(xref)
                        rects = page.get_image_rects(xref)
                        if not rects:
                            continue

                        pix = pymupdf.Pixmap(pdf, xref)
                        if pix.width < config.img_min_width or pix.height < config.img_min_height:
                            continue
                        if pix.alpha and pix.samples is not None and len(pix.samples) > 0:
                            try:
                                samples = np.frombuffer(pix.samples, dtype=np.uint8)
                                alpha = samples[pix.n - 1::pix.n]
                                if alpha.size > 0 and int(alpha.max()) == 0:
                                    continue
                            except Exception:
                                pass
                        if pix.n > 4:
                            converted_pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                            _release_pixmap(pix)
                            pix = converted_pix
                            converted_pix = None
                        if is_mostly_white(
                            pix, config.img_white_pixel_threshold, config.img_white_ratio_threshold
                        ):
                            continue
                        if is_low_variance(pix, config.img_variance_threshold):
                            continue
                        if is_extreme_aspect_ratio(
                            pix, config.img_min_aspect_ratio, config.img_max_aspect_ratio
                        ):
                            continue

                        img_path = resolve_owned_path(
                            image_dir, f"page_{page_num}_img_{img_index}.png"
                        )
                        if img_path.exists():
                            raise FileExistsError(f"refusing to overwrite existing image: {img_path}")
                        pix.save(img_path)
                        rect = rects[0]
                        page_images.append({
                            "image_id": f"{pdf_path.stem}_p{page_num}_i{img_index}",
                            "path": str(img_path),
                            "page": page_num,
                            "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                        })
                    except Exception as image_error:
                        LOGGER.warning("Error processing image %d in %s: %s", img_index, pdf_path.name, image_error)
                    finally:
                        if converted_pix is not None:
                            _release_pixmap(converted_pix)
                        if pix is not None:
                            _release_pixmap(pix)

                blocks = sorted(page.get_text("blocks"), key=lambda block: (block[1], block[0]))
                for block_id, block in enumerate(blocks):
                    x0, y0, x1, y1, text = block[:5]
                    text = text.strip()
                    if len(text) < config.chunk_min_text_len:
                        continue
                    text_blocks.append({
                        "block_id": block_id,
                        "text": text,
                        "bbox": [x0, y0, x1, y1],
                        "page": page_num,
                    })

                all_pages.append({
                    "source": pdf_path.name,
                    "page": page_num,
                    "text_blocks": text_blocks,
                    "images": page_images,
                })
            successful_files.append(pdf_path.name)
        except Exception as exc:
            failed_files.append({
                "source": pdf_path.name,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            LOGGER.error("Error loading %s: %s", pdf_path.name, exc)
            LOGGER.debug("PDF extraction traceback for %s:\n%s", pdf_path.name, traceback.format_exc())
        finally:
            if pdf is not None and not pdf.is_closed:
                pdf.close()

    return PdfExtractionResult(
        pages=all_pages,
        successful_files=successful_files,
        failed_files=failed_files,
        manifest_hash=manifest_hash,
    )


def _reject_duplicate_stems(manifest: DocumentManifest) -> None:
    stems: dict[str, list[str]] = {}
    for entry in manifest.entries:
        stem = Path(entry.relative_path).stem.casefold()
        stems.setdefault(stem, []).append(entry.relative_path)
    conflicts = [paths for paths in stems.values() if len(paths) > 1]
    if conflicts:
        formatted = "; ".join(", ".join(sorted(paths)) for paths in conflicts)
        raise ManifestValidationError(
            "duplicate PDF stems are not supported by the current extraction identity: "
            f"{formatted}"
        )


def extract_pdfs(
    config_or_plan: ResolvedRunConfig | RunPlan,
    input_dir: str | Path,
    storage_root: str | Path,
) -> PdfExtractionResult:
    """Bind extraction to a verified manifest and immutable source snapshot."""

    config = _execution_config(config_or_plan)
    manifest = build_document_manifest(input_dir)
    _reject_duplicate_stems(manifest)
    if isinstance(config_or_plan, RunPlan):
        if manifest.content_hash != config_or_plan.document_manifest_hash:
            raise ManifestMismatchError(
                "run-plan document_manifest_hash does not match source directory: "
                f"plan={config_or_plan.document_manifest_hash}, current={manifest.content_hash}"
            )
    snapshot_root = stage_document_snapshot(input_dir, manifest, storage_root)
    return _extract_pdf_pages(config, snapshot_root, storage_root, manifest.content_hash)


def vertical_gap(block1: Dict[str, Any], block2: Dict[str, Any]) -> float:
    return block2["bbox"][1] - block1["bbox"][3]


def merge_bbox(blocks: List[Dict[str, Any]]) -> Optional[Tuple[float, float, float, float]]:
    if not blocks:
        return None
    return (
        min(block["bbox"][0] for block in blocks),
        min(block["bbox"][1] for block in blocks),
        max(block["bbox"][2] for block in blocks),
        max(block["bbox"][3] for block in blocks),
    )


def stable_chunk_id(source: str, page_num: int, text: str) -> str:
    chunk_hash = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return f"{source}_p{page_num}_c{chunk_hash}"


def bbox_overlap(a: Tuple[float, ...], b: Tuple[float, ...]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def is_caption_block(
    text_block: Dict[str, Any],
    image: Dict[str, Any],
    max_words: int = 60,
    max_vertical_dist: int = 80,
) -> bool:
    text = text_block.get("text", "")
    if not text or len(text.split()) > max_words:
        return False
    tb_bbox = text_block.get("bbox")
    im_bbox = image.get("bbox")
    if not tb_bbox or not im_bbox:
        return False
    tb_x0, tb_y0, tb_x1, tb_y1 = tb_bbox
    im_x0, im_y0, im_x1, im_y1 = im_bbox
    horizontal_overlap = not (tb_x1 < im_x0 or tb_x0 > im_x1)
    vertical_distance = min(abs(tb_y0 - im_y1), abs(im_y0 - tb_y1))
    return horizontal_overlap and vertical_distance <= max_vertical_dist


def get_overlapping_images(
    chunk_bbox: Tuple[float, ...],
    page_images: List[Dict[str, Any]],
    vertical_tolerance: int = 200,
    horizontal_tolerance: int = 50,
) -> List[str]:
    if not chunk_bbox or not page_images:
        return []
    overlapping_ids = []
    chunk_x0, chunk_y0, chunk_x1, chunk_y1 = chunk_bbox
    for image in page_images:
        image_bbox = image.get("bbox")
        if not image_bbox:
            continue
        img_x0, img_y0, img_x1, img_y1 = image_bbox
        horizontal_overlap = not (
            chunk_x1 + horizontal_tolerance < img_x0
            or chunk_x0 > img_x1 + horizontal_tolerance
        )
        vertical_distance = min(
            abs(chunk_y0 - img_y1),
            abs(img_y0 - chunk_y1),
            abs(chunk_y0 - img_y0),
            abs(chunk_y1 - img_y1),
        )
        if horizontal_overlap and vertical_distance <= vertical_tolerance:
            overlapping_ids.append(image["image_id"])
        elif bbox_overlap(chunk_bbox, image_bbox):
            overlapping_ids.append(image["image_id"])
    return overlapping_ids


def build_image_objects(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    image_objects = []
    for page in pages:
        source = page.get("source", "unknown")
        page_num = page.get("page", 0)
        text_blocks = page.get("text_blocks", [])
        for image in page.get("images", []):
            caption_blocks = [
                block["text"]
                for block in text_blocks
                if block.get("text") and is_caption_block(block, image)
            ]
            image_objects.append({
                "image_id": image.get("image_id", "unknown"),
                "type": "image",
                "modality": "vision",
                "source": source,
                "page_num": page_num,
                "bbox": image.get("bbox"),
                "path": image.get("path"),
                "caption_text": " ".join(caption_blocks).strip() or None,
            })
    return image_objects


def build_chunk(
    source: str,
    page_num: int,
    blocks: List[Dict[str, Any]],
    page_images: List[Dict[str, Any]],
    related_image_ids: Optional[List[str]] = None,
) -> Optional[Document]:
    if not blocks:
        return None
    chunk_text = "\n".join(block.get("text", "") for block in blocks)
    if not chunk_text.strip():
        return None
    chunk_bbox = merge_bbox(blocks)
    if related_image_ids is None:
        related_image_ids = get_overlapping_images(chunk_bbox, page_images)
    return Document(
        page_content=chunk_text,
        metadata={
            "source": source,
            "page_num": page_num,
            "bbox": chunk_bbox,
            "chunk_id": stable_chunk_id(source, page_num, chunk_text),
            "related_image_ids": related_image_ids or [],
        },
    )


def bbox_chunker(pages: List[Dict[str, Any]], config: ResolvedRunConfig) -> List[Document]:
    """Port the research layout-aware chunker with explicit configuration."""

    config = _validated_config(config)
    tokenizer = tiktoken.get_encoding(config.tiktoken_encoding)
    all_chunks: List[Document] = []

    for page in pages:
        source = page.get("source", "")
        page_num = page.get("page", 0)
        blocks = page.get("text_blocks", [])
        page_images = page.get("images", [])
        if not blocks:
            continue

        i = 0
        while i < len(blocks):
            current_blocks = []
            current_tokens = 0
            start_i = i
            while i < len(blocks):
                block = blocks[i]
                text = block.get("text", "")
                if not text:
                    i += 1
                    continue
                block_tokens = len(tokenizer.encode(text))
                gap = vertical_gap(current_blocks[-1], block) if current_blocks else 0
                if current_blocks and (
                    current_tokens + block_tokens > config.chunk_max_tokens
                    or gap > config.chunk_max_vertical_gap
                ):
                    break
                current_blocks.append(block)
                current_tokens += block_tokens
                i += 1

            if current_blocks:
                chunk = build_chunk(source, page_num, current_blocks, page_images)
                if chunk:
                    first_sentence = current_blocks[0]["text"].split(".")[0][:100].strip()
                    chunk.metadata["semantic_tag"] = (
                        first_sentence[:50] + "..." if len(first_sentence) > 50 else first_sentence
                    )
                    all_chunks.append(chunk)

            if config.chunk_overlap_tokens > 0 and i < len(blocks):
                overlap_tok = 0
                step_back = 0
                for j in range(len(current_blocks) - 1, -1, -1):
                    block_text = current_blocks[j].get("text", "")
                    block_tok = len(tokenizer.encode(block_text))
                    if overlap_tok + block_tok <= config.chunk_overlap_tokens:
                        overlap_tok += block_tok
                        step_back += 1
                    else:
                        break
                i = max(start_i + 1, i - step_back)

    return all_chunks
