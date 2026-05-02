#!/usr/bin/env python
# coding: utf-8
# %%

# %% [markdown]
# # RAG Pipeline (PDF → Vector DB → Retrieval → LLM → Metrics)
#
# 1. **Document loading**: read PDFs, extract text blocks + useful images.
# 2. **Chunking**: group nearby text blocks into semantically coherent chunks (with page + bbox metadata).
# 3. **Embedding**: create vector embeddings for text and images.
# 4. **Vector store**: persist embeddings (text + images) to a vector database.
# 5. **Retrieval**: retrieve relevant chunks/images for each query (optionally hybrid + re-ranking).
# 6. **Context formatting**: build an LLM-ready prompt context.
# 7. **LLM evaluation**: answer questions with one or more local LLMs.
# 8. **Metrics + reports**: compute retrieval/generation/performance metrics and export PDFs.

# %% [markdown]
# # Document Loading
#
# Goal: convert PDFs into a structured intermediate format:
# - **Text blocks**: small rectangular “snippets” extracted from each page.
# - **Images**: exported to disk after filtering out low-quality/blank/decorative images.
#
# Output: a list of page dictionaries (one per page) with text + images + coordinates.

# %%
import hashlib
import numpy as np
import pymupdf
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import traceback
from dataclasses import dataclass, field

# %%
from config import Config, cfg, PROJECT_ROOT

# %%
import sys

NOTEBOOK_ROOT = PROJECT_ROOT / "notebooks" if (PROJECT_ROOT / "notebooks").exists() else PROJECT_ROOT
if str(NOTEBOOK_ROOT) not in sys.path:
    sys.path.insert(0, str(NOTEBOOK_ROOT))
# %%
import logging as py_logging
import shutil
import textwrap
import contextlib
import io

LOGGER = py_logging.getLogger("rag_pipeline")


def setup_logging(level: str = "INFO") -> None:
    """
    Configure console logging once (safe for notebooks and repeated imports).

    Why logging (instead of many `print()` calls)?
    - lets you dial verbosity up/down without editing the pipeline
    - avoids redundant, noisy output in notebooks
    """
    if LOGGER.handlers:
        return

    # Windows terminals sometimes default to legacy encodings (e.g., cp1252),
    # which can crash on unicode characters (✓, ✗, box-drawing). Prefer UTF-8.
    try:
        import sys as _sys

        for _stream in (_sys.stdout, _sys.stderr):
            if hasattr(_stream, "reconfigure"):
                _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    numeric = getattr(py_logging, level.upper(), py_logging.INFO)
    py_logging.basicConfig(level=numeric, format="%(message)s")

    # Silence noisy third-party libraries (HF Hub HTTP logs, tqdm progress bars, etc.).
    # You can still enable them by raising levels or removing these overrides.
    for name in (
            "huggingface_hub",
            "transformers",
            "sentence_transformers",
            "rouge_score",
            "bert_score",
            "httpcore",
            "requests",
            "urllib3",
            "httpx",
            "open_clip",
            "chromadb",
            "posthog",
    ):
        py_logging.getLogger(name).setLevel(py_logging.WARNING)

    try:
        from huggingface_hub.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
    except Exception:
        pass

    try:
        from transformers.utils import logging as t_logging

        t_logging.set_verbosity_error()
        t_logging.disable_progress_bar()
    except Exception:
        pass

    # rouge_score uses absl logging in some paths and can emit messages like
    # "Using default tokenizer." directly to stderr. Silence absl noise.
    try:
        from absl import logging as absl_logging

        absl_logging.set_verbosity(absl_logging.ERROR)
        absl_logging.set_stderrthreshold("error")
    except Exception:
        pass

    # Hide common non-actionable HF Hub warning in viewer-facing runs.
    try:
        import warnings as _warnings

        _warnings.filterwarnings(
            "ignore",
            message=r".*unauthenticated requests to the HF Hub.*",
        )
    except Exception:
        pass


def _terminal_width(default: int = 100) -> int:
    try:
        return max(shutil.get_terminal_size((default, 20)).columns, 60)
    except Exception:
        return default


def log_section(title: str, width: Optional[int] = None) -> None:
    width = width or _terminal_width()
    bar = "=" * min(width, 120)
    LOGGER.info("")
    LOGGER.info(bar)
    LOGGER.info(title.strip())
    LOGGER.info(bar)


def log_kv(label: str, value: str, indent: int = 2, width: Optional[int] = None) -> None:
    """
    Print a single key/value line with safe wrapping (prevents “broken tables/lines” in notebooks).
    """
    width = width or _terminal_width()
    prefix = " " * indent + f"{label}: "
    wrapped = textwrap.fill(str(value), width=width, subsequent_indent=" " * len(prefix))
    LOGGER.info(prefix + wrapped[len(prefix):] if wrapped.startswith(prefix) else prefix + wrapped)


@contextlib.contextmanager
def suppress_output(enabled: bool = True):
    """
    Best-effort suppression of noisy third-party stdout/stderr (HF HTTP logs, tokenizers messages, etc.).

    Use this in metrics where libraries sometimes print directly to stdout even when logging is configured.
    """
    if not enabled:
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


# %%
def is_low_variance(pix, threshold: int = cfg.img_variance_threshold) -> bool:
    """
    Checks if an image is too 'flat' or 'plain' (like a solid color block).
    Threshold is read from config so it can be tuned without touching code.
    """
    try:
        if pix is None or pix.samples is None:
            return True
        samples = np.frombuffer(pix.samples, dtype=np.uint8)
        if len(samples) == 0:
            return True
        if pix.n >= 3:
            samples = samples.reshape(-1, pix.n)[:, :3].mean(axis=1)
        return samples.std() < threshold
    except Exception as e:
        LOGGER.debug("is_low_variance failed: %s", e)
        return True


# %%
def is_mostly_white(pix, threshold: int = cfg.img_white_pixel_threshold,
                    ratio: float = cfg.img_white_ratio_threshold) -> bool:
    """
    Checks if an image is almost entirely white space.
    Threshold and ratio read from config.
    """
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
    except Exception as e:
        LOGGER.debug("is_mostly_white failed: %s", e)
        return True


# %%
def is_extreme_aspect_ratio(pix, min_ratio: float = cfg.img_min_aspect_ratio,
                            max_ratio: float = cfg.img_max_aspect_ratio) -> bool:
    """
    Checks if an image is extremely thin or wide (header/footer lines, decorative bars).
    Bounds read from config.
    """
    try:
        if pix is None:
            return True
        aspect_ratio = pix.width / max(pix.height, 1)
        return aspect_ratio < min_ratio or aspect_ratio > max_ratio
    except Exception as e:
        LOGGER.debug("is_extreme_aspect_ratio failed: %s", e)
        return True


# %%
def loading_pdf(dir_path: str = cfg.pdf_dir, images_dir: str = cfg.images_dir) -> List[Dict]:
    """
    Load every PDF in the input directory and extract page text blocks and images.

    The returned list is page-centric: each entry contains the source file name,
    the page number, the text blocks found on that page, and the images exported
    from that same page. This structure makes later chunking and text-image linking
    easier to explain and reproduce.
    """
    dir_path = Path(dir_path)
    images_root = Path(images_dir)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"{dir_path} is an invalid directory.")

    LOGGER.info("")
    LOGGER.info("=" * 80)
    LOGGER.info("  PDF LOADING")
    LOGGER.info("=" * 80)
    LOGGER.info("  Directory: %s", dir_path)

    pdf_files = sorted(dir_path.rglob("*.pdf"))  # Deterministic ordering keeps benchmarking reproducible.
    LOGGER.info("  Found %d PDF file(s)", len(pdf_files))

    if not pdf_files:
        LOGGER.warning("  WARNING: No documents found in directory")
        return []

    all_pdf_size = 0.0
    all_pages = []
    failed_pdf = []

    LOGGER.info("")
    LOGGER.info("  Loading PDFs...")
    LOGGER.info("  " + "-" * 76)

    for serial, pdf_path in enumerate(pdf_files, start=1):
        LOGGER.info("  [%d/%d] Loading: %s", serial, len(pdf_files), pdf_path.name)
        pdf_size_bytes = pdf_path.stat().st_size
        pdf_size_mb = pdf_size_bytes / (1024 ** 2)
        LOGGER.info("       Size: %.2f MB", pdf_size_mb)

        pdf = None
        try:
            image_dir = images_root / pdf_path.stem
            image_dir.mkdir(parents=True, exist_ok=True)
            pdf = pymupdf.open(filename=pdf_path, filetype="pdf")

            pdf_total_text_blocks = 0
            pdf_total_images = 0

            for page_num, page in enumerate(pdf, start=1):
                text_blocks = []
                page_images = []
                seen_xrefs = set()
                images = page.get_images(full=True)

                for img_index, img in enumerate(images):
                    pix = None
                    try:
                        if img[1] != 0:  # Skip soft mask.
                            # soft mask -> Transparency layer
                            continue
                        xref = img[0]
                        if xref in seen_xrefs:
                            continue
                        seen_xrefs.add(xref)
                        rects = page.get_image_rects(xref)
                        if not rects:
                            continue

                        pix = pymupdf.Pixmap(pdf, xref)
                        if pix.width < cfg.img_min_width or pix.height < cfg.img_min_height:
                            pix = None
                            continue
                        # Skip fully transparent images (alpha channel all zeros).
                        # Previous logic checked *all* channels, which misses many transparent images.
                        if pix.alpha and pix.samples is not None and len(pix.samples) > 0:
                            try:
                                samples = np.frombuffer(pix.samples, dtype=np.uint8)
                                # Alpha is the last channel when pix.alpha is True (pix.n includes alpha).
                                alpha = samples[pix.n - 1::pix.n]
                                if alpha.size > 0 and int(alpha.max()) == 0:
                                    continue
                            except Exception:
                                # If we cannot safely inspect alpha, do not block extraction.
                                pass
                        if pix.n > 4:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        if is_mostly_white(pix):
                            pix = None
                            continue
                        if is_low_variance(pix):
                            pix = None
                            continue
                        if is_extreme_aspect_ratio(pix):
                            pix = None
                            continue

                        img_path = image_dir / f"page_{page_num}_img_{img_index}.png"
                        pix.save(img_path)
                        pix = None
                        rect = rects[0]
                        page_images.append({
                            "image_id": f"{pdf_path.stem}_p{page_num}_i{img_index}",
                            "path": str(img_path),
                            "page": page_num,
                            "bbox": [rect.x0, rect.y0, rect.x1, rect.y1]
                        })
                    except Exception as img_error:
                        LOGGER.warning("       Error processing image %d: %s", img_index, img_error)
                    finally:
                        if pix is not None:
                            pix.close()

                blocks = sorted(page.get_text("blocks"), key=lambda b: (b[1], b[0]))
                for block_id, b in enumerate(blocks):
                    x0, y0, x1, y1, text = b[:5]
                    text = text.strip()
                    if len(text) < cfg.chunk_min_text_len:
                        continue
                    block_bbox = [x0, y0, x1, y1]
                    text_blocks.append({
                        "block_id": block_id,
                        "text": text,
                        "bbox": block_bbox,
                        "page": page_num,
                    })

                all_pages.append({
                    "source": pdf_path.name,
                    "page": page_num,
                    "text_blocks": text_blocks,
                    "images": page_images
                })
                pdf_total_text_blocks += len(text_blocks)
                pdf_total_images += len(page_images)
            all_pdf_size += pdf_size_mb
            pdf.close()
            LOGGER.info("       [OK] Extracted %d text blocks, %d images", pdf_total_text_blocks, pdf_total_images)

        except Exception as e:
            LOGGER.error("       [ERROR] Error loading %s: %s", pdf_path.name, e)  # Exception handling.
            failed_pdf.append(pdf_path.name)
            traceback.print_exc()
        finally:
            if pdf is not None and not pdf.is_closed:
                pdf.close()

    LOGGER.info("  " + "-" * 76)
    LOGGER.info("")
    LOGGER.info("  SUMMARY:")
    LOGGER.info("       Total size: %.2f MB", all_pdf_size)
    LOGGER.info("       Total pages extracted: %d", len(all_pages))
    LOGGER.info("       Successful: %d/%d", len(pdf_files) - len(failed_pdf), len(pdf_files))

    if failed_pdf:
        # Surface the failure rate as a percentage so it appears in logs
        # alongside the raw count and is easy to spot during batch runs.
        fail_rate_pct = len(failed_pdf) / max(len(pdf_files), 1) * 100
        LOGGER.warning(
            "  PDF failure rate: %.1f%% (%d/%d files failed)",
            fail_rate_pct, len(failed_pdf), len(pdf_files),
        )
        LOGGER.info("")
        LOGGER.info("  Failed PDFs:")
        for fp in failed_pdf:
            LOGGER.info("       - %s", fp)

    LOGGER.info("")
    LOGGER.info("=" * 80)
    LOGGER.info("")
    return all_pages


# %% [markdown]
# # Chunking
#
# Goal: convert per-page text blocks into **chunks** sized for embedding + retrieval.
#
# Why chunking matters:
# - If chunks are too large, retrieval becomes noisy and LLM context overflows.
# - If chunks are too small, important meaning gets split across pieces.
#
# This pipeline uses **bounding boxes** (layout-aware chunking):
# - Blocks close together vertically are merged.
# - Each chunk keeps page + bbox + linked image IDs for cross-modal retrieval.

# %%
from langchain_core.documents import Document
from typing import List
from typing import Tuple
import tiktoken


# %%
def vertical_gap(block1, block2) -> float:
    """Return the vertical distance (points) between the bottom edge of block1 and the top edge of block2."""
    return block2["bbox"][1] - block1["bbox"][3]


# %%
def merge_bbox(blocks):
    """Return the bounding box that tightly encloses all blocks as (x0, y0, x1, y1)."""
    if not blocks:
        return None

    return (
        min(b["bbox"][0] for b in blocks),  # x0 left
        min(b["bbox"][1] for b in blocks),  # y0 top
        max(b["bbox"][2] for b in blocks),  # x1 right
        max(b["bbox"][3] for b in blocks)  # y1 bottom
    )


# %%
def stable_chunk_id(source: str, page_num: int, text: str) -> str:
    """Generate a deterministic chunk identifier from source, page, and text content."""
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return f"{source}_p{page_num}_c{h}"


# %%
def bbox_overlap(a, b) -> bool:
    """Return True if bounding boxes a and b overlap in both axes."""
    return not (
            a[2] < b[0] or  # right edge of a and left edge of b
            a[0] > b[2] or  # left edge of a and right edge of b
            a[3] < b[1] or  # bottom edge of a and top edge of b
            a[1] > b[3]  # top edge of a and bottom edge of b
    )


# %%
def is_caption_block(text_block: Dict, image: Dict, max_words: int = 60, max_vertical_dist: int = 80) -> bool:
    """
    Return True if text_block is likely a caption for the given image.

    A block qualifies when it is horizontally aligned with the image, within
    max_vertical_dist points vertically, and does not exceed max_words in length.
    """
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

    vertical_distance = min(  # Vertical distance between text block and image.
        abs(tb_y0 - im_y1),
        abs(im_y0 - tb_y1)
    )
    return horizontal_overlap and vertical_distance <= max_vertical_dist


# %%
def get_overlapping_images(chunk_bbox: Tuple, page_images: List[Dict], vertical_tolerance: int = 200,
                           horizontal_tolerance: int = 50) -> List[str]:
    """
    Return the image IDs of all images spatially associated with chunk_bbox.

    Association is determined by horizontal proximity combined with vertical
    proximity within tolerance, or by direct bounding-box overlap.
    """
    if not chunk_bbox or not page_images:
        return []

    overlapping_ids = []

    chunk_x0, chunk_y0, chunk_x1, chunk_y1 = chunk_bbox

    for img in page_images:
        img_bbox = img.get("bbox")
        if not img_bbox:
            continue

        img_x0, img_y0, img_x1, img_y1 = img_bbox

        # Check for horizontal overlap or proximity
        horizontal_overlap = not (
                chunk_x1 + horizontal_tolerance < img_x0 or
                chunk_x0 > img_x1 + horizontal_tolerance
        )

        # Calculate vertical distance between chunk and image
        vertical_distance = min(
            abs(chunk_y0 - img_y1),  # Distance from chunk top to image bottom
            abs(img_y0 - chunk_y1),  # Distance from image top to chunk bottom
            abs(chunk_y0 - img_y0),  # Distance between tops
            abs(chunk_y1 - img_y1)  # Distance between bottoms
        )

        # Method 1: Check if horizontally aligned and vertically close
        if horizontal_overlap and vertical_distance <= vertical_tolerance:
            overlapping_ids.append(img["image_id"])
        # Method 2: Or if they actually overlap perfectly
        elif bbox_overlap(chunk_bbox, img_bbox):
            overlapping_ids.append(img["image_id"])

    return overlapping_ids


# %%
def build_image_objects(pages: List[Dict]) -> List[Dict]:
    """
    Construct image metadata objects from extracted PDF pages.

    Each object carries the image path, source coordinates, page number, and
    any caption text identified from adjacent text blocks.
    """
    image_objects = []

    for page in pages:
        source = page.get("source", "unknown")
        page_num = page.get("page", 0)
        text_blocks = page.get("text_blocks", [])
        images = page.get("images", [])

        for img in images:
            caption_blocks = [
                tb["text"] for tb in text_blocks
                if tb.get("text") and is_caption_block(tb, img)
            ]

            caption_text = " ".join(caption_blocks).strip() or None

            image_objects.append({
                "image_id": img.get("image_id", "unknown"),
                "type": "image",
                "modality": "vision",
                "source": source,
                "page_num": page_num,
                "bbox": img.get("bbox"),
                "path": img.get("path"),
                "caption_text": caption_text
            })

    return image_objects


# %%
def build_chunk(source: str, page_num: int, blocks: List, page_images: List[Dict],
                related_image_ids: Optional[List[str]] = None) -> Optional[Document]:
    """
    Assemble a LangChain Document from a list of text blocks.

    Merges block text, computes a bounding box, generates a stable chunk ID,
    and associates spatially co-located image IDs with the chunk.
    """
    if not blocks:
        return None

    chunk_text = "\n".join(b.get("text", "") for b in blocks)

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
            "related_image_ids": related_image_ids or []
        }
    )


# %%
def bbox_chunker(
        pages: List[Dict],
        max_tokens: int = cfg.chunk_max_tokens,
        max_vertical_gap: int = cfg.chunk_max_vertical_gap,
        overlap_tokens: int = cfg.chunk_overlap_tokens,
        token_model: str = cfg.tiktoken_encoding
) -> List[Document]:
    """
    Segment page text blocks into overlapping chunks using spatial and token constraints.

    A new chunk is started when adding the next block would exceed max_tokens or
    when the vertical gap between blocks exceeds max_vertical_gap. Overlap is
    implemented by stepping back up to overlap_tokens worth of blocks before
    starting each new chunk.
    """
    tokenizer = tiktoken.get_encoding(token_model)

    all_chunks = []

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

            # Building a chunk until max_tokens or vertical gap threshold
            while i < len(blocks):
                block = blocks[i]
                text = block.get("text", "")

                if not text:
                    i += 1
                    continue

                block_tokens = len(tokenizer.encode(text))

                if current_blocks:
                    gap = vertical_gap(current_blocks[-1], block)
                else:
                    gap = 0

                # Checking conditions for creating a chunk.
                # (Basically threshold that chunk should have certain number of tokens and text block distances.)
                if current_blocks and (
                        current_tokens + block_tokens > max_tokens
                        or gap > max_vertical_gap
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

            if overlap_tokens > 0 and i < len(blocks):
                overlap_tok = 0
                step_back = 0

                for j in range(len(current_blocks) - 1, -1, -1):
                    block_text = current_blocks[j].get("text", "")
                    block_tok = len(tokenizer.encode(block_text))
                    if overlap_tok + block_tok <= overlap_tokens:
                        overlap_tok += block_tok
                        step_back += 1
                    else:
                        break

                i = max(start_i + 1, i - step_back)

    LOGGER.info("  Created %d chunks from %d pages (max_tokens=%d, overlap_tokens=%d)",
                len(all_chunks), len(pages), max_tokens, overlap_tokens)

    return all_chunks


# %% [markdown]
# # Embedding
#
# Goal: turn text chunks and images into vectors.
#
# - Text embeddings: using the model configured in `cfg.text_embed_model`.
# - Image embeddings: using OpenCLIP (model + pretrained weights from config).
#
# Output: NumPy vectors + metadata that will be written to the vector store.

# %%
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

# %%
import numpy as np
import torch
from PIL import Image
import open_clip  # Image embedding model .
from typing import List, Dict, Tuple
from dotenv import load_dotenv
from time import perf_counter
import os

# %%
load_dotenv()

# %%
# Suppress verbose warnings from sentence-transformers.
import warnings
from transformers import logging as transformers_logging

transformers_logging.set_verbosity_error()
transformers_logging.disable_progress_bar()
warnings.filterwarnings("ignore", message=".*position_ids.*UNEXPECTED.*")


# %%
class ImageEmbeddingModel:
    """
    Embed document images with OpenCLIP.

    Each image can optionally be fused with its nearby caption so the final
    vector carries both visual and textual cues. Batching is used to take
    advantage of the GPU instead of processing one image at a time.
    """

    def __init__(
            self,
            model_name: str = cfg.image_embed_model,
            pretrained: str = cfg.image_embed_pretrained,
            caption_image_weight: float = cfg.image_caption_image_weight,
            batch_size: int = cfg.image_embed_batch_size
    ):
        if not 0.0 <= caption_image_weight <= 1.0:
            raise ValueError("caption_image_weight must be between 0.0 and 1.0.")
        self.model_name = model_name
        self.pretrained = pretrained
        self.caption_image_weight = caption_image_weight
        self.batch_size = batch_size
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        try:
            self.model, self.preprocess, _ = open_clip.create_model_and_transforms(
                model_name=model_name,
                pretrained=pretrained
            )
            self.tokenizer = open_clip.get_tokenizer(model_name)
        except Exception as e:
            raise RuntimeError(f"Failed to load OpenCLIP model '{model_name}': {e}") from e

        self.model = self.model.to(self.device)
        self.model.eval()

        if self.device == "cuda":
            LOGGER.info("OpenCLIP running on %s.", torch.cuda.get_device_name(0))
        else:
            LOGGER.info("OpenCLIP running on CPU.")

        model_config = open_clip.get_model_config(self.model_name)
        self.embed_dim = model_config["embed_dim"]
        LOGGER.info("Embedding dimension of %s is %d", self.model_name, self.embed_dim)

    @torch.no_grad()
    def embed_image(self, image_objects: List[Dict]) -> Tuple[np.ndarray, float, Dict]:
        # Image embedding must be fault-tolerant:
        # - PDFs often contain broken/unsupported images
        # - the pipeline should still run on remaining valid images
        if not image_objects:
            stats = {
                "count": 0,
                "embeddings_created": 0,
                "failed_loads": 0,
                "total_time": 0.0,
                "avg_time": 0.0,
                "dimension": getattr(self, "embed_dim", 0),
                "caption_image_weight": self.caption_image_weight,
            }
            return np.empty((0, stats["dimension"]), dtype=np.float32), 0.0, stats

        start_time = perf_counter()
        original_count = len(image_objects)
        captions: List[str] = []
        image_tensors: List[torch.Tensor] = []
        failed_paths: List[str] = []
        kept_image_objects: List[Dict] = []

        for image_object in image_objects:
            image_path = image_object.get("path")
            if not isinstance(image_path, str) or not image_path.strip():
                failed_paths.append(str(image_path))
                continue
            try:
                with Image.open(image_path) as pil_image:
                    processed_image = self.preprocess(pil_image.convert("RGB"))
            except Exception as e:
                LOGGER.warning("Image load failed; skipping: %s (%s)", image_path, e)
                failed_paths.append(image_path)
                continue

            image_tensors.append(processed_image)
            raw_caption = image_object.get("caption_text") or ""
            captions.append(raw_caption.strip() if isinstance(raw_caption, str) else "")
            kept_image_objects.append(image_object)

        # Keep caller data consistent: downstream storage expects documents and embeddings to align 1:1.
        # We mutate the list in-place so `image_objects` corresponds exactly to the returned embeddings.
        image_objects[:] = kept_image_objects

        if not image_tensors:
            stats = {
                "count": original_count,
                "embeddings_created": 0,
                "failed_loads": len(failed_paths),
                "total_time": perf_counter() - start_time,
                "avg_time": 0.0,
                "dimension": self.embed_dim,
                "caption_image_weight": self.caption_image_weight,
                "kept": 0,
            }
            LOGGER.warning("All extracted images failed to load; continuing without image vectors.")
            return np.empty((0, self.embed_dim), dtype=np.float32), stats["total_time"], stats
        if failed_paths:
            LOGGER.warning("Skipped %s image(s) that failed to load during embedding.", len(failed_paths))

        image_embeddings: List[np.ndarray] = []
        for batch_start in range(0, len(image_tensors), self.batch_size):
            batch_tensor = torch.stack(
                image_tensors[batch_start: batch_start + self.batch_size]
            ).to(self.device)
            batch_embeddings = self.model.encode_image(batch_tensor)
            batch_embeddings = batch_embeddings / batch_embeddings.norm(dim=-1, keepdim=True)
            image_embeddings.extend(batch_embeddings.cpu().numpy())

        # Keep weights stable and always summing to 1.0:
        # fused = (image_weight * image_vec) + (caption_weight * caption_vec)
        image_weight = float(self.caption_image_weight)
        caption_weight = 1.0 - image_weight
        final_embeddings: List[np.ndarray] = []

        for image_embedding, caption in zip(image_embeddings, captions):
            if caption:
                try:
                    tokens = self.tokenizer([caption]).to(self.device)
                    caption_embedding = self.model.encode_text(tokens)
                    caption_embedding = caption_embedding / caption_embedding.norm(dim=-1, keepdim=True)
                    caption_embedding = caption_embedding.cpu().numpy()[0]
                    # Both components are already L2-normalised. Renormalising after
                    # the weighted sum would distort the caller-specified blend ratio,
                    # because the post-fusion norm depends on the angle between the
                    # two vectors and is not guaranteed to equal 1. Storing the raw
                    # weighted sum preserves the intended image_weight/caption_weight
                    # balance while still producing a vector whose magnitude is in
                    # (0, 1] when both inputs are unit vectors.
                    fused_embedding = (image_weight * image_embedding) + (caption_weight * caption_embedding)
                    final_embeddings.append(fused_embedding)
                except Exception as e:
                    LOGGER.warning("Caption embedding failed; using image-only embedding (%s)", e)
                    final_embeddings.append(image_embedding)
            else:
                final_embeddings.append(image_embedding)

        total_time = perf_counter() - start_time
        avg_time = total_time / len(final_embeddings) if final_embeddings else 0.0

        stats = {
            "count": original_count,
            "embeddings_created": len(final_embeddings),
            "failed_loads": len(failed_paths),
            "total_time": total_time,
            "avg_time": avg_time,
            "dimension": self.embed_dim,
            "caption_image_weight": self.caption_image_weight,
            "kept": len(final_embeddings),
        }

        return np.vstack(final_embeddings), total_time, stats

    @torch.no_grad()
    def embed_query(self, query: str) -> np.ndarray:
        if not isinstance(query, str):
            raise TypeError("Query must be a string.")
        if not query.strip():
            raise ValueError("Please give a valid prompt.")
        tokens = self.tokenizer([query]).to(self.device)
        query_embedding = self.model.encode_text(tokens)
        query_embedding = query_embedding / query_embedding.norm(dim=-1, keepdim=True)
        query_embedding = query_embedding.cpu().numpy()[0]
        return query_embedding


# %%
from sentence_transformers import SentenceTransformer
import numpy as np
import torch
from typing import List
from langchain_core.documents import Document
from time import perf_counter


# %%
class TextEmbeddingModel:
    def __init__(self, model_name: str = cfg.text_embed_model, batch_size: int = cfg.text_embed_batch_size):
        self.model_name = model_name
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.batch_size = batch_size

        try:
            self.model = SentenceTransformer(model_name_or_path=model_name, device=self.device)
        except Exception as e:
            raise RuntimeError(f"Failed to load SentenceTransformer model: {e}")

        self.query_prefix = "Represent this sentence for searching relevant passages: " if "bge" in model_name.lower() else ""

        if self.device == "cuda":
            LOGGER.info("BGE running on %s.", torch.cuda.get_device_name(0))
        else:
            LOGGER.info("BGE running on CPU.")
        LOGGER.info(
            "Embedding dimension of %s is %d.",
            model_name,
            self.model.get_sentence_embedding_dimension(),
        )

    @torch.no_grad()
    def embed_documents(self, documents: List[Document]) -> Tuple:
        if not documents:
            raise ValueError("No documents to embed.")

        texts = []
        for index, doc in enumerate(documents):
            if not hasattr(doc, "page_content"):
                raise TypeError(f"Document at index {index} is missing page_content.")
            content = (doc.page_content or "").strip()
            if not content:
                raise ValueError(f"Document at index {index} has empty page_content.")
            texts.append(content)

        # Embedding texts.
        # sentences -> input/texts
        # batch_size -> Number of inputs embedding at a single time.
        # convert_to_numpy -> Convert output to numpy array.
        # normalize_embeddings -> Normalizing all the output vectors.

        start_time = perf_counter()  # Start the timer before text embedding starts . # M1 (Embedding time )

        text_embeddings = self.model.encode(
            sentences=texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False
        )
        total_time = perf_counter() - start_time
        avg_time = total_time / len(texts)

        # Store stats in dict instead of printing
        stats = {
            "count": len(texts),
            "embeddings_created": len(text_embeddings),
            "total_time": total_time,
            "avg_time": avg_time,
            "dimension": self.model.get_sentence_embedding_dimension()
        }

        return text_embeddings, total_time, stats

    @torch.no_grad()
    def encode_text(self, text: str, use_query_prefix: bool = False) -> np.ndarray:
        if not isinstance(text, str):
            raise TypeError("Text must be a string.")
        text = text.strip()
        if not text:
            raise ValueError("Given text is not valid.")
        model_input = f"{self.query_prefix}{text}" if use_query_prefix and self.query_prefix else text
        embedding = self.model.encode(
            sentences=model_input,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False
        )
        return embedding

    @torch.no_grad()
    def embed_query(self, query: str) -> np.ndarray:
        return self.encode_text(query, use_query_prefix=True)


# %% [markdown]
# # VectorStore
#
# Goal: store vectors in a database that supports fast similarity search.
#
# This code uses ChromaDB as the vector store:
# - One collection for text vectors.
# - One collection for image vectors.

# %%
import json
from typing import List, Dict, Union, Optional
import os
import hashlib
import numpy as np
from langchain_core.documents import Document

# Reduce noisy “telemetry enabled” messages for ChromaDB (best-effort; safe if ignored).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY", "False")

import chromadb


# %%
def stable_hash(obj: dict | str) -> str:
    """Return a SHA-256 hex digest of a dict (JSON-serialised) or string."""
    if isinstance(obj, dict):
        obj = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    elif not isinstance(obj, str):
        raise TypeError(f"stable_hash expects dict or str, got {type(obj)}")

    return hashlib.sha256(obj.encode("utf-8")).hexdigest()


# %%
def sanitize_metadata(metadata: dict) -> dict:
    """
    Normalise a metadata dict for ChromaDB storage.

    None values become empty strings; lists, tuples, and dicts are
    JSON-serialised; all other non-primitive types are coerced to str.
    """
    if not isinstance(metadata, dict):
        raise TypeError(f"sanitize_metadata expects dict , got {type(metadata)}")

    clean = {}
    for k, v in metadata.items():
        if v is None:
            clean[k] = ""  # None -> Empty string
        elif isinstance(v, (str, int, float, bool)):
            clean[k] = v
        elif isinstance(v, (list, tuple, dict)):
            clean[k] = json.dumps(v, ensure_ascii=False)
        else:
            clean[k] = str(v)

    return clean


# %%
def deserialize_metadata(metadata: dict) -> dict:
    if not isinstance(metadata, dict):
        return {}

    parsed = dict(metadata)
    for key in ["related_image_ids", "bbox"]:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            try:
                parsed[key] = json.loads(value)
            except json.JSONDecodeError:
                parsed[key] = value
    return parsed


# %%
class VectorStore:
    """Persistent ChromaDB-backed vector store for text chunks and image embeddings."""

    def __init__(
            self,
            collection_name: str,
            directory: str = cfg.database_dir,
            silent: bool = False,
            reset_collection: bool = False,
    ):
        if not collection_name or not isinstance(collection_name, str):
            raise ValueError("Collection name must be a non-empty string.")
        self.collection_name = collection_name
        self.persistent_directory = directory
        self.collection = None
        self.client = None
        self.silent = silent
        self.reset_collection = reset_collection
        self._existing_ids = set()
        self.initialize_store()  # Initializing vectorDB.

    def initialize_store(self) -> None:
        """Connect to the persistent ChromaDB client and load or create the named collection."""
        try:
            os.makedirs(name=self.persistent_directory, exist_ok=True)
            self.client = chromadb.PersistentClient(path=self.persistent_directory)
            exists = self.collection_exists(self.collection_name)
            if exists and self.reset_collection:
                try:
                    self.client.delete_collection(name=self.collection_name)
                    # Brief pause after deletion so the filesystem can
                    # release any locks on the underlying SQLite file before
                    # create_collection() opens a new handle.
                    import time as _reset_time
                    _reset_time.sleep(0.1)
                except Exception:
                    pass
                exists = False

            if exists:
                if not self.silent:
                    LOGGER.info("Loading collection '%s' from database.", self.collection_name)
                self.collection = self.client.get_collection(self.collection_name)
            else:  # If collection does not exist, create it.
                if not self.silent:
                    LOGGER.info("Creating new collection '%s'.", self.collection_name)
                self.collection = self.client.create_collection(name=self.collection_name,
                                                                metadata={"hnsw:space": "cosine"})
            self._existing_ids = set(self.collection.get(include=[])["ids"])
            if not self.silent:
                LOGGER.info(
                    "Vector store ready — %d existing documents.", len(self._existing_ids)
                )
        except Exception as e:  # Exception handling.
            raise RuntimeError(f"Could not initialize vector store: {e}") from e

    def add_documents(self, documents: List[Union[Dict, Document]], embeddings: np.ndarray) -> None:
        """
        Insert documents and their embeddings into the collection.

        Duplicate IDs are detected via an in-memory set and skipped without
        re-inserting into ChromaDB. Accepts both LangChain Document objects
        (text chunks) and plain dicts (image objects).
        """
        if not self.collection:
            raise RuntimeError("Collection is not initialized.")
        if not documents:
            raise ValueError("Documents list is empty.")
        if len(documents) != len(embeddings):
            raise ValueError(f"Number of documents ({len(documents)}) does not match embeddings ({len(embeddings)}).")

        ids, metadatas, texts, embedding_rows = [], [], [], []
        for idx, (doc, embedding) in enumerate(zip(documents, embeddings)):
            if isinstance(doc, Document):
                content = (doc.page_content or "").strip()
                if not content:
                    raise ValueError(f"Text document at index {idx} has empty page_content.")
                metadata_raw = doc.metadata or {}
                metadata = sanitize_metadata(metadata_raw)
                hash_input = {  # Data used for creating hashid.
                    "content": content,
                    "source": metadata.get("source"),
                    "page": metadata.get("page_num", ""),
                    "chunk_id": metadata.get("chunk_id", "")
                }
                doc_id = str(metadata_raw.get("chunk_id") or stable_hash(hash_input))
                texts.append(content)
                metadatas.append(metadata)
                ids.append(doc_id)
                embedding_rows.append(np.asarray(embedding, dtype=float).tolist())
            elif isinstance(doc, dict):
                bbox = doc.get("bbox")
                image_metadata = {
                    "image_id": doc.get("image_id", ""),
                    "source": doc.get("source", ""),
                    "page_num": doc.get("page_num", ""),
                    "image_path": doc.get("path", ""),
                    "caption_text": doc.get("caption_text", ""),
                    "bbox": bbox if bbox is not None else "",
                }
                image_metadata = sanitize_metadata(image_metadata)
                hash_input = {  # Used for hashid.
                    "image_id": image_metadata["image_id"],
                    "image_path": image_metadata["image_path"],
                    "source": image_metadata["source"],
                    "page_num": image_metadata["page_num"],
                    "bbox": image_metadata["bbox"],
                    "caption": image_metadata["caption_text"],
                }
                doc_id = str(doc.get("image_id") or stable_hash(hash_input))
                texts.append(doc.get("caption_text") or doc.get("image_id") or "")
                metadatas.append(image_metadata)
                ids.append(doc_id)
                embedding_rows.append(np.asarray(embedding, dtype=float).tolist())
            else:
                raise TypeError(f"Unsupported document type: {type(doc)}")

        if not ids:
            if not self.silent:
                LOGGER.info("No valid documents to process.")
            return

        seen = self._existing_ids.copy()
        new_indices = []
        for i, doc_id in enumerate(ids):
            if doc_id in self._existing_ids or doc_id in seen:
                continue
            seen.add(doc_id)
            new_indices.append(i)

        if not new_indices:
            if not self.silent:
                LOGGER.info("No new documents to add — all IDs already indexed.")
            return

        self.collection.add(
            ids=[ids[i] for i in new_indices],
            documents=[texts[i] for i in new_indices],
            metadatas=[metadatas[i] for i in new_indices],
            embeddings=[embedding_rows[i] for i in new_indices]
        )
        self._existing_ids.update(ids[i] for i in new_indices)
        if not self.silent:
            LOGGER.info("Added %d new documents to collection.", len(new_indices))

    def collection_exists(self, collection_name: str) -> bool:
        """Return True if a collection with the given name already exists in the database."""
        collections_in_db = self.client.list_collections()
        return any(col.name == collection_name for col in collections_in_db)

    def query(self, query_embedding: np.ndarray, k: int = 5, where: Optional[Dict] = None) -> Dict:
        """Return the top-k nearest neighbours for the given query embedding."""
        if not self.collection:
            raise RuntimeError("Collection is not initialized.")
        if query_embedding.ndim != 1:
            raise ValueError("Query embedding must be a 1D vector.")
        if not isinstance(k, int) or k <= 0:
            raise ValueError(f"k must be a positive integer, got {k}.")
        collection_count = self.collection.count()
        if collection_count <= 0:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}

        results = self.collection.query(  # Storing results.
            query_embeddings=[query_embedding.tolist()],
            n_results=min(k, collection_count),
            where=where,
            include=["documents", "metadatas", "distances"]
        )
        return results

    def get_collection_stats(self) -> Dict:
        if not self.collection:
            raise RuntimeError("Collection is not initialized.")

        return {
            "name": self.collection_name,
            "count": self.collection.count(),
            "directory": self.persistent_directory
        }

    def delete_collection(self) -> None:
        """Permanently remove the collection from the database and reset local state."""
        if not self.collection:
            raise RuntimeError("Collection is not initialized.")
        self.client.delete_collection(name=self.collection_name)
        self.collection = None
        self._existing_ids.clear()
        LOGGER.info("Collection '%s' deleted.", self.collection_name)


# %% [markdown]
# # Retrieval
#
# Goal: for each question, retrieve the most relevant text chunks and images.
#
# Features supported (controlled by `cfg.*` flags):
# - **Semantic search** (vector similarity)
# - **BM25** keyword search (lexical matching)
# - **Hybrid fusion** (combine BM25 + semantic)
# - **Cross-encoder re-ranking** (optional; improves precision at top-k)

# %%
from PIL import Image
import os
from typing import List, Dict, Optional
from sentence_transformers import CrossEncoder


# %%
class Reranker:
    """
    Cross-encoder reranker that rescores retrieved candidates against the query.

    Optionally blends the cross-encoder score with the upstream hybrid fusion
    score and a cross-modal proximity boost.
    """

    def __init__(self, model_name: str = cfg.reranker_model):
        self.model = CrossEncoder(model_name)
        LOGGER.info("Reranker initialized: %s", model_name)

    @staticmethod
    def _normalize(values: List[float], neutral: float = 0.5) -> List[float]:
        if values is None or len(values) == 0:
            return []

        arr = np.asarray(values, dtype=float)
        min_val = float(np.min(arr))
        max_val = float(np.max(arr))

        if max_val - min_val < 1e-6:
            return [neutral] * len(arr)

        normalized = (arr - min_val) / (max_val - min_val)
        return normalized.tolist()

    def rerank(self, query: str, items: List[Dict], top_k: int = cfg.rerank_k) -> List[Dict]:
        if not items:
            return []

        pairs = [[query, item["text"]] for item in items]
        ce_scores = self.model.predict(pairs)
        ce_normalized = self._normalize(ce_scores)

        has_hybrid_signal = any(item.get("fused_score") is not None for item in items)
        fused_scores = [float(item.get("fused_score", 0.0) or 0.0) for item in
                        items] if has_hybrid_signal else [0.0] * len(items)
        fused_normalized = self._normalize(fused_scores) if has_hybrid_signal else [0.0] * len(items)

        cross_modal_boosts = [float(item.get("cross_modal_boost", 0.0) or 0.0) for item in items]
        boost_normalized = self._normalize(cross_modal_boosts, neutral=0.0) if any(
            boost > 0 for boost in cross_modal_boosts) else [0.0] * len(items)

        ranked_items = []
        for item, ce_score, ce_norm, fused_norm, boost_norm in zip(items, ce_scores, ce_normalized, fused_normalized,
                                                                   boost_normalized):
            item_copy = dict(item)
            item_copy["reranker_score"] = float(ce_score)
            final_rank_score = (
                    (cfg.reranker_ce_weight * ce_norm) +
                    (cfg.reranker_fused_weight * fused_norm) +
                    (cfg.reranker_boost_weight * boost_norm)
            ) if has_hybrid_signal else ce_norm
            item_copy["final_rank_score"] = round(float(final_rank_score), 6)
            ranked_items.append(item_copy)

        ranked = sorted(
            ranked_items,
            key=lambda item: (
                item.get("final_rank_score", 0.0),
                item.get("fused_score", float("-inf")) if item.get("fused_score") is not None else float("-inf"),
                item.get("reranker_score", float("-inf"))
            ),
            reverse=True
        )
        return ranked[:top_k]


# %% [markdown]
# # Context formatter
#
# Goal: turn retrieved results into a clean LLM-ready context:
# - limits to top-N text chunks and top-N images
# - avoids duplication
# - produces a structured, readable “context” string for the prompt

# %%
@dataclass
class RetrievalItem:
    """
    One ranked retrieval result from either the text collection or image collection.
    """
    doc_id: str
    text: str
    metadata: Dict[str, Any]
    distance: float
    similarity: float
    fused_score: Optional[float]
    rank: int
    modality: str
    retrieval_latency_sec: float


# %%
@dataclass
class RetrievalOutput:
    """
    Structured retrieval response used by the main pipeline and evaluation code.

    Latency is tracked at four distinct stages so benchmarks can attribute
    wall-clock time accurately:
      - embed_time_sec   : dense vector encoding of the query
      - bm25_time_sec    : BM25 index lookup
      - fusion_time_sec  : RRF or weighted score merge
      - rerank_time_sec  : cross-encoder reranking (0 when reranker is off)

    The properties below rebuild the older nested-dictionary format so existing
    formatting and metric utilities can keep working while the pipeline gains a
    more explicit internal structure.
    """
    query: str
    text_items: List[RetrievalItem]
    image_items: List[RetrievalItem]
    text_latency_sec: float
    image_latency_sec: float
    total_latency_sec: float
    cosine_sim_text: float
    cosine_sim_image: float
    search_mode: str
    hybrid_stats: Dict[str, Any] = field(default_factory=dict)
    # Granular stage latencies — populated by RetrievalRag.retrieve()
    embed_time_sec: float = 0.0
    bm25_time_sec: float = 0.0
    fusion_time_sec: float = 0.0
    rerank_time_sec: float = 0.0

    @property
    def latency_breakdown(self) -> Dict[str, float]:
        """Return per-stage latency as a flat dict for logging and export."""
        return {
            "embed_time_sec": round(self.embed_time_sec, 4),
            "bm25_time_sec": round(self.bm25_time_sec, 4),
            "fusion_time_sec": round(self.fusion_time_sec, 4),
            "rerank_time_sec": round(self.rerank_time_sec, 4),
            "total_latency_sec": round(self.total_latency_sec, 4),
        }

    @property
    def text_results(self) -> Dict:
        return {
            "documents": [[item.text for item in self.text_items]],
            "metadatas": [[item.metadata for item in self.text_items]],
            "distances": [[item.distance for item in self.text_items]],
            "ids": [[item.doc_id for item in self.text_items]],
            "fused_scores": [[item.fused_score for item in self.text_items]],
            "retrieval_metrics": {
                "text_search_time": round(self.text_latency_sec, 4),
                "search_mode": self.search_mode,
                "hybrid_stats": self.hybrid_stats,
                "latency_breakdown": self.latency_breakdown,
            }
        }

    @property
    def image_results(self) -> Dict:
        return {
            "documents": [[item.text for item in self.image_items]],
            "metadatas": [[item.metadata for item in self.image_items]],
            "distances": [[item.distance for item in self.image_items]],
            "ids": [[item.doc_id for item in self.image_items]],
            "fused_scores": [[item.fused_score for item in self.image_items]],
            "retrieval_metrics": {
                "image_search_time": round(self.image_latency_sec, 4),
                "image_total_retrieval_time": round(self.image_latency_sec, 4),
                "search_mode": self.search_mode
            }
        }

    @property
    def retrieval_metrics(self) -> Dict:
        blended_similarity = 0.0
        if self.text_items and self.image_items:
            blended_similarity = (self.cosine_sim_text + self.cosine_sim_image) / 2.0
        elif self.text_items:
            blended_similarity = self.cosine_sim_text
        elif self.image_items:
            blended_similarity = self.cosine_sim_image

        return {
            # Total wall-clock time from the start of retrieve() to completion.
            "overall_retrieval_time": self.total_latency_sec,
            "cosine_similarity": blended_similarity,
            "cosine_similarity_text": self.cosine_sim_text,
            "cosine_similarity_image": self.cosine_sim_image,
            "text_metrics": {
                "text_search_time": round(self.text_latency_sec, 4),
                "text_total_retrieval_time": round(self.text_latency_sec, 4)
            },
            "image_metrics": {
                "image_search_time": round(self.image_latency_sec, 4),
                "image_total_retrieval_time": round(self.image_latency_sec, 4)
            },
            "search_mode": self.search_mode,
            # Granular stage breakdown — enables per-phase benchmarking.
            "latency_breakdown": self.latency_breakdown,
            # Hybrid fusion diagnostics forwarded to metrics and export paths.
            "hybrid_stats": self.hybrid_stats,
        }

    def to_legacy_dict(self) -> Dict:
        return {
            "query": self.query,
            "text_results": self.text_results,
            "image_results": self.image_results,
            "retrieval_metrics": self.retrieval_metrics
        }

    def get(self, key: str, default=None):
        return self.to_legacy_dict().get(key, default)

    def keys(self):
        return self.to_legacy_dict().keys()

    def items(self):
        return self.to_legacy_dict().items()

    def values(self):
        return self.to_legacy_dict().values()

    def __getitem__(self, key: str):
        return self.to_legacy_dict()[key]

    def __contains__(self, key: str) -> bool:
        return key in self.to_legacy_dict()

    def __iter__(self):
        return iter(self.to_legacy_dict())


# %%
class ContextFormatter:
    def __init__(self, max_text_chunks: int = cfg.max_text_chunks, max_images: int = cfg.max_images,
                 text_distance_threshold: float = cfg.text_distance_threshold,
                 image_distance_threshold: float = cfg.image_distance_threshold,
                 use_filtering: bool = cfg.use_filtering,
                 use_percentile_filtering: bool = cfg.use_percentile_filtering,
                 percentile_cutoff: int = cfg.percentile_cutoff,
                 max_context_tokens: int = cfg.max_context_tokens):

        self.max_text_chunks = max_text_chunks
        self.max_images = max_images
        self.text_distance_threshold = text_distance_threshold
        self.image_distance_threshold = image_distance_threshold
        self.use_filtering = use_filtering
        self.use_percentile_filtering = use_percentile_filtering
        self.percentile_cutoff = percentile_cutoff
        # Character budget derived from the token limit (approximate 4 chars/token).
        # Enforced in _format_text_context so the assembled context string never
        # exceeds the LLM's effective context window.
        self._max_context_chars: int = max_context_tokens * 4

    def _flatten_results(self, results: Dict) -> List[Dict]:
        if not results or not isinstance(results, dict):
            return []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        ids = results.get("ids", [[]])[0]
        fused_scores = results.get("fused_scores", [[]])[0]
        bm25_scores = results.get("bm25_scores", [[]])[0]

        min_length = min(len(documents), len(metadatas), len(distances), len(ids) if ids else len(documents))
        if min_length == 0:
            return []

        flattened = []
        for i in range(min_length):
            item = {
                "doc_id": ids[i] if ids else "",
                "text": documents[i],
                "metadata": deserialize_metadata(metadatas[i]),
                "distance": float(distances[i]),
                "rank": i + 1
            }
            if fused_scores and i < len(fused_scores):
                item["fused_score"] = fused_scores[i]
            if bm25_scores and i < len(bm25_scores):
                item["bm25_raw_score"] = bm25_scores[i]
            flattened.append(item)
        return flattened

    def _select_text_chunks(self, text_results: Dict) -> List[Dict]:
        items = self._flatten_results(text_results)
        if not items:
            return []

        search_mode = text_results.get("retrieval_metrics", {}).get("search_mode", "") if isinstance(text_results,
                                                                                                     dict) else ""
        preserve_ranked_order = search_mode.startswith("hybrid") or search_mode == "semantic_fallback"

        # Two-pass deduplication:
        #   Pass 1 — exact match via MD5 of the full text.
        #   Pass 2 — near-duplicate detection via 5-gram shingle fingerprints.
        #            Two chunks whose shingle sets overlap by ≥70 % are
        #            considered near-duplicates; only the higher-ranked one is kept.
        def _shingle_set(text: str, n: int = 5) -> set:
            words = text.lower().split()
            return {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 1))}

        seen_exact: set = set()
        seen_shingles: List[set] = []
        unique_items: List[Dict] = []

        for item in items:
            text = item["text"]
            exact_hash = hashlib.md5(text.encode()).hexdigest()
            if exact_hash in seen_exact:
                continue

            shingles = _shingle_set(text)
            is_near_dup = False
            for prev_shingles in seen_shingles:
                if not prev_shingles or not shingles:
                    continue
                overlap = len(shingles & prev_shingles) / len(shingles | prev_shingles)
                if overlap >= 0.70:
                    is_near_dup = True
                    break

            if is_near_dup:
                continue

            seen_exact.add(exact_hash)
            seen_shingles.append(shingles)
            unique_items.append(item)

        if not self.use_filtering:
            return unique_items[:self.max_text_chunks]

        distances = [i["distance"] for i in unique_items]
        if self.use_percentile_filtering and len(distances) > 2:
            threshold = np.percentile(distances, self.percentile_cutoff)
            filtered = [i for i in unique_items if i["distance"] <= threshold]
        else:
            filtered = [i for i in unique_items if i["distance"] <= self.text_distance_threshold]

        if not filtered:
            filtered = sorted(unique_items, key=lambda x: x["distance"])[:self.max_text_chunks]

        if not preserve_ranked_order:
            filtered.sort(key=lambda x: x["distance"])
        return filtered[:self.max_text_chunks]

    def _load_image(self, image_path: str) -> Optional[Image.Image]:
        if not image_path or not isinstance(image_path, str):
            return None
        if not os.path.exists(image_path):
            LOGGER.warning("Image path does not exist: %s", image_path)
            return None
        try:
            return Image.open(image_path).convert("RGB")
        except Exception as e:
            LOGGER.warning("Failed to load image %s: %s", image_path, e)
            return None

    def _format_text_context(self, text_items: List[Dict]) -> str:
        if not text_items or not isinstance(text_items, list):
            return ""
        lines = []
        accumulated_chars = 0
        for idx, item in enumerate(text_items, start=1):
            if not isinstance(item, dict):
                continue
            meta = item["metadata"] or {}
            source = meta.get("source", "unknown")
            page = meta.get("page_num", "N/A")
            text = item["text"].strip()
            if not text:
                continue
            entry = (
                f"[{idx}] {text}\n"
                f"(Source: {source}, page {page})"
            )
            # Enforce the configured token budget (approximated as chars / 4).
            # Chunks are already ranked by relevance; stopping here keeps the
            # most relevant material and prevents silent LLM context overflow.
            if accumulated_chars + len(entry) > self._max_context_chars:
                remaining = self._max_context_chars - accumulated_chars
                if remaining > 100:
                    lines.append(entry[:remaining])
                break
            lines.append(entry)
            accumulated_chars += len(entry) + 2  # +2 for the "\n\n" separator
        return "\n\n".join(lines)

    def _select_images(self, image_results: Dict) -> List[Dict]:
        items = self._flatten_results(image_results)
        if not items:
            return []
        search_mode = image_results.get("retrieval_metrics", {}).get("search_mode", "") if isinstance(image_results,
                                                                                                      dict) else ""
        preserve_ranked_order = search_mode.startswith("hybrid") or any("fused_score" in i for i in items)
        if self.use_filtering:
            items = [i for i in items if i["distance"] <= self.image_distance_threshold]
        if not preserve_ranked_order:
            items.sort(key=lambda x: x["distance"])
        return items[:self.max_images]

    def _format_image_context(self, image_items: List[Dict]) -> List[Dict]:
        if not image_items or not isinstance(image_items, list):
            return []
        formatted_images = []
        for item in image_items:
            if not isinstance(item, dict):
                continue
            meta = item.get("metadata") or {}
            image_path = meta.get("image_path")
            caption = meta.get("caption_text", "").strip()
            image = self._load_image(image_path)
            if image is None:
                continue
            formatted_images.append({"image": image, "caption": caption})
        return formatted_images

    def format(self, retrieval_output: Dict) -> Dict:
        if hasattr(retrieval_output, "to_legacy_dict"):
            retrieval_output = retrieval_output.to_legacy_dict()
        if not retrieval_output or not isinstance(retrieval_output, dict):
            raise ValueError("retrieval_output must be a non-empty dictionary.")
        query = retrieval_output.get("query", "")
        text_items = self._select_text_chunks(retrieval_output.get("text_results", {}))
        if not text_items:
            raw_items = self._flatten_results(retrieval_output.get("text_results", {}))
            text_items = raw_items[:self.max_text_chunks]
        image_items = self._select_images(retrieval_output.get("image_results", {}))
        return {
            "query": query,
            "text_context": self._format_text_context(text_items),
            "images": self._format_image_context(image_items)
        }


# %%
import statistics
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
from nltk.stem import PorterStemmer
from rank_bm25 import BM25Okapi
import re
import nltk

try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    pass
try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    pass

# Expanded fallback used only when NLTK's stopword corpus is unavailable.
# Matches the most commonly removed English function words to avoid inflating
# faithfulness and coverage scores by counting trivial function words as content.
FALLBACK_STOPWORDS = {
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
}


def safe_word_tokenize(text: str) -> List[str]:
    try:
        return word_tokenize(text)
    except LookupError:
        return re.findall(r"[A-Za-z0-9]+", text)


def get_stopword_set() -> set:
    try:
        return set(stopwords.words("english"))
    except LookupError:
        return set(FALLBACK_STOPWORDS)


# %%
class BM25Index:
    def __init__(self, documents: List[str], doc_ids: List[str], metadatas: List[Dict],
                 enable_preprocessing: bool = True):
        self.doc_ids = doc_ids
        self.documents = documents
        self.metadatas = metadatas
        self.stemmer = PorterStemmer()
        self.stop_words = get_stopword_set()
        self.domain_stopwords = {'et', 'al', 'using', 'used', 'shown', 'show', 'via', 'thus', 'therefore', 'hence'}
        self.stop_words.update(self.domain_stopwords)
        self.processed_docs = [self._preprocess(doc) for doc in documents] if enable_preprocessing else [
            safe_word_tokenize(doc.lower()) for doc in documents]
        self.bm25 = BM25Okapi(self.processed_docs)
        self.enable_preprocessing = enable_preprocessing
        self.corpus_tokens = set()
        for doc in self.processed_docs:
            self.corpus_tokens.update(doc)
        self.query_stats_history = []
        LOGGER.info("BM25 index built over %d documents with preprocessing=%s",
                    len(documents), enable_preprocessing)

    def _preprocess(self, text: str) -> List[str]:
        if not text:
            return []
        text = text.lower()
        text = re.sub(r'([a-z])(\d)', r'\1 \2', text)
        text = re.sub(r'(\d)([a-z])', r'\1 \2', text)
        tokens = safe_word_tokenize(text)
        processed = []
        for token in tokens:
            if token.isdigit() or (any(c.isdigit() for c in token) and any(c.isalpha() for c in token)):
                processed.append(token)
                continue
            if token in self.stop_words or len(token) < 3:
                continue
            processed.append(self.stemmer.stem(token))
        return processed

    def _expand_query(self, tokens: List[str]) -> List[str]:
        expanded = set(tokens)
        for token in tokens:
            variants = [token + 's', token + 'es', token + 'ed', token + 'ing',
                        token[:-1] if token.endswith('s') else None, token[:-2] if token.endswith('es') else None]
            for var in variants:
                if var and var in self.corpus_tokens:
                    expanded.add(var)
        return list(expanded)

    def query(self, query: str, k: int = 10, expand_query: bool = True) -> Dict:
        if not query or not query.strip():
            raise ValueError("Query must be a non-empty string.")
        tokenized_query = self._preprocess(query)
        if not tokenized_query:
            tokenized_query = safe_word_tokenize(query.lower())
            tokenized_query = [t for t in tokenized_query if t not in self.stop_words and len(t) > 2]
        if expand_query:
            tokenized_query = self._expand_query(tokenized_query)
            tokenized_query = list(set(tokenized_query))

        scores = self.bm25.get_scores(tokenized_query)
        ranked_indices = np.argsort(scores)[::-1]
        positive_indices = [i for i in ranked_indices if scores[i] > 0]
        if positive_indices:
            top_indices = positive_indices[:k]
            top_scores = [float(scores[i]) for i in top_indices]
        else:
            query_token_set = set(tokenized_query)
            overlap_scores = np.array([
                len(query_token_set.intersection(doc_tokens)) for doc_tokens in self.processed_docs
            ], dtype=float)
            overlap_ranked = np.argsort(overlap_scores)[::-1]
            top_indices = [i for i in overlap_ranked if overlap_scores[i] > 0][:k]
            top_scores = [float(overlap_scores[i]) for i in top_indices]
        stats = {
            "query_length_tokens": len(tokenized_query),
            "expanded_tokens": len(tokenized_query) if expand_query else 0,
            "max_score": max(top_scores) if top_scores else 0.0,
            "mean_score": statistics.mean(top_scores) if top_scores else 0.0,
            "min_score": min(top_scores) if top_scores else 0.0,
            "std_score": statistics.stdev(top_scores) if len(top_scores) > 1 else 0.0,
            "non_zero_docs": len([s for s in scores if s > 0]) if positive_indices else len(top_indices),
            "corpus_coverage": (
                len([s for s in scores if s > 0]) / len(scores)
                if positive_indices and len(scores) > 0
                else len(top_indices) / len(scores) if len(scores) > 0 else 0.0
            ),
            "used_overlap_fallback": not bool(positive_indices)
        }
        self.query_stats_history.append(stats)
        return {
            "documents": [[self.documents[i] for i in top_indices]],
            "metadatas": [[self.metadatas[i] for i in top_indices]],
            "ids": [[self.doc_ids[i] for i in top_indices]],
            "scores": top_scores,
            "query_tokens": tokenized_query,
            "bm25_stats": stats
        }


# %%
class BM25IndexBuilder:
    @staticmethod
    def from_vectorstore(vectorstore: 'VectorStore', enable_preprocessing: bool = True) -> 'BM25Index':
        if not vectorstore.collection:
            raise RuntimeError("VectorStore collection is not initialized.")
        all_data = vectorstore.collection.get(include=["documents", "metadatas"])
        documents = all_data.get("documents", [])
        metadatas = all_data.get("metadatas", [])
        doc_ids = all_data.get("ids", [])
        if not documents:
            raise RuntimeError("No documents found in VectorStore to build BM25 index.")
        LOGGER.info("Building BM25 index from %d documents.", len(documents))
        return BM25Index(
            documents=documents,
            doc_ids=doc_ids,
            metadatas=metadatas,
            enable_preprocessing=enable_preprocessing
        )

    @staticmethod
    def from_chunks(chunks: list, enable_preprocessing: bool = True) -> 'BM25Index':
        """
        Build a BM25 index directly from a list of Document objects (text chunks).
        Used in BM25-only mode to avoid building the dense vector DB at all.
        """
        from langchain_core.documents import Document as LCDocument
        documents, doc_ids, metadatas = [], [], []
        for chunk in chunks:
            if isinstance(chunk, LCDocument):
                content = (chunk.page_content or "").strip()
                if not content:
                    continue
                metadata = sanitize_metadata(chunk.metadata or {})
                chunk_id = str(chunk.metadata.get("chunk_id") or stable_hash({
                    "content": content,
                    "source": metadata.get("source"),
                    "page": metadata.get("page_num", ""),
                    "chunk_id": metadata.get("chunk_id", "")
                }))
                documents.append(content)
                doc_ids.append(chunk_id)
                metadatas.append(metadata)
        if not documents:
            raise RuntimeError("No valid chunks to build BM25 index from.")
        LOGGER.info("Building BM25 index from %d chunks (no vector DB).", len(documents))
        return BM25Index(
            documents=documents,
            doc_ids=doc_ids,
            metadatas=metadatas,
            enable_preprocessing=enable_preprocessing
        )

    @staticmethod
    def from_image_objects(image_objects: list, enable_preprocessing: bool = True) -> Optional['BM25Index']:
        """
        Build a BM25 image index directly from image object dicts.
        Used in BM25-only mode.
        """
        if not image_objects:
            LOGGER.info("Skipping image BM25 index: no image objects.")
            return None
        documents, doc_ids, metadatas = [], [], []
        for img in image_objects:
            caption = (img.get("caption_text") or img.get("image_id") or "").strip()
            if not caption:
                continue
            image_id = str(img.get("image_id") or stable_hash({
                "image_path": img.get("path", ""),
                "source": img.get("source", ""),
                "page_num": img.get("page_num", ""),
            }))
            metadata = sanitize_metadata({
                "image_id": img.get("image_id", ""),
                "source": img.get("source", ""),
                "page_num": img.get("page_num", ""),
                "image_path": img.get("path", ""),
                "caption_text": caption,
                "bbox": img.get("bbox") if img.get("bbox") is not None else "",
            })
            documents.append(caption)
            doc_ids.append(image_id)
            metadatas.append(metadata)
        if not documents:
            LOGGER.info("Skipping image BM25 index: no valid captions found.")
            return None
        LOGGER.info("Building image BM25 index from %d image objects (no vector DB).", len(documents))
        return BM25Index(
            documents=documents,
            doc_ids=doc_ids,
            metadatas=metadatas,
            enable_preprocessing=enable_preprocessing
        )


# %%
from typing import Tuple, Dict, List, Optional


# %%
class RetrievalRag:
    def __init__(self,
                 image_embedder: 'ImageEmbeddingModel',
                 text_embedder: 'TextEmbeddingModel',
                 image_vectordb: 'VectorStore',
                 text_vectordb: 'VectorStore',
                 use_reranker: bool = cfg.use_reranker,
                 bm25_weight: float = cfg.bm25_weight,
                 semantic_weight: float = cfg.semantic_weight,
                 formatter: Optional['ContextFormatter'] = None,
                 adaptive_weighting: bool = cfg.adaptive_weighting,
                 score_fusion: bool = cfg.use_weighted_fusion,
                 retrieval_mode: str = cfg.retrieval_mode,
                 # Optional pre-built BM25 indexes for BM25-only mode
                 # Provide these when retrieval_mode is 'bm25' to bypass vector store
                 # population. When omitted, indexes are constructed from the vector store.
                 _bm25_index: Optional['BM25Index'] = None,
                 _image_bm25_index: Optional['BM25Index'] = None):
        if retrieval_mode not in {"semantic", "bm25", "hybrid"}:
            raise ValueError("retrieval_mode must be one of 'semantic', 'bm25', or 'hybrid'.")
        if adaptive_weighting and retrieval_mode != "hybrid":
            raise ValueError("adaptive_weighting requires retrieval_mode='hybrid'.")
        if score_fusion and retrieval_mode != "hybrid":
            raise ValueError("score_fusion requires retrieval_mode='hybrid'.")

        # Flag-consolidation note:
        #   retrieval_mode="hybrid" is the authoritative switch; the flags
        #   adaptive_weighting and score_fusion are sub-mode controls that only
        #   take effect when retrieval_mode == "hybrid".  Both duplicate the
        #   information already carried by retrieval_mode for the common cases
        #   (semantic-only, BM25-only) and exist solely for per-feature toggling
        #   within hybrid mode.  Any caller that sets retrieval_mode != "hybrid"
        #   should leave both flags False to avoid a misleading config state.
        if retrieval_mode != "hybrid" and (adaptive_weighting or score_fusion):
            LOGGER.warning(
                "retrieval_mode='%s' but adaptive_weighting=%s or score_fusion=%s — "
                "those flags have no effect outside hybrid mode.",
                retrieval_mode, adaptive_weighting, score_fusion,
            )
        self.image_embedder = image_embedder
        self.text_embedder = text_embedder
        self.image_vectordb = image_vectordb
        self.text_vectordb = text_vectordb
        self.reranker = Reranker() if use_reranker else None
        self.formatter = formatter or ContextFormatter()
        self.retrieval_mode = retrieval_mode
        self.use_hybrid = retrieval_mode == "hybrid"
        self.adaptive_weighting = adaptive_weighting
        self.score_fusion = score_fusion
        if not (0.0 <= bm25_weight <= 1.0) or not (0.0 <= semantic_weight <= 1.0):
            raise ValueError("BM25 and semantic weights must be between 0.0 and 1.0.")
        if abs((bm25_weight + semantic_weight) - 1.0) > 1e-6:
            raise ValueError("BM25 weight and semantic weight must sum to 1.0.")
        self.bm25_weight = bm25_weight
        self.semantic_weight = semantic_weight
        self.hybrid_metrics_history = []
        if self.retrieval_mode in ("bm25", "hybrid"):
            # ── Text BM25 index ───────────────────────────────────────────────
            if _bm25_index is not None:
                # Pre-built index injected by caller (BM25-only / bypass mode).
                # Avoids calling from_vectorstore on an empty DB.
                self.bm25_index = _bm25_index
                LOGGER.info("BM25 text index injected externally (%d docs).",
                            len(_bm25_index.documents))
            else:
                # Normal path: build from populated vector DB.
                self.bm25_index = BM25IndexBuilder.from_vectorstore(
                    text_vectordb, enable_preprocessing=True)

            # ── Image BM25 index ─────────────────────────────────────────────
            if _image_bm25_index is not None:
                self.image_bm25_index = _image_bm25_index
                LOGGER.info("BM25 image index injected externally (%d docs).",
                            len(_image_bm25_index.documents))
            else:
                self.image_bm25_index = self._build_optional_bm25_index(image_vectordb, "image")

            if self.retrieval_mode == "hybrid":
                LOGGER.info("Hybrid search enabled: %s with adaptive=%s",
                            'Weighted Sum' if score_fusion else 'RRF', adaptive_weighting)
            else:
                LOGGER.info("BM25-only retrieval enabled.")
        else:
            self.bm25_index = None
            self.image_bm25_index = None
            LOGGER.info("Semantic-only retrieval enabled.")

    @staticmethod
    def _build_optional_bm25_index(vectorstore: 'VectorStore', label: str) -> Optional['BM25Index']:
        try:
            if not vectorstore.collection or vectorstore.collection.count() <= 0:
                LOGGER.info("Skipping %s BM25 index: collection is empty.", label)
                return None
            return BM25IndexBuilder.from_vectorstore(vectorstore, enable_preprocessing=True)
        except Exception as exc:
            LOGGER.warning("Skipping %s BM25 index: %s", label, exc)
            return None

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    def _classify_query_type(self, query: str) -> str:
        """
        Classify the query as 'keyword', 'semantic', or 'balanced' to guide
        per-query adaptive BM25 / semantic weight selection.

        Checks are evaluated in strict priority order so that high-signal
        patterns (numeric entities, short lookups) are never overridden by
        lower-priority heuristics:

          1. Numeric-entity queries  → 'keyword'
             BM25 excels at exact-value matching (years, distances,
             measurements).  Even a single number at a word-ratio ≥ 0.10
             indicates the user wants a precise factual value.

          2. Short queries           → 'keyword'
             Six-word-or-fewer queries are typically exact lookups rather
             than conceptual searches.

          3. Explanatory queries     → 'semantic'
             Dense retrieval handles intent and paraphrase better than
             keyword matching for "why", "how", "explain", etc.
             Note: 'what' is intentionally excluded — it appears in both
             factual lookups ("What is the mirror diameter?") and conceptual
             questions ("What causes star formation?") and is therefore too
             ambiguous to be a reliable semantic indicator on its own.

          4. Long queries            → 'balanced'
             Without explicit explanatory markers, length alone suggests
             multi-aspect intent that benefits from both retrievers.

          5. Default                 → 'balanced'
        """
        query_lower = query.lower().strip()
        words = query_lower.split()
        word_count = len(words)
        num_count = len(re.findall(r'\d+', query_lower))
        num_ratio = num_count / max(word_count, 1)

        # Numeric-entity check takes the highest priority: a query mentioning
        # specific values strongly benefits from BM25's exact token matching.
        # Threshold lowered from 0.18 to 0.10 so that a single number in a
        # ten-word question (ratio = 0.10) is still flagged as keyword-oriented.
        if num_count >= 1 and num_ratio >= 0.10:
            return 'keyword'

        # Short queries are typical of targeted lookups rather than open-ended
        # exploration.
        if word_count <= 6:
            return 'keyword'

        # Explanatory and comparative queries benefit most from semantic
        # retrieval, which handles intent and paraphrase better than BM25.
        explanatory_markers = [
            'why', 'how', 'explain', 'describe', 'compare', 'analyze', 'summarize'
        ]
        if any(marker in query_lower for marker in explanatory_markers):
            return 'semantic'

        # Long queries without explicit explanatory markers carry multiple
        # aspects; balanced retrieval handles them best.
        if word_count >= 14:
            return 'balanced'

        return 'balanced'

    def _normalize_scores(self, scores: List[float], method: str = 'minmax') -> List[float]:
        if not scores:
            return []
        scores = np.array(scores)
        if method == 'minmax':
            min_score, max_score = np.min(scores), np.max(scores)
            if max_score - min_score < 1e-6:
                return [1.0 / len(scores)] * len(scores)
            normalized = (scores - min_score) / (max_score - min_score)
        elif method == 'zscore':
            mean, std = np.mean(scores), np.std(scores)
            if std < 1e-6:
                return [0.5] * len(scores)
            normalized = (scores - mean) / std
            normalized = (normalized - np.min(normalized)) / (np.max(normalized) - np.min(normalized) + 1e-6)
        else:
            exp_scores = np.exp(scores - np.max(scores))
            normalized = exp_scores / np.sum(exp_scores)
        return normalized.tolist()

    @staticmethod
    def _bm25_distances(scores: List[float]) -> List[List[float]]:
        """Convert raw BM25 scores to pseudo-distances in [0, 1].

        The naive ``score / max_score`` normalization guarantees the top result
        always achieves distance = 0 (similarity = 1.0) regardless of its
        absolute retrieval quality.  This inflates M4 BM25 Relevance and makes
        it appear high even when the actual BM25 signal is weak.

        A proportional ceiling of 1.20 × max_score is used as the denominator
        so the best-matching document maps to at most 0.833 similarity.  This
        preserves relative ranking while reporting an honest absolute estimate
        that is commensurable with cosine distance values from the vector DB.
        """
        if not scores:
            return [[]]
        max_score = max(scores)
        if max_score <= 0:
            return [[1.0 for _ in scores]]
        effective_ceiling = max_score * 1.20
        return [[max(0.0, 1.0 - (float(score) / effective_ceiling)) for score in scores]]

    def _calculate_overlap(self, list1: List[str], list2: List[str]) -> Dict:
        set1, set2 = set(list1), set(list2)
        intersection = set1 & set2
        union = set1 | set2
        return {
            "intersection_size": len(intersection),
            "union_size": len(union),
            "jaccard_similarity": len(intersection) / len(union) if union else 0.0,
            "overlap_percentage": (len(intersection) / len(set1) * 100) if set1 else 0.0,
            "intersection_ids": list(intersection)
        }

    def _fuse_scores(self, bm25_results: Dict, semantic_results: Dict) -> Tuple[Dict, Dict]:
        """
        Combine BM25 and semantic results using normalized weighted sum.

        Both score distributions are min-max normalized to [0,1] before
        applying the configured bm25_weight and semantic_weight. This
        prevents scale mismatch between lexical scores and cosine distances.
        """
        bm25_docs = bm25_results.get("documents", [[]])[0]
        bm25_ids = bm25_results.get("ids", [[]])[0]
        bm25_metas = bm25_results.get("metadatas", [[]])[0]
        bm25_scores = bm25_results.get("scores", [])
        sem_docs = semantic_results.get("documents", [[]])[0]
        sem_ids = semantic_results.get("ids", [[]])[0]
        sem_metas = semantic_results.get("metadatas", [[]])[0]
        sem_distances = semantic_results.get("distances", [[]])[0]
        sem_scores = [1.0 - float(d) for d in sem_distances]
        norm_bm25 = self._normalize_scores(bm25_scores, method='minmax')
        norm_semantic = self._normalize_scores(sem_scores, method='minmax')
        combined = {}
        for idx, doc_id in enumerate(bm25_ids):
            combined[doc_id] = {
                "text": bm25_docs[idx], "metadata": bm25_metas[idx], "bm25_norm": norm_bm25[idx],
                "semantic_norm": 0.0, "bm25_raw": bm25_scores[idx], "semantic_dist": 1.0, "semantic_sim": 0.0
            }
        for idx, doc_id in enumerate(sem_ids):
            sem_score = norm_semantic[idx]
            if doc_id in combined:
                combined[doc_id]["semantic_norm"] = sem_score
                combined[doc_id]["semantic_dist"] = float(sem_distances[idx])
                combined[doc_id]["semantic_sim"] = sem_scores[idx]
            else:
                combined[doc_id] = {
                    "text": sem_docs[idx], "metadata": sem_metas[idx], "bm25_norm": 0.0,
                    "semantic_norm": sem_score, "bm25_raw": 0.0, "semantic_dist": float(sem_distances[idx]),
                    "semantic_sim": sem_scores[idx]
                }
        for doc_id in combined:
            entry = combined[doc_id]
            entry["fused_score"] = (entry["bm25_norm"] * self.bm25_weight) + (
                    entry["semantic_norm"] * self.semantic_weight)
        sorted_docs = sorted(combined.items(), key=lambda x: x[1]["fused_score"], reverse=True)
        seen_hashes = set()
        unique_sorted = []
        for doc_id, entry in sorted_docs:
            text_hash = hashlib.md5(entry["text"].encode()).hexdigest()[:16]
            if text_hash not in seen_hashes:
                seen_hashes.add(text_hash)
                unique_sorted.append((doc_id, entry))
        sorted_docs = unique_sorted[:20]
        fusion_stats = {
            "bm25_contribution_mean": np.mean(
                [entry["bm25_norm"] * self.bm25_weight for _, entry in sorted_docs]) if sorted_docs else 0.0,
            "semantic_contribution_mean": np.mean(
                [entry["semantic_norm"] * self.semantic_weight for _, entry in sorted_docs]) if sorted_docs else 0.0,
            "bm25_only_docs": len([d for _, d in sorted_docs if d["semantic_norm"] == 0]),
            "semantic_only_docs": len([d for _, d in sorted_docs if d["bm25_norm"] == 0]),
            "both_signals_docs": len([d for _, d in sorted_docs if d["bm25_norm"] > 0 and d["semantic_norm"] > 0])
        }
        return {
            "documents": [[entry["text"] for _, entry in sorted_docs]],
            "metadatas": [[entry["metadata"] for _, entry in sorted_docs]],
            "distances": [[entry["semantic_dist"] for _, entry in sorted_docs]],
            "ids": [[doc_id for doc_id, _ in sorted_docs]],
            "fused_scores": [[entry["fused_score"] for _, entry in sorted_docs]],
            "bm25_scores": [[entry["bm25_raw"] for _, entry in sorted_docs]]
        }, fusion_stats

    def _rrf_fusion(
            self,
            bm25_results: Dict,
            semantic_results: Dict,
            k_rrf: int = cfg.rrf_k_constant,
            bm25_weight: Optional[float] = None,
            semantic_weight: Optional[float] = None,
    ) -> Tuple[Dict, Dict]:
        """
        Combine BM25 and semantic results using weighted Reciprocal Rank Fusion.

        RRF operates on rank positions rather than raw scores, which makes it
        robust to scale differences between lexical and vector retrievers.
        Each document accumulates contributions from both lists; the contribution
        from list l at rank r is:

            weight_l * (1 / (k_rrf + r + 1))

        When weights are equal (0.5 / 0.5) this reduces to the standard,
        unweighted RRF formulation.  Passing per-query adaptive weights allows
        the fusion to emphasise whichever retriever produced stronger signal for
        that specific query.

        Parameters
        ----------
        bm25_weight :
            Weight applied to every BM25 rank contribution.  Defaults to the
            instance's configured self.bm25_weight so callers that do not use
            adaptive weighting need not pass this argument.
        semantic_weight :
            Weight applied to every semantic rank contribution.  Defaults to
            self.semantic_weight for the same reason.
        """
        # Resolve weights: caller-supplied values (from adaptive logic) take
        # precedence; fall back to the instance's configured static values.
        effective_bm25_w = float(bm25_weight if bm25_weight is not None else self.bm25_weight)
        effective_sem_w = float(semantic_weight if semantic_weight is not None else self.semantic_weight)

        scores = {}
        bm25_docs = bm25_results.get("documents", [[]])[0]
        bm25_ids = bm25_results.get("ids", [[]])[0]
        bm25_metas = bm25_results.get("metadatas", [[]])[0]
        bm25_scores = bm25_results.get("scores", [])
        bm25_max = max(bm25_scores) if bm25_scores else 0.0
        bm25_effective_ceiling = bm25_max * 1.20 if bm25_max > 0 else 1.0

        def _bm25_dist(raw_score: float) -> float:
            if bm25_max <= 0:
                return 1.0
            return max(0.0, 1.0 - (raw_score / bm25_effective_ceiling))

        # Accumulate weighted BM25 rank contributions.
        for rank, doc_id in enumerate(bm25_ids):
            raw = bm25_scores[rank] if rank < len(bm25_scores) else 0.0
            rrf_score = effective_bm25_w * (1.0 / (k_rrf + rank + 1))
            scores[doc_id] = {
                "rrf_score": rrf_score, "text": bm25_docs[rank],
                "metadata": bm25_metas[rank], "distance": _bm25_dist(raw),
                "bm25_raw": raw, "source": "bm25",
            }

        # Accumulate weighted semantic rank contributions.
        sem_docs = semantic_results.get("documents", [[]])[0]
        sem_ids = semantic_results.get("ids", [[]])[0]
        sem_metas = semantic_results.get("metadatas", [[]])[0]
        sem_distances = semantic_results.get("distances", [[]])[0]
        for rank, doc_id in enumerate(sem_ids):
            rrf_score = effective_sem_w * (1.0 / (k_rrf + rank + 1))
            if doc_id in scores:
                scores[doc_id]["rrf_score"] += rrf_score
                scores[doc_id]["distance"] = float(sem_distances[rank])
                scores[doc_id]["source"] = "both"
            else:
                scores[doc_id] = {
                    "rrf_score": rrf_score, "text": sem_docs[rank],
                    "metadata": sem_metas[rank], "distance": float(sem_distances[rank]),
                    "bm25_raw": 0.0, "source": "semantic",
                }

        sorted_docs = sorted(scores.items(), key=lambda x: x[1]["rrf_score"], reverse=True)
        stats = {
            "bm25_only_docs": len([s for s in scores.values() if s["source"] == "bm25"]),
            "semantic_only_docs": len([s for s in scores.values() if s["source"] == "semantic"]),
            "both_signals_docs": len([s for s in scores.values() if s["source"] == "both"]),
        }
        return {
            "documents": [[entry["text"] for _, entry in sorted_docs]],
            "metadatas": [[entry["metadata"] for _, entry in sorted_docs]],
            "distances": [[entry["distance"] for _, entry in sorted_docs]],
            "ids": [[doc_id for doc_id, _ in sorted_docs]],
            "fused_scores": [[entry["rrf_score"] for _, entry in sorted_docs]],
            "bm25_scores": [[entry["bm25_raw"] for _, entry in sorted_docs]],
        }, stats

    def _get_adaptive_weights(self, query: str, bm25_results: Dict) -> Tuple[float, float, Dict[str, Any]]:
        if not self.adaptive_weighting:
            return self.bm25_weight, self.semantic_weight, {"query_type": self._classify_query_type(query),
                                                            "bm25_signal_strength": 0.0, "lexical_query_signal": 0.0,
                                                            "fallback_to_semantic": False, "fallback_reason": "",
                                                            "weight_adjusted": False}
        query_type = self._classify_query_type(query)
        bm25_stats = bm25_results.get("bm25_stats", {})
        max_score = float(bm25_stats.get("max_score", 0.0))
        std_score = float(bm25_stats.get("std_score", 0.0))
        coverage = float(bm25_stats.get("corpus_coverage", 0.0))
        non_zero_docs = int(bm25_stats.get("non_zero_docs", 0))
        query_lower = query.lower().strip()
        words = query_lower.split()
        word_count = len(words)
        num_ratio = len(re.findall(r'\d+', query_lower)) / max(word_count, 1)
        explanatory_signal = 1.0 if any(
            token in query_lower for token in ['why', 'how', 'explain', 'describe', 'compare', 'analyze']) else 0.0
        short_query_signal = max(0.0, 1.0 - min(word_count / 12.0, 1.0))
        numeric_signal = min(num_ratio / 0.25, 1.0)
        lexical_query_signal = max(short_query_signal, numeric_signal)
        max_signal = min(max_score / max(cfg.bm25_strong_max_score_threshold * 4.0, 1.0), 1.0)
        std_signal = min(std_score / max(cfg.bm25_strong_std_threshold * 3.0, 1.0), 1.0)
        specificity_signal = 1.0 - min(coverage, 1.0)
        bm25_signal_strength = (
                (cfg.bm25_signal_max_weight * max_signal) +
                (cfg.bm25_signal_std_weight * std_signal) +
                (cfg.bm25_signal_spec_weight * specificity_signal) +
                (cfg.bm25_signal_lex_weight * lexical_query_signal)
        )
        if non_zero_docs == 0 or max_score <= 0.0:
            return 0.0, 1.0, {"query_type": query_type, "bm25_signal_strength": round(bm25_signal_strength, 4),
                              "lexical_query_signal": round(lexical_query_signal, 4), "fallback_to_semantic": True,
                              "fallback_reason": "no_positive_bm25_scores", "weight_adjusted": True}
        if query_type == "keyword":
            preset = cfg.adaptive_weights_keyword
        elif query_type == "semantic":
            preset = cfg.adaptive_weights_semantic
        elif (
                max_score >= cfg.bm25_strong_max_score_threshold or
                std_score >= cfg.bm25_strong_std_threshold or
                bm25_signal_strength >= 0.55
        ):
            preset = cfg.adaptive_weights_balanced_strong_bm25
        elif max_score <= cfg.bm25_weak_max_score_threshold or bm25_signal_strength <= 0.35:
            preset = cfg.adaptive_weights_balanced_weak_bm25
        else:
            preset = (self.bm25_weight, self.semantic_weight)

        bm25_weight = round(float(preset[0]), 3)
        semantic_weight = round(float(preset[1]), 3)
        if abs((bm25_weight + semantic_weight) - 1.0) > 1e-6:
            total = max(bm25_weight + semantic_weight, 1e-6)
            bm25_weight = round(bm25_weight / total, 3)
            semantic_weight = round(1.0 - bm25_weight, 3)
        return bm25_weight, semantic_weight, {"query_type": query_type,
                                              "bm25_signal_strength": round(bm25_signal_strength, 4),
                                              "lexical_query_signal": round(lexical_query_signal, 4),
                                              "fallback_to_semantic": False, "fallback_reason": "",
                                              "weight_adjusted": abs(bm25_weight - self.bm25_weight) > 1e-6}

    def retrieve_text(self, query: str, k: int = cfg.text_k) -> Dict:
        if not query or not query.strip():
            raise ValueError("Query must be a non-empty string.")
        if not isinstance(k, int) or k <= 0:
            raise ValueError(f"k must be a positive integer, got {k}.")
        start_total = perf_counter()
        embed_time = 0.0
        semantic_results = None
        bm25_results = None
        hybrid_stats = {}
        search_mode = self.retrieval_mode

        start_search = perf_counter()
        bm25_elapsed = 0.0
        fusion_elapsed = 0.0

        if self.retrieval_mode in ("semantic", "hybrid"):
            start_embed = perf_counter()
            query_embedding = self.text_embedder.embed_query(query)
            embed_time = perf_counter() - start_embed
            semantic_results = self.text_vectordb.query(query_embedding=query_embedding, k=k * 2)

        if self.retrieval_mode in ("bm25", "hybrid") and self.bm25_index:
            _bm25_start = perf_counter()
            bm25_results = self.bm25_index.query(query=query, k=k * 2, expand_query=True)
            bm25_elapsed = perf_counter() - _bm25_start

        if self.retrieval_mode == "bm25":
            results = bm25_results or {"documents": [[]], "metadatas": [[]], "ids": [[]], "scores": []}
            scores = results.get("scores", [])
            results["distances"] = self._bm25_distances(scores)
            results["bm25_scores"] = [scores]
            search_mode = "bm25_only"

        elif self.retrieval_mode == "semantic":
            results = semantic_results or {"documents": [[]], "metadatas": [[]], "ids": [[]], "distances": [[]]}
            search_mode = "semantic_only"

        else:  # hybrid
            if not bm25_results or not semantic_results:
                results = semantic_results or bm25_results
                search_mode = "hybrid_fallback"
            else:
                adaptive_bm25_w, adaptive_sem_w, adaptive_info = self._get_adaptive_weights(query, bm25_results)
                bm25_ids = bm25_results.get("ids", [[]])[0]
                overlap_stats = self._calculate_overlap(bm25_ids,
                                                        semantic_results.get("ids", [[]])[0]) if bm25_ids else {
                    "intersection_size": 0, "union_size": 0, "jaccard_similarity": 0.0, "overlap_percentage": 0.0,
                    "intersection_ids": []}
                if adaptive_info.get("fallback_to_semantic", False):
                    results = semantic_results
                    fusion_stats = {"bm25_contribution_mean": 0.0, "semantic_contribution_mean": 1.0,
                                    "bm25_only_docs": 0,
                                    "semantic_only_docs": len(semantic_results.get("ids", [[]])[0]),
                                    "both_signals_docs": 0}
                    search_mode = "semantic_fallback"
                else:
                    _fusion_start = perf_counter()
                    if self.score_fusion:
                        # _fuse_scores reads self.bm25_weight / self.semantic_weight
                        # internally, so the instance attributes must be temporarily
                        # set to the per-query adaptive values before calling it.
                        orig_bm25_w, orig_sem_w = self.bm25_weight, self.semantic_weight
                        self.bm25_weight, self.semantic_weight = adaptive_bm25_w, adaptive_sem_w
                        try:
                            results, fusion_stats = self._fuse_scores(bm25_results, semantic_results)
                        finally:
                            self.bm25_weight, self.semantic_weight = orig_bm25_w, orig_sem_w
                    else:
                        # _rrf_fusion accepts weights as explicit parameters, so no
                        # instance-attribute swap is required.  Passing the adaptive
                        # weights directly ensures they genuinely influence the rank
                        # contributions from each retriever rather than being tracked
                        # but silently ignored.
                        results, fusion_stats = self._rrf_fusion(
                            bm25_results, semantic_results,
                            k_rrf=cfg.rrf_k_constant,
                            bm25_weight=adaptive_bm25_w,
                            semantic_weight=adaptive_sem_w,
                        )
                    fusion_elapsed = perf_counter() - _fusion_start
                    search_mode = "hybrid_score_fusion" if self.score_fusion else "hybrid_rrf"
                hybrid_stats = {
                    "query_type": adaptive_info["query_type"], "bm25_weight_used": adaptive_bm25_w,
                    "semantic_weight_used": adaptive_sem_w,
                    "bm25_max_score": bm25_results.get("bm25_stats", {}).get("max_score", 0),
                    "bm25_mean_score": bm25_results.get("bm25_stats", {}).get("mean_score", 0),
                    "bm25_std_score": bm25_results.get("bm25_stats", {}).get("std_score", 0),
                    "bm25_corpus_coverage": bm25_results.get("bm25_stats", {}).get("corpus_coverage", 0),
                    "bm25_used_overlap_fallback": bm25_results.get("bm25_stats", {}).get("used_overlap_fallback",
                                                                                         False),
                    "fusion_type": cfg.fusion_type,
                    "bm25_signal_strength": adaptive_info.get("bm25_signal_strength", 0.0),
                    "lexical_query_signal": adaptive_info.get("lexical_query_signal", 0.0),
                    "weight_adjusted": adaptive_info.get("weight_adjusted", False),
                    "fallback_to_semantic": adaptive_info.get("fallback_to_semantic", False),
                    "fallback_reason": adaptive_info.get("fallback_reason", ""),
                    "overlap_jaccard": overlap_stats["jaccard_similarity"],
                    "overlap_percentage": overlap_stats["overlap_percentage"], "fusion_stats": fusion_stats
                }

        for key in ["documents", "metadatas", "distances", "ids", "fused_scores", "bm25_scores"]:
            if key in results and results[key]:
                results[key][0] = results[key][0][:k]
        search_time = perf_counter() - start_search
        total_time = perf_counter() - start_total
        results["retrieval_metrics"] = {
            "text_embed_time": round(embed_time, 4),
            "text_search_time": round(search_time, 4),
            "text_total_retrieval_time": round(total_time, 4),
            # Granular stage splits forwarded to RetrievalOutput.latency_breakdown.
            "bm25_time": round(bm25_elapsed, 4),
            "fusion_time": round(fusion_elapsed, 4),
            "search_mode": search_mode,
            "hybrid_stats": hybrid_stats,
        }
        return results

    def retrieve_images(self, query: str, k: int = cfg.image_k) -> Dict:
        if not query or not query.strip():
            raise ValueError("Query must be a non-empty string.")
        if not isinstance(k, int) or k <= 0:
            raise ValueError(f"k must be a positive integer, got {k}.")
        start_total = perf_counter()
        embed_time = 0.0
        bm25_elapsed = 0.0
        fusion_elapsed = 0.0
        semantic_results = None
        bm25_results = None
        search_mode = self.retrieval_mode

        start_query = perf_counter()
        if self.retrieval_mode in ("semantic", "hybrid"):
            start_embed = perf_counter()
            query_embedding = self.image_embedder.embed_query(query)
            embed_time = perf_counter() - start_embed
            semantic_results = self.image_vectordb.query(query_embedding=query_embedding, k=k * 2)

        if self.retrieval_mode in ("bm25", "hybrid") and self.image_bm25_index:
            _bm25_start = perf_counter()
            bm25_results = self.image_bm25_index.query(query=query, k=k * 2, expand_query=True)
            bm25_elapsed = perf_counter() - _bm25_start

        if self.retrieval_mode == "bm25":
            results = bm25_results or {"documents": [[]], "metadatas": [[]], "ids": [[]], "scores": []}
            scores = results.get("scores", [])
            results["distances"] = self._bm25_distances(scores)
            results["bm25_scores"] = [scores]
            search_mode = "bm25_only"
        elif self.retrieval_mode == "semantic" or not bm25_results:
            results = semantic_results or {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}
            search_mode = "semantic_only" if self.retrieval_mode == "semantic" else "semantic_fallback"
        elif not semantic_results:
            results = bm25_results
            scores = results.get("scores", [])
            results["distances"] = self._bm25_distances(scores)
            results["bm25_scores"] = [scores]
            search_mode = "bm25_fallback"
        else:
            # Hybrid image fusion respects the same use_weighted_fusion flag as text
            # retrieval so the two modalities remain configuration-consistent.
            adaptive_bm25_w, adaptive_sem_w, _ = self._get_adaptive_weights(query, bm25_results)
            _fusion_start = perf_counter()
            if self.score_fusion:
                # _fuse_scores reads instance weights, so set them temporarily.
                orig_bm25_w, orig_sem_w = self.bm25_weight, self.semantic_weight
                self.bm25_weight, self.semantic_weight = adaptive_bm25_w, adaptive_sem_w
                try:
                    results, _ = self._fuse_scores(bm25_results, semantic_results)
                finally:
                    self.bm25_weight, self.semantic_weight = orig_bm25_w, orig_sem_w
            else:
                # _rrf_fusion accepts weights explicitly; no instance-attribute
                # swap is needed or appropriate here.
                results, _ = self._rrf_fusion(
                    bm25_results, semantic_results,
                    k_rrf=cfg.rrf_k_constant,
                    bm25_weight=adaptive_bm25_w,
                    semantic_weight=adaptive_sem_w,
                )
            fusion_elapsed = perf_counter() - _fusion_start
            search_mode = "hybrid_score_fusion" if self.score_fusion else "hybrid_rrf"

        for key in ["documents", "metadatas", "distances", "ids", "fused_scores", "bm25_scores"]:
            if key in results and results[key]:
                results[key][0] = results[key][0][:k]
        query_time = perf_counter() - start_query
        total_time = perf_counter() - start_total
        results["retrieval_metrics"] = {
            "image_embed_time": round(embed_time, 4),
            "image_search_time": round(query_time, 4),
            "image_total_retrieval_time": round(total_time, 4),
            # Granular stage splits forwarded to RetrievalOutput.latency_breakdown.
            "bm25_time": round(bm25_elapsed, 4),
            "fusion_time": round(fusion_elapsed, 4),
            "search_mode": search_mode,
        }
        return results

    def retrieve(self, query: str, text_k: int = cfg.text_k, image_k: int = cfg.image_k,
                 rerank_k: int = cfg.rerank_k) -> RetrievalOutput:
        if not query or not query.strip():
            raise ValueError("Query must be non-empty string.")
        if not isinstance(text_k, int) or text_k <= 0:
            raise ValueError(f"text_k must be a positive integer, got {text_k}.")
        if not isinstance(image_k, int) or image_k <= 0:
            raise ValueError(f"image_k must be a positive integer, got {image_k}.")

        start_total = perf_counter()

        text_start = perf_counter()
        raw_text_results = self.retrieve_text(query=query, k=text_k)
        text_latency = perf_counter() - text_start

        image_start = perf_counter()
        raw_image_results = self.retrieve_images(query=query, k=image_k)
        image_latency = perf_counter() - image_start

        # Pull per-stage timing from the sub-retrieval metrics dictionaries.
        # retrieve_text and retrieve_images each emit a retrieval_metrics block
        # containing their internal embed and search splits.
        _text_rm = raw_text_results.get("retrieval_metrics", {})
        _image_rm = raw_image_results.get("retrieval_metrics", {})

        embed_time = (_text_rm.get("text_embed_time", 0.0)
                      + _image_rm.get("image_embed_time", 0.0))
        bm25_time = (_text_rm.get("bm25_time", 0.0)
                     + _image_rm.get("bm25_time", 0.0))
        fusion_time = (_text_rm.get("fusion_time", 0.0)
                       + _image_rm.get("fusion_time", 0.0))

        text_items = self.formatter._flatten_results(raw_text_results)
        image_items = self.formatter._flatten_results(raw_image_results)

        # ── Cross-modal boost ──────────────────────────────────────────────
        if cfg.use_cross_modal_boost:
            retrieved_image_refs = set()
            for item in image_items:
                meta = item["metadata"]
                image_id = meta.get("image_id")
                if image_id:
                    retrieved_image_refs.add(str(image_id))
                image_path = meta.get("image_path", "")
                if image_path:
                    normalized_path = image_path.replace("\\", "/")
                    retrieved_image_refs.add(normalized_path.split("/")[-1])
                    match = re.search(r"/([^/]+)/page_(\d+)_img_(\d+)\.png$", normalized_path)
                    if match:
                        retrieved_image_refs.add(f"{match.group(1)}_p{int(match.group(2))}_i{int(match.group(3))}")

            # Build reverse index: image IDs referenced by any retrieved text chunk.
            retrieved_text_image_refs: set = set()
            for item in text_items:
                for rid in item["metadata"].get("related_image_ids", []):
                    related_str = str(rid)
                    retrieved_text_image_refs.add(related_str)
                    retrieved_text_image_refs.add(
                        related_str.replace("\\", "/").split("/")[-1]
                    )

            for item in text_items:
                related_ids = item["metadata"].get("related_image_ids", [])
                related_refs = set()
                for related_id in related_ids:
                    related_str = str(related_id)
                    related_refs.add(related_str)
                    related_refs.add(related_str.replace("\\", "/").split("/")[-1])
                overlap = related_refs & retrieved_image_refs
                if overlap:
                    boost = min(cfg.cross_modal_max_boost, len(overlap) * cfg.cross_modal_boost_per_overlap)
                    item["distance"] = item["distance"] * (1.0 - boost)
                    item["cross_modal_boost"] = round(boost, 3)

            # Symmetric boost: images co-occurring with retrieved text chunks.
            for item in image_items:
                meta = item["metadata"]
                img_id = str(meta.get("image_id", ""))
                img_filename = img_id.replace("\\", "/").split("/")[-1]
                if img_id in retrieved_text_image_refs or img_filename in retrieved_text_image_refs:
                    boost = min(cfg.cross_modal_max_boost, cfg.cross_modal_boost_per_overlap)
                    item["distance"] = item["distance"] * (1.0 - boost)
                    item["cross_modal_boost"] = round(boost, 3)

        # ── Issue 10: related_image_ids ranking signal ─────────────────────
        # Beyond the distance boost above, promote text chunks that explicitly
        # reference one or more of the retrieved image IDs by improving their
        # effective fused_score when a fused_score is already present.  This
        # turns the spatial co-occurrence relationship into a visible ranking
        # signal rather than a silent distance tweak only.
        if cfg.use_cross_modal_boost:
            for item in text_items:
                if item.get("fused_score") is None:
                    continue
                related_refs = set()
                for rid in item["metadata"].get("related_image_ids", []):
                    related_refs.add(str(rid))
                    related_refs.add(str(rid).replace("\\", "/").split("/")[-1])
                match_count = len(related_refs & retrieved_image_refs)
                if match_count > 0:
                    # Scale the fused_score upward proportionally to the number
                    # of co-located images, capped at a 10 % lift so that
                    # cross-modal signal supplements rather than overrides
                    # lexical and semantic relevance.
                    scale = min(1.0 + match_count * 0.05, 1.10)
                    item["fused_score"] = round(float(item["fused_score"]) * scale, 6)

        # ── Reranking ──────────────────────────────────────────────────────
        rerank_start = perf_counter()
        if self.reranker and text_items:
            reranked_items = self.reranker.rerank(
                query=query, items=text_items, top_k=min(rerank_k, len(text_items))
            )
        elif any("fused_score" in item for item in text_items):
            reranked_items = sorted(
                text_items, key=lambda item: item.get("fused_score", 0.0), reverse=True
            )[:rerank_k]
        else:
            reranked_items = sorted(text_items, key=lambda item: item["distance"])[:rerank_k]
        rerank_time = perf_counter() - rerank_start

        text_result_items = [
            RetrievalItem(
                doc_id=item.get("doc_id", ""), text=item.get("text", ""),
                metadata=item.get("metadata", {}),
                distance=float(item.get("distance", 1.0)),
                similarity=max(0.0, 1.0 - float(item.get("distance", 1.0))),
                fused_score=item.get("fused_score"), rank=rank, modality="text",
                retrieval_latency_sec=round(text_latency, 4)
            )
            for rank, item in enumerate(reranked_items, start=1)
        ]
        image_result_items = [
            RetrievalItem(
                doc_id=item.get("doc_id", ""), text=item.get("text", ""),
                metadata=item.get("metadata", {}),
                distance=float(item.get("distance", 1.0)),
                similarity=max(0.0, 1.0 - float(item.get("distance", 1.0))),
                fused_score=item.get("fused_score"), rank=rank,
                modality="image", retrieval_latency_sec=round(image_latency, 4)
            )
            for rank, item in enumerate(image_items, start=1)
        ]

        cosine_sim_text = float(np.mean([i.similarity for i in text_result_items])) if text_result_items else 0.0
        cosine_sim_image = float(np.mean([i.similarity for i in image_result_items])) if image_result_items else 0.0
        overall_time = perf_counter() - start_total

        return RetrievalOutput(
            query=query,
            text_items=text_result_items,
            image_items=image_result_items,
            text_latency_sec=round(text_latency, 4),
            image_latency_sec=round(image_latency, 4),
            total_latency_sec=round(overall_time, 4),
            cosine_sim_text=round(cosine_sim_text, 4),
            cosine_sim_image=round(cosine_sim_image, 4),
            search_mode=_text_rm.get("search_mode", "semantic_only"),
            hybrid_stats=_text_rm.get("hybrid_stats", {}),
            # Granular stage latencies exposed for benchmarking.
            embed_time_sec=round(embed_time, 4),
            bm25_time_sec=round(bm25_time, 4),
            fusion_time_sec=round(fusion_time, 4),
            rerank_time_sec=round(rerank_time, 4),
        )


# %%
from typing import List, Dict
import matplotlib.pyplot as plt
from PIL import Image

# %% [markdown]
# # LLM
#
# Goal: run one or more local LLMs on the formatted context and capture responses.
#
# Notes:
# - LLM responses are evaluated against ground truth if available in `TEST_QUESTIONS`.
# - Runtime metrics (latency/CPU/RAM/GPU) are captured per query for analysis.

# %%
import ollama  # Used to load model .
from textwrap import dedent  # Used for spacing problems in prompt .
from tabulate import tabulate  # Used for creating a table for displaying models .
import subprocess  # Used to start ollama server .
import time  # For waiting .
import requests  # Used to access ollama server .
import base64
import io
from PIL import Image
from sentence_transformers import util
import re


# %%
class LocalLLM:
    """
    Interface to a locally hosted Ollama language model.

    Manages server lifecycle, model availability checks, prompt construction,
    streaming inference, and per-query Factual Consistency Distance computation.
    """

    def __init__(self, model_name: str = cfg.llm_model, text_embedder: TextEmbeddingModel = None):
        self.model_name = model_name
        self.process = self.ollama_server(process="start")
        self.text_embedder = text_embedder or TextEmbeddingModel()
        if not self.is_model_available(self.model_name):
            available = [m['model_name'] for m in self.available_models()]
            raise ValueError(
                f"Model '{model_name}' not available. "
                f"Available models: {available}"
            )
        self.tokenizer = tiktoken.get_encoding(cfg.tiktoken_encoding)

    def ollama_server(self, process: str):
        """Start or stop the local Ollama server process."""
        if process == "start":
            try:
                requests.get("http://localhost:11434/api/tags", timeout=1)
                LOGGER.info("Ollama server already running.")
                return "external"
            except Exception:
                pass
            ollama_process = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False
            )
            for _ in range(10):
                try:
                    requests.get("http://localhost:11434/api/tags", timeout=1)
                    LOGGER.info("Ollama server started.")
                    return ollama_process
                except Exception:
                    time.sleep(1)
            raise RuntimeError("Ollama failed to start")  # If server did not start after many tries, raise error.

        elif process == "stop":  # Stopping ollama server.
            if isinstance(self.process, subprocess.Popen):  # Checking if server was started here.
                self.process.terminate()
                self.process.wait()
                LOGGER.info("Ollama server stopped.")
            else:
                LOGGER.info("Ollama server was started externally — not stopping.")  # If started externally, notify.
            return None
        else:
            raise ValueError("Input can be either 'start' or 'stop'")  # Input validation.

    def available_models(self):
        """Return list of locally available Ollama models."""
        response = ollama.list()

        # ollama >= 0.2 returns a Pydantic ListResponse object; older versions
        # returned a plain dict.  Handle both.
        if hasattr(response, "models"):
            models_available = response.models or []
        elif isinstance(response, dict):
            models_available = response.get("models", [])
        else:
            models_available = []

        models = []
        for m in models_available:
            if hasattr(m, "model"):
                name = m.model
                params = getattr(m, "details", None)
                params = params.parameter_size if params else None
            elif isinstance(m, dict):
                name = m.get("model") or m.get("name")
                params = m.get("details", {}).get("parameter_size")
            else:
                continue
            models.append({"model_name": name, "parameters": params})
        return models

    def is_model_available(self, model_name: str) -> bool:
        """Check whether a specific model is available in the local Ollama instance."""
        models = self.available_models()
        return any(m["model_name"] == model_name for m in models)

    def build_prompt(self, query: str, context: str) -> str:
        """Format the user prompt by injecting the retrieved context and query."""
        return f"""Use ONLY the context and images provided below to answer the question. Do not use any outside knowledge. If the context is insufficient, clearly state what is missing.

    If images are provided, treat them as direct evidence alongside the text — describe what they show and how they support or relate to the answer.

    Be thorough but natural. Write in clear prose, not bullet points or numbered sections. Aim for 150–250 words.

    Context:
    {context}

    Question: {query}

    Answer:""".strip()

    @staticmethod
    def pil_to_base64(img: Image.Image) -> str:
        """Encode a PIL image as a base64 PNG string for inclusion in the Ollama payload."""
        buffer = io.BytesIO()
        img = img.convert("RGB")
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def generate_response(
            self,
            query: str,
            context: str,
            images: Optional[List[Dict]] = None,
            stream: bool = True,
            temperature: float = cfg.llm_temperature,
            max_tokens: int = cfg.llm_max_tokens,
    ) -> Dict:
        """
        Run inference for a single query against the provided context and images.

        Returns a dict containing the generated response text, context length
        statistics, wall-clock generation time, and Factual Consistency Distance.
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        if not context or not context.strip():
            if not images:
                raise ValueError("Context cannot be empty when no images are provided")

        if len(context) > 10000:
            LOGGER.warning(
                "Context length (%d chars) exceeds 10,000 — inference may be slow.", len(context)
            )

        prompt = self.build_prompt(query, context)

        image_payload = []
        if images:
            for img_dict in images:
                img = img_dict.get("image")
                if isinstance(img, Image.Image):
                    image_payload.append(self.pil_to_base64(img))
                else:
                    raise TypeError("Images must be PIL.Image.Image")

        context_chars = len(context)
        context_tokens = len(self.tokenizer.encode(context))

        retries = int(getattr(cfg, "llm_retries", 2))
        backoff_sec = float(getattr(cfg, "llm_retry_backoff_sec", 1.0))
        fail_fast = bool(getattr(cfg, "llm_fail_fast", False))
        last_err: Optional[Exception] = None

        for attempt in range(retries + 1):
            try:
                start_time = time.perf_counter()

                user_message = {
                    "role": "user",
                    "content": prompt,
                }
                if image_payload:
                    user_message["images"] = image_payload

                response = ollama.chat(
                    model=self.model_name,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a precise scientific analyst. "
                                "Answer strictly from the provided context and images — never from prior knowledge. "
                                "Write naturally in prose. Be evidence-based and concise."
                            ),
                        },
                        user_message,
                    ],
                    think=cfg.llm_think_mode,
                    stream=stream,
                    options={
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                )

                if stream:
                    print(f"\n{'=' * 80}")
                    print(f"QUERY: {query}")
                    print(f"{'=' * 80}")
                    print("ANSWER:")
                    print("-" * 80)

                    full_response = ""
                    try:
                        for chunk in response:
                            content = ""
                            try:
                                if hasattr(chunk, "message") and hasattr(chunk.message, "content"):
                                    # chunk.message.content may be None during think-mode reasoning
                                    # tokens. Coerce to empty string to avoid concatenation errors.
                                    content = chunk.message.content or ""
                                elif isinstance(chunk, dict):
                                    if "message" in chunk and isinstance(chunk["message"], dict):
                                        content = chunk["message"].get("content") or ""
                                    elif "response" in chunk:
                                        content = chunk.get("response") or ""
                            except Exception:
                                pass

                            # Only append and print non-empty content tokens.
                            # Think-mode models emit intermediate reasoning chunks
                            # whose content field is None or empty; those are
                            # intentionally skipped here.
                            if content:
                                full_response += content
                                print(content, end="", flush=True)
                    except Exception as e:
                        # Streaming can fail mid-flight if the server restarts.
                        # Treat this as retryable.
                        raise RuntimeError(f"Streaming interrupted: {e}") from e

                    print()
                    final_response = (full_response or "").strip()
                    if not final_response:
                        final_response = "No sufficient answer could be generated from the provided context."
                else:
                    final_response = ""
                    try:
                        if hasattr(response, "message") and hasattr(response.message, "content"):
                            final_response = response.message.content or ""
                        elif isinstance(response, dict):
                            if "message" in response and isinstance(response["message"], dict):
                                final_response = response["message"].get("content", "")
                            elif "response" in response:
                                final_response = response.get("response", "")
                    except Exception:
                        pass

                    final_response = (final_response or "").strip()
                    if not final_response:
                        final_response = "No sufficient answer could be generated from the provided context."

                generation_time = time.perf_counter() - start_time

                fcd = None
                try:
                    resp_snippet = (final_response or "")[:2000].strip()
                    if not resp_snippet:
                        raise ValueError("Empty response for FCD")
                    # Limit the context snippet to the same token budget the
                    # LLM actually consumed. Using the full context string would
                    # include material the model never read, artificially
                    # inflating embedding overlap and understating FCD.
                    prompt_for_fcd = self.build_prompt(query, context)
                    ctx_snippet = prompt_for_fcd[:2000]
                    resp_emb = self.text_embedder.embed_query(resp_snippet)
                    ctx_emb = self.text_embedder.embed_query(ctx_snippet)
                    sim = util.cos_sim(resp_emb, ctx_emb).item()
                    fcd = (1.0 - sim) * 100
                except Exception as e:
                    LOGGER.warning("FCD computation failed: %s", e)
                    fcd = None

                return {
                    "response": final_response,
                    "context_length_chars": context_chars,
                    "context_length_tokens": context_tokens,
                    "generation_time_sec": round(generation_time, 4),
                    "factual_consistency_distance": round(fcd, 2) if fcd is not None else None,
                    "error": None,
                    "attempts": attempt + 1,
                }

            except Exception as e:
                last_err = e
                if attempt < retries:
                    wait = backoff_sec * (2 ** attempt)
                    LOGGER.warning("LLM call failed (attempt %s/%s). Retrying in %.1fs. Error: %s",
                                   attempt + 1, retries + 1, wait, e)
                    time.sleep(wait)
                    continue

                msg = f"LLM generation failed after {attempt + 1} attempt(s): {e}"
                if fail_fast:
                    raise RuntimeError(msg) from e
                LOGGER.error(msg)
                return {
                    "response": "No answer (LLM error).",
                    "context_length_chars": context_chars,
                    "context_length_tokens": context_tokens,
                    "generation_time_sec": 0.0,
                    "factual_consistency_distance": None,
                    "error": str(last_err),
                    "attempts": attempt + 1,
                }


# %% [markdown]
# # Testing
#
# `TEST_QUESTIONS` is a small evaluation set used to:
# - run consistent retrieval + generation experiments
# - compute repeatable metrics across model variants

# %%
import psutil

# %%
TEST_QUESTIONS = [
    # ── BENCHMARK v2: multimodal_rag_hard_benchmark ──────────────────────────
    # All 14 questions require joint text + image evidence.
    # Q1-Q7: semantic-focused  |  Q8-Q14: hybrid-focused
    # ─────────────────────────────────────────────────────────────────────────

    # ── SECTION 1: VOYAGER GRAND TOUR ────────────────────────────────────────
    {
        "id": 1,
        "type": "semantic",
        "source_document": "Voyager Grand Tour PDF.pdf",
        "difficulty": "very hard",
        "question": (
            "The trajectory diagram of Voyager 1 and Voyager 2 shows clearly diverging paths after Saturn. "
            "Using both the trajectory image and the mission text, explain why the two paths diverge at that point, "
            "and what specific orbital geometry decision made Voyager 1's post-Saturn path irrecoverable for "
            "further planetary encounters."
        ),
        "ground_truth_answer": (
            "The trajectory diagram shows Voyager 1's path bending sharply away from the ecliptic plane after "
            "Saturn while Voyager 2 continues along a shallower arc toward Uranus and Neptune. The text explains "
            "this divergence was caused by Voyager 1's close flyby of Titan, coming within 4,000 miles of its "
            "surface. That encounter changed Voyager 1's trajectory such that it could not make any further "
            "planetary encounters, sending it out of the solar system. Voyager 2 used a different Saturn geometry "
            "— a closest approach at 63,000 miles versus Voyager 1's 77,000 miles — that preserved the "
            "gravitational slingshot angle needed for the Uranus and Neptune legs. The diagram makes the "
            "irreversibility visually concrete: the labeled date nodes on Voyager 2's path continue to "
            "Jan 24, 1986 (Uranus) and Aug 25, 1989 (Neptune), while Voyager 1's path terminates its "
            "planetary arc at Saturn Nov 12, 1980."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [1, 3],
            "focus": (
                "Voyager 1 Titan flyby 4000 miles trajectory change, Voyager 2 Saturn geometry 63000 miles, "
                "gravitational slingshot, trajectory diagram diverging paths, ecliptic plane"
            )
        }
    },
    {
        "id": 2,
        "type": "semantic",
        "source_document": "Voyager Grand Tour PDF.pdf",
        "difficulty": "hard",
        "question": (
            "The Earth-Moon photograph taken by Voyager 1 is described as the first single-frame image of both "
            "bodies together. Using the image itself and the surrounding mission text, explain what engineering "
            "and operational conditions had to be simultaneously satisfied to produce it, and why this milestone "
            "was considered a meaningful preview of capabilities rather than merely a publicity photograph."
        ),
        "ground_truth_answer": (
            "The image shows both Earth and the Moon captured together in a single frame from 7.25 million miles, "
            "taken just 13 days after launch on September 18, 1977. To produce it, the camera had to achieve "
            "stable long-range pointing at two objects separated by significant angular distance, frame both "
            "within a single exposure without saturating on Earth's brightness, and do so while the spacecraft "
            "was still in early operational checkout. The text describes it as the first of Voyager 1's many "
            "firsts, framing it as a sneak preview of the discoveries ahead. The engineering significance is "
            "that it validated the imaging system's ability to handle multi-body framing at interplanetary "
            "distances under real flight conditions, not just in laboratory testing. For mission planners, a "
            "camera that could jointly frame Earth and Moon from 7.25 million miles had clearly demonstrated "
            "the pointing precision and dynamic range that would be needed to image moons near giant planets "
            "from comparable or greater distances during the actual science campaign."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [2],
            "focus": (
                "Earth-Moon single frame 7.25 million miles September 18 1977, pointing precision, "
                "dynamic range, operational checkout, first of many firsts"
            )
        }
    },

    # ── SECTION 2: MARS SCIENCE LABORATORY ───────────────────────────────────
    {
        "id": 3,
        "type": "semantic",
        "source_document": "mars-science-laboratory.pdf",
        "difficulty": "very hard",
        "question": (
            "Curiosity's Sky Crane landing image and the text description of the landing sequence together tell "
            "a more complete story than either alone. Using both the image of the sky crane lowering Curiosity "
            "and the technical description of the EDL sequence, explain why the sky crane approach was necessary "
            "rather than airbag-based landing used by earlier rovers, and what physical constraints of the "
            "crater site made this the only viable solution."
        ),
        "ground_truth_answer": (
            "The image shows Curiosity being lowered on a tether from the upper sky crane stage to land "
            "wheels-down on the Martian surface. The text explains that Curiosity's payload is more than 10 "
            "times as massive as earlier rovers and its science equipment required distributing samples to "
            "internal analytical instruments, which necessitated landing upright on wheels rather than rolling "
            "in a protective airbag cocoon. The precision landing capability reduced the target ellipse to about "
            "20 kilometers, a five-fold improvement over earlier missions. The Gale Crater site was so close to "
            "the crater wall and Mount Sharp that airbag-style landing with the wider uncertainty ellipses of "
            "earlier missions would have been unsafe. The sky crane therefore solved two simultaneous problems: "
            "it accommodated Curiosity's size and upright instrument layout while also enabling the precision "
            "that unlocked Gale Crater as a scientifically viable destination."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [1, 2],
            "focus": (
                "sky crane tether upright landing, 20 kilometer ellipse five-fold improvement, "
                "Gale Crater wall Mount Sharp proximity, airbag limitation payload 10x heavier"
            )
        }
    },
    {
        "id": 4,
        "type": "semantic",
        "source_document": "mars-science-laboratory.pdf",
        "difficulty": "very hard",
        "question": (
            "The image of the rock outcrop called Link and the image of the first drill sample at John Klein "
            "represent two distinct evidentiary steps in Curiosity's habitability argument. Using both images "
            "and the corresponding text, explain why the visual evidence from Link was scientifically necessary "
            "but not sufficient, and what the John Klein drilling contributed that images alone could never provide."
        ),
        "ground_truth_answer": (
            "The Link outcrop image shows rounded pebbles mixed with hardened sand in conglomerate rock, which "
            "the text identifies as evidence for past vigorous water flow — the rounding and sorting of the "
            "pebbles indicates sustained fluvial transport. However, visual evidence of ancient stream flow "
            "establishes only water presence and movement, not water chemistry, persistence, or geochemical "
            "habitability. The John Klein drill image shows the first borehole ever made in Martian rock, and "
            "the text explains that analysis of that drilled sample provided the full habitability evidence "
            "package: geological and mineralogical evidence for sustained liquid water, key elemental ingredients "
            "for life, a chemical energy source, and water that was neither too acidic nor too salty. Images "
            "could establish morphology and sedimentary context; only in situ chemical analysis of drilled "
            "interior material could constrain whether the ancient environment was genuinely suited to microbial life."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [2, 3],
            "focus": (
                "Link rounded pebbles conglomerate fluvial transport, John Klein first drill borehole, "
                "habitability sustained liquid water elemental ingredients chemical energy source"
            )
        }
    },

    # ── SECTION 3: HUBBLE ────────────────────────────────────────────────────
    {
        "id": 5,
        "type": "semantic",
        "source_document": "highlights_of_hubbles_exploration_of_the_universe.pdf",
        "difficulty": "very hard",
        "question": (
            "The Hubble deep field image and the panel of faint early galaxies shown alongside it are described "
            "as evidence for galaxy growth over cosmic time. Using the visual appearance of those galaxies and "
            "the text about hierarchical galaxy formation, explain how the morphological evidence in the images "
            "connects to the theoretical prediction that large present-day galaxies assembled through mergers "
            "rather than forming fully intact."
        ),
        "ground_truth_answer": (
            "The Ultra Deep Field image reveals thousands of galaxies at varying distances, and the close-up "
            "panel of faint, distant galaxies shows they are small, irregular, and frequently interacting — "
            "very different from the grand spiral and elliptical galaxies seen in the nearby universe. The text "
            "states that the most distant and earliest galaxies are smaller and more irregularly shaped, and "
            "that the universe was smaller in the past, making gravitational interactions between galaxies more "
            "frequent. The visual evidence of disturbed morphologies, asymmetric shapes, and apparent mergers "
            "in the distant panels directly supports hierarchical growth: these compact, irregular systems are "
            "the raw building blocks that collide and merge over billions of years into the familiar large "
            "galaxies seen nearby. The deep field effectively provides a time-ordered fossil record where "
            "increasing distance corresponds to increasing lookback time, allowing morphological evolution to "
            "be read directly from the images."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [4],
            "focus": (
                "Ultra Deep Field irregular galaxies mergers hierarchical growth, spiral elliptical cosmic time, "
                "lookback time universe smaller past gravitational interactions more frequent"
            )
        }
    },
    {
        "id": 6,
        "type": "semantic",
        "source_document": "highlights_of_hubbles_exploration_of_the_universe.pdf",
        "difficulty": "very hard",
        "question": (
            "The dark matter page shows two views of galaxy cluster Cl 0024+17 — one in visible light showing "
            "blue arcs, and one with a blue dark matter density overlay. Using both images together with the "
            "gravitational lensing text, explain the full chain of inference that connects the visible arc "
            "morphology in the first image to the quantitative dark matter mass estimate cited in the text."
        ),
        "ground_truth_answer": (
            "The visible-light image of Cl 0024+17 shows blue arc-shaped features among the yellowish cluster "
            "galaxies. The text explains these are magnified, distorted images of background galaxies whose "
            "light has been bent by the cluster's gravity — a phenomenon called gravitational lensing. The "
            "second image overlays in blue the dark matter density distribution required to mathematically "
            "account for the observed distortions. The inference chain is: (1) measure the shapes, positions, "
            "and degree of distortion of the background arcs; (2) mathematically reverse-engineer the mass "
            "distribution needed to produce those specific distortions; (3) compare that total mass to the "
            "luminous matter visible in the cluster. The text states the result is that the universe appears "
            "to contain about five times more dark matter than regular matter, and that large structures are "
            "found at intersections of immense dark matter filament networks. The two images together show "
            "both the raw observational input (arcs) and the derived mass product (blue overlay), making the "
            "lensing inference chain visually complete."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [6],
            "focus": (
                "Cl 0024+17 blue arcs gravitational lensing dark matter density five times reverse-engineer "
                "mass distribution filaments background galaxies distortion"
            )
        }
    },
    {
        "id": 7,
        "type": "semantic",
        "source_document": "highlights_of_hubbles_exploration_of_the_universe.pdf",
        "difficulty": "very hard",
        "question": (
            "The image series of Supernova 1987A from 1994 through 2006 shows a ring of material progressively "
            "lighting up over twelve years. Using both the time-sequence images and the text description of the "
            "shock wave dynamics, explain what physical process the brightening ring documents, why it is "
            "observable only over a multi-year baseline, and what this reveals about the pre-explosion mass "
            "loss history of the progenitor star."
        ),
        "ground_truth_answer": (
            "The six-panel image sequence shows SN 1987A's middle ring going from a faint structure in 1994 "
            "to a brightening necklace of hot spots by 2006. The text explains that an expanding shock wave "
            "from the central explosion is slamming into a pre-existing ring of material surrounding the dead "
            "star. As the blast wave reaches and compresses ring material, those sections are heated and begin "
            "to glow — the sequentially brightening spots represent the shock front progressively encountering "
            "denser regions around the ring's circumference. The process requires a multi-year baseline because "
            "the shock wave must physically travel from the explosion center to the ring, and the ring's finite "
            "circumference means different azimuthal sections are reached at slightly different times. The "
            "existence of the ring itself implies the progenitor star shed a structured shell of material in an "
            "asymmetric mass-loss event before it exploded, providing direct evidence that massive stars undergo "
            "significant pre-supernova mass ejection that shapes the circumstellar environment the eventual "
            "blast wave must propagate through."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [14, 15],
            "focus": (
                "SN 1987A shock wave ring brightening hot spots pre-existing ring mass loss progenitor "
                "multi-year sequence 1994 2006 azimuthal sections circumstellar"
            )
        }
    },

    # ── SECTION 4: HYBRID-FOCUSED QUESTIONS ──────────────────────────────────
    {
        "id": 8,
        "type": "hybrid",
        "source_document": "Voyager Grand Tour PDF.pdf",
        "difficulty": "very hard",
        "question": (
            "The Saturn montage image shows the planet alongside several of its moons at different scales. "
            "Using the image to identify visible moons and the text description of Voyager 1's Titan encounter, "
            "explain the specific trade-off that made Titan the highest-priority Saturn target, why that priority "
            "decision permanently closed Voyager 1's path to the outer planets, and what Voyager 2's different "
            "Saturn geometry preserved for the mission as a whole."
        ),
        "ground_truth_answer": (
            "The Saturn montage shows the planet's rings and several moons, with Titan visible as a large body. "
            "The text states Titan is larger than Earth's own Moon and was known to have a dense atmosphere, "
            "making it uniquely scientifically valuable among Saturn's satellites. Voyager 1 flew within 4,000 "
            "miles of Titan's surface near its South Pole — the text explicitly states this Titan encounter "
            "changed Voyager 1's trajectory so it could not make any further planetary encounters. Voyager 2's "
            "closest Saturn approach was at 63,000 miles, a different geometry that did not incur the same "
            "trajectory penalty. The consequence was that Voyager 2's Saturn flyby automatically set up a coast "
            "of five and a half years to Uranus, where it discovered 11 new moons, and then continued to Neptune "
            "where it discovered 6 more moons and observed geysers on Triton. The two-spacecraft strategy "
            "deliberately split the objectives: one vehicle maximized Titan science at the cost of the extended "
            "tour, while the other preserved the grand-tour continuation."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [1, 3],
            "focus": (
                "Titan dense atmosphere 4000 miles South Pole trajectory penalty, Voyager 2 63000 miles "
                "Uranus 11 moons Neptune 6 moons Triton geysers grand-tour continuation"
            )
        }
    },
    {
        "id": 9,
        "type": "hybrid",
        "source_document": "Voyager Grand Tour PDF.pdf",
        "difficulty": "very hard",
        "question": (
            "The Uranus montage and the Neptune-Triton montage each show a planet alongside several moons. "
            "Using these images in combination with the text on Voyager 2's encounters, explain why Voyager 2 "
            "discovered 11 moons at Uranus and 6 at Neptune despite having far less observing time at each "
            "world than it had spent at Jupiter, and what this implies about the relationship between mission "
            "geometry and small-body discovery yield."
        ),
        "ground_truth_answer": (
            "The Uranus montage shows several larger moons around the tilted ice giant, while the Neptune "
            "montage shows Triton prominently alongside Neptune. The text states Voyager 2 began studying "
            "Uranus in November 1985 and made its closest approach on January 24, 1986, discovering 11 new "
            "moons, while the Neptune encounter beginning June 1989 yielded 6 new moons. At Jupiter, which "
            "Voyager 2 studied intensively from April to August 1979 — a much longer campaign — only three "
            "previously undiscovered small moons were found. The inversion in discovery yield relative to time "
            "suggests that proximity geometry and illumination conditions during a fast flyby can be more "
            "favorable for detecting small, faint satellites than a longer but more distant observation campaign. "
            "At Uranus and Neptune the extremely close approach distances (50,600 miles and 3,076 miles "
            "respectively) provided the angular resolution and lighting angles needed to detect objects that "
            "would have remained hidden from farther distances, regardless of integration time."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [3, 4],
            "focus": (
                "Uranus 11 new moons 50600 miles January 1986, Neptune 6 new moons 3076 miles June 1989, "
                "Jupiter 3 moons longer campaign, angular resolution lighting flyby geometry"
            )
        }
    },
    {
        "id": 10,
        "type": "hybrid",
        "source_document": "mars-science-laboratory.pdf",
        "difficulty": "hard",
        "question": (
            "Curiosity's self-portrait assembled from MAHLI images is shown in the document. Using the image "
            "to assess what the composite reveals about rover configuration and condition, and the technical "
            "text describing MAHLI's capabilities and arm placement, explain why this imaging method provides "
            "more diagnostic value for mission engineers than a single wide-angle shot from a fixed camera would."
        ),
        "ground_truth_answer": (
            "The self-portrait shows the full rover body, mast, arm, and wheel configuration in a composite "
            "assembled from many close-range images. The text states MAHLI is the Mars Hand Lens Imager mounted "
            "on the robotic arm, capable of taking extreme close-up images revealing details smaller than a "
            "human hair, and that it can focus on hard-to-reach objects more than an arm's length away. Because "
            "the arm repositions the camera at multiple locations and angles, the resulting mosaic provides "
            "overlapping, close-range coverage of surfaces that a fixed wide-angle camera would capture only "
            "at low resolution and from a single viewpoint. For engineers, this means dust accumulation on "
            "solar panel analogs, wheel wear patterns, hardware surface changes, and instrument condition can "
            "all be assessed at sub-millimeter detail in routine imaging without requiring dedicated inspection "
            "hardware. The method also confirms arm articulation health implicitly — a successful multi-position "
            "mosaic is itself evidence of normal arm function."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [3],
            "focus": (
                "MAHLI robotic arm mosaic self-portrait close-up sub-millimeter detail arm articulation "
                "diagnostic composite wheel wear dust accumulation"
            )
        }
    },
    {
        "id": 11,
        "type": "hybrid",
        "source_document": "highlights_of_hubbles_exploration_of_the_universe.pdf",
        "difficulty": "very hard",
        "question": (
            "The Cepheid star brightness sequence images from the Andromeda galaxy and the distant supernova "
            "comparison images from 1995 and 2002 are presented as two complementary distance-measurement "
            "methods. Using both image sets and the text about refining the Hubble constant, explain why "
            "astronomers need both techniques rather than relying on only one, and how the combination of "
            "refined distances and velocity measurements changed the estimated age of the universe."
        ),
        "ground_truth_answer": (
            "The Cepheid sequence shows a star in Andromeda varying in brightness across multiple observation "
            "dates — this cyclical brightness change is the basis for the period-luminosity distance calibration. "
            "The 1995 and 2002 comparison images show a supernova appearing in a previously empty patch of the "
            "Hubble Deep Field, illustrating how Type Ia supernovas serve as standard candles at distances where "
            "individual Cepheids cannot be resolved. The text explains astronomers measure distances by comparing "
            "the known brightness of objects like Cepheid stars or standard supernovas to their apparent "
            "brightness in distant galaxies. Cepheids are well-resolved and reliable within the local universe "
            "but too faint to detect in very distant galaxies; supernovas are bright enough to see at "
            "cosmological distances but need Cepheid-based calibration in the local universe to set their "
            "absolute luminosity scale. By combining both ladders, Hubble refined galaxy distance measurements "
            "sufficiently to narrow the Hubble constant uncertainty, and coupling those distances with galaxy "
            "velocity measurements from other telescopes, calculations currently place the age of the universe "
            "at 13.8 billion years, far more precise than the pre-Hubble range of 10 to 20 billion years."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [3],
            "focus": (
                "Cepheid period-luminosity Andromeda, Type Ia supernova standard candle Hubble Deep Field "
                "1995 2002, Hubble constant 13.8 billion years distance ladder velocity measurements"
            )
        }
    },
    {
        "id": 12,
        "type": "hybrid",
        "source_document": "highlights_of_hubbles_exploration_of_the_universe.pdf",
        "difficulty": "very hard",
        "question": (
            "The M84 galaxy images show a camera view with a dark dust band and a spectrograph plot with "
            "dramatic color shifts from blue to red. Using both images and the text on black hole mass "
            "measurements, explain exactly what physical phenomenon the color shift encodes, why the spectrograph "
            "slit position relative to the core is critical to the measurement, and how this technique "
            "established the mass relationship between black holes and their host galaxies."
        ),
        "ground_truth_answer": (
            "The camera image shows M84's bright core crossed by a dark vertical band of gas and dust, while "
            "the spectrograph plot shows a sharp transition from blue on one side of the core to red on the "
            "other. The text explains that blueshifted light indicates the emitting source is moving toward "
            "Earth while redshifted light indicates recession — stars and gas nearest the core are orbiting a "
            "central black hole at 880,000 miles per hour, so material on one side of the rotation axis "
            "approaches Earth while material on the other side recedes. The spectrograph slit must be positioned "
            "centered on the core because the Doppler shift gradient is steepest where the orbital velocities "
            "are highest, right at the innermost region around the black hole. The width and sharpness of the "
            "blue-to-red transition encodes the rotational velocity, which through Kepler's laws yields the "
            "enclosed mass. Applying this technique across many galaxies in a Hubble census revealed that black "
            "hole mass scales with the mass of the host galaxy's central stellar bulge — the larger the galaxy, "
            "the larger the black hole — suggesting black holes and their galaxies co-evolved."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [7],
            "focus": (
                "M84 blueshift redshift 880000 miles per hour spectrograph slit Doppler enclosed mass "
                "bulge mass scaling black hole galaxy co-evolution Kepler"
            )
        }
    },
    {
        "id": 13,
        "type": "hybrid",
        "source_document": "highlights_of_hubbles_exploration_of_the_universe.pdf",
        "difficulty": "very hard",
        "question": (
            "The Jupiter impact sequence showing cloud darkening from Comet Shoemaker-Levy 9, the close-up "
            "plume image from the first large fragment impact, and the Saturn aurora image are all on the same "
            "or adjacent pages. Using these three images and the text covering both the impact events and aurora "
            "observations, explain what each image type contributes uniquely to understanding planetary "
            "atmospheric dynamics, and why continuous ultraviolet monitoring capability is the common "
            "prerequisite for all three scientific results."
        ),
        "ground_truth_answer": (
            "The Shoemaker-Levy 9 cloud darkening sequence shows Jupiter's atmosphere progressively accumulating "
            "impact scars as 21 fragments struck sequentially in 1994 — each dark sooty scar documents energy "
            "deposition depth and lateral spread in the Jovian cloud deck. The plume image from the first large "
            "fragment shows a colossal debris column rising above Jupiter's limb in time-stamped frames at "
            "minute-level cadence, capturing the transient ejecta dynamics during the first seconds to minutes "
            "after impact. The Saturn aurora image shows UV-bright curtains of light at Saturn's polar regions, "
            "capturing magnetospheric particle precipitation into the upper atmosphere. Each contributes "
            "something distinct: the scar sequence measures integrated impact effects over days to weeks; the "
            "plume sequence captures real-time ballistic ejecta dynamics; the aurora image characterizes "
            "steady-state magnetospheric coupling. All three depend on ultraviolet sensitivity because: impact "
            "plumes and atmospheric ejecta are bright in UV; auroral emission from excited atmospheric molecules "
            "peaks in the UV; and the temporal fidelity needed to capture transients requires space-based UV "
            "capability free from atmospheric UV absorption that blocks ground-based observation."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [8, 9],
            "focus": (
                "Shoemaker-Levy 9 21 fragments 1994 impact scars plume ejecta dynamics, "
                "Saturn aurora ultraviolet magnetosphere time-domain UV sensitivity"
            )
        }
    },
    {
        "id": 14,
        "type": "hybrid",
        "source_document": "highlights_of_hubbles_exploration_of_the_universe.pdf",
        "difficulty": "very hard",
        "question": (
            "The TW Hydrae disk image shows a gap structure around the star, and the 1997 and 2012 Beta "
            "Pictoris disk images show an edge-on disk changing over fifteen years. Using both sets of images "
            "alongside the planet-formation text, explain what specific morphological features in each image "
            "constitute evidence for an embedded or nearby planet, why temporal baseline matters for the Beta "
            "Pictoris case but not for TW Hydrae, and how these two systems together support a dynamic rather "
            "than static picture of disk evolution."
        ),
        "ground_truth_answer": (
            "The TW Hydrae image shows a protoplanetary disk with a gap approximately 1.9 billion miles wide "
            "that is not yet completely cleared of material. The text explains this gap is most likely caused "
            "by a growing, unseen planet gravitationally sweeping up material like a snowplow, carving a lane "
            "in the disk. The morphological evidence is the gap itself — its width, incomplete clearing, and "
            "position within the disk are all consistent with an embedded planet at that orbital radius. The "
            "Beta Pictoris pair shows the large edge-on disk in 1997 and 2012; the text states scientists "
            "studied changes in the orbiting material caused by a massive planet embedded within the dust disk. "
            "For TW Hydrae, a single image suffices because the gap is a static structural feature that persists "
            "on timescales far longer than an observation campaign. For Beta Pictoris, the 15-year baseline is "
            "essential because the planet-induced changes in disk structure — warps, asymmetries, or material "
            "redistribution — are only detectable as differences between epochs, not from any single snapshot. "
            "Together, TW Hydrae shows a planet actively carving its initial gap while Beta Pictoris shows a "
            "more evolved system where a mature embedded planet continues to dynamically reshape disk material "
            "over decadal timescales."
        ),
        "expected_images": True,
        "required_context": {
            "pages": [15],
            "focus": (
                "TW Hydrae gap 1.9 billion miles snowplow embedded planet, Beta Pictoris 1997 2012 "
                "15-year baseline warps asymmetries disk evolution temporal baseline"
            )
        }
    },
]

# %% [markdown]
# # METRICS
#
# Metrics are grouped into:
# - **Retrieval** (M1–M5, M9): embedding time, index size, retrieval latency, similarity, coverage, context length
# - **Generation quality** (M6–M15): ROUGE, BLEU, METEOR, BERTScore, FCD, faithfulness, ground-truth coverage
# - **Performance** (M16–M19 + GPU): end-to-end latency and resource usage


# %%
# rouge_score sometimes prints directly to stdout (e.g., "Using default tokenizer."). Suppress at import time.
with suppress_output(enabled=True):
    from rouge_score import rouge_scorer
import statistics
import nltk
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from nltk.translate.meteor_score import meteor_score as _nltk_meteor_score
from collections import Counter
import threading
import time


# %%
def _ensure_nltk_resources():
    """Download required NLTK data on first use if not already present.

    Downloads are guarded by a module-level lock so that parallel notebook
    cells cannot race each other into concurrent nltk.download() calls,
    which can corrupt the data directory on some platforms.
    """
    import threading as _threading
    _nltk_lock = getattr(_ensure_nltk_resources, "_lock", None)
    if _nltk_lock is None:
        _ensure_nltk_resources._lock = _threading.Lock()
        _nltk_lock = _ensure_nltk_resources._lock

    with _nltk_lock:
        for resource, path in [
            ("punkt_tab", "tokenizers/punkt_tab"),
            ("wordnet", "corpora/wordnet"),
            ("omw-1.4", "corpora/omw-1.4"),
        ]:
            try:
                nltk.data.find(path)
            except LookupError:
                nltk.download(resource, quiet=True)


# %%
class ResourceMonitor:
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        # Prime the CPU counter so the first real read is accurate
        self.process.cpu_percent(interval=None)

    def get_snapshot(self):
        """Non-blocking snapshot — CPU percent is delta since last call.

        The raw value from psutil is divided by the logical core count so the
        result is always in the conventional 0-100% single-process range.
        """
        raw_cpu = self.process.cpu_percent(interval=None)
        return {
            "cpu_percent": raw_cpu / _CPU_COUNT,
            "ram_gb": round(self.process.memory_info().rss / (1024 ** 3), 4),
        }

    def start_process_monitor(self, interval: float = 0.05) -> dict:
        """
        Sample process CPU and RSS while a query is running.

        psutil's process.cpu_percent is a delta counter, so a single snapshot
        after generation can miss short CPU spikes. Sampling gives a better
        per-query average/peak profile.

        Each raw cpu_percent reading is divided by the logical core count so
        values stay within the conventional 0-100% single-process range rather
        than potentially exceeding 100% on multi-core hosts.
        """
        results = {"cpu_samples": [], "ram_samples": [], "timestamps": [], "running": True}
        self.process.cpu_percent(interval=None)

        def _sample():
            while results["running"]:
                try:
                    raw_cpu = float(self.process.cpu_percent(interval=None))
                    results["cpu_samples"].append(raw_cpu / _CPU_COUNT)
                    results["ram_samples"].append(self.process.memory_info().rss / (1024 ** 3))
                    results["timestamps"].append(time.perf_counter())
                except Exception:
                    pass
                time.sleep(interval)

        t = threading.Thread(target=_sample, daemon=True)
        t.start()
        results["_thread"] = t
        return results

    def stop_process_monitor(self, results: dict) -> dict:
        results["running"] = False
        t = results.get("_thread")
        if t:
            t.join(timeout=2.0)
        cpu_samples = results.get("cpu_samples", [])
        ram_samples = results.get("ram_samples", [])
        return {
            "avg_cpu": statistics.mean(cpu_samples) if cpu_samples else 0.0,
            "peak_cpu": max(cpu_samples) if cpu_samples else 0.0,
            "avg_ram_gb": statistics.mean(ram_samples) if ram_samples else 0.0,
            "peak_ram_gb": max(ram_samples) if ram_samples else 0.0,
            "ram_delta_gb": (max(ram_samples) - min(ram_samples)) if len(ram_samples) > 1 else 0.0,
            "sample_count": len(cpu_samples),
        }

    def start_gpu_monitor(self, gpu_handle, interval: float = 0.05):
        """
        Start background thread that samples GPU utilization every `interval`
        seconds.  Returns a results dict that is filled in when stop_gpu_monitor
        is called.
        """
        results = {"util_samples": [], "vram_samples": [], "timestamps": [], "running": True}

        def _sample():
            while results["running"]:
                try:
                    util = pynvml.nvmlDeviceGetUtilizationRates(gpu_handle).gpu
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(gpu_handle)
                    vram_gb = mem_info.used / (1024 ** 3)
                    results["util_samples"].append(float(util))
                    results["vram_samples"].append(vram_gb)
                    results["timestamps"].append(time.perf_counter())
                except Exception:
                    pass
                time.sleep(interval)

        if gpu_handle is not None:
            t = threading.Thread(target=_sample, daemon=True)
            t.start()
            results["_thread"] = t

        return results

    def stop_gpu_monitor(self, results: dict) -> dict:
        """Stop the background GPU monitor and return avg/peak stats."""
        results["running"] = False
        t = results.get("_thread")
        if t:
            t.join(timeout=2.0)
        util_samples = results.get("util_samples", [])
        vram_samples = results.get("vram_samples", [])
        return {
            "avg_gpu": statistics.mean(util_samples) if util_samples else 0.0,
            "peak_gpu": max(util_samples) if util_samples else 0.0,
            "avg_gpu_active": statistics.mean([u for u in util_samples if u > 0]) if any(
                u > 0 for u in util_samples) else 0.0,
            "gpu_duty_cycle": (
                    sum(1 for u in util_samples if u > 0) / len(util_samples) * 100) if util_samples else 0.0,
            "avg_vram_gb": statistics.mean(vram_samples) if vram_samples else 0.0,
            "peak_vram_gb": max(vram_samples) if vram_samples else 0.0,
            "vram_delta_gb": (max(vram_samples) - min(vram_samples)) if len(vram_samples) > 1 else 0.0,
            "sample_count": len(util_samples),
            "active_samples": sum(1 for u in util_samples if u > 0),
        }


# %%
# M1 (Embedding Time)
def compute_embedding_time(text_time: float, image_time: float, verbose: bool = False) -> float:
    total = text_time + image_time
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M1 Embedding Time: %.4f seconds", total)
        LOGGER.info("%s", "─" * 60)
    return total


# %%
# M2 (Index Size)
def compute_index_size(text_db: 'VectorStore', image_db: 'VectorStore',
                       verbose: bool = False,
                       bm25_corpus_size: Optional[int] = None) -> int:
    if bm25_corpus_size is not None:
        # BM25-only mode: vector DB is not populated; report BM25 corpus size .
        if verbose:
            LOGGER.info("")
            LOGGER.info("%s", "─" * 60)
            LOGGER.info("  M2 Index Size (BM25 corpus): %s documents", bm25_corpus_size)
            LOGGER.info("%s", "─" * 60)
        return bm25_corpus_size
    text_count = text_db.get_collection_stats().get("count", 0)
    image_count = image_db.get_collection_stats().get("count", 0)
    total = text_count + image_count
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M2 Index Size: %s vectors", total)
        LOGGER.info("     └─ Text vectors: %s", text_count)
        LOGGER.info("     └─ Image vectors: %s", image_count)
        LOGGER.info("%s", "─" * 60)
    return total


# %%
# M3 (Retrieval Latency)
def compute_retrieval_latency(times: List[float], verbose: bool = False) -> float:
    if not times:
        if verbose:
            LOGGER.info("")
            LOGGER.info("%s", "─" * 60)
            LOGGER.info("  M3 Retrieval Latency: 0.0000 seconds")
            LOGGER.info("%s", "─" * 60)
        return 0.0
    avg = statistics.mean(times)
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M3 Retrieval Latency: %.4f seconds", avg)
        LOGGER.info("     └─ Min: %.4fs, Max: %.4fs", min(times), max(times))
        LOGGER.info("%s", "─" * 60)
    return avg


# %%
# M4 (Retrieval similarity)
def compute_retrieval_similarity(values: List[float], label: str = "M4 Retrieval Similarity",
                                 verbose: bool = False) -> float:
    if not values:
        if verbose:
            LOGGER.info("")
            LOGGER.info("%s", "─" * 60)
            LOGGER.info("  %s: 0.0000", label)
            LOGGER.info("%s", "─" * 60)
        return 0.0
    avg = statistics.mean(values)
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  %s: %.4f", label, avg)
        LOGGER.info("%s", "─" * 60)
    return avg


# Backward-compatible alias for older notebook cells.
compute_cosine_similarity = compute_retrieval_similarity


# %%
def _get_reference_text(result_item: Dict, top_k_chunks: int = cfg.rouge_top_k_chunks) -> str:
    """
    Choose the best available reference text for overlap-based metrics.

    Priority:
      1. Ground truth answer (reference_text) — if non-empty after stripping
      2. Top-k chunks of the retrieved context as a proxy reference
    """
    explicit_reference = result_item.get("reference_text", "").strip()
    # Guard: must be a meaningful string (> 20 chars) to count as GT
    if explicit_reference and len(explicit_reference) > 20:
        return explicit_reference

    context_text = result_item.get("context", "").strip()
    if not context_text:
        return ""

    context_parts = context_text.split("\n\n")
    # The context formatter already limits chunks to cfg.max_text_chunks
    # before this function is called. Capping top_k_chunks to that same
    # bound avoids silently requesting more chunks than exist.
    effective_k = min(top_k_chunks, cfg.max_text_chunks)
    return (
        "\n\n".join(context_parts[:effective_k])
        if len(context_parts) > effective_k
        else context_text
    )


# %%
# M5 (top-k required-page coverage)
def compute_top_k_accuracy(
        retrieval_output: List[Dict],
        test_questions: List[Dict],
        k: int = 5,
        verbose: bool = False,
) -> float:
    gt_pages_map = {}
    for q in test_questions:
        qid = q.get("id")
        if qid:
            pages = q.get("required_context", {}).get("pages", [])
            if pages:
                gt_pages_map[qid] = set(pages)

    coverage_scores = []
    perfect_hits = 0
    for item in retrieval_output:
        qid = item.get("id")
        if qid not in gt_pages_map:
            continue
        correct_pages = gt_pages_map[qid]
        try:
            metas = item["result"]["text_results"]["metadatas"][0][:k]
        except (KeyError, IndexError, TypeError):
            continue
        retrieved_pages = {m.get("page_num") or m.get("page") for m in metas if m is not None}
        if not correct_pages:
            continue
        coverage = len(correct_pages & retrieved_pages) / len(correct_pages)
        coverage_scores.append(coverage)
        if coverage == 1.0:
            perfect_hits += 1

    score = (statistics.mean(coverage_scores) * 100) if coverage_scores else 0.0
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M5 Top-k Page Coverage@%s: %.2f%%", k, score)
        LOGGER.info("       Perfect page hits: %s/%s", perfect_hits, len(coverage_scores))
        LOGGER.info("%s", "─" * 60)
    return score


# %%
# M6 (ROUGE-1)
def compute_rouge1(per_query_results: List[Dict], k: int = 5, verbose: bool = False) -> float:
    """
    M6: ROUGE-1 between generated response and retrieved context (top-k chunks as pseudo-ground-truth).
    Measures unigram overlap between response and retrieved documents.
    """
    scorer = rouge_scorer.RougeScorer(["rouge1"], use_stemmer=True)
    scores = []
    skipped = 0
    with suppress_output(enabled=not verbose):
        for item in per_query_results:
            response_text = item.get("response_text", "").strip()
            reference_text = _get_reference_text(item, top_k_chunks=k)

            if not response_text or not reference_text:
                skipped += 1
                continue

            try:
                score = scorer.score(reference_text, response_text)["rouge1"].fmeasure
                scores.append(score)
            except Exception as e:
                if verbose:
                    LOGGER.info("  M6 calculation failed: %s", e)
                skipped += 1

    avg_score = statistics.mean(scores) if scores else 0.0

    if verbose:
        LOGGER.info("")
        LOGGER.info("  %s", "─" * 60)
        LOGGER.info("  M6 ROUGE-1: %.4f", avg_score)
        LOGGER.info("       Evaluated: %s, Skipped: %s", len(scores), skipped)
        LOGGER.info("%s", "─" * 60)

    return avg_score


# %%
# M7 (ROUGE-2)
def compute_rouge2(per_query_results: List[Dict], k: int = 5, verbose: bool = False) -> float:
    """
    M7: ROUGE-2 between generated response and retrieved context.
    Measures bigram (2-word sequence) overlap - stricter than ROUGE-1.
    """
    scorer = rouge_scorer.RougeScorer(["rouge2"], use_stemmer=True)
    scores = []
    skipped = 0
    with suppress_output(enabled=not verbose):
        for item in per_query_results:
            response_text = item.get("response_text", "").strip()
            reference_text = _get_reference_text(item, top_k_chunks=k)

            if not response_text or not reference_text:
                skipped += 1
                continue

            try:
                score = scorer.score(reference_text, response_text)["rouge2"].fmeasure
                scores.append(score)
            except Exception as e:
                if verbose:
                    LOGGER.info("  M7 calculation failed: %s", e)
                skipped += 1

    avg_score = statistics.mean(scores) if scores else 0.0

    if verbose:
        LOGGER.info("")
        LOGGER.info("  %s", "─" * 60)
        LOGGER.info("  M7 ROUGE-2: %.4f", avg_score)
        LOGGER.info("       Evaluated: %s, Skipped: %s", len(scores), skipped)
        LOGGER.info("%s", "─" * 60)

    return avg_score


# %%
# M8 (ROUGE-L)
def compute_rougeL(per_query_results: List[Dict], k: int = 5, verbose: bool = False) -> float:
    """
    M8: ROUGE-L between generated response and retrieved context.
    Measures longest common subsequence - captures sentence structure similarity.
    """
    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    scores = []
    skipped = 0
    with suppress_output(enabled=not verbose):
        for item in per_query_results:
            response_text = item.get("response_text", "").strip()
            reference_text = _get_reference_text(item, top_k_chunks=k)

            if not response_text or not reference_text:
                skipped += 1
                continue

            try:
                score = scorer.score(reference_text, response_text)["rougeL"].fmeasure
                scores.append(score)
            except Exception as e:
                if verbose:
                    LOGGER.info("  M8 calculation failed: %s", e)
                skipped += 1

    avg_score = statistics.mean(scores) if scores else 0.0

    if verbose:
        LOGGER.info("")
        LOGGER.info("  %s", "─" * 60)
        LOGGER.info("  M8 ROUGE-L: %.4f", avg_score)
        LOGGER.info("       Evaluated: %s, Skipped: %s", len(scores), skipped)
        LOGGER.info("%s", "─" * 60)

    return avg_score


# %%
# M9 (Context Length)
def compute_context_length(formatted_output: List[Dict], verbose: bool = False) -> float:
    """
    Computes average context length in characters from formatted output.
    """
    if not formatted_output:
        if verbose:
            LOGGER.info("")
            LOGGER.info("%s", "─" * 60)
            LOGGER.info("  M9 Context Length: 0 characters")
            LOGGER.info("%s", "─" * 60)
        return 0.0

    lengths = []
    for item in formatted_output:
        text_context = item.get("text_context", "")
        if text_context:
            lengths.append(len(text_context))

    if not lengths:
        if verbose:
            LOGGER.info("")
            LOGGER.info("%s", "─" * 60)
            LOGGER.info("  M9 Context Length: 0 characters")
            LOGGER.info("%s", "─" * 60)
        return 0.0

    avg = statistics.mean(lengths)
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M9 Context Length: %.2f characters", avg)
        LOGGER.info("     └─ Min: %s, Max: %s", min(lengths), max(lengths))
        LOGGER.info("%s", "─" * 60)
    return avg


# %%
def _tokenize_for_metrics(text: str) -> List[str]:
    """Shared tokenizer for BLEU, METEOR, GT Coverage — lowercase word tokens."""
    try:
        return nltk.word_tokenize(text.lower())
    except LookupError:
        return re.findall(r"[A-Za-z0-9']+", text.lower())


# %%
# M10 (BLEU)
def compute_bleu(per_query_results: List[Dict], verbose: bool = False) -> float:
    """
    M10: Average sentence-level BLEU (smoothed) between generated response
    and ground truth answer.

    Using per-sentence BLEU averaged across queries is more stable than
    corpus_bleu when the evaluation set is small (< 50 queries).
    Reference: ground_truth_answer field.
    """
    _ensure_nltk_resources()
    smoothie = SmoothingFunction().method4

    scores = []
    skipped = 0

    for item in per_query_results:
        response_text = item.get("response_text", "").strip()
        reference_text = item.get("reference_text", "").strip()

        if not response_text or not reference_text:
            skipped += 1
            continue

        try:
            ref_tokens = _tokenize_for_metrics(reference_text)
            hyp_tokens = _tokenize_for_metrics(response_text)
            if not ref_tokens or not hyp_tokens:
                skipped += 1
                continue
            score = sentence_bleu(
                [ref_tokens], hyp_tokens,
                smoothing_function=smoothie
            )
            scores.append(float(score))
        except Exception as e:
            if verbose:
                LOGGER.info("  M10 BLEU tokenization failed: %s", e)
            skipped += 1

    avg_score = statistics.mean(scores) if scores else 0.0

    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M10 BLEU: %.4f", avg_score)
        LOGGER.info("       Evaluated: %s, Skipped: %s", len(scores), skipped)
        LOGGER.info("%s", "─" * 60)
    return avg_score


# %%
# M11 (METEOR)
def compute_meteor(per_query_results: List[Dict], verbose: bool = False) -> float:
    """
    M11: METEOR between generated response and ground truth answer.
    Handles synonyms and stemming — better semantic correctness than BLEU.
    Reference: ground_truth_answer field.
    """
    _ensure_nltk_resources()

    scores = []
    skipped = 0

    for item in per_query_results:
        response_text = item.get("response_text", "").strip()
        reference_text = item.get("reference_text", "").strip()

        if not response_text or not reference_text:
            skipped += 1
            continue

        try:
            ref_tokens = _tokenize_for_metrics(reference_text)
            hyp_tokens = _tokenize_for_metrics(response_text)
            if not ref_tokens or not hyp_tokens:
                skipped += 1
                continue
            # METEOR expects references as a list of token lists
            score = _nltk_meteor_score([ref_tokens], hyp_tokens)
            scores.append(float(score))
        except Exception as e:
            if verbose:
                LOGGER.info("  M11 METEOR failed for item: %s", e)
            skipped += 1

    avg_score = statistics.mean(scores) if scores else 0.0

    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M11 METEOR: %.4f", avg_score)
        LOGGER.info("       Evaluated: %s, Skipped: %s", len(scores), skipped)
        LOGGER.info("%s", "─" * 60)
    return avg_score


# %%
# M12 (BERTScore)
def compute_bertscore(per_query_results: List[Dict], lang: str = "en", verbose: bool = False) -> float:
    """
    M12: BERTScore F1 between generated response and ground truth answer.
    Uses contextual embeddings (roberta-large by default) for deep semantic
    similarity to the reference text.

    Reference target: ground_truth_answer field.
    This is intentionally different from M13 (FCD), which compares the
    response against the retrieved context rather than ground truth.
    Both metrics appear in the same report — M12 measures answer quality
    relative to the known correct answer; M13 measures how closely the
    response stays grounded in what was retrieved.

    Install: pip install bert-score
    """
    try:
        with suppress_output(enabled=not verbose):
            from bert_score import score as _bert_score_fn
    except ImportError:
        if verbose:
            LOGGER.info("  M12 BERTScore: optional dependency missing (pip install bert-score).")
        return 0.0

    candidates = []
    references = []
    skipped = 0

    for item in per_query_results:
        response_text = item.get("response_text", "").strip()
        reference_text = item.get("reference_text", "").strip()

        if not response_text or not reference_text:
            skipped += 1
            continue

        candidates.append(response_text)
        references.append(reference_text)

    if not candidates:
        if verbose:
            LOGGER.info("")
            LOGGER.info("%s", "─" * 60)
            LOGGER.info("  M12 BERTScore (F1): 0.0000  (no ground truth available)")
            LOGGER.info("%s", "─" * 60)
        return 0.0

    try:
        import torch as _torch
        # Explicitly pin BERTScore to the same device used for text
        # embeddings. When both GPU and CPU models coexist in the same
        # process, leaving BERTScore to auto-select can cause a device
        # mismatch or an OOM if two large models simultaneously claim GPU.
        _device = "cuda" if _torch.cuda.is_available() else "cpu"
        with suppress_output(enabled=not verbose):
            _P, _R, F1 = _bert_score_fn(
                candidates, references, lang=lang, verbose=False, device=_device
            )
            avg_f1 = float(F1.mean().item())
    except Exception as e:
        if verbose:
            LOGGER.info("  M12 BERTScore computation failed: %s", e)
        return 0.0

    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M12 BERTScore (F1): %.4f", avg_f1)
        LOGGER.info("       Evaluated: %s, Skipped: %s", len(candidates), skipped)
        LOGGER.info("%s", "─" * 60)
    return avg_f1


# %%
# M13 (FCD — Factual Consistency Distance, aggregate)
def compute_fcd(per_query_results: List[Dict], verbose: bool = False) -> float:
    """
    M13: Average Factual Consistency Distance across all queries.
    Reads the per-query FCD already computed in generate_response.
    Lower is better (response is more grounded in context).

    Reference target: retrieved context (via the LLM prompt), NOT ground truth.
    This differs from M12 (BERTScore), which compares against the ground truth
    answer.  Both appear side-by-side in reports and measure complementary
    properties: M12 checks answer correctness; M13 checks context grounding.
    """
    values = []
    for r in per_query_results:
        resp = r.get("response")
        if isinstance(resp, dict):
            fcd_val = resp.get("factual_consistency_distance")
            if fcd_val is not None:
                values.append(float(fcd_val))

    avg = statistics.mean(values) if values else 0.0
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M13 FCD (Factual Consistency Distance): %.2f", avg)
        LOGGER.info("       Evaluated: %s queries", len(values))
        LOGGER.info("%s", "─" * 60)
    return avg


# %%
def _split_sentences(text: str) -> List[str]:
    """
    Split text into non-empty sentences using punctuation boundaries.

    Prefers NLTK's punkt tokenizer when available for better handling of
    abbreviations (e.g. "NASA", "ca.") that should not split sentences.
    Falls back to a simple regex split so the function is always usable.
    """
    try:
        _ensure_nltk_resources()
        sentences = nltk.sent_tokenize(text)
    except Exception:
        sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]


def _faithfulness_term_overlap(
        response_sentences: List[str],
        context: str,
        stop_words: set,
        threshold: float = 0.30,
) -> float:
    """
    Fallback faithfulness via bag-of-words term overlap.

    Used only when the NLI cross-encoder cannot be loaded.  A sentence is
    grounded when at least `threshold` fraction of its content terms appear
    anywhere in the context vocabulary.
    """
    context_terms = {
        tok for tok in re.findall(r'\b[A-Za-z]{3,}\b', context.lower())
        if tok not in stop_words
    }
    if not context_terms:
        return 0.0

    grounded = 0
    countable = 0
    for sent in response_sentences:
        sent_terms = {
            tok for tok in re.findall(r'\b[A-Za-z]{3,}\b', sent.lower())
            if tok not in stop_words
        }
        if not sent_terms:
            continue
        countable += 1
        if len(sent_terms & context_terms) / len(sent_terms) >= threshold:
            grounded += 1

    return (grounded / countable) * 100.0 if countable > 0 else 0.0


def compute_faithfulness(
        per_query_results: List[Dict],
        verbose: bool = False,
) -> float:
    """
    M14: Faithfulness — percentage of response sentences supported by the
    retrieved context, evaluated via term overlap.

    Each response sentence is tokenized into content terms (alphabetic tokens
    of length ≥3, excluding stopwords). A sentence is considered grounded when
    at least 30% of its content terms appear in the retrieved context
    vocabulary. The metric aggregates the grounded-sentence ratio across all
    queries.

    Formula: (grounded_sentences / total_sentences) * 100
    Higher values indicate stronger grounding to retrieved context and lower
    hallucination risk. Reference target is the retrieved context.
    """
    stop_words = get_stopword_set()
    all_scores: List[float] = []
    skipped = 0

    for item in per_query_results:
        context = item.get("context", "").strip()
        response = item.get("response_text", "").strip()

        if not context or not response:
            skipped += 1
            continue

        sentences = _split_sentences(response)
        if not sentences:
            skipped += 1
            continue

        score = _faithfulness_term_overlap(sentences, context, stop_words)
        all_scores.append(score)

    avg_score = statistics.mean(all_scores) if all_scores else 0.0

    if verbose:
        LOGGER.info("Faithfulness (term-overlap): %.2f%% (n=%d, skipped=%d)", avg_score, len(all_scores), skipped)

    return avg_score


def _gt_coverage_semantic(
        gt_sentences: List[str],
        response_sentences: List[str],
        embedder,
        sim_threshold: float = 0.60,
) -> float:
    """
    Compute GT Coverage by checking whether each ground-truth sentence is
    semantically matched by at least one response sentence.

    Uses the shared text embedder so no additional model download is required.
    A GT sentence is 'covered' when its maximum cosine similarity to any
    response sentence meets or exceeds `sim_threshold`.

    Embedding-based coverage catches paraphrases that pure BoW misses:
    "spacecraft" and "probe" are synonyms that share zero word-overlap but
    will have high cosine similarity in a well-trained embedding space.

    Returns:
        float in [0, 100] — percentage of GT sentences that are covered.
    """
    from sentence_transformers import util as _st_util

    if not gt_sentences or not response_sentences:
        return 0.0

    try:
        gt_embs = np.array([embedder.encode_text(s) for s in gt_sentences])
        resp_embs = np.array([embedder.encode_text(s) for s in response_sentences])
    except Exception as e:
        LOGGER.warning("GT Coverage embedding failed: %s", e)
        return 0.0

    # sim_matrix shape: (n_gt, n_resp)
    gt_tensor = _st_util.pytorch_cos_sim(gt_embs, resp_embs)
    max_sims = gt_tensor.max(dim=1).values.cpu().numpy()
    covered = int(np.sum(max_sims >= sim_threshold))
    return (covered / len(gt_sentences)) * 100.0


def _gt_coverage_term_frequency(
        reference_text: str,
        response_text: str,
        stop_words: set,
) -> float:
    """
    Fallback GT Coverage via multiset (Counter) term-frequency intersection.

    Used only when no text embedder is available.  Multiset intersection
    gives credit proportional to how many times each term appears in the
    ground truth, preventing single rare terms from dominating the score.
    """
    gt_tokens = [
        tok for tok in re.findall(r'\b[A-Za-z]{3,}\b', reference_text.lower())
        if tok not in stop_words
    ]
    resp_tokens = [
        tok for tok in re.findall(r'\b[A-Za-z]{3,}\b', response_text.lower())
        if tok not in stop_words
    ]
    if not gt_tokens:
        return 0.0

    gt_counter = Counter(gt_tokens)
    resp_counter = Counter(resp_tokens)
    matched = sum((gt_counter & resp_counter).values())
    return (matched / sum(gt_counter.values())) * 100.0


def compute_gt_coverage(
        per_query_results: List[Dict],
        text_embedder=None,
        sim_threshold: float = 0.60,
        verbose: bool = False,
) -> float:
    """
    M15: GT Coverage — fraction of ground-truth sentences whose semantic
    content is present in the LLM response.

    Evaluated sentence-by-sentence using cosine similarity between
    sentence embeddings, so paraphrases and synonyms (e.g. "spacecraft" vs
    "probe") are correctly credited unlike a pure bag-of-words approach.

    A ground-truth sentence is considered 'covered' when its maximum cosine
    similarity to any response sentence reaches `sim_threshold` (default 0.60).
    This threshold was chosen to accept clear paraphrases while rejecting
    only vaguely topically related statements.

    When `text_embedder` is None the function falls back to multiset
    term-frequency intersection (the original BoW approach) so the pipeline
    can still run without an embedder reference.

    Reference target: ground_truth_answer field (falls back to context when
    ground truth is absent, e.g. during retrieval-only evaluation).
    """
    stop_words = get_stopword_set()
    coverage_scores: List[float] = []
    skipped = 0
    gt_available_count = 0
    method = "semantic-embedding" if text_embedder is not None else "term-frequency"

    for item in per_query_results:
        reference_text = item.get("reference_text", "").strip()
        response = item.get("response_text", "").strip()

        using_gt = bool(reference_text)
        if using_gt:
            gt_available_count += 1
        else:
            reference_text = item.get("context", "").strip()

        if not reference_text or not response:
            skipped += 1
            continue

        if text_embedder is not None:
            gt_sents = _split_sentences(reference_text)
            resp_sents = _split_sentences(response)
            if not gt_sents or not resp_sents:
                skipped += 1
                continue
            score = _gt_coverage_semantic(gt_sents, resp_sents, text_embedder, sim_threshold)
        else:
            score = _gt_coverage_term_frequency(reference_text, response, stop_words)

        coverage_scores.append(score)

    avg_coverage = statistics.mean(coverage_scores) if coverage_scores else 0.0
    majority_had_gt = gt_available_count > (len(per_query_results) // 2)
    label = "GT Coverage" if majority_had_gt else "Context Utilization (fallback)"

    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M15 %s [%s]: %.2f%%", label, method, avg_coverage)
        LOGGER.info("       Evaluated: %s, Skipped: %s", len(coverage_scores), skipped)
        LOGGER.info("%s", "─" * 60)
    return avg_coverage


# %%
# M16 (Query to response time)
def compute_e2e_latency(per_query_results: List[Dict], verbose: bool = False) -> float:
    times = [r.get("e2e_latency_sec", 0.0) for r in per_query_results]
    times = [t for t in times if t > 0]
    avg = statistics.mean(times) if times else 0.0
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M16 E2E Latency: %.4f seconds", avg)
        if times:
            LOGGER.info("     └─ Min: %.4fs, Max: %.4fs", min(times), max(times))
        LOGGER.info("%s", "─" * 60)
    return avg


# %%
# M17 (Queries processed per second)
def compute_throughput(per_query_results: List[Dict], verbose: bool = False) -> float:
    times = [r.get("e2e_latency_sec", 0.0) for r in per_query_results]
    times = [t for t in times if t > 0]
    total_time = sum(times)
    tp = (len(times) / total_time) if total_time > 0 else 0.0
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M17 Throughput: %.3f queries per second", tp)
        LOGGER.info("%s", "─" * 60)
    return tp


# %%
# M18 (CPU usage)
def compute_cpu_usage(per_query_results: List[Dict], verbose: bool = False) -> float:
    values = [float(r.get("avg_cpu_percent", 0.0) or 0.0) for r in per_query_results]
    avg = statistics.mean(values) if values else 0.0
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M18 CPU Usage: %.2f%%", avg)
        if values:
            peak_values = [float(r.get("peak_cpu_percent", 0.0) or 0.0) for r in per_query_results]
            LOGGER.info("     └─ Avg range: %.1f%% - %.1f%% | Peak max: %.1f%%",
                        min(values), max(values), max(peak_values) if peak_values else 0.0)
        LOGGER.info("%s", "─" * 60)
    return avg


# %%
# M19 (RAM usage)
def compute_ram_usage(per_query_results: List[Dict], verbose: bool = False) -> float:
    values = [float(r.get("avg_ram_gb", 0.0) or 0.0) for r in per_query_results]
    avg = statistics.mean(values) if values else 0.0
    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  M19 RAM Usage: %.3f GB", avg)
        if values:
            peak_values = [float(r.get("peak_ram_gb", 0.0) or 0.0) for r in per_query_results]
            delta_values = [float(r.get("ram_delta_gb", 0.0) or 0.0) for r in per_query_results]
            LOGGER.info("     └─ Avg range: %.3fGB - %.3fGB | Peak max: %.3fGB | Avg Δ: %.3fGB",
                        min(values), max(values), max(peak_values) if peak_values else 0.0,
                        statistics.mean(delta_values) if delta_values else 0.0)
        LOGGER.info("%s", "─" * 60)
    return avg


# %%
# GPU Usage
def compute_gpu_usage(per_query_results: List[Dict], verbose: bool = False) -> float:
    util_values = [r.get("avg_gpu_percent", 0.0) for r in per_query_results]
    avg_util = statistics.mean(util_values) if util_values else 0.0
    peak_util = max((r.get("peak_gpu_percent", 0.0) for r in per_query_results), default=0.0)
    active_values = [r.get("avg_gpu_active_percent", 0.0) for r in per_query_results]
    avg_active = statistics.mean(active_values) if active_values else 0.0
    duty_values = [r.get("gpu_duty_cycle", 0.0) for r in per_query_results]
    avg_duty = statistics.mean(duty_values) if duty_values else 0.0

    vram_values = [r.get("avg_vram_gb", 0.0) for r in per_query_results]
    avg_vram = statistics.mean(vram_values) if any(v > 0 for v in vram_values) else 0.0
    peak_vram = max((r.get("peak_vram_gb", 0.0) for r in per_query_results), default=0.0)

    if verbose:
        LOGGER.info("")
        LOGGER.info("%s", "─" * 60)
        LOGGER.info("  GPU Utilisation Avg : %.2f%%  |  Peak: %.2f%%", avg_util, peak_util)
        LOGGER.info("  GPU Active Avg      : %.2f%%  |  Duty Cycle: %.1f%%", avg_active, avg_duty)
        LOGGER.info("  VRAM Used Avg       : %.2f GB  |  Peak: %.2f GB", avg_vram, peak_vram)
        if util_values:
            LOGGER.info(
                "     └─ Per-query util range — Min: %.1f%%, Max: %.1f%%",
                min(util_values), max(util_values),
            )
        LOGGER.info("%s", "─" * 60)
    return avg_util


# %%
def compute_hybrid_stats(per_query_results: List[Dict]) -> Dict:
    """Calculate aggregate statistics for hybrid search performance."""
    hybrid_data = []
    for item in per_query_results:
        metrics = item.get("result", {}).get("retrieval_metrics", {})
        if "hybrid_stats" in metrics and metrics["hybrid_stats"]:
            hybrid_data.append(metrics["hybrid_stats"])
    if not hybrid_data:
        return {"avg_bm25_weight": 0.0, "bm25_weight_std": 0.0, "bm25_weight_min": 0.0, "bm25_weight_max": 0.0,
                "weight_adjusted_queries": 0, "keyword_queries": 0, "semantic_queries": 0, "balanced_queries": 0,
                "avg_bm25_max_score": 0.0, "avg_overlap_jaccard": 0.0, "fallback_to_semantic": 0}
    bm25_weights = [float(d["bm25_weight_used"]) for d in hybrid_data]
    query_types = [d["query_type"] for d in hybrid_data]
    max_scores = [d["bm25_max_score"] for d in hybrid_data]
    overlaps = [d["overlap_jaccard"] for d in hybrid_data]
    fallbacks = sum(1 for d in hybrid_data if d.get("fallback_to_semantic", False))
    adjusted_queries = sum(1 for d in hybrid_data if d.get("weight_adjusted", False))
    return {
        "avg_bm25_weight": statistics.mean(bm25_weights),
        "avg_semantic_weight": statistics.mean([float(d["semantic_weight_used"]) for d in hybrid_data]),
        "bm25_weight_std": statistics.stdev(bm25_weights) if len(bm25_weights) > 1 else 0.0,
        "bm25_weight_min": min(bm25_weights),
        "bm25_weight_max": max(bm25_weights),
        "weight_adjusted_queries": adjusted_queries,
        "keyword_queries": query_types.count("keyword"),
        "semantic_queries": query_types.count("semantic"),
        "balanced_queries": query_types.count("balanced"),
        "avg_bm25_max_score": statistics.mean(max_scores),
        "avg_bm25_std": statistics.mean([d["bm25_std_score"] for d in hybrid_data]),
        "avg_corpus_coverage": statistics.mean([d["bm25_corpus_coverage"] for d in hybrid_data]) * 100,
        "avg_bm25_signal_strength": statistics.mean([d.get("bm25_signal_strength", 0.0) for d in hybrid_data]) * 100,
        "avg_overlap_jaccard": statistics.mean(overlaps),
        "avg_overlap_percentage": statistics.mean([d["overlap_percentage"] for d in hybrid_data]),
        "fallback_to_semantic": fallbacks,
        "fallback_percentage": (fallbacks / len(hybrid_data)) * 100
    }


# %%
def print_hybrid_metrics_summary(stats: Dict):
    """Print formatted hybrid search metrics."""
    print(f"\n  {'─' * 78}")
    print(f"  {'HYBRID SEARCH STATISTICS':^76}")
    print(f"  {'─' * 78}")
    print(f"  {'Query Classification:':<40}")
    print(f"    • Keyword-heavy queries:  {stats['keyword_queries']}")
    print(f"    • Semantic queries:       {stats['semantic_queries']}")
    print(f"    • Balanced queries:       {stats['balanced_queries']}")
    print(f"  {'─' * 78}")
    print(f"  {'Adaptive Fusion Weights:':<40}")
    print(f"    • Avg BM25 weight:        {stats['avg_bm25_weight']:.2f}")
    print(f"    • Avg Semantic weight:    {stats['avg_semantic_weight']:.2f}")
    print(f"    • BM25 weight range:      {stats['bm25_weight_min']:.2f} - {stats['bm25_weight_max']:.2f}")
    print(f"    • BM25 weight std dev:    {stats['bm25_weight_std']:.3f}")
    print(f"    • Queries with adjusted weights: {stats['weight_adjusted_queries']}")
    print(f"  {'─' * 78}")
    print(f"  {'BM25 Signal Quality:':<40}")
    print(f"    • Avg max BM25 score:     {stats['avg_bm25_max_score']:.2f}")
    print(f"    • Avg score std dev:      {stats['avg_bm25_std']:.2f}")
    print(f"    • Avg corpus coverage:    {stats['avg_corpus_coverage']:.1f}%")
    print(f"    • Avg BM25 signal strength: {stats['avg_bm25_signal_strength']:.1f}%")
    print(f"  {'─' * 78}")
    print(f"  {'BM25-Semantic Alignment:':<40}")
    print(f"    • Avg Jaccard overlap:    {stats['avg_overlap_jaccard']:.3f}")
    print(f"    • Avg overlap percentage: {stats['avg_overlap_percentage']:.1f}%")
    print(f"  {'─' * 78}")
    print(f"  {'Fallback Statistics:':<40}")
    print(f"    • Semantic fallbacks:     {stats['fallback_to_semantic']} ({stats['fallback_percentage']:.1f}%)")
    print(f"  {'─' * 78}")


# %%
def compute_fusion_effectiveness(per_query_results: List[Dict]) -> Dict:
    """
    Measure how often the final fused ranking contains evidence supported by both signals.
    This is signal-agreement, not direct answer-quality lift.
    """
    agreement_scores = []
    for item in per_query_results:
        metrics = item.get("result", {}).get("retrieval_metrics", {})
        hybrid = metrics.get("hybrid_stats", {})
        if hybrid and "fusion_stats" in hybrid:
            stats = hybrid["fusion_stats"]
            both = stats.get("both_signals_docs", 0)
            total = both + stats.get("bm25_only_docs", 0) + stats.get("semantic_only_docs", 0)
            if total > 0:
                agreement_scores.append((both / total) * 100)
    if not agreement_scores:
        return {"avg_signal_agreement": 0.0, "mixed_signal_queries": 0, "agreement_std": 0.0}
    return {
        "avg_signal_agreement": statistics.mean(agreement_scores),
        "mixed_signal_queries": sum(1 for score in agreement_scores if score > 30),
        "agreement_std": statistics.stdev(agreement_scores) if len(agreement_scores) > 1 else 0.0
    }


# %%
try:
    import pynvml as pynvml

    _PYNVML_AVAILABLE = True
except ImportError:
    pynvml = None  # type: ignore
    _PYNVML_AVAILABLE = False
    print("  Note: pynvml not found — GPU monitoring disabled. Install with: pip install pynvml")

# psutil.Process.cpu_percent() accumulates usage across all logical cores,
# so on a 16-core machine a single fully-utilised core returns 100/16 ≈ 6.25%
# while two fully-utilised cores return ~12.5%, and so on up to a theoretical
# maximum of cpu_count × 100%. Dividing by cpu_count normalises the value to
# the conventional 0-100% single-process scale used in system monitors.
_CPU_COUNT: int = max(os.cpu_count() or 1, 1)


# %%
def llm_response(llm, formatted_output, test_questions, stream: bool = True):
    response_output = []
    monitor = ResourceMonitor()

    gpu_handle = None
    if _PYNVML_AVAILABLE:
        try:
            pynvml.nvmlInit()
            gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except pynvml.NVMLError as e:
            LOGGER.warning("GPU monitoring unavailable: %s", e)
        except Exception as e:
            LOGGER.warning("Unexpected GPU monitor initialisation error: %s", e)

    # Build lookup tables that survive any reordering of formatted_output.
    # Keying by question text avoids positional assumptions between the two lists.
    gt_map = {
        q.get("id"): q.get("ground_truth_answer", "")
        for q in test_questions
        if q.get("id") is not None
    }
    query_to_question: Dict[str, Dict] = {
        q.get("question", ""): q for q in test_questions if q.get("question")
    }

    print(f"\n{'=' * 100}")
    print(f"  RUNNING MODEL: {llm.model_name}")
    print(f"{'=' * 100}")

    for idx, output in enumerate(formatted_output, 1):
        query = output.get("query", "")
        text_context = output.get("text_context", "")
        images = output.get("images", [])

        if images:
            captions = [f"[Image {i + 1} Caption] {img.get('caption', '')}"
                        for i, img in enumerate(images) if img.get('caption')]
            if captions:
                text_context += "\n\n[Image Captions]\n" + "\n".join(captions)

        # Resolve the matching test question by query text; fall back to the
        # positional index only when the query string is absent or unrecognised.
        q_data = query_to_question.get(query)
        if q_data is None:
            q_data = test_questions[idx - 1] if idx <= len(test_questions) else {}
        qid = q_data.get("id")
        ground_truth = gt_map.get(qid, "")

        print(f"\n  QUERY #{idx}/{len(formatted_output)}")
        print(f"  {'─' * 96}")
        print(f"  Context: {len(text_context):,} chars | Images: {len(images)}")
        if ground_truth:
            print(f"  Ground Truth: available ({len(ground_truth)} chars)")

        proc_mon = monitor.start_process_monitor(interval=0.05)
        gpu_mon = monitor.start_gpu_monitor(gpu_handle, interval=0.05)
        start_total = time.perf_counter()

        response_dict = llm.generate_response(
            query=query,
            context=text_context,
            images=images,
            stream=stream,
            max_tokens=cfg.llm_max_tokens,
            temperature=cfg.llm_temperature
        )

        proc_stats = monitor.stop_process_monitor(proc_mon)
        gpu_stats = monitor.stop_gpu_monitor(gpu_mon)
        e2e_latency = round(time.perf_counter() - start_total, 4)

        cpu_usage = proc_stats["avg_cpu"]
        peak_cpu = proc_stats["peak_cpu"]
        ram_usage = proc_stats["avg_ram_gb"]
        peak_ram = proc_stats["peak_ram_gb"]
        ram_delta = proc_stats["ram_delta_gb"]
        avg_gpu = gpu_stats["avg_gpu"]
        peak_gpu = gpu_stats["peak_gpu"]
        avg_gpu_active = gpu_stats["avg_gpu_active"]
        duty_cycle = gpu_stats["gpu_duty_cycle"]
        avg_vram = gpu_stats["avg_vram_gb"]
        peak_vram = gpu_stats["peak_vram_gb"]

        full_text = response_dict.get("response", "") if isinstance(response_dict, dict) else str(response_dict)

        print(f"\n  {'─' * 96}")
        print(f"  METRICS:")
        print(f"       Inference Time : {response_dict.get('generation_time_sec', 0):.4f} s")
        print(f"       E2E Latency    : {e2e_latency:.4f} s")
        print(f"       CPU Usage      : {cpu_usage:.1f}%   Peak: {peak_cpu:.1f}%")
        print(f"       RAM Usage      : {ram_usage:.3f} GB  Peak: {peak_ram:.3f} GB  Δ: {ram_delta:.3f} GB")
        print(f"       GPU Util Avg   : {avg_gpu:.1f}%   Peak: {peak_gpu:.1f}%")
        print(f"       GPU Active Avg : {avg_gpu_active:.1f}%   Duty: {duty_cycle:.1f}%")
        print(f"       VRAM Used      : {avg_vram:.2f} GB  Peak: {peak_vram:.2f} GB")
        print(f"       GPU Samples    : {gpu_stats['sample_count']} (active {gpu_stats['active_samples']})")
        print(f"  {'─' * 96}")

        response_output.append({
            "id": qid if qid is not None else idx,
            "query": query,
            "context": text_context,
            "reference_text": ground_truth,
            "response": response_dict,
            "response_text": full_text,
            "images": images,
            "inference_time_sec": response_dict.get("generation_time_sec", 0),
            "e2e_latency_sec": e2e_latency,
            "avg_cpu_percent": round(cpu_usage, 2),
            "peak_cpu_percent": round(peak_cpu, 2),
            "avg_ram_gb": round(ram_usage, 4),
            "peak_ram_gb": round(peak_ram, 4),
            "ram_delta_gb": round(ram_delta, 4),
            "process_sample_count": proc_stats["sample_count"],
            "avg_gpu_percent": round(avg_gpu, 2),
            "peak_gpu_percent": round(peak_gpu, 2),
            "avg_gpu_active_percent": round(avg_gpu_active, 2),
            "gpu_duty_cycle": round(duty_cycle, 2),
            "avg_vram_gb": round(avg_vram, 2),
            "peak_vram_gb": round(peak_vram, 2),
            "num_images": len(images),
        })

    print(f"\n{'=' * 100}")
    print(f"  COMPLETED MODEL: {llm.model_name}")
    print(f"{'=' * 100}")

    if gpu_handle is not None and _PYNVML_AVAILABLE:
        try:
            pynvml.nvmlShutdown()
        except pynvml.NVMLError:
            pass

    return response_output


# %%
import io
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from xml.sax.saxutils import escape as _xml_escape

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Image as RLImage,
    KeepTogether,
    HRFlowable,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch


# %%
def active_retrieval_config_summary(html: bool = False) -> str:
    """Return a report-safe summary that only shows active retrieval features."""
    retrieval = getattr(cfg, "retrieval_mode", "semantic")
    parts = [
        f"Retrieval: {retrieval}",
        f"Reranker: {'ON' if cfg.use_reranker else 'OFF'}",
        f"Adaptive: {'ON' if cfg.adaptive_weighting else 'OFF'}",
    ]

    if retrieval == "hybrid":
        parts.append(f"Fusion: {'Weighted Sum' if cfg.use_weighted_fusion else 'RRF'}")
        # When adaptive weighting is active the per-query weights vary;
        # showing the static base weights alongside an ADAPTIVE marker
        # avoids the misleading impression that fusion always uses 0.4/0.6.
        if cfg.adaptive_weighting:
            parts.append(
                f"Weights: {cfg.bm25_weight}/{cfg.semantic_weight} (ADAPTIVE)"
            )
        else:
            parts.append(f"Weights: {cfg.bm25_weight}/{cfg.semantic_weight}")
    else:
        parts.append("Fusion: N/A")
        parts.append("Weights: N/A")

    parts.append(f"Filtering: {'ON' if cfg.use_filtering else 'OFF'}")
    if cfg.use_filtering:
        percentile = f", percentile={cfg.percentile_cutoff}" if cfg.use_percentile_filtering else ""
        # Use explicit format spec to guarantee a leading zero is always
        # printed (e.g. 1.00 not .0) so PDF font rendering cannot clip
        # the integer part at narrow column widths.
        parts.append(
            f"Thresholds: text={cfg.text_distance_threshold:.2f},"
            f" image={cfg.image_distance_threshold:.2f}{percentile}"
        )

    parts.append(f"Cross-modal boost: {'ON' if cfg.use_cross_modal_boost else 'OFF'}")
    parts.append(f"text_k={cfg.text_k}, rerank_k={cfg.rerank_k}, image_k={cfg.image_k}")

    separator = "<br/>" if html else " | "
    return separator.join(parts)


# ---------------------------------------------------------------------------
# Shared PDF design constants & helpers
# ---------------------------------------------------------------------------
_PDF_ACCENT      = colors.HexColor("#1a3f6f")   # dark navy — titles / section bars
_PDF_ACCENT_LIGHT= colors.HexColor("#dce8f5")   # pale blue  — header fills
_PDF_SEP         = colors.HexColor("#b0b8c8")   # muted steel — grid lines / rules
_PDF_GREEN       = colors.HexColor("#1a6b3c")   # metric value colour
_PDF_SECTION_BG  = colors.HexColor("#f0f4fa")   # section-header row background
_PDF_ROW_ALT     = colors.HexColor("#f7f9fc")   # alternating row tint

def _pdf_styles():
    """Return a dict of named ParagraphStyles used across all export functions."""
    base = getSampleStyleSheet()
    def _make(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    return {
        "title":   _make("rTitle",   fontSize=18, leading=22, textColor=_PDF_ACCENT,
                         spaceAfter=4, fontName="Helvetica-Bold"),
        "subtitle":_make("rSubtitle",fontSize=10, leading=13, textColor=colors.HexColor("#444444"),
                         spaceAfter=2),
        "h2":      _make("rH2",      fontSize=12, leading=15, textColor=_PDF_ACCENT,
                         fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=4),
        "h3":      _make("rH3",      fontSize=10, leading=13, textColor=_PDF_ACCENT,
                         fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=3),
        "body":    _make("rBody",    fontSize=9.5, leading=14, spaceAfter=6, wordWrap="CJK"),
        "caption": _make("rCaption", fontSize=8.5, leading=11, textColor=colors.grey,
                         alignment=1, spaceAfter=8),
        "metric":  _make("rMetric",  fontSize=9, leading=12, textColor=_PDF_GREEN,
                         spaceAfter=3, wordWrap="CJK"),
        "mono":    _make("rMono",    fontSize=8.5, leading=12, fontName="Courier",
                         spaceAfter=3, wordWrap="CJK"),
    }


def _config_table(styles: dict) -> Table:
    """
    Build a two-column Table that shows the active pipeline configuration.
    Reads directly from the global cfg object so it is always current.
    """
    retrieval = getattr(cfg, "retrieval_mode", "semantic")

    rows = [
        [Paragraph("<b>Configuration</b>", styles["h3"]), ""],
        ["Retrieval mode",    retrieval],
        ["Reranker",          "ON" if cfg.use_reranker else "OFF"],
        ["Adaptive weights",  "ON" if cfg.adaptive_weighting else "OFF"],
        ["Fusion strategy",   ("Weighted Sum" if cfg.use_weighted_fusion else "RRF")
                               if retrieval == "hybrid" else "N/A"],
        ["BM25 / Sem weights",
         f"{cfg.bm25_weight} / {cfg.semantic_weight} {'(ADAPTIVE)' if cfg.adaptive_weighting else ''}"
         if retrieval == "hybrid" else "N/A"],
        ["Filtering",         "ON" if cfg.use_filtering else "OFF"],
    ]
    if cfg.use_filtering:
        pct = f", percentile={cfg.percentile_cutoff}" if cfg.use_percentile_filtering else ""
        rows.append(["  Distance thresholds",
                      f"text={cfg.text_distance_threshold:.2f}  "
                      f"image={cfg.image_distance_threshold:.2f}{pct}"])
    rows += [
        ["Cross-modal boost",  "ON" if cfg.use_cross_modal_boost else "OFF"],
        ["text_k / rerank_k / image_k",
         f"{cfg.text_k} / {cfg.rerank_k} / {cfg.image_k}"],
        ["Embed model",        cfg.text_embed_model],
        ["Image embed model",  f"{cfg.image_embed_model} ({cfg.image_embed_pretrained})"],
        ["LLM model(s)",       ", ".join(cfg.llm_models)],
        ["Chunk tokens / overlap",
         f"{cfg.chunk_max_tokens} / {cfg.chunk_overlap_tokens}"],
    ]

    label_style = ParagraphStyle("cfgLabel", parent=styles["body"],
                                 fontName="Helvetica-Bold", fontSize=8.5)
    value_style = ParagraphStyle("cfgValue", parent=styles["body"], fontSize=8.5)

    tdata = []
    for label, value in rows:
        if isinstance(label, Paragraph):          # section heading spanning both columns
            tdata.append([label, ""])
        else:
            tdata.append([
                Paragraph(str(label), label_style),
                Paragraph(str(value), value_style),
            ])

    col_w = [160, 310]
    t = Table(tdata, colWidths=col_w)
    # Build per-row style commands
    style_cmds = [
        ("BACKGROUND",   (0, 0), (-1, 0),  _PDF_ACCENT_LIGHT),
        ("SPAN",         (0, 0), (-1, 0)),
        ("TOPPADDING",   (0, 0), (-1, 0),  4),
        ("BOTTOMPADDING",(0, 0), (-1, 0),  4),
        ("LINEBELOW",    (0, 0), (-1, 0),  0.5, _PDF_SEP),
        ("GRID",         (0, 1), (-1, -1), 0.25, _PDF_SEP),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 3),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i in range(1, len(tdata)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), _PDF_ROW_ALT))
    t.setStyle(TableStyle(style_cmds))
    return t


def _metrics_table(rows_spec: list, styles: dict) -> Table:
    """
    Build a labelled metrics Table from a list of (section_label | (label, value, unit)) tuples.

    rows_spec entries:
      str                        → full-width section-header row
      (label, value, unit)       → metric row; value already formatted as string
    """
    label_style = ParagraphStyle("mLabel", parent=styles["body"],
                                 fontName="Helvetica-Bold", fontSize=8.5)
    value_style = ParagraphStyle("mValue", parent=styles["metric"], fontSize=8.5,
                                 alignment=2)   # right-align values
    unit_style  = ParagraphStyle("mUnit",  parent=styles["body"],
                                 textColor=colors.HexColor("#666666"), fontSize=8)
    sec_style   = ParagraphStyle("mSec",   parent=styles["body"],
                                 fontName="Helvetica-Bold", fontSize=9,
                                 textColor=_PDF_ACCENT)

    tdata = []
    for entry in rows_spec:
        if isinstance(entry, str):
            tdata.append([Paragraph(entry, sec_style), "", ""])
        else:
            label, value, unit = entry
            tdata.append([
                Paragraph(str(label), label_style),
                Paragraph(str(value), value_style),
                Paragraph(str(unit),  unit_style),
            ])

    col_w = [210, 100, 70]
    t = Table(tdata, colWidths=col_w)

    style_cmds = [
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW",    (0, -1), (-1, -1), 0.25, _PDF_SEP),
    ]
    section_rows = [i for i, e in enumerate(rows_spec) if isinstance(e, str)]
    for i in section_rows:
        style_cmds += [
            ("BACKGROUND", (0, i), (-1, i), _PDF_ACCENT_LIGHT),
            ("SPAN",       (0, i), (-1, i)),
            ("LINEABOVE",  (0, i), (-1, i), 0.5, _PDF_SEP),
            ("LINEBELOW",  (0, i), (-1, i), 0.5, _PDF_SEP),
            ("TOPPADDING", (0, i), (-1, i), 4),
            ("BOTTOMPADDING", (0, i), (-1, i), 4),
        ]
    # Alternating tint on non-section rows
    non_sec = [i for i in range(len(rows_spec)) if i not in section_rows]
    for j, i in enumerate(non_sec):
        if j % 2 == 1:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), _PDF_ROW_ALT))
    t.setStyle(TableStyle(style_cmds))
    return t


def _add_page_number(canvas_obj, doc):
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica", 8)
    canvas_obj.setFillColor(colors.HexColor("#888888"))
    canvas_obj.drawRightString(A4[0] - 36, 18, f"Page {doc.page}")
    canvas_obj.restoreState()


def _add_header_footer(canvas_obj, doc, title: str = "RAG Pipeline Report"):
    """Draw a thin accent rule under the header text on every page."""
    _add_page_number(canvas_obj, doc)
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica", 8)
    canvas_obj.setFillColor(colors.HexColor("#888888"))
    canvas_obj.drawString(36, A4[1] - 24, title)
    canvas_obj.setStrokeColor(_PDF_SEP)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(36, A4[1] - 28, A4[0] - 36, A4[1] - 28)
    canvas_obj.restoreState()


def _image_flowable(pil_img, max_w: float = 4.8 * inch,
                    max_h: float = 3.5 * inch) -> RLImage:
    scale = min(max_w / pil_img.size[0], max_h / pil_img.size[1], 1.0)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    buf.seek(0)
    rl = RLImage(buf)
    rl.drawWidth  = pil_img.size[0] * scale
    rl.drawHeight = pil_img.size[1] * scale
    rl.hAlign = "CENTER"
    return rl


# %%
def export_retrieved_results_to_pdf(formatted_output, output_dir=cfg.retrieval_results_dir):
    """
    Export per-query retrieval results (text context + images) to a formatted PDF.
    Each query gets its own page showing the query, retrieved text context, and any
    retrieved images with captions. A config panel on the cover page records the
    exact pipeline settings used.
    """
    if not formatted_output:
        LOGGER.warning("export_retrieved_results_to_pdf: no output to export.")
        return

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = output_dir / f"retrieval_results_{timestamp}.pdf"

    doc = SimpleDocTemplate(
        str(filename), pagesize=A4,
        rightMargin=45, leftMargin=45, topMargin=52, bottomMargin=45,
    )
    S = _pdf_styles()
    elements = []

    # ── Cover page ────────────────────────────────────────────────────────
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph("RAG Pipeline", S["subtitle"]))
    elements.append(Paragraph("Retrieval Results Report", S["title"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}  ·  "
        f"Total queries: {len(formatted_output)}",
        S["subtitle"],
    ))
    elements.append(Spacer(1, 0.25 * inch))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=_PDF_ACCENT))
    elements.append(Spacer(1, 0.2 * inch))

    # Config panel
    elements.append(Paragraph("Pipeline Configuration", S["h2"]))
    elements.append(Spacer(1, 4))
    elements.append(_config_table(S))
    elements.append(PageBreak())

    # ── Per-query pages ───────────────────────────────────────────────────
    for idx, item in enumerate(formatted_output, 1):
        query        = _xml_escape(item.get("query", "") or "")
        text_context = _xml_escape(item.get("text_context", "") or "").replace("\n", "<br/>")
        images       = item.get("images", [])

        elements.append(Paragraph(f"Query {idx} of {len(formatted_output)}", S["h2"]))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=_PDF_SEP))
        elements.append(Spacer(1, 6))

        elements.append(Paragraph("User Query", S["h3"]))
        elements.append(Paragraph(query, S["body"]))
        elements.append(Spacer(1, 6))

        elements.append(Paragraph("Retrieved Text Context", S["h3"]))
        elements.append(Paragraph(text_context or "<i>(none)</i>", S["body"]))

        if images:
            img_section = [
                Spacer(1, 0.25 * inch),
                Paragraph("Retrieved Images", S["h3"]),
                Spacer(1, 4),
            ]
            for img_obj in images:
                pil_img = img_obj.get("image")
                caption = _xml_escape(img_obj.get("caption", "") or "")
                if pil_img is None:
                    continue
                img_section.append(_image_flowable(pil_img))
                if caption:
                    img_section.append(Paragraph(f"<i>{caption}</i>", S["caption"]))
                img_section.append(Spacer(1, 0.25 * inch))
            elements.append(KeepTogether(img_section))

        if idx != len(formatted_output):
            elements.append(PageBreak())

    _title = "RAG Pipeline — Retrieval Results"
    doc.build(
        elements,
        onFirstPage=lambda c, d: _add_header_footer(c, d, _title),
        onLaterPages=lambda c, d: _add_header_footer(c, d, _title),
    )
    LOGGER.info("  [OK] Saved retrieval report: %s", filename)


# %%
def export_results_to_pdf(results, model_name: str, metrics_summary: dict, output_dir=cfg.results_dir):
    """
    Export the per-model evaluation report: config panel, structured metrics table,
    and per-query question / context / answer / image pages.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name  = re.sub(r"[^\w\-]", "_", model_name)
    filename   = output_dir / f"{safe_name}_rag_results_{timestamp}.pdf"

    doc = SimpleDocTemplate(
        str(filename), pagesize=A4,
        rightMargin=45, leftMargin=45, topMargin=52, bottomMargin=45,
    )
    S = _pdf_styles()
    elements = []

    # ── Cover / metrics page ──────────────────────────────────────────────
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph("RAG Pipeline", S["subtitle"]))
    elements.append(Paragraph("Evaluation Report", S["title"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"Model: <b>{_xml_escape(model_name)}</b>  ·  "
        f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}",
        S["subtitle"],
    ))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=_PDF_ACCENT))
    elements.append(Spacer(1, 0.15 * inch))

    # Config panel
    elements.append(Paragraph("Pipeline Configuration", S["h2"]))
    elements.append(Spacer(1, 4))
    elements.append(_config_table(S))
    elements.append(Spacer(1, 0.2 * inch))

    # Metrics table
    elements.append(Paragraph("Metrics Summary", S["h2"]))
    elements.append(Spacer(1, 4))

    gt_available = int(metrics_summary.get("gt_available_count", 0) or 0)
    gt_total     = int(metrics_summary.get("gt_total", 0) or 0)
    gen_label    = (
        f"Generation Quality  (vs ground truth — {gt_available}/{gt_total} available)"
        if gt_available > 0
        else "Generation Quality  (vs retrieved context)"
    )
    m2_val = (f"{metrics_summary.get('m2_index_size', 0)}"
              f" {metrics_summary.get('m2_unit', 'vectors')}")

    rows_spec = [
        "Retrieval Quality",
        ("M1   Embedding time ↓",
         f"{metrics_summary.get('m1_embedding_time', 0):.4f}", "s"),
        ("M2   Index size",      m2_val, ""),
        ("M3   Retrieval latency ↓",
         f"{metrics_summary.get('m3_retrieval_latency', 0):.4f}", "s"),
        ("      └ embed",
         f"{metrics_summary.get('embed_time_sec', 0):.4f}", "s"),
        ("      └ BM25",
         f"{metrics_summary.get('bm25_time_sec', 0):.4f}", "s"),
        ("      └ fusion",
         f"{metrics_summary.get('fusion_time_sec', 0):.4f}", "s"),
        ("      └ rerank",
         f"{metrics_summary.get('rerank_time_sec', 0):.4f}", "s"),
        (metrics_summary.get('m4_text_label',  'M4   Cosine sim (Text) ↑'),
         f"{metrics_summary.get('m4_cosine_similarity', 0):.4f}", ""),
        (metrics_summary.get('m4_image_label', 'M4   Cosine sim (Image) ↑'),
         f"{metrics_summary.get('m4_cosine_similarity_image', 0):.4f}", ""),
        ("M5   Page Coverage@k ↑",
         f"{metrics_summary.get('m5_top_k_accuracy', 0):.2f}", "%"),
        ("M9   Context length",
         f"{metrics_summary.get('m9_context_length', 0):.0f}", "chars"),

        gen_label,
        ("M6   ROUGE-1 ↑",       f"{metrics_summary.get('m6_rouge1', 0):.4f}",     ""),
        ("M7   ROUGE-2 ↑",       f"{metrics_summary.get('m7_rouge2', 0):.4f}",     ""),
        ("M8   ROUGE-L ↑",       f"{metrics_summary.get('m8_rougeL', 0):.4f}",     ""),
        ("M10  BLEU ↑",          f"{metrics_summary.get('m10_bleu', 0):.4f}",      ""),
        ("M11  METEOR ↑",        f"{metrics_summary.get('m11_meteor', 0):.4f}",    ""),
        ("M12  BERTScore F1 ↑",  f"{metrics_summary.get('m12_bertscore', 0):.4f}", ""),
        ("M13  FCD ↓",           f"{metrics_summary.get('m13_fcd', 0):.2f}",       ""),
        ("M14  Faithfulness ↑",  f"{metrics_summary.get('m14_faithfulness', 0):.2f}", "%"),
        ("M15  GT Coverage ↑",   f"{metrics_summary.get('m15_gt_coverage', 0):.2f}",  "%"),

        "Performance",
        ("M16  E2E Latency ↓",   f"{metrics_summary.get('m16_e2e_latency', 0):.4f}",  "s"),
        ("M17  Throughput ↑",    f"{metrics_summary.get('m17_throughput', 0):.3f}",   "q/s"),
        ("M18  CPU Usage ↓",     f"{metrics_summary.get('m18_cpu_usage', 0):.2f}",    "%"),
        ("M19  RAM Usage ↓",     f"{metrics_summary.get('m19_ram_usage', 0):.3f}",    "GB"),
        ("     GPU avg",         f"{metrics_summary.get('gpu_usage', 0):.2f}",        "%"),
        ("     GPU peak",        f"{metrics_summary.get('peak_gpu_usage', 0):.2f}",   "%"),
    ]
    elements.append(_metrics_table(rows_spec, S))
    elements.append(PageBreak())

    # ── Per-query results ─────────────────────────────────────────────────
    for idx, item in enumerate(results, 1):
        query          = _xml_escape(item.get("query", "") or "")
        context        = _xml_escape(item.get("context", "") or "").replace("\n", "<br/>")
        reference_text = _xml_escape(item.get("reference_text", "") or "").replace("\n", "<br/>")
        response_text  = _xml_escape(item.get("response_text", "") or "").replace("\n", "<br/>")
        resp_dict      = item.get("response", {})
        fcd_val        = resp_dict.get("factual_consistency_distance") if isinstance(resp_dict, dict) else None

        elements.append(Paragraph(f"Query {idx} of {len(results)}", S["h2"]))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=_PDF_SEP))
        elements.append(Spacer(1, 6))

        elements.append(Paragraph("Question", S["h3"]))
        elements.append(Paragraph(query, S["body"]))

        elements.append(Paragraph("Retrieved Context", S["h3"]))
        elements.append(Paragraph(context or "<i>(none)</i>", S["body"]))

        if reference_text:
            elements.append(Paragraph("Ground Truth Answer", S["h3"]))
            elements.append(Paragraph(reference_text, S["body"]))

        elements.append(Paragraph("Model Answer", S["h3"]))
        elements.append(Paragraph(response_text or "<i>(no response)</i>", S["body"]))

        # Per-query performance strip
        perf_parts = [
            f"Inference: {item.get('inference_time_sec', 0):.4f} s",
            f"E2E: {item.get('e2e_latency_sec', 0):.4f} s",
            f"CPU: {item.get('avg_cpu_percent', 0):.1f}%",
            f"RAM: {item.get('avg_ram_gb', 0):.3f} GB",
            f"GPU: {item.get('avg_gpu_percent', 0):.1f}%",
            f"Images: {item.get('num_images', 0)}",
        ]
        if fcd_val is not None:
            perf_parts.append(f"FCD: {fcd_val:.2f}")
        elements.append(Paragraph("  ·  ".join(perf_parts), S["metric"]))

        if item.get("images"):
            img_section = [
                Spacer(1, 0.2 * inch),
                Paragraph("Retrieved Images", S["h3"]),
            ]
            for img_obj in item["images"]:
                pil_img = img_obj.get("image")
                caption = _xml_escape(img_obj.get("caption", "") or "")
                if pil_img is None:
                    continue
                img_section.append(_image_flowable(pil_img))
                if caption:
                    img_section.append(Paragraph(f"<i>{caption}</i>", S["caption"]))
                img_section.append(Spacer(1, 0.2 * inch))
            elements.append(KeepTogether(img_section))

        if idx != len(results):
            elements.append(PageBreak())

    _title = f"RAG Pipeline — Evaluation Report  ({model_name})"
    doc.build(
        elements,
        onFirstPage=lambda c, d: _add_header_footer(c, d, _title),
        onLaterPages=lambda c, d: _add_header_footer(c, d, _title),
    )
    LOGGER.info("  [OK] Saved evaluation report: %s", filename)


# %%
def export_comparison_to_pdf(all_model_metrics: Dict[str, Dict], shared_metrics: Dict,
                             output_dir: str = cfg.model_comparison_results_dir):
    """
    Export a side-by-side comparison table for all evaluated models / ablation configs.

    Layout:
      Page 1 — title, pipeline configuration panel, run metadata
      Page 2+ — full metric comparison table (M1–M19 + GPU, all three groups)

    Retrieval metrics live in shared_metrics (computed once for all models).
    Generation and performance metrics live in all_model_metrics keyed by model name.
    """
    if not all_model_metrics:
        return

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = output_dir / f"model_comparison_{timestamp}.pdf"

    doc = SimpleDocTemplate(
        str(filename), pagesize=A4,
        rightMargin=36, leftMargin=36, topMargin=52, bottomMargin=40,
    )
    S = _pdf_styles()
    elements = []

    configs = list(all_model_metrics.keys())

    # ── Cover page ────────────────────────────────────────────────────────
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph("RAG Pipeline", S["subtitle"]))
    elements.append(Paragraph("Model Comparison Report", S["title"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}  ·  "
        f"Models evaluated: {len(configs)}",
        S["subtitle"],
    ))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=_PDF_ACCENT))
    elements.append(Spacer(1, 0.15 * inch))

    # Config panel (shared across all models in this run)
    elements.append(Paragraph("Pipeline Configuration", S["h2"]))
    elements.append(Spacer(1, 4))
    elements.append(_config_table(S))
    elements.append(PageBreak())

    # ── Comparison table ──────────────────────────────────────────────────
    elements.append(Paragraph("Metric Comparison — All Models", S["h2"]))
    elements.append(Spacer(1, 6))

    # Helper: read a value from shared_metrics first, then per-model metrics.
    def _shared(key: str) -> Optional[float]:
        v = shared_metrics.get(key)
        if v is None and configs:
            v = all_model_metrics.get(configs[0], {}).get(key)
        return v

    def _fmt_shared(key: str, fmt: str) -> str:
        v = _shared(key)
        return fmt.format(v) if v is not None else "—"

    def _fmt_per(model: str, key: str, fmt: str) -> str:
        v = all_model_metrics.get(model, {}).get(key)
        return fmt.format(v) if v is not None else "—"

    # ── Column widths: label col + one col per model ─────────────────────
    usable_w = A4[0] - 36 - 36    # page width minus margins
    label_w  = 160
    n_models = max(len(configs), 1)
    model_w  = max(55, int((usable_w - label_w) / n_models))
    col_widths = [label_w] + [model_w] * n_models

    # Abbreviate long model names for the header row
    def _abbrev(name: str, max_len: int = 14) -> str:
        return name if len(name) <= max_len else name[:max_len - 1] + "…"

    label_hdr  = ParagraphStyle("cHdrL", parent=S["body"],
                                fontName="Helvetica-Bold", fontSize=8, textColor=_PDF_ACCENT)
    model_hdr  = ParagraphStyle("cHdrM", parent=S["body"],
                                fontName="Helvetica-Bold", fontSize=8,
                                textColor=_PDF_ACCENT, alignment=1)
    label_cell = ParagraphStyle("cLabel", parent=S["body"], fontSize=8)
    value_cell = ParagraphStyle("cValue", parent=S["metric"],  fontSize=8, alignment=1)
    sec_cell   = ParagraphStyle("cSec",   parent=S["body"],
                                fontName="Helvetica-Bold", fontSize=8.5,
                                textColor=_PDF_ACCENT)

    def _hdr_p(text): return Paragraph(text, label_hdr)
    def _mhdr_p(text): return Paragraph(_abbrev(text), model_hdr)
    def _lbl(text):   return Paragraph(str(text), label_cell)
    def _val(text):   return Paragraph(str(text), value_cell)
    def _sec(text):   return Paragraph(str(text), sec_cell)

    # ── Section: retrieval (shared) ───────────────────────────────────────
    # Note: retrieval metrics are shared across all models in a single run.
    # They are shown once per column (same value repeated) to keep the table
    # self-contained; a note below the table explains this.
    m2_str = (f"{shared_metrics.get('m2_index_size', '—')} "
              f"{shared_metrics.get('m2_unit', 'vectors')}")

    _RETRIEVAL = [
        ("M1  Embedding time ↓",        "m1_embedding_time",         "{:.4f} s",  True),
        ("M2  Index size",               None,                        m2_str,      True),
        ("M3  Retrieval latency ↓",      "m3_retrieval_latency",      "{:.4f} s",  True),
        (shared_metrics.get("m4_text_label",  "M4  Sim (Text) ↑"),
                                         "m4_cosine_similarity",      "{:.4f}",    True),
        (shared_metrics.get("m4_image_label", "M4  Sim (Image) ↑"),
                                         "m4_cosine_similarity_image","{:.4f}",    True),
        ("M5  Page Coverage@k ↑",        "m5_top_k_accuracy",        "{:.2f} %",  True),
        ("M9  Context length",            "m9_context_length",        "{:.0f} ch", True),
    ]
    _GENERATION = [
        ("M6   ROUGE-1 ↑",    "m6_rouge1",       "{:.4f}"),
        ("M7   ROUGE-2 ↑",    "m7_rouge2",       "{:.4f}"),
        ("M8   ROUGE-L ↑",    "m8_rougeL",       "{:.4f}"),
        ("M10  BLEU ↑",       "m10_bleu",        "{:.4f}"),
        ("M11  METEOR ↑",     "m11_meteor",      "{:.4f}"),
        ("M12  BERTScore ↑",  "m12_bertscore",   "{:.4f}"),
        ("M13  FCD ↓",        "m13_fcd",         "{:.2f}"),
        ("M14  Faithfulness ↑","m14_faithfulness","{:.2f} %"),
        ("M15  GT Coverage ↑","m15_gt_coverage",  "{:.2f} %"),
    ]
    _PERFORMANCE = [
        ("M16  E2E Latency ↓", "m16_e2e_latency","{:.4f} s"),
        ("M17  Throughput ↑",  "m17_throughput",  "{:.3f} q/s"),
        ("M18  CPU Usage ↓",   "m18_cpu_usage",   "{:.2f} %"),
        ("M19  RAM Usage ↓",   "m19_ram_usage",   "{:.3f} GB"),
        ("     GPU avg",       "gpu_usage",       "{:.2f} %"),
        ("     GPU peak",      "peak_gpu_usage",  "{:.2f} %"),
    ]

    tdata = []
    # Header row
    tdata.append([_hdr_p("Metric")] + [_mhdr_p(m) for m in configs])

    def _sec_row(label):
        return [_sec(label)] + [Paragraph("", value_cell)] * n_models

    # Retrieval section
    tdata.append(_sec_row("── RETRIEVAL  (shared across all models) ──"))
    for label, key, fmt, _shared_flag in _RETRIEVAL:
        if key is None:
            # M2: pre-formatted string, same for all models
            tdata.append([_lbl(label)] + [_val(fmt)] * n_models)
        else:
            raw = shared_metrics.get(key)
            cell_str = fmt.format(raw) if raw is not None else "—"
            tdata.append([_lbl(label)] + [_val(cell_str)] * n_models)

    # Generation section
    tdata.append(_sec_row("── GENERATION QUALITY  (per model, vs ground truth) ──"))
    for label, key, fmt in _GENERATION:
        tdata.append(
            [_lbl(label)] + [_val(_fmt_per(m, key, fmt)) for m in configs]
        )

    # Performance section
    tdata.append(_sec_row("── PERFORMANCE  (per model) ──"))
    for label, key, fmt in _PERFORMANCE:
        tdata.append(
            [_lbl(label)] + [_val(_fmt_per(m, key, fmt)) for m in configs]
        )

    # Build section-row index set for styling
    section_indices = [
        1,
        1 + 1 + len(_RETRIEVAL),
        1 + 1 + len(_RETRIEVAL) + 1 + len(_GENERATION),
    ]

    style_cmds = [
        # Header
        ("BACKGROUND",    (0, 0), (-1, 0),  _PDF_ACCENT),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  8),
        ("ALIGN",         (0, 0), (-1, 0),  "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, 0),  5),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  5),
        # All rows
        ("FONTSIZE",      (0, 1), (-1, -1), 8),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("GRID",          (0, 0), (-1, -1), 0.25, _PDF_SEP),
        ("ALIGN",         (1, 1), (-1, -1), "CENTER"),
    ]
    # Section header rows
    for si in section_indices:
        style_cmds += [
            ("BACKGROUND",    (0, si), (-1, si), _PDF_ACCENT_LIGHT),
            ("SPAN",          (0, si), (-1, si)),
            ("LINEABOVE",     (0, si), (-1, si), 0.75, _PDF_ACCENT),
            ("TOPPADDING",    (0, si), (-1, si), 4),
            ("BOTTOMPADDING", (0, si), (-1, si), 4),
        ]
    # Alternating tint on data rows
    data_rows = [i for i in range(1, len(tdata)) if i not in section_indices]
    for j, i in enumerate(data_rows):
        if j % 2 == 1:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), _PDF_ROW_ALT))

    comp_table = Table(tdata, colWidths=col_widths, repeatRows=1)
    comp_table.setStyle(TableStyle(style_cmds))
    elements.append(comp_table)
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "<i>* Retrieval metrics are computed once and are identical across model columns "
        "because retrieval runs before any LLM is invoked.</i>",
        S["caption"],
    ))

    _title = "RAG Pipeline — Model Comparison"
    doc.build(
        elements,
        onFirstPage=lambda c, d: _add_header_footer(c, d, _title),
        onLaterPages=lambda c, d: _add_header_footer(c, d, _title),
    )
    LOGGER.info("Comparison PDF written to %s", filename)


def print_model_comparison_table(all_model_metrics: Dict[str, Dict], shared_metrics: Dict, is_hybrid: bool = False):
    """
    Print a side-by-side comparison table of all evaluated models in the terminal.
    Retrieval metrics (shared) are shown once. Generation/performance metrics
    are shown per model as columns.
    """
    models = list(all_model_metrics.keys())
    gt_available = int(shared_metrics.get("gt_available_count", 0) or 0)
    gt_total = int(shared_metrics.get("gt_total", 0) or 0)
    gen_title = (
        "Generation Quality Metrics  (per model, vs ground truth)"
        if gt_available > 0
        else "Generation Quality Metrics  (per model, vs context)"
    )

    n = len(models)

    # Abbreviate long model names for column headers
    def abbrev(name: str, max_len: int = 14) -> str:
        return name if len(name) <= max_len else name[:max_len - 1] + "…"

    term_w = _terminal_width()
    max_table_w = min(term_w, 120)

    # Reserve space for borders and allocate remaining width
    label_w = max(32, min(38, int(max_table_w * 0.34)))  # slightly wider for arrows
    remaining = max_table_w - label_w - 3
    remaining -= max(n - 1, 0)
    col_w = max(10, int(remaining / max(n, 1)))

    sep_row = f"  ├{'─' * label_w}┼" + "┼".join(["─" * col_w] * n) + "┤"
    top_row = f"  ┌{'─' * label_w}┬" + "┬".join(["─" * col_w] * n) + "┐"
    bot_row = f"  └{'─' * label_w}┴" + "┴".join(["─" * col_w] * n) + "┘"
    mid_sep = f"  ├{'─' * label_w}┼" + "┼".join(["─" * col_w] * n) + "┤"

    def shared_row(label: str, val_str: str) -> str:
        total_cols_w = (col_w + 1) * n - 1
        return f"  │{label:<{label_w}}│{val_str:^{total_cols_w}}│"

    def model_val_row(label: str, values: List[str]) -> str:
        cells = "│".join(f"{v:^{col_w}}" for v in values)
        return f"  │{label:<{label_w}}│{cells}│"

    # Header
    print()
    print("=" * 100)
    print(f"  PHASE 6: MODEL COMPARISON TABLE  ({n} model{'s' if n > 1 else ''})")
    print("=" * 100)
    print(f"  Config: {active_retrieval_config_summary(html=False)}")
    print("-" * 100)

    headers = [abbrev(m) for m in models]

    print(top_row)
    print(f"  │{'METRIC':<{label_w}}│" + "│".join(f"{h:^{col_w}}" for h in headers) + "│")
    print(mid_sep)

    # ── Shared Retrieval Metrics ───────────────────────────────────────
    print(f"  │{'  ── RETRIEVAL (1 for all) ──':<{label_w}}│" +
          "│".join([" " * col_w] * n) + "│")

    print(shared_row("M1 Embedding Time (s, lower better)", f"{shared_metrics.get('m1_embedding_time', 0):.4f} s"))
    print(shared_row("M2 Index Size",
                     f"{shared_metrics.get('m2_index_size', 0)} {shared_metrics.get('m2_unit', 'vectors')}"))
    print(
        shared_row("M3 Retrieval Latency (s, lower better)", f"{shared_metrics.get('m3_retrieval_latency', 0):.4f} s"))
    print(shared_row(f"{shared_metrics.get('m4_text_label', 'M4 Retrieval Sim (Text)')} (higher better)",
                     f"{shared_metrics.get('m4_cosine_similarity', 0):.4f}"))
    print(shared_row(f"{shared_metrics.get('m4_image_label', 'M4 Retrieval Sim (Image)')} (higher better)",
                     f"{shared_metrics.get('m4_cosine_similarity_image', 0):.4f}"))
    print(shared_row("M5 Page Coverage@k (%)", f"{shared_metrics.get('m5_top_k_accuracy', 0):.2f} %"))
    print(shared_row("M9 Context Length (chars)", f"{shared_metrics.get('m9_context_length', 0):.0f} ch"))
    print(mid_sep)

    # ── Generation Quality ─────────────────────────────────────────────
    gt_note = "(vs gt)" if gt_available > 0 else "(vs context)"
    print(f"  │{'  ── GENERATION QUALITY ' + gt_note + ' ──':<{label_w}}│" +
          "│".join(f"{h:^{col_w}}" for h in headers) + "│")

    def mrow(label: str, key: str, fmt: str = ".4f"):
        vals = [format(all_model_metrics[m].get(key, 0.0), fmt) for m in models]
        return model_val_row(label, vals)

    def mrow_pct(label: str, key: str):
        vals = [f"{all_model_metrics[m].get(key, 0.0):.2f} %" for m in models]
        return model_val_row(label, vals)

    print(mrow("M6 ROUGE-1", "m6_rouge1"))
    print(mrow("M7 ROUGE-2", "m7_rouge2"))
    print(mrow("M8 ROUGE-L", "m8_rougeL"))
    print(mrow("M10 BLEU", "m10_bleu"))
    print(mrow("M11 METEOR", "m11_meteor"))
    print(mrow("M12 BERTScore (F1)", "m12_bertscore"))
    print(mrow("M13 FCD (lower better)", "m13_fcd", ".2f"))
    print(mrow_pct("M14 Faithfulness (%)", "m14_faithfulness"))
    print(mrow_pct("M15 GT Coverage (%)", "m15_gt_coverage"))
    print(mid_sep)

    # ── Performance Metrics ────────────────────────────────────────────
    print(f"  │{'  ── PERFORMANCE ──':<{label_w}}│" +
          "│".join(f"{h:^{col_w}}" for h in headers) + "│")

    def mrow_s(label: str, key: str):  # seconds
        vals = [f"{all_model_metrics[m].get(key, 0.0):.4f} s" for m in models]
        return model_val_row(label, vals)

    print(mrow_s("M16 E2E Latency (s)", "m16_e2e_latency"))
    vals_tp = [f"{all_model_metrics[m].get('m17_throughput', 0.0):.3f} q/s" for m in models]
    print(model_val_row("M17 Throughput (q/s)", vals_tp))
    print(mrow_pct("M18 CPU Usage (%)", "m18_cpu_usage"))
    vals_ram = [f"{all_model_metrics[m].get('m19_ram_usage', 0.0):.3f} GB" for m in models]
    print(model_val_row("M19 RAM Usage (GB)", vals_ram))
    vals_gpu = [f"{all_model_metrics[m].get('gpu_usage', 0.0):.2f} %" for m in models]
    print(model_val_row("GPU Usage (%)", vals_gpu))  # neutral

    print(bot_row)


# %%
def main(test_questions):
    setup_logging(getattr(cfg, "log_level", "INFO"))
    cfg.validate()
    gt_available_count = sum(1 for q in (test_questions or []) if (q.get("ground_truth_answer") or "").strip())
    gt_total = len(test_questions or [])

    # ── PHASE 1 ───────────────────────────────────────────────────────────
    log_section("PHASE 1 — Loading documents & chunking")
    pages = loading_pdf(dir_path=cfg.pdf_dir, images_dir=cfg.images_dir)
    chunks = bbox_chunker(pages, max_tokens=cfg.chunk_max_tokens, overlap_tokens=cfg.chunk_overlap_tokens)
    image_objects = build_image_objects(pages)

    # ── PHASE 2 ───────────────────────────────────────────────────────────
    log_section("PHASE 2 — Embedding & vector store initialization")
    needs_dense = cfg.retrieval_mode in ("semantic", "hybrid")

    text_embedder = TextEmbeddingModel(model_name=cfg.text_embed_model, batch_size=cfg.text_embed_batch_size)
    image_embedder = ImageEmbeddingModel(model_name=cfg.image_embed_model, pretrained=cfg.image_embed_pretrained,
                                         caption_image_weight=cfg.image_caption_image_weight,
                                         batch_size=cfg.image_embed_batch_size)

    text_db = VectorStore(
        collection_name=cfg.text_collection_name,
        directory=cfg.database_dir,
        silent=True,
        reset_collection=bool(getattr(cfg, "reset_collections_on_start", False)),
    )
    image_db = VectorStore(
        collection_name=cfg.image_collection_name,
        directory=cfg.database_dir,
        silent=True,
        reset_collection=bool(getattr(cfg, "reset_collections_on_start", False)),
    )

    bm25_corpus_size = None  # only set in BM25-only mode .

    if needs_dense:
        text_embeddings, text_time, text_stats = text_embedder.embed_documents(chunks)
        image_embeddings, image_time, image_stats = image_embedder.embed_image(image_objects)
        text_db.add_documents(chunks, text_embeddings)
        if getattr(image_embeddings, "shape", (0,))[0] > 0:
            image_db.add_documents(image_objects, image_embeddings)
        else:
            LOGGER.warning("No image embeddings to index; skipping image vector store insert.")
    else:
        # BM25-only: skip dense embedding entirely so M1 correctly reports 0 .
        LOGGER.info("  BM25-only mode: skipping dense embedding and vector DB population.")
        text_time, image_time = 0.0, 0.0
        text_stats, image_stats = {}, {}
        bm25_corpus_size = len(chunks)  # report BM25 corpus size for M2 .

    # ── PHASE 3 ───────────────────────────────────────────────────────────
    log_section("PHASE 3 — Retrieval (runs once; shared across all models)")

    if needs_dense:
        # Normal path: BM25 index (if hybrid) is built from vector DB .
        retriever = RetrievalRag(
            image_embedder=image_embedder, text_embedder=text_embedder,
            image_vectordb=image_db, text_vectordb=text_db,
            adaptive_weighting=cfg.adaptive_weighting,
            score_fusion=cfg.use_weighted_fusion, use_reranker=cfg.use_reranker,
            bm25_weight=cfg.bm25_weight, semantic_weight=cfg.semantic_weight,
            retrieval_mode=cfg.retrieval_mode
        )
    else:
        # BM25-only: build BM25 indexes from raw chunks BEFORE constructing
        # RetrievalRag so the constructor never calls from_vectorstore on the
        # empty (unpopulated) DB — that was the root cause of the crash.
        LOGGER.info("  BM25-only: building indexes directly from chunks...")
        bm25_text_idx = BM25IndexBuilder.from_chunks(chunks, enable_preprocessing=True)
        bm25_image_idx = BM25IndexBuilder.from_image_objects(image_objects, enable_preprocessing=True)
        LOGGER.info("  BM25-only: text index=%d docs, image index=%s docs.",
                    len(bm25_text_idx.documents) if bm25_text_idx else 0,
                    len(bm25_image_idx.documents) if bm25_image_idx else 0)
        retriever = RetrievalRag(
            image_embedder=image_embedder, text_embedder=text_embedder,
            image_vectordb=image_db, text_vectordb=text_db,
            adaptive_weighting=cfg.adaptive_weighting,
            score_fusion=cfg.use_weighted_fusion, use_reranker=cfg.use_reranker,
            bm25_weight=cfg.bm25_weight, semantic_weight=cfg.semantic_weight,
            retrieval_mode=cfg.retrieval_mode,
            # Pass pre-built indexes — constructor will use these directly
            # instead of querying the empty VectorStore.
            _bm25_index=bm25_text_idx,
            _image_bm25_index=bm25_image_idx,
        )
        LOGGER.info("  BM25-only: indexes built from chunks directly (vector DB bypassed).")

    is_hybrid = retriever.retrieval_mode == "hybrid"
    formatter = ContextFormatter(
        max_text_chunks=cfg.max_text_chunks, max_images=cfg.max_images,
        text_distance_threshold=cfg.text_distance_threshold,
        image_distance_threshold=cfg.image_distance_threshold,
        use_filtering=cfg.use_filtering,
        use_percentile_filtering=cfg.use_percentile_filtering,
        percentile_cutoff=cfg.percentile_cutoff,
        max_context_tokens=cfg.max_context_tokens,
    )

    # Run retrieval once for all questions
    formatted_output = []
    retrieval_times = []
    text_latencies = []
    image_latencies = []
    cosine_sims_text = []
    cosine_sims_image = []
    retrieval_for_m5 = []
    raw_retrieval_results = []

    LOGGER.info("  Retrieving context for %s question(s)...", len(test_questions))
    _stage_embed: List[float] = []
    _stage_bm25: List[float] = []
    _stage_fusion: List[float] = []
    _stage_rerank: List[float] = []

    for q in test_questions:
        start = time.perf_counter()
        out = retriever.retrieve(q["question"], text_k=cfg.text_k, image_k=cfg.image_k, rerank_k=cfg.rerank_k)
        retrieval_times.append(time.perf_counter() - start)
        text_latencies.append(out.text_latency_sec)
        image_latencies.append(out.image_latency_sec)
        cosine_sims_text.append(out.cosine_sim_text)
        cosine_sims_image.append(out.cosine_sim_image)
        # Collect granular stage latencies for aggregate reporting.
        _stage_embed.append(out.embed_time_sec)
        _stage_bm25.append(out.bm25_time_sec)
        _stage_fusion.append(out.fusion_time_sec)
        _stage_rerank.append(out.rerank_time_sec)

        legacy_result = out.to_legacy_dict()
        formatted = formatter.format(legacy_result)
        formatted["query"] = q["question"]
        formatted_output.append(formatted)
        retrieval_for_m5.append({"id": q.get("id"), "result": legacy_result})
        raw_retrieval_results.append({"id": q.get("id"), "query": q["question"], "result": legacy_result})

    # ── Shared retrieval metrics (computed once) ───────────────────────────
    shared_metrics = {}
    shared_metrics["m1_embedding_time"] = compute_embedding_time(text_time, image_time)
    # Assign the unit label before compute_index_size() so the export
    # functions always have a consistent value available in shared_metrics.
    shared_metrics["m2_unit"] = "documents" if cfg.retrieval_mode == "bm25" else "vectors"
    shared_metrics["m2_index_size"] = compute_index_size(
        text_db, image_db,
        bm25_corpus_size=bm25_corpus_size  # None for semantic/hybrid, int for BM25-only .
    )
    shared_metrics["m3_retrieval_latency"] = compute_retrieval_latency(retrieval_times)
    shared_metrics["text_search_latency"] = round(float(np.mean(text_latencies)), 4) if text_latencies else 0.0
    shared_metrics["image_search_latency"] = round(float(np.mean(image_latencies)), 4) if image_latencies else 0.0
    shared_metrics["embedding_time"] = text_time + image_time
    if retriever.retrieval_mode == "semantic":
        m4_text_label = "M4 Cosine Sim (Text)"
        m4_image_label = "M4 Cosine Sim (Image)"
    elif retriever.retrieval_mode == "bm25":
        m4_text_label = "M4 BM25 Relevance (Text)"
        m4_image_label = "M4 BM25 Relevance (Image)"
    else:
        m4_text_label = "M4 Fused Relevance (Text)"
        m4_image_label = "M4 Fused Relevance (Image)"
    shared_metrics["m4_text_label"] = m4_text_label
    shared_metrics["m4_image_label"] = m4_image_label
    shared_metrics["m4_cosine_similarity"] = compute_retrieval_similarity(cosine_sims_text,
                                                                          label=m4_text_label)
    shared_metrics["m4_cosine_similarity_image"] = compute_retrieval_similarity(cosine_sims_image,
                                                                                label=m4_image_label)
    shared_metrics["m5_top_k_accuracy"] = compute_top_k_accuracy(retrieval_for_m5, test_questions,
                                                                 k=cfg.top_k_accuracy_k)
    shared_metrics["m9_context_length"] = compute_context_length(formatted_output)
    shared_metrics["gt_available_count"] = gt_available_count
    shared_metrics["gt_total"] = gt_total

    # Aggregate per-query stage latencies so the PDF export can render the
    # latency breakdown row without additional computation.
    _all_breakdown_keys = ("embed_time_sec", "bm25_time_sec", "fusion_time_sec", "rerank_time_sec")
    for _bk in _all_breakdown_keys:
        shared_metrics[_bk] = 0.0  # default; overwritten below when data exists

    if _stage_embed:
        shared_metrics["embed_time_sec"] = round(statistics.mean(_stage_embed), 4)
        shared_metrics["bm25_time_sec"] = round(statistics.mean(_stage_bm25), 4)
        shared_metrics["fusion_time_sec"] = round(statistics.mean(_stage_fusion), 4)
        shared_metrics["rerank_time_sec"] = round(statistics.mean(_stage_rerank), 4)
        LOGGER.info(
            "  Latency breakdown (avg per query) — embed: %.4fs | bm25: %.4fs | "
            "fusion: %.4fs | rerank: %.4fs",
            shared_metrics["embed_time_sec"], shared_metrics["bm25_time_sec"],
            shared_metrics["fusion_time_sec"], shared_metrics["rerank_time_sec"],
        )

    # Image failure rate — surfaces silent image loading failures in metrics
    if image_stats:
        _img_total = image_stats.get("count", 0)
        _img_failed = image_stats.get("failed_loads", 0)
        shared_metrics["image_fail_rate"] = round(_img_failed / max(_img_total, 1), 4)
        if shared_metrics["image_fail_rate"] > 0:
            LOGGER.warning("  Image fail rate: %.1f%% (%d/%d)",
                           shared_metrics["image_fail_rate"] * 100, _img_failed, _img_total)
    else:
        shared_metrics["image_fail_rate"] = 0.0

    # Config snapshot for reproducibility
    import json as _json
    try:
        import uuid as _uuid
        _run_id = _uuid.uuid4().hex[:8]
        _snap_path = Path(cfg.results_dir) / f"config_snapshot_{_run_id}.json"
        _snap_path.parent.mkdir(parents=True, exist_ok=True)
        _cfg_dict = {k: str(v) if not isinstance(v, (int, float, bool, str, list, type(None))) else v
                     for k, v in cfg.__dict__.items()}
        _snap_path.write_text(_json.dumps(_cfg_dict, indent=2))
    except Exception as _e:
        LOGGER.warning("Config snapshot could not be saved: %s", _e)

    if is_hybrid:
        hybrid_stats = compute_hybrid_stats(raw_retrieval_results)
        fusion_signal = compute_fusion_effectiveness(raw_retrieval_results)
        log_kv("Hybrid Stats", str(hybrid_stats))
        # Persist into shared_metrics so the comparison PDF export can surface
        # the diagnostics without requiring a separate function argument.
        shared_metrics["hybrid_stats"] = hybrid_stats
        shared_metrics["fusion_signal"] = fusion_signal
    else:
        hybrid_stats = None
        fusion_signal = None

    # Export retrieval PDF once
    log_section("PHASE 3b — Exporting retrieval results")
    export_retrieved_results_to_pdf(formatted_output=formatted_output, output_dir=cfg.retrieval_results_dir)

    # ── PHASE 4-5: Per-model evaluation ───────────────────────────────────
    models = cfg.llm_models
    all_model_metrics = {}  # model_name → full metrics_summary

    for idx, model_name in enumerate(models, start=1):
        log_section(f"PHASE 4 — Evaluating model: {model_name} ({idx}/{len(models)})")

        llm = LocalLLM(model_name=model_name, text_embedder=text_embedder)
        per_query_results = llm_response(
            llm=llm, formatted_output=formatted_output,
            test_questions=test_questions, stream=True
        )

        log_section(f"PHASE 5 — Metrics: {model_name}")

        # Merge shared metrics + compute per-model metrics
        ms = dict(shared_metrics)  # copy shared base

        metrics_verbose = bool(getattr(cfg, "metrics_verbose", False))
        with suppress_output(enabled=not metrics_verbose):
            ms["m6_rouge1"] = compute_rouge1(per_query_results, k=cfg.rouge_top_k_chunks, verbose=metrics_verbose)
            ms["m7_rouge2"] = compute_rouge2(per_query_results, k=cfg.rouge_top_k_chunks, verbose=metrics_verbose)
            ms["m8_rougeL"] = compute_rougeL(per_query_results, k=cfg.rouge_top_k_chunks, verbose=metrics_verbose)
            ms["m10_bleu"] = compute_bleu(per_query_results, verbose=metrics_verbose)
            ms["m11_meteor"] = compute_meteor(per_query_results, verbose=metrics_verbose)
            ms["m12_bertscore"] = compute_bertscore(per_query_results, verbose=metrics_verbose)
            ms["m13_fcd"] = compute_fcd(per_query_results, verbose=metrics_verbose)
            ms["m14_faithfulness"] = compute_faithfulness(per_query_results, verbose=metrics_verbose)
            ms["m15_gt_coverage"] = compute_gt_coverage(per_query_results, text_embedder=text_embedder,
                                                        verbose=metrics_verbose)
            ms["m16_e2e_latency"] = compute_e2e_latency(per_query_results, verbose=metrics_verbose)
            ms["m17_throughput"] = compute_throughput(per_query_results, verbose=metrics_verbose)
            ms["m18_cpu_usage"] = compute_cpu_usage(per_query_results, verbose=metrics_verbose)
            ms["m19_ram_usage"] = compute_ram_usage(per_query_results, verbose=metrics_verbose)
            ms["gpu_usage"] = compute_gpu_usage(per_query_results, verbose=metrics_verbose)
            # Also store peak GPU so the PDF export can surface it alongside avg.
            peak_gpu_vals = [r.get("peak_gpu_percent", 0.0) for r in per_query_results]
            ms["peak_gpu_usage"] = (
                max(peak_gpu_vals) if peak_gpu_vals else 0.0
            )

        if is_hybrid:
            ms["hybrid"] = hybrid_stats
            ms["fusion_signal_agreement"] = fusion_signal

        all_model_metrics[model_name] = ms

        # Per-model detailed PDF
        log_section(f"PHASE 7 — Exporting results: {model_name}")
        export_results_to_pdf(
            results=per_query_results, model_name=model_name,
            metrics_summary=ms, output_dir=cfg.results_dir
        )

    # ── PHASE 6: Final comparison table ───────────────────────────────────
    print_model_comparison_table(all_model_metrics, shared_metrics, is_hybrid)

    if is_hybrid and hybrid_stats:
        print_hybrid_metrics_summary(hybrid_stats)
        print(f"\n  {'─' * 78}")
        print(f"  {'FUSION SIGNAL AGREEMENT':^76}")
        print(f"  {'─' * 78}")
        print(f"  {'Avg mixed-signal support:':<50} {fusion_signal['avg_signal_agreement']:.1f}%")
        print(f"  {'Queries with >30% mixed support:':<50} {fusion_signal['mixed_signal_queries']}")
        print(f"  {'Agreement std dev:':<50} {fusion_signal['agreement_std']:.2f}")
        print(f"  {'─' * 78}")

    # Export comparison PDF (all models side by side)
    if len(all_model_metrics) > 0:
        export_comparison_to_pdf(all_model_metrics, shared_metrics, output_dir=cfg.model_comparison_results_dir)

    log_section("Pipeline completed successfully")


# %%
# ===========================================================================
# Caption selection helper for image retrieval quality
# ===========================================================================
def _select_best_caption(block_caption: str, section_heading: str,
                         fallback_id: str) -> str:
    """
    Return the most descriptive caption for an image.

    Priority:
      1. block_caption  — text immediately adjacent to the image in the PDF
                          (e.g. "Left: galaxy M84 ...  Right: spectrograph …")
      2. section_heading — the nearest section heading only when no specific
                           block caption is available
      3. fallback_id    — image file name / hash

    A block caption must be >12 chars to exclude spurious single-word matches.
    Without this priority, section titles like "Realizing Monster Black Holes
    Are Everywhere" end up as the caption for the M84 spectrograph image,
    causing the wrong image to be retrieved for that query.
    """
    if block_caption and len(block_caption.strip()) > 12:
        return block_caption.strip()
    if section_heading and len(section_heading.strip()) > 0:
        return section_heading.strip()
    return fallback_id


# %%
# ===========================================================================
# Six-configuration ablation harness for systematic evaluation
# Run:  python rag_pipeline.py --ablation
# ===========================================================================
_ABLATION_CONFIGS = [
    (
        "1_Dense_only",
        dict(retrieval_mode="semantic", use_reranker=False,
             adaptive_weighting=False, use_weighted_fusion=False, use_filtering=False,
             use_percentile_filtering=False),
    ),
    (
        "2_BM25_only",
        dict(retrieval_mode="bm25", use_reranker=False,
             adaptive_weighting=False, use_weighted_fusion=False, use_filtering=False,
             use_percentile_filtering=False),
    ),
    (
        "3_Hybrid_static",
        dict(retrieval_mode="hybrid", adaptive_weighting=False,
             use_weighted_fusion=False, use_reranker=False, use_filtering=False,
             use_percentile_filtering=False),
    ),
    (
        "4_Hybrid_adaptive",
        dict(retrieval_mode="hybrid", adaptive_weighting=True,
             use_weighted_fusion=False, use_reranker=False, use_filtering=False,
             use_percentile_filtering=False),
    ),
    (
        "5_Hybrid_reranker",
        dict(retrieval_mode="hybrid", adaptive_weighting=False,
             use_weighted_fusion=False, use_reranker=True, use_filtering=False,
             use_percentile_filtering=False),
    ),
    (
        "6_Full_pipeline",
        dict(retrieval_mode="hybrid", adaptive_weighting=True,
             use_weighted_fusion=False, use_reranker=True, use_filtering=True,
             use_percentile_filtering=True, percentile_cutoff=80),
    ),
]


def _apply_cfg_overrides(overrides: dict) -> None:
    """Apply a dict of attribute overrides to the global cfg object."""
    for k, v in overrides.items():
        setattr(cfg, k, v)


def run_ablation(test_questions: list) -> None:
    """
    Execute all six retrieval configurations in sequence.
    Each config writes its PDFs into a labeled subdirectory so results
    never overwrite each other.
    """
    import os as _os

    original_results_dir = cfg.results_dir
    original_retrieval_dir = cfg.retrieval_results_dir
    original_comparison_dir = cfg.model_comparison_results_dir

    for label, overrides in _ABLATION_CONFIGS:
        print("\n" + "=" * 78)
        print(f"  ABLATION CONFIG: {label}")
        print("=" * 78)

        _apply_cfg_overrides(overrides)

        # Route each config's output to its own subdirectory.
        cfg.results_dir = str(Path(original_results_dir) / label)
        cfg.retrieval_results_dir = str(Path(original_retrieval_dir) / label)
        cfg.model_comparison_results_dir = str(Path(original_comparison_dir) / label)

        for d in (cfg.results_dir, cfg.retrieval_results_dir,
                  cfg.model_comparison_results_dir):
            _os.makedirs(d, exist_ok=True)

        try:
            cfg.validate()
        except Exception as e:
            print(f"  [SKIP] Config validation failed for {label}: {e}")
            continue

        try:
            main(test_questions)
        except Exception as e:
            import traceback as _tb
            print(f"  [ERROR] {label} failed: {e}")
            _tb.print_exc()

    # Restore original output dirs.
    cfg.results_dir = original_results_dir
    cfg.retrieval_results_dir = original_retrieval_dir
    cfg.model_comparison_results_dir = original_comparison_dir
    print("\nAblation run complete — results written to labelled subdirectories.")


# %%
if __name__ == "__main__":
    import sys as _sys

    if "--ablation" in _sys.argv:
        run_ablation(TEST_QUESTIONS)
    else:
        main(TEST_QUESTIONS)