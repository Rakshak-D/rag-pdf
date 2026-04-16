#!/usr/bin/env python
# coding: utf-8

# # Document Loading

# In[1]:


import hashlib # Used for creating chunk id
import numpy as np # Used for images .
import pymupdf # For PDF loading
from pathlib import Path # For Loading the directory or PDF path .
from typing import Any, Dict, List, Optional, Tuple
import traceback # Used for error handling .
from dataclasses import dataclass, field


# In[2]:


try:
    PROJECT_ROOT = Path(__file__).resolve().parent
except NameError:
    PROJECT_ROOT = Path.cwd()
    if not (PROJECT_ROOT / 'data').exists() and (PROJECT_ROOT.parent / 'data').exists():
        PROJECT_ROOT = PROJECT_ROOT.parent


# In[3]:


import sys

NOTEBOOK_ROOT = PROJECT_ROOT / "notebooks" if (PROJECT_ROOT / "notebooks").exists() else PROJECT_ROOT
if str(NOTEBOOK_ROOT) not in sys.path:
    sys.path.insert(0, str(NOTEBOOK_ROOT))


# In[4]:


from config import Config, cfg


# In[5]:


# The following function is used to check if the image has detailing since if there is no much variance
# (basically flat image) it removes it.
# threshold -> minimum variance value.
def is_low_variance(pix, threshold: int = 5) -> bool:
    try:
        if pix is None or pix.samples is None:  # Checking if image is empty.
            return True

        samples = np.frombuffer(pix.samples, dtype=np.uint8)  # Storing pixels (raw bytes) into numpy array.

        if len(samples) == 0:  # Checking if image array is empty.
            return True

        if pix.n >= 3:
            samples = samples.reshape(-1, pix.n)[:, :3].mean(axis=1)  # Converting to grayscale

        return samples.std() < threshold  # Returns True if standard deviation is less than threshold.
    except Exception as e:
        print(f"Error in is_low_variance: {e}")
        return True


# In[6]:


# The following function checks if image is mostly white.
# threshold -> 245 -> near white
# ratio -> The threshold ratio of white pixels in the whole picture.
def is_mostly_white(pix, threshold=245, ratio=0.98) -> bool:
    try:
        if pix is None or pix.samples is None:  # Checking if image is empty.
            return True

        samples = np.frombuffer(pix.samples, dtype=np.uint8)  # Storing pixels (raw bytes) into numpy array.

        if len(samples) == 0:  # Checking if image array is empty.
            return True

        if pix.n >= 3:
            samples = samples.reshape(-1, pix.n)[:, :3].mean(axis=1)
        white_pixels = np.sum(samples > threshold)  # Number of near-white pixels in image.

        return (white_pixels / len(samples)) > ratio  # Returns True if most pixels are near white.
    except Exception as e:
        print(f"Error in is_mostly_white: {e}")
        return True


# In[7]:


# The following function checks if image has extreme aspect ratio (too wide or too tall) which are usually headers/footers.
# min_ratio -> minimum acceptable aspect ratio
# max_ratio -> maximum acceptable aspect ratio
def is_extreme_aspect_ratio(pix, min_ratio: float = 0.1, max_ratio: float = 10.0) -> bool:
    try:
        if pix is None:  # Checking if image is empty.
            return True

        aspect_ratio = pix.width / max(pix.height, 1)  # Calculating aspect ratio.

        return aspect_ratio < min_ratio or aspect_ratio > max_ratio  # Returns True if aspect ratio is extreme.
    except Exception as e:
        print(f"Error in is_extreme_aspect_ratio: {e}")
        return True


# In[8]:


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
    if not dir_path.is_dir():  # Checking if the directory is valid.
        raise NotADirectoryError(f"{dir_path} is an invalid directory.")

    print(f"\n{'='*80}")
    print(f"  PDF LOADING")
    print(f"{'='*80}")
    print(f"  Directory: {dir_path}")

    pdf_files = sorted(dir_path.rglob("*.pdf"))  # Deterministic ordering keeps benchmarking reproducible.
    print(f"  Found {len(pdf_files)} PDF file(s)")

    if len(pdf_files) == 0:  # Checking if any PDFs exist in the directory.
        print('  WARNING: No documents found in directory')
        return []

    # All these variables used for stats check at the end.
    all_pdf_size = 0.0
    all_pages = []
    failed_pdf = []

    print(f"\n  Loading PDFs...")
    print("  " + "-" * 76)

    for serial, pdf_path in enumerate(pdf_files, start=1):  # Iterating through all PDFs in directory.
        print(f"  [{serial}/{len(pdf_files)}] Loading: {pdf_path.name}")
        pdf_size_bytes = pdf_path.stat().st_size
        pdf_size_mb = pdf_size_bytes / (1024 ** 2)  # Calculating size of the PDF.
        print(f"       Size: {pdf_size_mb:.2f} MB")

        pdf = None  # For cleanup
        try:
            image_dir = images_root / pdf_path.stem
            image_dir.mkdir(parents=True, exist_ok=True)
            pdf = pymupdf.open(filename=pdf_path, filetype="pdf")  # Loading PDF.

            pdf_total_text_blocks = 0
            pdf_total_images = 0

            for page_num, page in enumerate(pdf, start=1):
                text_blocks = []  # Used for storing details about blocks of a page.
                page_images = []  # Used for storing images of current page.
                seen_xrefs = set()
                images = page.get_images(full=True)  # Extracting images.

                for img_index, img in enumerate(images):  # Extracting images.
                    pix = None  # For cleanup
                    try:
                        if img[1] != 0:  # Skip soft mask.
                            # soft mask -> Transparency layer
                            continue
                        xref = img[0]
                        if xref in seen_xrefs:  # Checking if the same images are being stored
                            continue
                        seen_xrefs.add(xref)
                        rects = page.get_image_rects(xref)  # Used for getting image edges.
                        if not rects:  # Checking if coordinates or image is empty.
                            continue

                        pix = pymupdf.Pixmap(pdf, xref)  # xref is used to find position of image in PDF.
                        if pix.width < 50 or pix.height < 50:  # Removing very tiny images.
                            pix = None
                            continue
                        if pix.alpha and pix.samples is not None:  # Removing fully transparent images.
                            if max(pix.samples) == 0 and len(pix.samples) > 0:
                                continue
                        if pix.n > 4:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        if is_mostly_white(pix):  # Checking if the image is mostly white.
                            pix = None
                            continue
                        if is_low_variance(pix):  # Checking if it's a flat image.
                            pix = None
                            continue
                        if is_extreme_aspect_ratio(pix):  # Checking if aspect ratio is extreme (headers/footers).
                            pix = None
                            continue

                        img_path = image_dir / f"page_{page_num}_img_{img_index}.png"  # Location for storing images in local disk.
                        pix.save(img_path)  # Saving images in local disk.
                        pix = None
                        rect = rects[0]  # Changed the method since we needed only approx coordinates and not all approx coordinates to be merged, using a FOR loop made multiple copy of image.
                        page_images.append({
                            "image_id": f"{pdf_path.stem}_p{page_num}_i{img_index}",
                            "path": str(img_path),
                            "page": page_num,
                            "bbox": [rect.x0, rect.y0, rect.x1, rect.y1]
                        })  # For metadata.
                    except Exception as img_error:
                        print(f"       Error processing image {img_index}: {img_error}")
                    finally:
                        if pix is not None:
                            pix = None

                # Following loop is to extract texts from a page.
                blocks = sorted(page.get_text("blocks"), key=lambda b: (b[1], b[0]))
                for block_id, b in enumerate(blocks):
                    x0, y0, x1, y1, text = b[:5]  # Coordinates and text of text block.
                    text = text.strip()
                    if len(text) < cfg.chunk_min_text_len:
                        continue
                    block_bbox = [x0, y0, x1, y1]  # Coordinates of the text blocks. Used while checking relevance of image and text.
                    text_blocks.append({
                        "block_id": block_id,
                        "text": text,
                        "bbox": block_bbox,
                        "page": page_num,
                    })  # Used while appending metadata.

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
            print(f"       ✓ Extracted {pdf_total_text_blocks} text blocks, {pdf_total_images} images")

        except Exception as e:
            print(f"       ✗ Error loading {pdf_path.name}: {e}")  # Exception handling.
            failed_pdf.append(pdf_path.name)  # Storing the PDF failed to load.
            traceback.print_exc()  # Used to trace failures similar to python interpreter stack trace.
        finally:
            if pdf is not None and not pdf.is_closed:
                pdf.close()

    # Some stats of Loading all the PDF in a directory.
    print("  " + "-" * 76)
    print(f"\n  SUMMARY:")
    print(f"       Total size: {all_pdf_size:.2f} MB")
    print(f"       Total pages extracted: {len(all_pages)}")
    print(f"       Successful: {len(pdf_files) - len(failed_pdf)}/{len(pdf_files)}")

    # Printing all the PDF which were not able to load.
    if failed_pdf:
        print(f"\n  Failed PDFs:")
        for fp in failed_pdf:
            print(f"       - {fp}")

    print(f"\n{'='*80}\n")
    return all_pages  # Returning the loaded pages.


# # Chunking

# In[9]:


from langchain_core.documents import Document # Datatype of a block or a chunk .
from typing import List # Used to store list of Documents or to specify return type .
from typing import Tuple
import tiktoken


# In[10]:


# The following function is to calculate distance between two blocks and a threshold is set such that
# if two blocks are far those both blocks are separated with different chunks.
def vertical_gap(block1, block2) -> float:
    return block2["bbox"][1] - block1["bbox"][3]  # Distance between bottom of block 1 and top of block 2.


# In[11]:


# The following function is used for getting outermost edge of all the chunks combined.
def merge_bbox(blocks):
    if not blocks:
        return None

    return (
        min(b["bbox"][0] for b in blocks),  # x0 left
        min(b["bbox"][1] for b in blocks),  # y0 top
        max(b["bbox"][2] for b in blocks),  # x1 right
        max(b["bbox"][3] for b in blocks)   # y1 bottom
    )


# In[12]:


# The following function creates a constant chunk id for same text.
def stable_chunk_id(source: str, page_num: int, text: str) -> str:
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return f"{source}_p{page_num}_c{h}"


# In[13]:


# The following function is used to check if a block is relevant to another block using coordinates.
def bbox_overlap(a, b) -> bool:
    return not (
        a[2] < b[0] or  # right edge of a and left edge of b
        a[0] > b[2] or  # left edge of a and right edge of b
        a[3] < b[1] or  # bottom edge of a and top edge of b
        a[1] > b[3]     # top edge of a and bottom edge of b
    )


# In[14]:


# The following function helps to identify if the text block near the image is caption of the image based on the coordinates and length of the text.
def is_caption_block(text_block: Dict, image: Dict, max_words: int = 60, max_vertical_dist: int = 80) -> bool:
    text = text_block.get("text", "")  # Text

    if not text or len(text.split()) > max_words:  # Checking if the text is large.
        return False

    tb_bbox = text_block.get("bbox")  # Fetching text block bbox.
    im_bbox = image.get("bbox")  # Fetching image block bbox.

    if not tb_bbox or not im_bbox:  # Checking if bbox is empty.
        return False

    tb_x0, tb_y0, tb_x1, tb_y1 = tb_bbox  # Coordinates of text block.
    im_x0, im_y0, im_x1, im_y1 = im_bbox  # Coordinates of image.

    horizontal_overlap = not (tb_x1 < im_x0 or tb_x0 > im_x1)  # Check for horizontal overlap.

    vertical_distance = min(  # Vertical distance between text block and image.
        abs(tb_y0 - im_y1),
        abs(im_y0 - tb_y1)
    )
    return horizontal_overlap and vertical_distance <= max_vertical_dist  # Returns False if any condition fails.


# In[15]:


# The following function gets images that overlap with a chunk's bbox.
def get_overlapping_images(chunk_bbox: Tuple, page_images: List[Dict], vertical_tolerance: int = 200, horizontal_tolerance: int = 50) -> List[str]:
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
            abs(chunk_y1 - img_y1)   # Distance between bottoms
        )

        # Method 1: Check if horizontally aligned and vertically close
        if horizontal_overlap and vertical_distance <= vertical_tolerance:
            overlapping_ids.append(img["image_id"])
        # Method 2: Or if they actually overlap perfectly
        elif bbox_overlap(chunk_bbox, img_bbox):
            overlapping_ids.append(img["image_id"])

    return overlapping_ids


# In[16]:


# Build image objects, mainly used for embedding using openclip and also can be used parallely with text block in graph db.
def build_image_objects(pages: List[Dict]) -> List[Dict]:
    image_objects = []

    # Getting text block and images from the page. (For reference this are the pages already loaded from pdf_loading.)
    for page in pages:
        source = page.get("source", "unknown")
        page_num = page.get("page", 0)
        text_blocks = page.get("text_blocks", [])
        images = page.get("images", [])

        # For every text block in page checking if the image is relevant or caption to it.
        for img in images:
            caption_blocks = [
                tb["text"] for tb in text_blocks
                if tb.get("text") and is_caption_block(tb, img)
            ]

            caption_text = " ".join(caption_blocks).strip() or None  # Caption gathered from text blocks.

            # Appending image objects.
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

    return image_objects  # List of image objects with relevant metadata.


# In[17]:


# The following function is used create a chunk by adding
def build_chunk(source: str, page_num: int, blocks: List, page_images: List[Dict], related_image_ids: List[str] | None = None) -> Document:
    if not blocks:
        return None

    chunk_text = "\n".join(b.get("text", "") for b in blocks)  # Combing all the texts from the blocks.

    if not chunk_text.strip():  # Checking if the text blocks are empty.
        return None

    chunk_bbox = merge_bbox(blocks)  # Used to get overall chunk coordinates.

    if related_image_ids is None:  # If no related images then fetch related images based on bbox overlap.
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
    )  # Adding metadata.


# In[18]:


# The following function is main chunking strategy, it uses bbox , max characters to chunk different blocks together.
# max_tokens -> maximum tokens in a single chunk (improved from char-based).
# (Replaced char-based with token-based using tiktoken for better semantics.)

# max_vertical_gap -> maximum vertical height between two blocks.
# Calculated using bbox.

# overlap_tokens -> number of tokens to overlap between consecutive chunks
def bbox_chunker(
    pages: List[Dict],
    max_tokens: int = 256,
    max_vertical_gap: int = 60,
    overlap_tokens: int = 64,
    token_model: str = "cl100k_base"
) -> List[Document]:
    tokenizer = tiktoken.get_encoding(token_model)  # For token counting

    all_chunks = []  # Used to store chunks.

    # Extracting data from a dictionary.
    for page in pages:
        source = page.get("source", "")
        page_num = page.get("page", 0)
        blocks = page.get("text_blocks", [])
        page_images = page.get("images", [])  # Getting images in a page.

        if not blocks:
            continue

        i = 0
        while i < len(blocks):
            current_blocks = []  # Used to store blocks to store in a chunk.
            current_tokens = 0  # Calculating maximum tokens in chunk.
            start_i = i  # Used to prevent infinite loop during overlap

            # Building a chunk until max_tokens or vertical gap threshold
            while i < len(blocks):
                block = blocks[i]
                text = block.get("text", "")

                if not text:
                    i += 1
                    continue

                block_tokens = len(tokenizer.encode(text))  # Token count for the block.

                if current_blocks:
                    gap = vertical_gap(current_blocks[-1], block)
                else:
                    gap = 0  # Basically first block of a page.

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

            # Creating chunk from collected blocks.
            if current_blocks:
                chunk = build_chunk(source, page_num, current_blocks, page_images)
                if chunk:
                    # FIXED: Semantic tag enrichment for better retrieval semantics
                    # Derived dynamically from the first sentence — fully domain-agnostic
                    first_sentence = current_blocks[0]["text"].split(".")[0][:100].strip()
                    chunk.metadata["semantic_tag"] = (
                        first_sentence[:50] + "..." if len(first_sentence) > 50 else first_sentence
                    )
                    all_chunks.append(chunk)  # Creating a chunk and appending it.

            # Step back to create overlap for next chunk.
            if overlap_tokens > 0 and i < len(blocks):
                overlap_tok = 0
                step_back = 0

                # Counting how many blocks to include in overlap.
                for j in range(len(current_blocks) - 1, -1, -1):
                    block_text = current_blocks[j].get("text", "")
                    block_tok = len(tokenizer.encode(block_text))
                    if overlap_tok + block_tok <= overlap_tokens:
                        overlap_tok += block_tok
                        step_back += 1
                    else:
                        break

                # Move index back safely (avoid infinite loop).
                i = max(start_i + 1, i - step_back)

    print(f"  Created {len(all_chunks)} chunks from {len(pages)} pages (max_tokens={max_tokens}, overlap_tokens={overlap_tokens})")

    return all_chunks


# # Embedding

# In[19]:


import warnings
warnings.filterwarnings("ignore", category=FutureWarning)


# In[20]:


import numpy as np # Used for storing embeddings .
import torch # For device selection , model execution and tensor operation .
from PIL import Image # Used for image creation and other image operation .
import open_clip # Image embedding model .
from typing import List, Dict,Tuple # Used for type return .
from dotenv import load_dotenv # Used for loading Huggingface api .
from time import perf_counter
import os


# In[21]:


load_dotenv() # For loading Huggingface api .


# In[22]:


# Removing unwanted warnings from sentence transformers library .(Optional)
import warnings
from transformers import logging

logging.set_verbosity_error()
warnings.filterwarnings("ignore", message=".*position_ids.*UNEXPECTED.*")


# In[23]:


# Used for loading embedding model and embedding images with their caption .
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
            print(f"OpenCLIP running on {torch.cuda.get_device_name(0)}.")
        else:
            print("OpenCLIP running on CPU.")

        model_config = open_clip.get_model_config(self.model_name)
        self.embed_dim = model_config["embed_dim"]
        print(f"Embedding dimension of {self.model_name} is {self.embed_dim}")

    @torch.no_grad()
    def embed_image(self, image_objects: List[Dict]) -> Tuple[np.ndarray, float, Dict]:
        if not image_objects:
            raise ValueError("No images in the image objects.")

        start_time = perf_counter()
        captions: List[str] = []
        image_tensors: List[torch.Tensor] = []
        failed_paths: List[str] = []

        for image_object in image_objects:
            image_path = image_object.get("path")
            if not isinstance(image_path, str) or not image_path.strip():
                failed_paths.append(str(image_path))
                continue
            try:
                with Image.open(image_path) as pil_image:
                    processed_image = self.preprocess(pil_image.convert("RGB"))
            except Exception as e:
                print(f"Failed to load {image_path}: {e}")
                failed_paths.append(image_path)
                continue

            image_tensors.append(processed_image)
            raw_caption = image_object.get("caption_text") or ""
            captions.append(raw_caption.strip() if isinstance(raw_caption, str) else "")

        if not image_tensors:
            raise ValueError("No valid images were processed. All images failed to load.")
        if failed_paths:
            preview = ", ".join(failed_paths[:3])
            raise RuntimeError(
                f"Failed to load {len(failed_paths)} image(s) during embedding. "
                f"Fix the extracted image paths before indexing. Examples: {preview}"
            )

        image_embeddings: List[np.ndarray] = []
        for batch_start in range(0, len(image_tensors), self.batch_size):
            batch_tensor = torch.stack(
                image_tensors[batch_start: batch_start + self.batch_size]
            ).to(self.device)
            batch_embeddings = self.model.encode_image(batch_tensor)
            batch_embeddings = batch_embeddings / batch_embeddings.norm(dim=-1, keepdim=True)
            image_embeddings.extend(batch_embeddings.cpu().numpy())

        caption_weight = 1.0 - self.caption_image_weight
        final_embeddings: List[np.ndarray] = []

        for image_embedding, caption in zip(image_embeddings, captions):
            if caption:
                tokens = self.tokenizer([caption]).to(self.device)
                caption_embedding = self.model.encode_text(tokens)
                caption_embedding = caption_embedding / caption_embedding.norm(dim=-1, keepdim=True)
                caption_embedding = caption_embedding.cpu().numpy()[0]
                fused_embedding = (
                    self.caption_image_weight * image_embedding +
                    caption_weight * caption_embedding
                )
                norm = np.linalg.norm(fused_embedding)
                if norm > 1e-8:
                    fused_embedding = fused_embedding / norm
                final_embeddings.append(fused_embedding)
            else:
                final_embeddings.append(image_embedding)

        total_time = perf_counter() - start_time
        avg_time = total_time / len(final_embeddings) if final_embeddings else 0.0

        stats = {
            "count": len(image_objects),
            "embeddings_created": len(final_embeddings),
            "failed_loads": 0,
            "total_time": total_time,
            "avg_time": avg_time,
            "dimension": self.embed_dim,
            "caption_image_weight": self.caption_image_weight
        }

        return np.vstack(final_embeddings), total_time, stats

    @torch.no_grad()
    def embed_query(self, query: str) -> np.ndarray:
        if not isinstance(query, str):  # Checking if query is valid.
            raise TypeError("Query must be a string.")
        if not query.strip():
            raise ValueError("Please give a valid prompt.")
        tokens = self.tokenizer([query]).to(self.device)  # Convert to tensor.
        query_embedding = self.model.encode_text(tokens)  # Embedding text or tensor value.
        query_embedding = query_embedding / query_embedding.norm(dim=-1, keepdim=True)  # Normalizing values
        query_embedding = query_embedding.cpu().numpy()[0]  # Converting tensor to numpy array.
        return query_embedding  # Returning the embedding.


# In[24]:


from sentence_transformers import SentenceTransformer # Used for loading model .
import numpy as np # Used to store embedding .
import torch # Used for device selection and model execution .
from typing import List # Used for return type .
from langchain_core.documents import Document # Used for storing documents .
from time import perf_counter


# In[25]:


# Used for loading model and embedding text .
class TextEmbeddingModel:
    def __init__(self, model_name: str = cfg.text_embed_model, batch_size: int = cfg.text_embed_batch_size):
        self.model_name = model_name
        self.device = "cuda" if torch.cuda.is_available() else "cpu"  # Device check.
        self.batch_size = batch_size

        try:
            self.model = SentenceTransformer(model_name_or_path=model_name, device=self.device)  # Loading model.
        except Exception as e:
            raise RuntimeError(f"Failed to load SentenceTransformer model: {e}")

        self.query_prefix = "Represent this sentence for searching relevant passages: " if "bge" in model_name.lower() else ""

        if self.device == "cuda":
            print(f"BGE running on {torch.cuda.get_device_name(0)}.")
        else:
            print("BGE running on CPU.")
        print(f"Embedding dimension of {model_name} is {self.model.get_sentence_embedding_dimension()}")

    @torch.no_grad()
    def embed_documents(self, documents: List[Document]) -> Tuple:
        if not documents:
            raise ValueError("No documents to embed.")

        texts = []
        for index, doc in enumerate(documents):  # Checking if data is available in documents.
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
        total_time = perf_counter() - start_time  # Calculate total time took to embedd .
        avg_time = total_time / len(texts)  # Calculate average time .

        # Store stats in dict instead of printing
        stats = {
            "count": len(texts),
            "embeddings_created": len(text_embeddings),
            "total_time": total_time,
            "avg_time": avg_time,
            "dimension": self.model.get_sentence_embedding_dimension()
        }

        return text_embeddings, total_time, stats  # Return embeddings, time, and stats.

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

    # The following function is used to create embedding for user input prompt.
    @torch.no_grad()
    def embed_query(self, query: str) -> np.ndarray:
        return self.encode_text(query, use_query_prefix=True)


# # VectorStore

# In[26]:


import json # Used for receiving image object or document while creating a hash id .
from typing import List,Dict,Union,Optional # For datatype of a variable .
import chromadb # Used for creating a vectorDB .
import os # For getting directory path for storing vector database .
import hashlib # Used for creating hashid for documents
import numpy as np
from langchain_core.documents import Document


# In[27]:


# The following function is used to create hashid using content of a document or image object .
def stable_hash(obj:dict|str)->str:
    if isinstance(obj,dict):
        obj=json.dumps(obj,sort_keys=True,ensure_ascii=False)
    elif not isinstance(obj,str):
        raise TypeError(f"stable_hash expects dict or str, got {type(obj)}")

    return hashlib.sha256(obj.encode("utf-8")).hexdigest()


# In[28]:


# The following function is used to remove None type and replace it with empty string since chromadb cannot store type None .
def sanitize_metadata(metadata: dict) -> dict:
    if not isinstance(metadata,dict): # Type validation .
        raise TypeError(f"sanitize_metadata expects dict , got {type(metadata)}")

    clean = {}
    for k, v in metadata.items():
        if v is None:
            clean[k] = "" # None -> Empty string
        elif isinstance(v, (str, int, float, bool)):
            clean[k] = v # Keep it as it is .
        elif isinstance(v, (list, tuple, dict)):
            clean[k] = json.dumps(v, ensure_ascii=False)
        else:
            clean[k] = str(v) # Convert unknown type to string .

    return clean # Return sanitized metadata .


# In[29]:


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


# In[30]:


# Used for initializing vectorDB and also store data in collection .
class VectorStore:
    def __init__(self, collection_name: str, directory: str = cfg.database_dir, silent: bool = False):
        if not collection_name or not isinstance(collection_name, str):
            raise ValueError("Collection name must be a non-empty string.")
        self.collection_name = collection_name  # Collection name.
        self.persistent_directory = directory  # Directory to store database.
        self.collection = None  # Collection, used to store data.
        self.client = None  # Used to connect database.
        self.silent = silent  # Flag to suppress printing
        self._existing_ids = set()
        self.initialize_store()  # Initializing vectorDB.

    def initialize_store(self):
        try:
            os.makedirs(name=self.persistent_directory, exist_ok=True)  # Checking if directory exists; if not, creating one.
            self.client = chromadb.PersistentClient(path=self.persistent_directory)
            if self.collection_exists(self.collection_name):  # Checking if the collection exists; if so, load it.
                if not self.silent:
                    print(f"Loading collection {self.collection_name} from database.")
                self.collection = self.client.get_collection(self.collection_name)
            else:  # If collection does not exist, create it.
                if not self.silent:
                    print(f"New collection {self.collection_name} created in database.")
                self.collection = self.client.create_collection(name=self.collection_name, metadata={"hnsw:space": "cosine"})
            self._existing_ids = set(self.collection.get(include=[])["ids"])
            if not self.silent:
                print(f"Vector store initialized.")  # Success message.
                print(f"Existing documents in collection: {len(self._existing_ids)}")
        except Exception as e:  # Exception handling.
            raise RuntimeError(f"Could not initialize vector store: {e}") from e

    # The following function is used to add data and its embeddings to a collection.
    def add_documents(self, documents: List[Union[Dict, Document]], embeddings: np.ndarray):
        if not self.collection:  # Checking if collection is initialized.
            raise RuntimeError("Collection is not initialized.")
        if not documents:
            raise ValueError("Documents list is empty.")
        if len(documents) != len(embeddings):  # Checking if number of documents and embeddings are the same.
            raise ValueError(f"Number of documents ({len(documents)}) does not match embeddings ({len(embeddings)}).")

        ids, metadatas, texts, embedding_rows = [], [], [], []  # Used to store main content and metadata.
        for idx, (doc, embedding) in enumerate(zip(documents, embeddings)):
            if isinstance(doc, Document):  # For text embeddings. Document type.
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
                texts.append(content)  # Appending data to store in vectorDB
                metadatas.append(metadata)
                ids.append(doc_id)
                embedding_rows.append(np.asarray(embedding, dtype=float).tolist())
            elif isinstance(doc, dict):  # For image embeddings. Dict type.
                bbox = doc.get("bbox")
                image_metadata = {
                    "image_id": doc.get("image_id", ""),
                    "source": doc.get("source", ""),
                    "page_num": doc.get("page_num", ""),
                    "image_path": doc.get("path", ""),
                    "caption_text": doc.get("caption_text", ""),
                    "bbox": bbox if bbox is not None else "",
                }
                image_metadata = sanitize_metadata(image_metadata)  # Removing None or unknown datatype.
                hash_input = {  # Used for hashid.
                    "image_id": image_metadata["image_id"],
                    "image_path": image_metadata["image_path"],
                    "source": image_metadata["source"],
                    "page_num": image_metadata["page_num"],
                    "bbox": image_metadata["bbox"],
                    "caption": image_metadata["caption_text"],
                }
                doc_id = str(doc.get("image_id") or stable_hash(hash_input))
                texts.append(doc.get("caption_text") or doc.get("image_id") or "")  # Appending data to store in vectorDB
                metadatas.append(image_metadata)
                ids.append(doc_id)
                embedding_rows.append(np.asarray(embedding, dtype=float).tolist())
            else:
                raise TypeError(f"Unsupported document type: {type(doc)}")  # If input is neither Document nor Dict.

        if not ids:
            if not self.silent:
                print("No valid documents to process.")
            return

        seen = set()
        new_indices = []
        for i, doc_id in enumerate(ids):  # Check for redundant documents.
            if doc_id in self._existing_ids or doc_id in seen:
                continue
            seen.add(doc_id)
            new_indices.append(i)

        if not new_indices:  # Checking if there are new documents to add to collection.
            if not self.silent:
                print("No new documents to add")
            return

        self.collection.add(  # Adding new documents to collection
            ids=[ids[i] for i in new_indices],
            documents=[texts[i] for i in new_indices],
            metadatas=[metadatas[i] for i in new_indices],
            embeddings=[embedding_rows[i] for i in new_indices]
        )
        self._existing_ids.update(ids[i] for i in new_indices)
        if not self.silent:
            print(f"Added {len(new_indices)} new documents to collection.")

    # The following function is used to check if the collection exists.
    def collection_exists(self, collection_name: str) -> bool:
        collections_in_db = self.client.list_collections()
        return any(col.name == collection_name for col in collections_in_db)

    # The following function is used to query and get relevant documents.
    def query(self, query_embedding: np.ndarray, k: int = 5, where: Optional[Dict] = None):
        if not self.collection:  # Checking if collection is initialized.
            raise RuntimeError("Collection is not initialized.")
        if query_embedding.ndim != 1:  # Checking if query is 1-dimensional.
            raise ValueError("Query embedding must be a 1D vector.")
        if not isinstance(k, int) or k <= 0:
            raise ValueError(f"k must be a positive integer, got {k}.")

        results = self.collection.query(  # Storing results.
            query_embeddings=[query_embedding.tolist()],
            n_results=k,  # Getting top results.
            where=where,  # Acts as a filter.
            include=["documents", "metadatas", "distances"]  # Including the following data.
        )
        return results

    # The following function is used to get stats about the collection.
    def get_collection_stats(self) -> Dict:
        if not self.collection:
            raise RuntimeError("Collection is not initialized.")
        # Stats about collection . M2 (Collection count)
        return {
            "name": self.collection_name,
            "count": self.collection.count(),
            "directory": self.persistent_directory
        }

    # The following function is used to delete current collection.
    def delete_collection(self) -> None:
        if not self.collection:
            raise RuntimeError("Collection is not initialized.")
        self.client.delete_collection(name=self.collection_name)
        self.collection = None
        self._existing_ids.clear()
        print(f"Collection {self.collection_name} deleted.")


# # Retrieval

# In[31]:


from PIL import Image
import os
from typing import List,Dict,Optional
from sentence_transformers import CrossEncoder


# In[32]:


# Used to rerank the chunks with relevance .
class Reranker:
    def __init__(self, model_name: str = cfg.reranker_model):
        self.model = CrossEncoder(model_name)
        print(f"Reranker initialized: {model_name}")

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
        fused_scores = [float(item.get("fused_score", 0.0) or 0.0) for item in items] if has_hybrid_signal else [0.0] * len(items)
        fused_normalized = self._normalize(fused_scores) if has_hybrid_signal else [0.0] * len(items)

        cross_modal_boosts = [float(item.get("cross_modal_boost", 0.0) or 0.0) for item in items]
        boost_normalized = self._normalize(cross_modal_boosts, neutral=0.0) if any(boost > 0 for boost in cross_modal_boosts) else [0.0] * len(items)

        ranked_items = []
        for item, ce_score, ce_norm, fused_norm, boost_norm in zip(items, ce_scores, ce_normalized, fused_normalized, boost_normalized):
            item_copy = dict(item)
            item_copy["reranker_score"] = float(ce_score)
            final_rank_score = (0.75 * ce_norm) + (0.20 * fused_norm) + (0.05 * boost_norm) if has_hybrid_signal else ce_norm
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


# # Context formatter

# In[33]:


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


# In[34]:


@dataclass
class RetrievalOutput:
    """
    Structured retrieval response used by the main pipeline and evaluation code.

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
                "hybrid_stats": self.hybrid_stats
            }
        }

    @property
    def image_results(self) -> Dict:
        return {
            "documents": [[item.text for item in self.image_items]],
            "metadatas": [[item.metadata for item in self.image_items]],
            "distances": [[item.distance for item in self.image_items]],
            "ids": [[item.doc_id for item in self.image_items]],
            "retrieval_metrics": {
                "image_search_time": round(self.image_latency_sec, 4),
                "image_total_retrieval_time": round(self.image_latency_sec, 4)
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
            "hybrid_stats": self.hybrid_stats
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


# In[35]:


# Used to format context for the LLM.
class ContextFormatter:
    def __init__(self, max_text_chunks: int = cfg.max_text_chunks, max_images: int = cfg.max_images,
                 text_distance_threshold: float = cfg.text_distance_threshold,
                 image_distance_threshold: float = cfg.image_distance_threshold,
                 use_percentile_filtering: bool = True,
                 percentile_cutoff: int = cfg.percentile_cutoff):

        self.max_text_chunks = max_text_chunks
        self.max_images = max_images
        self.text_distance_threshold = text_distance_threshold
        self.image_distance_threshold = image_distance_threshold
        self.use_percentile_filtering = use_percentile_filtering
        self.percentile_cutoff = percentile_cutoff

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

        search_mode = text_results.get("retrieval_metrics", {}).get("search_mode", "") if isinstance(text_results, dict) else ""
        preserve_ranked_order = search_mode.startswith("hybrid") or search_mode == "semantic_fallback"

        seen = set()
        unique_items = []
        for item in items:
            text_hash = hashlib.md5(item["text"].encode()).hexdigest()[:16]
            if text_hash not in seen:
                seen.add(text_hash)
                unique_items.append(item)

        if preserve_ranked_order:
            return unique_items[:self.max_text_chunks]

        distances = [i["distance"] for i in unique_items]
        if self.use_percentile_filtering and len(distances) > 2:
            threshold = np.percentile(distances, self.percentile_cutoff)
            filtered = [i for i in unique_items if i["distance"] <= threshold]
        else:
            filtered = [i for i in unique_items if i["distance"] <= self.text_distance_threshold]

        if not filtered:
            filtered = unique_items[:self.max_text_chunks]

        filtered.sort(key=lambda x: x["distance"])
        return filtered[:self.max_text_chunks]

    def _load_image(self, image_path: str) -> Optional[Image.Image]:
        if not image_path or not isinstance(image_path, str):
            return None
        if not os.path.exists(image_path):
            print(f"Warning: Image path does not exist: {image_path}")
            return None
        try:
            return Image.open(image_path).convert("RGB")
        except Exception as e:
            print(f"Failed to load image {image_path}: {e}")
            return None

    def _format_text_context(self, text_items: List[Dict]) -> str:
        if not text_items or not isinstance(text_items, list):
            return ""
        lines = []
        for idx, item in enumerate(text_items, start=1):
            if not isinstance(item, dict):
                continue
            meta = item["metadata"] or {}
            source = meta.get("source", "unknown")
            page = meta.get("page_num", "N/A")
            text = item["text"].strip()
            if not text:
                continue
            lines.append(
                f"[{idx}] {text}\n"
                f"(Source: {source}, page {page})"
            )
        return "\n\n".join(lines)

    def _select_images(self, image_results: Dict) -> List[Dict]:
        items = self._flatten_results(image_results)
        if not items:
            return []
        items = [i for i in items if i["distance"] <= self.image_distance_threshold]
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
        image_items = self._select_images(retrieval_output.get("image_results", {}))
        return {
            "query": query,
            "text_context": self._format_text_context(text_items),
            "images": self._format_image_context(image_items)
        }


# In[36]:


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

FALLBACK_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "he", "in", "is", "it", "its", "of", "on", "that", "the", "to", "was",
    "were", "will", "with"
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


# In[37]:


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
        self.processed_docs = [self._preprocess(doc) for doc in documents] if enable_preprocessing else [safe_word_tokenize(doc.lower()) for doc in documents]
        self.bm25 = BM25Okapi(self.processed_docs)
        self.enable_preprocessing = enable_preprocessing
        self.corpus_tokens = set()
        for doc in self.processed_docs:
            self.corpus_tokens.update(doc)
        self.query_stats_history = []
        print(f"BM25 index built over {len(documents)} documents with preprocessing={enable_preprocessing}")

    def _preprocess(self, text: str) -> List[str]:
        if not text:
            return []
        text = text.lower()
        text = re.sub(r'([a-z])(\d)', r' ', text)
        text = re.sub(r'(\d)([a-z])', r' ', text)
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
            variants = [token + 's', token + 'es', token + 'ed', token + 'ing', token[:-1] if token.endswith('s') else None, token[:-2] if token.endswith('es') else None]
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

        scores = self.bm25.get_scores(tokenized_query)
        ranked_indices = np.argsort(scores)[::-1]
        positive_indices = [i for i in ranked_indices if scores[i] > 0]
        top_indices = positive_indices[:k]
        top_scores = [float(scores[i]) for i in top_indices]
        stats = {
            "query_length_tokens": len(tokenized_query),
            "expanded_tokens": len(tokenized_query) if expand_query else 0,
            "max_score": max(top_scores) if top_scores else 0.0,
            "mean_score": statistics.mean(top_scores) if top_scores else 0.0,
            "min_score": min(top_scores) if top_scores else 0.0,
            "std_score": statistics.stdev(top_scores) if len(top_scores) > 1 else 0.0,
            "non_zero_docs": len([s for s in scores if s > 0]),
            "corpus_coverage": len([s for s in scores if s > 0]) / len(scores) if len(scores) > 0 else 0.0
        }
        self.query_stats_history.append(stats)
        return {
            "documents": [[self.documents[i] for i in top_indices]],
            "metadatas": [[self.metadatas[i] for i in top_indices]],
            "ids": [[self.doc_ids[i] for i in top_indices]],
            "scores": [float(scores[i]) for i in top_indices],
            "query_tokens": tokenized_query,
            "bm25_stats": stats
        }


# In[38]:


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

        print(f"Building BM25 index from {len(documents)} documents.")
        return BM25Index(
            documents=documents,
            doc_ids=doc_ids,
            metadatas=metadatas,
            enable_preprocessing=enable_preprocessing
        )


# In[39]:


from typing import Tuple, Dict, List, Optional


# In[40]:


# Used to retrieve documents from database using input query from user .
class RetrievalRag:
    def __init__(self, image_embedder: 'ImageEmbeddingModel', text_embedder: 'TextEmbeddingModel',
                 image_vectordb: 'VectorStore', text_vectordb: 'VectorStore',
                 use_reranker: bool = cfg.use_reranker, use_hybrid: bool = cfg.use_hybrid,
                 bm25_weight: float = cfg.bm25_weight, semantic_weight: float = cfg.semantic_weight,
                 formatter: Optional['ContextFormatter'] = None,
                 adaptive_weighting: bool = cfg.adaptive_weighting,
                 score_fusion: bool = cfg.score_fusion):
        self.image_embedder = image_embedder
        self.text_embedder = text_embedder
        self.image_vectordb = image_vectordb
        self.text_vectordb = text_vectordb
        self.reranker = Reranker() if use_reranker else None
        self.formatter = formatter or ContextFormatter()
        self.use_hybrid = use_hybrid
        self.adaptive_weighting = adaptive_weighting
        self.score_fusion = score_fusion
        if not (0.0 <= bm25_weight <= 1.0) or not (0.0 <= semantic_weight <= 1.0):
            raise ValueError("BM25 and semantic weights must be between 0.0 and 1.0.")
        if abs((bm25_weight + semantic_weight) - 1.0) > 1e-6:
            raise ValueError("BM25 weight and semantic weight must sum to 1.0.")
        self.bm25_weight = bm25_weight
        self.semantic_weight = semantic_weight
        self.hybrid_metrics_history = []
        if self.use_hybrid:
            self.bm25_index = BM25IndexBuilder.from_vectorstore(text_vectordb, enable_preprocessing=True)
            print(f"Hybrid search enabled: {'Score-based fusion' if score_fusion else 'RRF'} with adaptive={adaptive_weighting}")
        else:
            self.bm25_index = None
            print("Hybrid search disabled: using semantic search only.")

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    def _classify_query_type(self, query: str) -> str:
        query_lower = query.lower().strip()
        words = query_lower.split()
        word_count = len(words)
        num_count = len(re.findall(r'\d+', query_lower))
        explanatory_markers = ['why', 'how', 'explain', 'describe', 'what', 'compare', 'analyze']
        if word_count <= 6 or (num_count > 0 and num_count / max(word_count, 1) > 0.18):
            return 'keyword'
        elif word_count >= 14 or any(marker in query_lower for marker in explanatory_markers):
            return 'semantic'
        return 'balanced'

    def _normalize_scores(self, scores: List[float], method: str = 'minmax') -> List[float]:
        if not scores:
            return []
        scores = np.array(scores)
        if method == 'minmax':
            min_score, max_score = np.min(scores), np.max(scores)
            if max_score - min_score < 1e-6:
                return [0.5] * len(scores)
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
            entry["fused_score"] = (entry["bm25_norm"] * self.bm25_weight) + (entry["semantic_norm"] * self.semantic_weight)
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
            "bm25_contribution_mean": np.mean([entry["bm25_norm"] * self.bm25_weight for _, entry in sorted_docs]) if sorted_docs else 0.0,
            "semantic_contribution_mean": np.mean([entry["semantic_norm"] * self.semantic_weight for _, entry in sorted_docs]) if sorted_docs else 0.0,
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

    def _rrf_fusion(self, bm25_results: Dict, semantic_results: Dict, k_rrf: int = cfg.rrf_k_constant) -> Tuple[Dict, Dict]:
        scores = {}
        bm25_docs = bm25_results.get("documents", [[]])[0]
        bm25_ids = bm25_results.get("ids", [[]])[0]
        bm25_metas = bm25_results.get("metadatas", [[]])[0]
        bm25_scores = bm25_results.get("scores", [])
        for rank, doc_id in enumerate(bm25_ids):
            rrf_score = self.bm25_weight * (1 / (k_rrf + rank + 1))
            scores[doc_id] = {"rrf_score": rrf_score, "text": bm25_docs[rank], "metadata": bm25_metas[rank], "distance": 1.0, "bm25_raw": bm25_scores[rank] if rank < len(bm25_scores) else 0.0, "source": "bm25"}
        sem_docs = semantic_results.get("documents", [[]])[0]
        sem_ids = semantic_results.get("ids", [[]])[0]
        sem_metas = semantic_results.get("metadatas", [[]])[0]
        sem_distances = semantic_results.get("distances", [[]])[0]
        for rank, doc_id in enumerate(sem_ids):
            rrf_score = self.semantic_weight * (1 / (k_rrf + rank + 1))
            if doc_id in scores:
                scores[doc_id]["rrf_score"] += rrf_score
                scores[doc_id]["distance"] = float(sem_distances[rank])
                scores[doc_id]["source"] = "both"
            else:
                scores[doc_id] = {"rrf_score": rrf_score, "text": sem_docs[rank], "metadata": sem_metas[rank], "distance": float(sem_distances[rank]), "bm25_raw": 0.0, "source": "semantic"}
        sorted_docs = sorted(scores.items(), key=lambda x: x[1]["rrf_score"], reverse=True)
        stats = {
            "bm25_only_docs": len([s for s in scores.values() if s["source"] == "bm25"]),
            "semantic_only_docs": len([s for s in scores.values() if s["source"] == "semantic"]),
            "both_signals_docs": len([s for s in scores.values() if s["source"] == "both"])
        }
        return {
            "documents": [[entry["text"] for _, entry in sorted_docs]], "metadatas": [[entry["metadata"] for _, entry in sorted_docs]],
            "distances": [[entry["distance"] for _, entry in sorted_docs]], "ids": [[doc_id for doc_id, _ in sorted_docs]],
            "fused_scores": [[entry["rrf_score"] for _, entry in sorted_docs]], "bm25_scores": [[entry["bm25_raw"] for _, entry in sorted_docs]]
        }, stats

    def _get_adaptive_weights(self, query: str, bm25_results: Dict) -> Tuple[float, float, Dict[str, Any]]:
        if not self.adaptive_weighting:
            return self.bm25_weight, self.semantic_weight, {"query_type": self._classify_query_type(query), "bm25_signal_strength": 0.0, "lexical_query_signal": 0.0, "fallback_to_semantic": False, "fallback_reason": "", "weight_adjusted": False}
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
        explanatory_signal = 1.0 if any(token in query_lower for token in ['why', 'how', 'explain', 'describe', 'compare', 'analyze']) else 0.0
        short_query_signal = max(0.0, 1.0 - min(word_count / 12.0, 1.0))
        numeric_signal = min(num_ratio / 0.25, 1.0)
        lexical_query_signal = max(short_query_signal, numeric_signal)
        max_signal = min(max_score / max(cfg.bm25_strong_max_score_threshold * 4.0, 1.0), 1.0)
        std_signal = min(std_score / max(cfg.bm25_strong_std_threshold * 3.0, 1.0), 1.0)
        specificity_signal = 1.0 - min(coverage, 1.0)
        bm25_signal_strength = (0.45 * max_signal) + (0.20 * std_signal) + (0.20 * specificity_signal) + (0.15 * lexical_query_signal)
        if non_zero_docs == 0 or max_score <= 0.0:
            return 0.0, 1.0, {"query_type": query_type, "bm25_signal_strength": round(bm25_signal_strength, 4), "lexical_query_signal": round(lexical_query_signal, 4), "fallback_to_semantic": True, "fallback_reason": "no_positive_bm25_scores", "weight_adjusted": True}
        base_weight = {"keyword": 0.55, "balanced": 0.40, "semantic": 0.25}.get(query_type, self.bm25_weight)
        bm25_weight = base_weight + (0.35 * (bm25_signal_strength - 0.5))
        if explanatory_signal > 0:
            bm25_weight -= 0.05
        if numeric_signal > 0.35:
            bm25_weight += 0.05
        lower_bound, upper_bound = {"keyword": (0.25, 0.85), "balanced": (0.20, 0.75), "semantic": (0.15, 0.60)}.get(query_type, (0.15, 0.85))
        bm25_weight = round(self._clamp(bm25_weight, lower_bound, upper_bound), 3)
        semantic_weight = round(1.0 - bm25_weight, 3)
        return bm25_weight, semantic_weight, {"query_type": query_type, "bm25_signal_strength": round(bm25_signal_strength, 4), "lexical_query_signal": round(lexical_query_signal, 4), "fallback_to_semantic": False, "fallback_reason": "", "weight_adjusted": abs(bm25_weight - self.bm25_weight) > 1e-6}

    def retrieve_text(self, query: str, k: int = cfg.text_k) -> Dict:
        if not query or not query.strip():
            raise ValueError("Query must be a non-empty string.")
        if not isinstance(k, int) or k <= 0:
            raise ValueError(f"k must be a positive integer, got {k}.")
        start_total = perf_counter()
        start_embed = perf_counter()
        query_embedding = self.text_embedder.embed_query(query)
        embed_time = perf_counter() - start_embed
        start_search = perf_counter()
        semantic_results = self.text_vectordb.query(query_embedding=query_embedding, k=k*2)
        hybrid_stats = {}
        if self.use_hybrid and self.bm25_index:
            bm25_results = self.bm25_index.query(query=query, k=k*2, expand_query=True)
            adaptive_bm25_w, adaptive_sem_w, adaptive_info = self._get_adaptive_weights(query, bm25_results)
            bm25_ids = bm25_results.get("ids", [[]])[0]
            overlap_stats = self._calculate_overlap(bm25_ids, semantic_results.get("ids", [[]])[0]) if bm25_ids else {"intersection_size": 0, "union_size": 0, "jaccard_similarity": 0.0, "overlap_percentage": 0.0, "intersection_ids": []}
            if adaptive_info.get("fallback_to_semantic", False):
                results = semantic_results
                fusion_stats = {"bm25_contribution_mean": 0.0, "semantic_contribution_mean": 1.0, "bm25_only_docs": 0, "semantic_only_docs": len(semantic_results.get("ids", [[]])[0]), "both_signals_docs": 0}
                search_mode = "semantic_fallback"
            else:
                orig_bm25_w, orig_sem_w = self.bm25_weight, self.semantic_weight
                self.bm25_weight, self.semantic_weight = adaptive_bm25_w, adaptive_sem_w
                try:
                    results, fusion_stats = self._fuse_scores(bm25_results, semantic_results) if self.score_fusion else self._rrf_fusion(bm25_results, semantic_results, k_rrf=cfg.rrf_k_constant)
                finally:
                    self.bm25_weight, self.semantic_weight = orig_bm25_w, orig_sem_w
                search_mode = "hybrid_score_fusion" if self.score_fusion else "hybrid_rrf"
            hybrid_stats = {
                "query_type": adaptive_info["query_type"], "bm25_weight_used": adaptive_bm25_w, "semantic_weight_used": adaptive_sem_w,
                "bm25_max_score": bm25_results.get("bm25_stats", {}).get("max_score", 0), "bm25_mean_score": bm25_results.get("bm25_stats", {}).get("mean_score", 0),
                "bm25_std_score": bm25_results.get("bm25_stats", {}).get("std_score", 0), "bm25_corpus_coverage": bm25_results.get("bm25_stats", {}).get("corpus_coverage", 0),
                "bm25_signal_strength": adaptive_info.get("bm25_signal_strength", 0.0), "lexical_query_signal": adaptive_info.get("lexical_query_signal", 0.0),
                "weight_adjusted": adaptive_info.get("weight_adjusted", False), "fallback_to_semantic": adaptive_info.get("fallback_to_semantic", False),
                "fallback_reason": adaptive_info.get("fallback_reason", ""), "overlap_jaccard": overlap_stats["jaccard_similarity"],
                "overlap_percentage": overlap_stats["overlap_percentage"], "fusion_stats": fusion_stats
            }
        else:
            results = semantic_results
            search_mode = "semantic_only"
        for key in ["documents", "metadatas", "distances", "ids", "fused_scores", "bm25_scores"]:
            if key in results and results[key]:
                results[key][0] = results[key][0][:k]
        search_time = perf_counter() - start_search
        total_time = perf_counter() - start_total
        results["retrieval_metrics"] = {"text_embed_time": round(embed_time, 4), "text_search_time": round(search_time, 4), "text_total_retrieval_time": round(total_time, 4), "search_mode": search_mode, "hybrid_stats": hybrid_stats}
        return results

    def retrieve_images(self, query: str, k: int = cfg.image_k) -> Dict:
        if not query or not query.strip():
            raise ValueError("Query must be a non-empty string.")
        if not isinstance(k, int) or k <= 0:
            raise ValueError(f"k must be a positive integer, got {k}.")
        start_total = perf_counter()
        start_embed = perf_counter()
        query_embedding = self.image_embedder.embed_query(query)
        embed_time = perf_counter() - start_embed
        start_query = perf_counter()
        results = self.image_vectordb.query(query_embedding=query_embedding, k=k)
        query_time = perf_counter() - start_query
        total_time = perf_counter() - start_total
        results["retrieval_metrics"] = {"image_embed_time": round(embed_time, 4), "image_search_time": round(query_time, 4), "image_total_retrieval_time": round(total_time, 4)}
        return results

    def retrieve(self, query: str, text_k: int = cfg.text_k, image_k: int = cfg.image_k, rerank_k: int = cfg.rerank_k) -> RetrievalOutput:
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
        text_items = self.formatter._flatten_results(raw_text_results)
        image_items = self.formatter._flatten_results(raw_image_results)
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
        for item in text_items:
            related_ids = item["metadata"].get("related_image_ids", [])
            related_refs = set()
            for related_id in related_ids:
                related_str = str(related_id)
                related_refs.add(related_str)
                related_refs.add(related_str.replace("\\", "/").split("/")[-1])
            overlap = related_refs & retrieved_image_refs
            if overlap:
                boost = min(0.3, len(overlap) * 0.1)
                item["distance"] = item["distance"] * (1.0 - boost)
                item["cross_modal_boost"] = round(boost, 3)
        if self.reranker and text_items:
            reranked_items = self.reranker.rerank(query=query, items=text_items, top_k=min(rerank_k, len(text_items)))
        elif any("fused_score" in item for item in text_items):
            reranked_items = sorted(text_items, key=lambda item: item.get("fused_score", 0.0), reverse=True)[:rerank_k]
        else:
            reranked_items = sorted(text_items, key=lambda item: item["distance"])[:rerank_k]
        text_result_items = [RetrievalItem(doc_id=item.get("doc_id", ""), text=item.get("text", ""), metadata=item.get("metadata", {}), distance=float(item.get("distance", 1.0)), similarity=max(0.0, 1.0 - float(item.get("distance", 1.0))), fused_score=item.get("fused_score"), rank=rank, modality="text", retrieval_latency_sec=round(text_latency, 4)) for rank, item in enumerate(reranked_items, start=1)]
        image_result_items = [RetrievalItem(doc_id=item.get("doc_id", ""), text=item.get("text", ""), metadata=item.get("metadata", {}), distance=float(item.get("distance", 1.0)), similarity=max(0.0, 1.0 - float(item.get("distance", 1.0))), fused_score=None, rank=rank, modality="image", retrieval_latency_sec=round(image_latency, 4)) for rank, item in enumerate(image_items, start=1)]
        cosine_sim_text = float(np.mean([item.similarity for item in text_result_items])) if text_result_items else 0.0
        cosine_sim_image = float(np.mean([item.similarity for item in image_result_items])) if image_result_items else 0.0
        overall_time = perf_counter() - start_total
        return RetrievalOutput(query=query, text_items=text_result_items, image_items=image_result_items, text_latency_sec=round(text_latency, 4), image_latency_sec=round(image_latency, 4), total_latency_sec=round(overall_time, 4), cosine_sim_text=round(cosine_sim_text, 4), cosine_sim_image=round(cosine_sim_image, 4), search_mode=raw_text_results.get("retrieval_metrics", {}).get("search_mode", "semantic_only"), hybrid_stats=raw_text_results.get("retrieval_metrics", {}).get("hybrid_stats", {}))


# In[41]:


from typing import List, Dict
import matplotlib.pyplot as plt
from PIL import Image


# # LLM

# In[42]:


import ollama # Used to load model .
from textwrap import dedent # Used for spacing problems in prompt .
from tabulate import tabulate # Used for creating a table for displaying models .
import subprocess # Used to start ollama server .
import time # For waiting .
import requests # Used to access ollama server .
import base64
import io
from PIL import Image
from sentence_transformers import util
import re


# In[43]:


# Used to initialize a language model and generate responses .
class LocalLLM:
    def __init__(self, model_name: str = cfg.llm_model, text_embedder: TextEmbeddingModel = None):
        # Default model; can change.
        self.model_name = model_name
        self.process = self.ollama_server(process="start")
        self.text_embedder = text_embedder or TextEmbeddingModel()
        if not self.is_model_available(self.model_name):
            # Checking if model is available or valid.
            available = [m['model_name'] for m in self.available_models()]
            raise ValueError(
                f"Model '{model_name}' not available. "
                f"Available models: {available}"
            )
        self.tokenizer = tiktoken.get_encoding(cfg.tiktoken_encoding)

    # The following function is used to start or stop ollama server.
    def ollama_server(self, process: str):
        if process == "start":
            try:
                requests.get("http://localhost:11434/api/tags", timeout=1)  # Checking if ollama server is already started.
                print("Ollama already running")
                return "external"
            except:
                pass
            ollama_process = subprocess.Popen(  # Starting ollama server if not started.
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False
            )
            for _ in range(10):  # Checking if server started.
                try:
                    requests.get("http://localhost:11434/api/tags", timeout=1)
                    print("Ollama server started")
                    return ollama_process
                except:
                    time.sleep(1)
            raise RuntimeError("Ollama failed to start")  # If server did not start after many tries, raise error.

        elif process == "stop":  # Stopping ollama server.
            if isinstance(self.process, subprocess.Popen):  # Checking if server was started here.
                self.process.terminate()
                self.process.wait()
                print("Ollama server successfully stopped.")
            else:
                print("Ollama was not started by this process")  # If started externally, notify.
            return None
        else:
            raise ValueError("Input can be either 'start' or 'stop'")  # Input validation.

    # The following function is used to check available models in the local device.
    def available_models(self):
        response = ollama.list()  # Getting available models.
        models_available = response.get('models', [])  # Safer access
        models = []
        for m in models_available:  # Getting required information from model.
            models.append({
                "model_name": m.get('model'),
                "parameters": m.get('details', {}).get('parameter_size')
            })
        return models if models else []

    # The following function is to check if a specific model is available in local device.
    def is_model_available(self, model_name):
        models = self.available_models()
        return any(m['model_name'] == model_name for m in models)

    # The following function is used to build prompt using user query and retrieved documents.
    # Enhanced prompt with chain-of-thought reasoning for better quality answers.
    def build_prompt(self, query: str, context: str):
        return f"""You are a rigorous scientific analyst specialized in PDF documents with images and captions. Your task is to answer the question using ONLY the provided context, images, and captions. You must remain strictly evidence-based and avoid speculation. You are not allowed to use external knowledge, assumptions, or inferred facts. If the available evidence is insufficient, you must clearly explain why.

----------------------------------------------------------------
CORE PRINCIPLES
----------------------------------------------------------------
- Use only the provided text, images, and captions.
- Prioritize visual evidence from images if they directly relate to the text.
- Do not introduce outside knowledge.
- Do not assume missing details.
- If the text does not explicitly support the answer, clearly state that the context is insufficient.
- If an image or caption contradicts the text, clearly explain the inconsistency.
- If an image is unrelated to the object described in the text, explicitly state that it is not relevant.
- Before evaluating relevance, verify that the image depicts the SAME object or phenomenon mentioned in the text.
- Emphasize captions as they provide direct context to images.

----------------------------------------------------------------
REQUIRED STRUCTURE - USE CHAIN-OF-THOUGHT REASONING
----------------------------------------------------------------
1) Textual Evidence Assessment
   - Identify the specific object(s), phenomenon, or event described in the text.
   - Determine whether the text explicitly supports the question.
   - Summarize the exact supporting statements.
   - If the text does not adequately support the answer, explain why and stop.

2) Image and Caption Evaluation
   - Images provided: Yes / No
   - For each image: Identify what object or phenomenon is shown, describe the caption if present.
   - Compare it to the object described in the text.
   - State whether they refer to the same object.
   - If they refer to different objects, clearly state that the image is not relevant.
   - Describe only what is directly visible.
   - Conclude whether the image/caption:
     • Supports the text
     • Contradicts the text
     • Is unrelated or insufficient

3) Integrated Reasoning with Chain-of-Thought
   - Think step-by-step about how the evidence connects to the question.
   - Connect the validated textual evidence with any relevant visual/caption evidence.
   - Explain mechanisms, processes, and any numerical details mentioned.
   - Identify logical steps that link evidence to conclusion.
   - Show your reasoning process explicitly.
   - Explicitly mention any limitations or missing information.

4) Final Conclusion
   - Provide a well-structured, natural explanation based on your reasoning.
   - Minimum 8–12 detailed sentences.
   - The conclusion must strictly follow from validated evidence.
   - Do not introduce any information not present in the provided material.
   - Cite specific sources when making claims (e.g., "According to [Source], page [X]...").

----------------------------------------------------------------
Context: {context}

Question: {query}

Answer (think step-by-step):""".strip()

    # The following function is used to convert PIL image to base64 since LLM models can read base64 or need image path.
    @staticmethod
    def pil_to_base64(img: Image.Image) -> str:
        buffer = io.BytesIO()
        img = img.convert("RGB")
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    # The following function is used to generate response from language model.
    def generate_response(self, query: str, context: str, images: Optional[List[Dict]] = None,
                         stream: bool = True, temperature: float = 0.7, max_tokens: int = 500) -> Dict:
        if not query or not query.strip():  # Query validation.
            raise ValueError("Query cannot be empty")
        if not context or not context.strip():
            if not images:
                raise ValueError("Context cannot be empty when no images are provided")  # Context validation.
        if len(context) > 10000:  # Checking if context is too large.
            print("Warning: Large context may be slow")

        try:
            prompt = self.build_prompt(query, context)  # Building a prompt using query and context.
            image_payload = []
            if images:
                for img_dict in images:
                    img = img_dict.get("image")
                    if isinstance(img, Image.Image):
                        image_payload.append(self.pil_to_base64(img))
                    else:
                        raise TypeError("Images must be PIL.Image.Image")

            # Context length . M9 (Input context length)
            context_chars = len(context)
            context_tokens = len(self.tokenizer.encode(context))

            start_time = time.perf_counter()
            response = ollama.chat(  # Getting response from model.
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are a grounded assistant that answers only from provided text and images. Think step-by-step and show your reasoning."},
                    {
                        "role": "user",
                        "content": prompt,
                        "images": image_payload if image_payload else None
                    }
                ],
                stream=stream,
                options={
                    'temperature': temperature,
                    "num_predict": max_tokens
                },
                keep_alive=0
            )

            if stream:  # Displaying output through streaming.
                print(f"\n{'='*80}")
                print(f"QUERY: {query}")
                print(f"{'='*80}")
                print("ANSWER:")
                print("-" * 80)
                full_response = ""
                try:
                    for chunk in response:  # Displaying response as model gives output.
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            print(content, end="", flush=True)
                            full_response += content
                    print("\n" + "=" * 80)
                except Exception as e:
                    print(f"\nError during streaming: {e}")
                    raise
                final_response = full_response
            else:
                final_response = response["message"]["content"]  # If stream is off, give output all at once.

            generation_time = time.perf_counter() - start_time

            # ──────────────────── M13: Factual Consistency Distance (FCD) ───────────────────────────────
            fcd = None
            try:
                resp_emb = self.text_embedder.embed_query(final_response)
                ctx_emb = self.text_embedder.embed_query(prompt)
                sim = util.cos_sim(resp_emb, ctx_emb).item()
                dist = 1 - sim
                fcd = dist * 100
            except Exception as e:
                print(f"FCD computation failed: {e}")
                fcd = None

            # ────────────── M14: Faithfulness / Citation Recall ───────────────────────────────
            faithfulness = 0.0
            try:
                response_lower = final_response.lower()
                cited_sources = set(re.findall(r'(source:\s*[^,\n]+?|\w+\.pdf|https?://[^\s<>\n]+)', response_lower, re.IGNORECASE))
                cited_pages = set(re.findall(r'page\s*(\d+)', response_lower, re.IGNORECASE))
                cited_images = set(re.findall(r'(image\s*\d+|caption|figure\s*\d+)', response_lower, re.IGNORECASE))

                context_lower = context.lower()
                retrieved_sources = set(re.findall(r'(source:\s*[^,\n]+?|\w+\.pdf|https?://[^\s<>\n]+)', context_lower, re.IGNORECASE))
                retrieved_pages = set(re.findall(r'page\s*(\d+)', context_lower, re.IGNORECASE))
                retrieved_images = set(re.findall(r'(image\s*\d+|caption|figure\s*\d+)', context_lower, re.IGNORECASE))

                total_retrieved = len(retrieved_sources | retrieved_pages | retrieved_images) or 1
                total_cited = len(cited_sources | cited_pages | cited_images)
                faithfulness = (total_cited / total_retrieved) * 100
            except Exception as e:
                print(f"M14 Faithfulness computation failed: {e}")
                faithfulness = 0.0

            return {
                "response": final_response,
                "context_length_chars": context_chars,
                "context_length_tokens": context_tokens,
                "generation_time_sec": round(generation_time, 4),
                "factual_consistency_distance": round(fcd, 2) if fcd is not None else None,
                "faithfulness_percentage": round(faithfulness, 2)
            }
        except Exception as e:
            raise RuntimeError(f"Error generating response: {e}") from e


# # Testing

# In[44]:


import psutil


# In[45]:


TEST_QUESTIONS = [
    # ======================
    # SECTION 1: VOYAGER GRAND TOUR (Questions 1-8)
    # ======================
    {
        "id": 1,
        "type": "numerical_trajectory_constraint",
        "question": (
            "The trajectory diagram labels Voyager 1 reaching Jupiter on 9 Jul 79 and Saturn on 12 Nov 80, "
            "while Voyager 2's path extends to Uranus on 24 Jan 86 and Neptune on 25 Aug 89. "
            "Why do these four specific encounter dates form a one-time window that will not repeat for 175 years, "
            "and how does the visual geometry of the two diverging paths encode the irreversible cost of "
            "prioritizing the Titan flyby over allowing Voyager 1 to continue toward Uranus and Neptune?"
        ),
        "expected_images": True,
        "image_reference": {
            "page": 1,
            "position": "Left diagram: annotated flight paths of Voyager 1 and 2 with labeled planetary encounter dates"
        },
        "required_context": {
            "pages": [1, 3],
            "focus": (
                "175-year planetary alignment window, labeled encounter dates on trajectory diagram, "
                "Titan flyby as trajectory-terminating decision, Grand Tour original vs scaled-back plan"
            )
        }
    },
    {
        "id": 2,
        "type": "scale_paradox_with_specific_distance",
        "question": (
            "At exactly 7.25 million miles, Voyager 1 captured the first-ever single-frame photograph "
            "of the Earth-Moon system on September 18, 1977. Given that 7.25 million miles is less than "
            "0.008% of the distance to Neptune the spacecraft would later reach, why is this specific "
            "distance the precise geometric threshold at which both bodies first fit within a single camera "
            "frame, and what does successfully framing both bodies prove about the optical system's "
            "capabilities that the subsequent planetary montage photographs would depend on?"
        ),
        "expected_images": True,
        "image_reference": {
            "page": 2,
            "position": "Left image: first photograph of Earth and Moon together in a single frame, taken September 18, 1977"
        },
        "required_context": {
            "pages": [2, 4],
            "focus": (
                "Earth-Moon photo at 7.25 million miles on September 18 1977, solar system scale context, "
                "camera field-of-view implication, Family Portrait from 6 billion km as endpoint comparison"
            )
        }
    },
    {
        "id": 3,
        "type": "jupiter_discovery_density_visual_evidence",
        "question": (
            "At Jupiter, the Voyagers discovered that Io has active volcanoes, Europa has a geologically "
            "young ice surface possibly floating on an ocean of liquid water, Jupiter has a faint ring system, "
            "and three previously undiscovered small moons exist. Looking at the Jupiter montage image, "
            "which of these four discoveries is visually detectable from the montage alone, "
            "which require spectroscopic or instrument data beyond photography, and why does the "
            "distinction between visually-inferable and instrument-dependent discoveries fundamentally "
            "limit what any future flyby mission relying only on cameras could confirm?"
        ),
        "expected_images": True,
        "image_reference": {
            "page": 3,
            "position": "Left montage: Jupiter and its four largest moons Io, Europa, Ganymede, Callisto — not to scale"
        },
        "required_context": {
            "pages": [2, 3],
            "focus": (
                "Io active volcanoes, Europa ice surface and possible liquid water ocean, "
                "Jupiter faint ring system, three undiscovered small moons, "
                "visual inference vs instrument measurement boundary"
            )
        }
    },
    {
        "id": 4,
        "type": "saturn_titan_flyby_tradeoff_irreversibility",
        "question": (
            "Voyager 1 came within 4,000 miles of Titan's South Pole during its closest approach, "
            "while Voyager 2 made its closest Saturn approach at 63,000 miles on August 25, 1981. "
            "The Titan encounter permanently altered Voyager 1's trajectory, ending all future planetary flybys. "
            "Given that the Voyagers discovered Saturn's rings were far more complex than previously believed, "
            "Titan may have lakes of liquid hydrocarbons, and three previously unknown satellites were found — "
            "reconstruct the decision logic that made a 4,000-mile Titan flyby worth sacrificing all future "
            "encounters, and explain what the Saturn and moons montage image reveals about ring complexity "
            "that could not have been anticipated from Earth-based observations."
        ),
        "expected_images": True,
        "image_reference": {
            "page": 3,
            "position": "Right montage: Saturn and several of its moons — not to scale"
        },
        "required_context": {
            "pages": [3],
            "focus": (
                "Titan flyby 4,000 miles South Pole, Voyager 2 Saturn closest approach 63,000 miles August 25 1981, "
                "ring complexity discovery, Titan liquid hydrocarbon lakes, trajectory sacrifice decision, "
                "three new Saturn satellites"
            )
        }
    },
    {
        "id": 5,
        "type": "moon_discovery_asymmetry_numerical",
        "question": (
            "Voyager 2 discovered 11 new moons at Uranus during a flyby at 50,600 miles altitude "
            "and only 6 new moons at Neptune during a much closer pass at 3,076 miles altitude. "
            "Why does cutting flyby altitude by more than 94% at Neptune result in fewer moon discoveries "
            "than the more distant Uranus encounter, and what does this specific inverse relationship — "
            "combined with the visual dominance of Triton in the Neptune montage image — reveal about "
            "how a single massive moon can suppress the detectability of smaller satellites in its system?"
        ),
        "expected_images": True,
        "image_reference": {
            "page": 4,
            "position": "Left montage: Uranus with several larger moons; Right image: Neptune with its largest moon Triton dominating the frame"
        },
        "required_context": {
            "pages": [3, 4],
            "focus": (
                "Uranus flyby 50,600 miles yielding 11 moons, Neptune flyby 3,076 miles yielding 6 moons, "
                "Triton dominance at Neptune, satellite system architecture, observation geometry constraints, "
                "Neptune Great Dark Spot and magnetic field"
            )
        }
    },
    # {
    #     "id": 6,
    #     "type": "family_portrait_timing_philosophy",
    #     "question": (
    #         "On February 14, 1990 — more than 12 years after launch and shortly before its cameras "
    #         "were permanently turned off to conserve power — Voyager 1 spun around and captured a "
    #         "60-image mosaic Family Portrait of six planets from 6 billion kilometers. "
    #         "Why was this specific act structurally impossible to perform at any earlier point in the mission, "
    #         "what does the decision to use the final camera power for a backward-looking portrait rather than "
    #         "forward-looking interstellar imaging reveal about the mission's philosophical priorities, "
    #         "and why is Earth's appearance as a 'pale blue dot' in this image scientifically informative "
    #         "despite being too small to show any surface detail?"
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 4,
    #         "position": "Bottom image: Voyager 1 Family Portrait mosaic of six planets taken February 14, 1990, from 6 billion km"
    #     },
    #     "required_context": {
    #         "pages": [4],
    #         "focus": (
    #             "February 14 1990 Family Portrait, 60-image mosaic, 6 billion km distance, "
    #             "camera shutdown to conserve power, pale blue dot Earth appearance, "
    #             "backward-looking vs forward-looking imaging philosophy"
    #         )
    #     }
    # },
    # {
    #     "id": 7,
    #     "type": "interstellar_mission_power_projection",
    #     "question": (
    #         "The Voyager Interstellar Mission began in 1989 with both spacecraft operating more than "
    #         "10 billion miles from Earth, most science instruments turned off to conserve power, "
    #         "and projected operational life estimated to continue until approximately 2025. "
    #         "Voyager 1 crossed into true interstellar space in August 2012, while Voyager 2 was "
    #         "expected to follow within a few years. Given that neither spacecraft will come within "
    #         "one light-year of any star for tens of thousands of years, explain why the boundary "
    #         "crossing in 2012 was scientifically difficult to confirm in real time, and what the "
    #         "sequential shutdown of science instruments tells us about how the mission's definition "
    #         "of 'operational' shifted across its four decades."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 2,
    #         "position": "Right image: Voyager spacecraft as it would appear in space, showing the dish antenna, instrument boom, and RTG power source configuration"
    #     },
    #     "required_context": {
    #         "pages": [5],
    #         "focus": (
    #             "Voyager Interstellar Mission 1989, 10 billion miles from Earth, science instruments shutdown, "
    #             "2025 operational projection, Voyager 1 interstellar boundary August 2012, "
    #             "Voyager 2 follow-suit estimate, boundary confirmation data analysis difficulty"
    #         )
    #     }
    # },
    # {
    #     "id": 8,
    #     "type": "analog_encoding_temporal_robustness",
    #     "question": (
    #         "Each Voyager carries a golden record using 1970s phonograph vinyl technology, physically "
    #         "paired with a stylus and engraved playback instructions — intended for a potential finder "
    #         "tens of thousands of years from now, long after the spacecraft will have ceased to "
    #         "communicate with Earth. The record contains sounds including music, greetings in multiple "
    #         "languages, and whale songs, plus photographs of Earthly activities. "
    #         "Explain why analog groove-based encoding is specifically more durable than any digital format "
    #         "for this timescale, what universal physical assumption about the finder the stylus-and-groove "
    #         "mechanism encodes, and why the engraved cover instructions are themselves a self-referential "
    #         "instance of the communication problem they are trying to solve."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 5,
    #         "position": "Both images: Golden Record vinyl disc showing 'Sounds of Earth' label, and cover plate with engraved playback diagram, pulsar star map, and stylus illustration"
    #     },
    #     "required_context": {
    #         "pages": [5],
    #         "focus": (
    #             "Golden Record 1970s analog vinyl, physical stylus inclusion, tens-of-thousands-year timescale, "
    #             "engraved playback instructions self-referentiality, sounds music greetings whale songs, "
    #             "photographs of Earthly activities, interstellar finder physical law assumptions"
    #         )
    #     }
    # },
    #
    # # ======================
    # # SECTION 2: CURIOSITY / MARS SCIENCE LABORATORY (Questions 9-16)
    # # ======================
    # {
    #     "id": 9,
    #     "type": "precision_landing_causal_prerequisite",
    #     "question": (
    #         "The Mars Science Laboratory achieved a landing ellipse of approximately 20 kilometers — "
    #         "a five-fold improvement over earlier Mars landings — and the document explicitly states "
    #         "the Gale Crater site 'would not have been considered safe' under the previous precision standard. "
    #         "Trace the complete causal chain from the 20-kilometer ellipse to the specific geological "
    #         "target at Yellowknife Bay, explaining why each intermediate step — site selection eligibility, "
    #         "proximity to the crater wall, access to Mount Sharp's layered base strata — was individually "
    #         "unlocked by this specific numerical precision improvement and not by any lesser improvement."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 1,
    #         "position": "Bottom image: Gale Crater aerial view showing crater size comparable to Connecticut and Rhode Island combined"
    #     },
    #     "required_context": {
    #         "pages": [1, 2],
    #         "focus": (
    #             "20km landing ellipse five-fold improvement, Gale Crater wall proximity Mount Sharp, "
    #             "Yellowknife Bay ancient river and fan system, site selection over 30 Martian locations 100 scientists, "
    #             "habitability evidence chain"
    #         )
    #     }
    # },
    # {
    #     "id": 10,
    #     "type": "geological_stream_flow_evidence_limits",
    #     "question": (
    #         "In the first weeks after landing, the rock outcrop called Link showed rounded pebbles "
    #         "mixed with hardened sand in conglomerate rocks, which the team interpreted as evidence "
    #         "that 'water once coursed vigorously over the surface.' Explain the specific physical "
    #         "process by which flowing water rounds pebbles, why this visual evidence alone cannot "
    #         "distinguish between a single catastrophic flood and sustained habitable water flow, "
    #         "and what additional instrument measurements from Yellowknife Bay's John Klein drill sample "
    #         "were required to upgrade 'water flowed here' into 'conditions were favorable for microbial life.'"
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 2,
    #         "position": "Top-left image: rock outcrop called Link showing rounded pebbles in conglomerate rock, with 1cm scale bar"
    #     },
    #     "required_context": {
    #         "pages": [2],
    #         "focus": (
    #             "Link rock outcrop rounded pebbles conglomerate, stream flow evidence, "
    #             "John Klein drill sample habitability evidence: sustained liquid water, "
    #             "elemental ingredients, chemical energy source, non-acidic non-saline water, "
    #             "flood vs sustained flow distinction"
    #         )
    #     }
    # },
    # {
    #     "id": 11,
    #     "type": "radiometric_age_preservation_paradox",
    #     "question": (
    #         "The John Klein drill sample measured 4.2 billion years old yet had been exposed at the "
    #         "Martian surface for only 80 million years — an age gap of approximately 4.12 billion years "
    #         "of subsurface preservation. Explain why this specific gap is paradoxical given Mars' known "
    #         "history of impact cratering and surface erosion over the same period, what geological "
    #         "mechanism must have protected the sample from surface processes for over four billion years, "
    #         "and why the powdered interior color visible in the drill hole image is itself direct "
    #         "mineralogical evidence of this preservation state rather than merely a byproduct of drilling."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 2,
    #         "position": "Bottom-right image: Curiosity's first sample drilling at rock called John Klein showing circular drill hole and surrounding rock surface"
    #     },
    #     "required_context": {
    #         "pages": [2],
    #         "focus": (
    #             "John Klein 4.2 billion years old, 80 million years surface exposure, "
    #             "4.12 billion year preservation gap, Mars erosion and cratering history, "
    #             "drill powder color mineralogical evidence, first age measurement on another planet"
    #         )
    #     }
    # },
    # {
    #     "id": 12,
    #     "type": "self_portrait_assembly_mobility_inference",
    #     "question": (
    #         "The Curiosity self-portrait was assembled from multiple images taken by the Mars Hand Lens "
    #         "Imager mounted on the rover's arm — an instrument designed primarily for extreme close-up "
    #         "photography revealing details smaller than the width of a human hair. Explain the technical "
    #         "procedure by which a close-range arm-mounted camera produces a full-body self-portrait with "
    #         "no arm visible in the final composite, what the completed portrait reveals about the rover's "
    #         "positional mobility across the Martian terrain, and why the same instrument that produces "
    #         "panoramic self-portraits can also examine textures invisible to the Mast Camera."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 3,
    #         "position": "Left image: self-portrait of Curiosity assembled from Mars Hand Lens Imager frames showing full rover on Martian surface"
    #     },
    #     "required_context": {
    #         "pages": [3],
    #         "focus": (
    #             "Mars Hand Lens Imager arm-mounted close-up camera, details smaller than human hair width, "
    #             "self-portrait assembly technique, arm absent from final composite, "
    #             "Mast Camera stereo high-resolution imaging at human-eye height, mobility documentation"
    #         )
    #     }
    # },
    # {
    #     "id": 13,
    #     "type": "RTG_power_decay_longevity_validation",
    #     "question": (
    #         "The multi-mission radioisotope thermoelectric generator produced approximately 110 watts at "
    #         "launch and was still producing over 100 watts two years after landing — a decline of fewer "
    #         "than 10 watts across roughly 730 Earth days. Given that plutonium-238 has a half-life of "
    #         "approximately 87.7 years, explain why the observed sub-10-watt decline is physically consistent "
    #         "with radioactive decay on this timescale, why this validates the designed operational lifespan "
    #         "of one full Martian year (687 Earth days) or more, and why the same excess heat that causes "
    #         "the power decline is simultaneously essential to keeping rover electronics within safe "
    #         "operating temperatures on the Martian surface."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 3,
    #         "position": "Left image: Curiosity self-portrait showing RTG protruding at rear of rover"
    #     },
    #     "required_context": {
    #         "pages": [3],
    #         "focus": (
    #             "RTG 110W at launch, over 100W two years after landing, plutonium-238 radioactive decay, "
    #             "687 Earth day Mars year design lifespan, warm fluids thermal plumbing for electronics, "
    #             "power decline rate validation"
    #         )
    #     }
    # },
    # {
    #     "id": 14,
    #     "type": "atmospheric_loss_top_down_mechanism",
    #     "question": (
    #         "Early in the mission, Curiosity's analysis of Mars' atmospheric composition provided evidence "
    #         "that the planet lost much of its original atmosphere 'by a process favoring loss from the top "
    #         "of the atmosphere rather than interaction with the surface.' Explain the physical mechanism "
    #         "by which atmospheric loss preferentially occurs from the top rather than the bottom, "
    #         "why this top-down process is specifically linked to Mars losing its global magnetic field, "
    #         "and why the isotope ratio measurements from the Sample Analysis at Mars instrument suite "
    #         "are the specific data type that can reconstruct this historical loss process rather than "
    #         "any imaging or mineralogical instrument."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 2,
    #         "position": "Top-left image: rock outcrop Link as contextual anchor for early mission surface environment findings"
    #     },
    #     "required_context": {
    #         "pages": [2, 3],
    #         "focus": (
    #             "Mars atmospheric loss from top of atmosphere not surface interaction, "
    #             "Sample Analysis at Mars gas chromatograph mass spectrometer tunable laser spectrometer, "
    #             "isotope ratios atmosphere and water history clues, magnetic field loss mechanism"
    #         )
    #     }
    # },
    # {
    #     "id": 15,
    #     "type": "instrument_hierarchy_habitability_proof",
    #     "question": (
    #         "Curiosity carries instruments including Sample Analysis at Mars, CheMin X-ray diffraction, "
    #         "Mars Hand Lens Imager, Alpha Particle X-ray Spectrometer, ChemCam laser spectrometer, "
    #         "and the Mast Camera. Construct the minimum ordered chain of instruments whose combined "
    #         "outputs were necessary and sufficient to confirm that Yellowknife Bay's John Klein rock "
    #         "represented a past habitable environment — explaining why each instrument in your chain "
    #         "provides a piece of evidence that no other instrument in the suite could substitute for, "
    #         "and why the Mast Camera alone — despite its stereo high-definition imaging capability — "
    #         "could never have been the final confirming instrument."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 3,
    #         "position": "Left image: Curiosity self-portrait showing mast with Mast Camera and ChemCam, arm with MAHLI and APXS, and rear RTG"
    #     },
    #     "required_context": {
    #         "pages": [3],
    #         "focus": (
    #             "Sample Analysis at Mars carbon compounds isotopes, CheMin mineral identification quantification, "
    #             "MAHLI close-up rock texture, APXS elemental abundances, ChemCam laser remote spectroscopy, "
    #             "Mast Camera stereo color imaging, habitability evidence: sustained water, ingredients, energy source, chemistry"
    #         )
    #     }
    # },
    # {
    #     "id": 16,
    #     "type": "sky_crane_site_selection_enablement",
    #     "question": (
    #         "The sky crane landing system lowered Curiosity upright on a tether in the final seconds of "
    #         "descent, after S-curve atmospheric maneuvers, parachute deployment, and retrorocket firing "
    #         "around the rim of the upper stage. This sequence enabled landing within a 20-kilometer ellipse "
    #         "at 4.6 degrees south latitude, 137.4 degrees east longitude, at minus 4,501 meters elevation. "
    #         "Explain why landing Curiosity upright on its wheels — rather than using airbag bouncing "
    #         "as earlier rovers did — was a physical prerequisite for carrying a science payload more than "
    #         "10 times as massive as Spirit and Opportunity, and how the specific elevation of minus 4,501 "
    #         "meters at Gale Crater relates to the atmospheric density available for the parachute phase."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 1,
    #         "position": "Top-right image: Curiosity being lowered by sky crane tether during final landing seconds"
    #     },
    #     "required_context": {
    #         "pages": [1],
    #         "focus": (
    #             "Sky crane tether upright landing, S-curve maneuvers, parachute retrorocket sequence, "
    #             "20km ellipse 4.6S 137.4E minus 4501m elevation, payload 10x heavier than Spirit Opportunity, "
    #             "airbag limitation for heavy rovers, atmospheric density at elevation"
    #         )
    #     }
    # },
    #
    # # ======================
    # # SECTION 3: HUBBLE (Questions 17-23)
    # # ======================
    # {
    #     "id": 17,
    #     "type": "hubble_constant_uncertainty_cosmological_consequence",
    #     "question": (
    #         "Before Hubble launched, the Hubble constant was so imprecise that the universe's age ranged "
    #         "from 10 billion to 20 billion years. Hubble observations refined this to 13.8 billion years. "
    #         "Using Cepheid variable star brightness cycles observed in Andromeda across four dates — "
    #         "December 17, December 21, December 30, 2010, and January 26, 2011 — combined with Type Ia "
    #         "supernova standard candles from the 1995 and 2002 Hubble Deep Field comparisons, explain "
    #         "why a 10-billion-year uncertainty was not merely an age error but a fundamental barrier to "
    #         "determining whether the universe would expand forever, collapse, or reach equilibrium — "
    #         "and why each method covers a different rung of the cosmic distance ladder."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 3,
    #         "position": "Top: four-panel inset time series of Cepheid in Andromeda Dec 2010–Jan 2011; Bottom pair: 1995 and 2002 Hubble Deep Field with supernova arrow"
    #     },
    #     "required_context": {
    #         "pages": [3],
    #         "focus": (
    #             "Hubble constant pre-launch imprecision 10–20 billion year range, refined 13.8 billion years, "
    #             "Cepheid variable cyclical brightness distance measurement, Type Ia supernova characteristic brightness, "
    #             "dark energy acceleration Nobel Prize 2011, universe fate expansion collapse equilibrium"
    #         )
    #     }
    # },
    # {
    #     "id": 18,
    #     "type": "ultra_deep_field_galaxy_morphology_evolution",
    #     "question": (
    #         "The Hubble Ultra Deep Field required approximately one million seconds of cumulative exposure "
    #         "time across 11 days to capture. The accompanying sample images show the faintest and farthest "
    #         "galaxies to be irregularly shaped and frequently interacting. Hubble also predicts with "
    #         "certainty that the Milky Way and Andromeda — currently 2.5 million light-years apart — "
    #         "will begin colliding 4 billion years from now. Explain why the deep field galaxies being "
    #         "irregular and interacting is not a coincidence but a direct consequence of observing the "
    #         "universe when it was physically smaller, and how the predicted Milky Way-Andromeda collision "
    #         "is itself an observable instance of the same process frozen in the deep field images."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 4,
    #         "position": "Left: Hubble Ultra Deep Field full mosaic; Right: sample grid of faintest farthest galaxies showing irregular shapes and interactions"
    #     },
    #     "required_context": {
    #         "pages": [4],
    #         "focus": (
    #             "Ultra Deep Field one million seconds 11 days exposure, farthest galaxies irregular frequently interacting, "
    #             "universe smaller in past galaxies closer more likely to interact, "
    #             "Andromeda 2.5 million light-years approaching, collision in 4 billion years, "
    #             "giant elliptical galaxy merger outcome"
    #         )
    #     }
    # },
    # {
    #     "id": 19,
    #     "type": "europa_ganymede_ocean_evidence_method_difference",
    #     "question": (
    #         "Hubble found spectroscopic evidence of oxygen and hydrogen on Europa's surface — the blue "
    #         "mapped areas — consistent with water vapor plumes erupting from a subsurface ocean, "
    #         "with recent visual confirmation of those plumes also captured by Hubble. Separately, "
    #         "Hubble detected a subsurface saltwater ocean on Ganymede — the largest moon in the solar "
    #         "system — by observing related activity in Ganymede's own auroras, with that ocean estimated "
    #         "to contain more water than all of Earth's surface water combined. Explain why the aurora-based "
    #         "evidence for Ganymede's ocean and the spectroscopic plume evidence for Europa's ocean "
    #         "represent fundamentally different inference chains, and which provides stronger evidence "
    #         "for a currently liquid subsurface ocean rather than a frozen or historical one."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 9,
    #         "position": "Bottom-left: Europa globe with blue hydrogen-oxygen spectroscopic evidence areas mapped; Bottom-right: Europa surface showing cracked ice texture from Voyager and Galileo data"
    #     },
    #     "required_context": {
    #         "pages": [8, 9],
    #         "focus": (
    #             "Europa spectroscopic oxygen hydrogen blue areas water vapor plumes, visual plume confirmation, "
    #             "Ganymede saltwater ocean aurora activity evidence, more water than all Earth surface water, "
    #             "aurora magnetic field liquid conductor inference vs spectroscopic atmospheric composition"
    #         )
    #     }
    # },
    # {
    #     "id": 20,
    #     "type": "supernova_ring_temporal_sequence_proof",
    #     "question": (
    #         "The six-panel sequence of Supernova 1987A spans from September 24, 1994 through December 6, 2006 "
    #         "and shows spots on the pre-existing ring brightening progressively. The document states the rings "
    #         "were 'three mysterious rings of material encircling the doomed star' that were revealed by Hubble. "
    #         "Explain why the spots brighten sequentially around the ring rather than simultaneously, "
    #         "why the ring was already in place before the 1987 explosion rather than being ejected by it, "
    #         "and why this specific 12-year observational baseline was the minimum necessary to distinguish "
    #         "the expanding shockwave hypothesis from the alternative that the brightening was caused by "
    #         "the light pulse itself illuminating the ring."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 15,
    #         "position": "Top-left six-panel grid: SN 1987A from Sept 24 1994 through Dec 6 2006 showing progressive spot brightening on ring; Top-right: 1994 single full view"
    #     },
    #     "required_context": {
    #         "pages": [14, 15],
    #         "focus": (
    #             "SN 1987A three rings pre-existing around doomed star, expanding shockwave slamming into ring, "
    #             "progressive spot brightening 1994–2006, shockwave vs direct light illumination distinction, "
    #             "12-year minimum temporal baseline, light travel time across ring"
    #         )
    #     }
    # },
    # {
    #     "id": 21,
    #     "type": "planetary_nebula_shape_complexity_white_dwarf_dynamics",
    #     "question": (
    #         "Ground-based images suggested that planetary nebulas had simple spherical shapes. "
    #         "Hubble revealed that their shapes are much more varied: pinwheels, butterflies, hourglasses, "
    #         "and other complex forms. The 16-image mosaic shows this full morphological range. "
    #         "Explain why spherical symmetry was the default ground-based assumption given atmospheric "
    #         "seeing limitations, what physical processes during a Sun-like star's death — specifically the "
    #         "release of outer gaseous layers before collapse to a white dwarf — cause non-spherical "
    #         "geometries, and why an hourglass shape specifically implies a binary companion star "
    #         "rather than a solitary stellar death."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 14,
    #         "position": "Bottom: 4x4 mosaic of sixteen planetary nebulas showing pinwheel, butterfly, hourglass, ring, and complex asymmetric morphologies"
    #     },
    #     "required_context": {
    #         "pages": [14],
    #         "focus": (
    #             "Planetary nebulas ground-based spherical assumption, Hubble revealed varied complex shapes, "
    #             "pinwheels butterflies hourglasses, outer gaseous layer release before white dwarf collapse, "
    #             "binary companion star hourglass mechanism, atmospheric seeing limitation on ground"
    #         )
    #     }
    # },
    # {
    #     "id": 22,
    #     "type": "dark_matter_filament_arc_reverse_engineering",
    #     "question": (
    #         "The visible-light image of cluster Cl 0024+17 shows blue arcs among yellowish galaxies, "
    #         "while the blue overlay reconstructs the dark matter density required to account for those "
    #         "distortions. The universe is estimated to contain approximately five times more dark matter "
    #         "than regular matter, organized around an immense network of filaments with galaxy clusters "
    #         "at intersections. Explain the specific mathematical steps by which arc curvature and "
    #         "angular position in the camera image uniquely constrain the three-dimensional dark matter "
    #         "distribution — rather than just total cluster mass — and why dark matter's inability to "
    #         "emit, absorb, or reflect electromagnetic radiation makes the gravitational lensing arc "
    #         "method the only currently viable technique for mapping its structure."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 6,
    #         "position": "Bottom pair: visible-light image of Cl 0024+17 with elongated blue arcs among yellowish cluster galaxies, and blue dark-matter-density overlay on same cluster"
    #     },
    #     "required_context": {
    #         "pages": [6],
    #         "focus": (
    #             "Gravitational lensing arc distortion mathematics reverse engineering, Cl 0024+17 blue arcs, "
    #             "dark matter 5x normal matter ratio, filament network galaxy clusters at intersections, "
    #             "no electromagnetic interaction dark matter, lensing as only mapping technique"
    #         )
    #     }
    # },
    # {
    #     "id": 23,
    #     "type": "protoplanetary_disk_gap_planet_inference",
    #     "question": (
    #         "Using a coronagraphic mask to block the star TW Hydrae's light, Hubble identified a gap "
    #         "1.9 billion miles wide in the surrounding protoplanetary disk of gas and dust — attributed "
    #         "to an unseen growing planet gravitationally sweeping material like a snowplow. Separately, "
    #         "Hubble resolved protoplanetary disks around nearly 200 stars in the Orion Nebula, visible "
    #         "as silhouettes illuminated from behind. Explain why a gap in a disk is stronger evidence "
    #         "for a forming planet than a brightness variation, why the gap being 'not yet completely "
    #         "cleared of material' is a more informative finding than a fully cleared gap would be, "
    #         "and what the 1997-to-2012 Beta Pictoris disk comparison images reveal about the "
    #         "timescale on which a massive embedded planet reshapes its disk."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 17,
    #         "position": "Bottom pair: Hubble image and illustration of TW Hydrae disk with dark gap ring; Top-right: sample Orion Nebula protoplanetary disk silhouettes; Middle pair: Beta Pictoris edge-on disk in 1997 and 2012"
    #     },
    #     "required_context": {
    #         "pages": [17],
    #         "focus": (
    #             "TW Hydrae protoplanetary disk gap 1.9 billion miles wide coronagraphic mask, "
    #             "unseen planet gravitationally sweeping material, gap not fully cleared, "
    #             "Orion Nebula 200 disk silhouettes, Beta Pictoris 1997 and 2012 disk changes, "
    #             "embedded massive planet disk reshaping timescale"
    #         )
    #     }
    # },
    #
    # # ======================
    # # SECTION 4: CROSS-DOCUMENT SYNTHESIS (Questions 24-25)
    # # ======================
    # {
    #     "id": 24,
    #     "type": "cross_doc_power_philosophy_mission_duration",
    #     "question": (
    #         "Voyager 1 permanently shut off its cameras on February 14, 1990 — more than 12 years into "
    #         "the mission and after capturing the Family Portrait from 6 billion kilometers — to conserve "
    #         "power, with both spacecraft projected to function only until approximately 2025. "
    #         "Curiosity's radioisotope generator was engineered to deliver over 100 watts continuously "
    #         "across at least 687 Earth days, with warm fluids plumbed throughout the rover to maintain "
    #         "electronics at safe temperatures. Hubble has taken over one million observations across "
    #         "more than three decades, with six Space Shuttle servicing missions replacing and upgrading "
    #         "instruments to keep it state-of-the-art. "
    #         "These three missions represent three distinct philosophies of engineering for longevity. "
    #         "Analyze what each philosophy reveals about its mission's relationship to irreplaceability, "
    #         "and whether the Family Portrait — a backward-looking final act — could have been "
    #         "planned from the beginning or required the constraint of imminent shutdown to be meaningful."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 4,
    #         "position": "Bottom image: Voyager 1 Family Portrait mosaic, February 14, 1990, 6 billion km — cameras permanently shut down shortly after"
    #     },
    #     "required_context": {
    #         "pages": [4, 3, 2],
    #         "focus": (
    #             "Voyager camera shutdown February 14 1990 power conservation, 2025 operational projection, "
    #             "Curiosity RTG 110W→100W sustained warm fluid thermal plumbing 687 day lifespan, "
    #             "Hubble six servicing missions one million observations 30 years, "
    #             "three longevity philosophies: expendable, sustained isotope, serviceable orbital"
    #         )
    #     }
    # },
    # {
    #     "id": 25,
    #     "type": "cross_doc_habitability_evidence_hierarchy_three_missions",
    #     "question": (
    #         "Voyager imaged Europa's geologically young ice surface 'possibly floating on an ocean of "
    #         "liquid water.' Hubble later mapped spectroscopic evidence of oxygen and hydrogen on Europa "
    #         "and detected water vapor plumes, while also finding Ganymede's aurora implies a subsurface "
    #         "saltwater ocean with more water than all of Earth's surface combined. Curiosity drilled "
    #         "the John Klein rock and found geological and mineralogical evidence for 'sustained liquid "
    #         "water, key elemental ingredients for life, a chemical energy source, and water not too "
    #         "acidic or too salty' — 4.2 billion years ago. "
    #         "Rank these four pieces of evidence by their proximity to confirming the existence of "
    #         "current extant life rather than past or possible habitability. Justify each rank by "
    #         "identifying the specific evidentiary gap between what was measured and what extant life "
    #         "requires — and explain why the mission with the most direct physical contact with its "
    #         "target produced confirmed past habitability while the missions relying on remote sensing "
    #         "produced only probable or possible present liquid water."
    #     ),
    #     "expected_images": True,
    #     "image_reference": {
    #         "page": 3,
    #         "position": "Left montage: Jupiter and its four largest moons including Europa — Voyager basis for ice surface and possible ocean claim"
    #     },
    #     "required_context": {
    #         "pages": [3, 2, 8, 9],
    #         "focus": (
    #             "Europa ice surface possibly on liquid water ocean (Voyager), "
    #             "Europa spectroscopic oxygen hydrogen plumes (Hubble), "
    #             "Ganymede saltwater ocean aurora evidence more water than Earth surface (Hubble), "
    #             "John Klein drill sustained liquid water elemental ingredients chemical energy non-acidic (Curiosity), "
    #             "remote sensing vs in-situ contact evidence hierarchy, extant vs past life distinction"
    #         )
    #     }
    # },
]


# # METRICS

# In[46]:


from rouge_score import rouge_scorer
import statistics


# In[47]:


class ResourceMonitor:
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        # Initialize CPU percent to get accurate readings later
        self.process.cpu_percent(interval=None)

    def get_snapshot(self):
        # Get CPU percent with interval=None for non-blocking read (requires previous call)
        cpu_percent = self.process.cpu_percent(interval=None)
        return {
            "cpu_percent": cpu_percent,
            "ram_gb": round(self.process.memory_info().rss / (1024 ** 3), 4)
        }


# In[48]:


# M1 (Embedding Time)
def compute_embedding_time(text_time: float, image_time: float) -> float:
    total = text_time + image_time
    print(f"\n{'─'*60}")
    print(f"  M1 Embedding Time: {total:.4f} seconds")
    print(f"{'─'*60}")
    return total


# In[49]:


# M2 (Index Size)
def compute_index_size(text_db: 'VectorStore', image_db: 'VectorStore') -> int:
    text_count = text_db.get_collection_stats().get("count", 0)
    image_count = image_db.get_collection_stats().get("count", 0)
    total = text_count + image_count
    print(f"\n{'─'*60}")
    print(f"  M2 Index Size: {total} vectors")
    print(f"     └─ Text vectors: {text_count}")
    print(f"     └─ Image vectors: {image_count}")
    print(f"{'─'*60}")
    return total


# In[50]:


# M3 (Retrieval Latency)
def compute_retrieval_latency(times: List[float]) -> float:
    if not times:
        print(f"\n{'─'*60}")
        print(f"  M3 Retrieval Latency: 0.0000 seconds")
        print(f"{'─'*60}")
        return 0.0
    avg = statistics.mean(times)
    print(f"\n{'─'*60}")
    print(f"  M3 Retrieval Latency: {avg:.4f} seconds")
    print(f"     └─ Min: {min(times):.4f}s, Max: {max(times):.4f}s")
    print(f"{'─'*60}")
    return avg


# In[51]:


# M4 (Cosine Similarity)
def compute_cosine_similarity(values: List[float], label: str = "M4 Cosine Similarity") -> float:
    if not values:
        print(f"\n{'─'*60}")
        print(f"  {label}: 0.0000")
        print(f"{'─'*60}")
        return 0.0
    avg = statistics.mean(values)
    print(f"\n{'─'*60}")
    print(f"  {label}: {avg:.4f}")
    print(f"{'─'*60}")
    return avg


# In[52]:


def _get_reference_text(result_item: Dict, top_k_chunks: int = cfg.rouge_top_k_chunks) -> str:
    """
    Choose the best available reference text for overlap-based evaluation.

    If a gold reference is available in the future, this helper can use it.
    For the current notebooks, it falls back to the retrieved context so the
    ROUGE metrics remain explicit about being retrieval-grounded proxies.
    """
    explicit_reference = result_item.get("reference_text", "").strip()
    if explicit_reference:
        return explicit_reference

    context_text = result_item.get("context", "").strip()
    if not context_text:
        return ""

    context_parts = context_text.split("\n\n")
    return "\n\n".join(context_parts[:top_k_chunks]) if len(context_parts) > top_k_chunks else context_text


# In[53]:


# M5 (top-k required-page coverage)
def compute_top_k_accuracy(
    retrieval_output: List[Dict],
    test_questions: List[Dict],
    k: int = 5
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
    print(f"\n{'─'*60}")
    print(f"  M5 Top-k Page Coverage@{k}: {score:.2f}%")
    print(f"       Perfect page hits: {perfect_hits}/{len(coverage_scores)}")
    print(f"{'─'*60}")
    return score


# In[54]:


# M6 (ROUGE-1)
def compute_rouge1(per_query_results: List[Dict], k: int = 5) -> float:
    """
    M6: ROUGE-1 between generated response and retrieved context (top-k chunks as pseudo-ground-truth).
    Measures unigram overlap between response and retrieved documents.
    """
    scorer = rouge_scorer.RougeScorer(["rouge1"], use_stemmer=True)
    scores = []
    skipped = 0

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
            print(f"  M6 calculation failed: {e}")
            skipped += 1

    avg_score = statistics.mean(scores) if scores else 0.0

    print(f"\n  {'─'*60}")
    print(f"  M6 ROUGE-1: {avg_score:.4f}")
    print(f"       Evaluated: {len(scores)}, Skipped: {skipped}")
    print(f"{'─'*60}")

    return avg_score


# In[55]:


# M7 (ROUGE-2)
def compute_rouge2(per_query_results: List[Dict], k: int = 5) -> float:
    """
    M7: ROUGE-2 between generated response and retrieved context.
    Measures bigram (2-word sequence) overlap - stricter than ROUGE-1.
    """
    scorer = rouge_scorer.RougeScorer(["rouge2"], use_stemmer=True)
    scores = []
    skipped = 0

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
            print(f"  M7 calculation failed: {e}")
            skipped += 1

    avg_score = statistics.mean(scores) if scores else 0.0

    print(f"\n  {'─'*60}")
    print(f"  M7 ROUGE-2: {avg_score:.4f}")
    print(f"       Evaluated: {len(scores)}, Skipped: {skipped}")
    print(f"{'─'*60}")

    return avg_score


# In[56]:


# M8 (ROUGE-L)
def compute_rougeL(per_query_results: List[Dict], k: int = 5) -> float:
    """
    M8: ROUGE-L between generated response and retrieved context.
    Measures longest common subsequence - captures sentence structure similarity.
    """
    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    scores = []
    skipped = 0

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
            print(f"  M8 calculation failed: {e}")
            skipped += 1

    avg_score = statistics.mean(scores) if scores else 0.0

    print(f"\n  {'─'*60}")
    print(f"  M8 ROUGE-L: {avg_score:.4f}")
    print(f"       Evaluated: {len(scores)}, Skipped: {skipped}")
    print(f"{'─'*60}")

    return avg_score


# In[57]:


# M9 (Context Length)
def compute_context_length(formatted_output: List[Dict]) -> float:
    """
    Computes average context length in characters from formatted output.
    """
    if not formatted_output:
        print(f"\n{'─'*60}")
        print(f"  M9 Context Length: 0.00 characters")
        print(f"{'─'*60}")
        return 0.0

    lengths = []
    for item in formatted_output:
        text_context = item.get("text_context", "")
        if text_context:
            lengths.append(len(text_context))

    if not lengths:
        print(f"\n{'─'*60}")
        print(f"  M9 Context Length: 0.00 characters")
        print(f"{'─'*60}")
        return 0.0

    avg = statistics.mean(lengths)
    print(f"\n{'─'*60}")
    print(f"  M9 Context Length: {avg:.2f} characters")
    print(f"     └─ Min: {min(lengths)}, Max: {max(lengths)}")
    print(f"{'─'*60}")
    return avg


# In[58]:


# M15 (Context utilization)
def compute_context_coverage(per_query_results: List[Dict]) -> float:
    """
    M15: Context Utilization - percentage of retrieved context terms reused in the response.
    This is a retrieval-grounded proxy for how much of the supplied context the answer uses.
    """
    coverage_scores = []
    skipped = 0
    stop_words = get_stopword_set()
    for item in per_query_results:
        context = item.get("context", "")
        response = item.get("response_text", "")
        if not context or not response:
            skipped += 1
            continue
        context_terms = {token for token in re.findall(r'\b[A-Za-z]{4,}\b', context.lower()) if token not in stop_words}
        response_terms = {token for token in re.findall(r'\b[A-Za-z]{4,}\b', response.lower()) if token not in stop_words}
        if not context_terms:
            skipped += 1
            continue
        overlap = len(context_terms & response_terms)
        coverage_scores.append((overlap / len(context_terms)) * 100)

    avg_coverage = statistics.mean(coverage_scores) if coverage_scores else 0.0
    print(f"\n  {'─'*60}")
    print(f"  M15 Context Utilization: {avg_coverage:.2f}%")
    print(f"       Evaluated: {len(coverage_scores)}, Skipped: {skipped}")
    print(f"{'─'*60}")
    return avg_coverage


# In[59]:


# M16 (Query to response time)
def compute_e2e_latency(per_query_results: List[Dict]) -> float:
    times = [r["e2e_latency_sec"] for r in per_query_results]
    avg = statistics.mean(times) if times else 0.0
    print(f"\n{'─'*60}")
    print(f"  M16 E2E Latency: {avg:.4f} seconds")
    if times:
        print(f"     └─ Min: {min(times):.4f}s, Max: {max(times):.4f}s")
    print(f"{'─'*60}")
    return avg


# In[60]:


# M17 (Queries processed per second)
def compute_throughput(per_query_results: List[Dict]) -> float:
    times = [r["e2e_latency_sec"] for r in per_query_results]
    total_time = sum(times)
    tp = (len(times) / total_time) if total_time > 0 else 0.0
    print(f"\n{'─'*60}")
    print(f"  M17 Throughput: {tp:.3f} queries per second")
    print(f"{'─'*60}")
    return tp


# In[61]:


# M18 (CPU usage)
def compute_cpu_usage(per_query_results: List[Dict]) -> float:
    values = [r["avg_cpu_percent"] for r in per_query_results]
    avg = statistics.mean(values) if values else 0.0
    print(f"\n{'─'*60}")
    print(f"  M18 CPU Usage: {avg:.2f}%")
    if values:
        print(f"     └─ Min: {min(values):.1f}%, Max: {max(values):.1f}%")
    print(f"{'─'*60}")
    return avg


# In[62]:


# M19 (RAM usage)
def compute_ram_usage(per_query_results: List[Dict]) -> float:
    values = [r["avg_ram_gb"] for r in per_query_results]
    avg = statistics.mean(values) if values else 0.0
    print(f"\n{'─'*60}")
    print(f"  M19 RAM Usage: {avg:.3f} GB")
    if values:
        print(f"     └─ Min: {min(values):.3f}GB, Max: {max(values):.3f}GB")
    print(f"{'─'*60}")
    return avg


# In[63]:


# GPU Usage
def compute_gpu_usage(per_query_results: List[Dict]) -> float:
    values = [r.get("avg_gpu_percent", 0.0) for r in per_query_results]
    avg = statistics.mean(values) if values else 0.0
    print(f"\n{'─'*60}")
    print(f"  GPU Usage: {avg:.2f}%")
    if values:
        print(f"     └─ Min: {min(values):.1f}%, Max: {max(values):.1f}%")
    print(f"{'─'*60}")
    return avg


# In[64]:


def compute_hybrid_stats(per_query_results: List[Dict]) -> Dict:
    """Calculate aggregate statistics for hybrid search performance."""
    hybrid_data = []
    for item in per_query_results:
        metrics = item.get("result", {}).get("retrieval_metrics", {})
        if "hybrid_stats" in metrics and metrics["hybrid_stats"]:
            hybrid_data.append(metrics["hybrid_stats"])
    if not hybrid_data:
        return {"avg_bm25_weight": 0.0, "bm25_weight_std": 0.0, "bm25_weight_min": 0.0, "bm25_weight_max": 0.0, "weight_adjusted_queries": 0, "keyword_queries": 0, "semantic_queries": 0, "balanced_queries": 0, "avg_bm25_max_score": 0.0, "avg_overlap_jaccard": 0.0, "fallback_to_semantic": 0}
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


# In[65]:


def print_hybrid_metrics_summary(stats: Dict):
    """Print formatted hybrid search metrics."""
    print(f"\n  {'─'*78}")
    print(f"  {'HYBRID SEARCH STATISTICS':^76}")
    print(f"  {'─'*78}")
    print(f"  {'Query Classification:':<40}")
    print(f"    • Keyword-heavy queries:  {stats['keyword_queries']}")
    print(f"    • Semantic queries:       {stats['semantic_queries']}")
    print(f"    • Balanced queries:       {stats['balanced_queries']}")
    print(f"  {'─'*78}")
    print(f"  {'Adaptive Fusion Weights:':<40}")
    print(f"    • Avg BM25 weight:        {stats['avg_bm25_weight']:.2f}")
    print(f"    • Avg Semantic weight:    {stats['avg_semantic_weight']:.2f}")
    print(f"    • BM25 weight range:      {stats['bm25_weight_min']:.2f} - {stats['bm25_weight_max']:.2f}")
    print(f"    • BM25 weight std dev:    {stats['bm25_weight_std']:.3f}")
    print(f"    • Queries with adjusted weights: {stats['weight_adjusted_queries']}")
    print(f"  {'─'*78}")
    print(f"  {'BM25 Signal Quality:':<40}")
    print(f"    • Avg max BM25 score:     {stats['avg_bm25_max_score']:.2f}")
    print(f"    • Avg score std dev:      {stats['avg_bm25_std']:.2f}")
    print(f"    • Avg corpus coverage:    {stats['avg_corpus_coverage']:.1f}%")
    print(f"    • Avg BM25 signal strength: {stats['avg_bm25_signal_strength']:.1f}%")
    print(f"  {'─'*78}")
    print(f"  {'BM25-Semantic Alignment:':<40}")
    print(f"    • Avg Jaccard overlap:    {stats['avg_overlap_jaccard']:.3f}")
    print(f"    • Avg overlap percentage: {stats['avg_overlap_percentage']:.1f}%")
    print(f"  {'─'*78}")
    print(f"  {'Fallback Statistics:':<40}")
    print(f"    • Semantic fallbacks:     {stats['fallback_to_semantic']} ({stats['fallback_percentage']:.1f}%)")
    print(f"  {'─'*78}")


# In[66]:


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


# In[67]:


import pynvml


# In[68]:


def llm_response(llm, formatted_output, test_questions, stream: bool = True):
    response_output = []
    monitor = ResourceMonitor()

    gpu_handle = None
    try:
        pynvml.nvmlInit()
        gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    except pynvml.NVMLError as e:
        print(f"  GPU monitoring unavailable: {e}")
    except Exception as e:
        print(f"  Warning: unexpected GPU monitor initialization error: {e}")

    print(f"\n{'='*100}")
    print(f"  RUNNING MODEL: {llm.model_name}")
    print(f"{'='*100}")

    for idx, output in enumerate(formatted_output, 1):
        query = output.get("query", "")
        text_context = output.get("text_context", "")
        images = output.get("images", [])

        if images:
            captions = [f"[Image {i+1} Caption] {img.get('caption','')}"
                       for i, img in enumerate(images) if img.get('caption')]
            if captions:
                text_context += "\n\n[Image Captions]\n" + "\n".join(captions)

        print(f"\n  QUERY #{idx}/{len(formatted_output)}")
        print(f"  {'─'*96}")
        print(f"  Context: {len(text_context):,} chars | Images: {len(images)}")

        start_total = time.perf_counter()

        # Get initial snapshot
        start_snap = monitor.get_snapshot()
        time.sleep(0.1)  # Small delay for CPU measurement

        gpu_start = pynvml.nvmlDeviceGetUtilizationRates(gpu_handle).gpu if gpu_handle else 0

        response_dict = llm.generate_response(
            query=query,
            context=text_context,
            images=images,
            stream=stream,  # Live streaming for clean output
            max_tokens=cfg.llm_max_tokens,
            temperature=cfg.llm_temperature
        )

        # Get final measurements
        time.sleep(0.1)  # Small delay for CPU measurement
        end_snap = monitor.get_snapshot()

        gpu_end = pynvml.nvmlDeviceGetUtilizationRates(gpu_handle).gpu if gpu_handle else 0
        e2e_latency = round(time.perf_counter() - start_total, 4)

        # Calculate average CPU usage between start and end
        avg_cpu = (start_snap["cpu_percent"] + end_snap["cpu_percent"]) / 2
        avg_ram = (start_snap["ram_gb"] + end_snap["ram_gb"]) / 2
        avg_gpu = (gpu_start + gpu_end) / 2

        full_text = response_dict.get("response", "") if isinstance(response_dict, dict) else str(response_dict)

        print(f"\n  {'─'*96}")
        print(f"  METRICS:")
        print(f"       Inference Time: {response_dict.get('generation_time_sec', 0):.4f} s")
        print(f"       E2E Latency: {e2e_latency:.4f} s")
        print(f"       CPU Usage: {avg_cpu:.1f}%")
        print(f"       RAM Usage: {avg_ram:.3f} GB")
        print(f"       GPU Usage: {avg_gpu:.1f}%")
        print(f"  {'─'*96}")

        response_output.append({
            "id": test_questions[idx-1].get("id") if idx <= len(test_questions) else idx,
            "query": query,
            "context": text_context,
            "response": response_dict,
            "response_text": full_text,
            "images": images,
            "inference_time_sec": response_dict.get("generation_time_sec", 0),
            "e2e_latency_sec": e2e_latency,
            "avg_cpu_percent": round(avg_cpu, 2),
            "avg_ram_gb": round(avg_ram, 4),
            "avg_gpu_percent": round(avg_gpu, 2),
            "num_images": len(images)
        })

    print(f"\n{'='*100}")
    print(f"  COMPLETED MODEL: {llm.model_name}")
    print(f"{'='*100}")

    if gpu_handle is not None:
        try:
            pynvml.nvmlShutdown()
        except pynvml.NVMLError:
            pass

    return response_output


# In[69]:


import os
import re
import io
from datetime import datetime
from html import escape
from typing import List, Dict

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Image as RLImage,
    KeepTogether,
    HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch


# In[70]:


def export_retrieved_results_to_pdf(formatted_output, output_dir=cfg.retrieval_results_dir):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{output_dir}/retrieval_results_{timestamp}.pdf"

    doc = SimpleDocTemplate(filename, pagesize=A4, rightMargin=45, leftMargin=45,
                            topMargin=60, bottomMargin=50)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("TitleStyle", parent=styles["Heading1"], fontSize=20, spaceAfter=20, textColor=colors.darkblue)
    section_style = ParagraphStyle("SectionStyle", parent=styles["Heading3"], fontSize=12, spaceAfter=6)
    body_style = ParagraphStyle("BodyStyle", parent=styles["Normal"], fontSize=10.5, leading=15, spaceAfter=8)
    caption_style = ParagraphStyle("CaptionStyle", parent=styles["Normal"], fontSize=9, textColor=colors.grey, leading=12, spaceAfter=10, alignment=1)

    elements.append(Paragraph("Retrieved Results Report", title_style))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}", body_style))
    elements.append(Paragraph(f"Total Queries: {len(formatted_output)}", body_style))
    elements.append(Spacer(1, 0.4 * inch))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
    elements.append(Spacer(1, 0.5 * inch))
    elements.append(PageBreak())

    for idx, item in enumerate(formatted_output, 1):
        query = escape(item.get("query", ""))
        text_context = escape(item.get("text_context", "")).replace("\n", "<br/>")
        images = item.get("images", [])

        elements.append(Paragraph(f"Query {idx}", styles["Heading2"]))
        elements.append(Spacer(1, 0.2 * inch))
        elements.append(Paragraph("User Query", section_style))
        elements.append(Paragraph(query, body_style))
        elements.append(Paragraph("Retrieved Context", section_style))
        elements.append(Paragraph(text_context, body_style))

        if images:
            image_section = [Spacer(1, 0.3 * inch), Paragraph("Retrieved Image(s)", section_style), Spacer(1, 0.2 * inch)]
            for img_obj in images:
                pil_img = img_obj.get("image")
                caption = escape(img_obj.get("caption", ""))
                if pil_img is None:
                    continue
                img_buffer = io.BytesIO()
                pil_img.save(img_buffer, format="PNG")
                img_buffer.seek(0)
                rl_img = RLImage(img_buffer)
                scale = min(4.8 * inch / pil_img.size[0], 3.5 * inch / pil_img.size[1])
                rl_img.drawWidth = pil_img.size[0] * scale
                rl_img.drawHeight = pil_img.size[1] * scale
                rl_img.hAlign = "CENTER"
                image_section.append(rl_img)
                if caption:
                    image_section.append(Paragraph(f"<i>{caption}</i>", caption_style))
                image_section.append(Spacer(1, 0.35 * inch))
            elements.append(KeepTogether(image_section))

        elements.append(PageBreak())

    def add_page_number(canvas_obj, doc):
        canvas_obj.setFont("Helvetica", 9)
        canvas_obj.drawRightString(A4[0] - 40, 20, f"Page {doc.page}")

    doc.build(elements, onLaterPages=add_page_number)
    print(f"\n  ✓ Saved retrieval report: {filename}")


# In[71]:


import os
import re
import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Image as RLImage,
    KeepTogether,
    HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors


# In[72]:


def export_results_to_pdf(results, model_name: str, metrics_summary: dict, output_dir=cfg.results_dir):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe_name = re.sub(r"[^\w\-]", "_", model_name)
    filename = f"{output_dir}/{safe_name}_rag_results_{timestamp}.pdf"

    doc = SimpleDocTemplate(filename, pagesize=A4, rightMargin=45, leftMargin=45,
                            topMargin=60, bottomMargin=50)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("TitleStyle", parent=styles["Heading1"], fontSize=20, spaceAfter=20, textColor=colors.darkblue)
    section_style = ParagraphStyle("SectionStyle", parent=styles["Heading3"], fontSize=12, spaceAfter=6)
    body_style = ParagraphStyle("BodyStyle", parent=styles["Normal"], fontSize=10.5, leading=15, spaceAfter=8)
    metrics_style = ParagraphStyle("MetricsStyle", parent=styles["Normal"], fontSize=9.5, textColor=colors.darkgreen, spaceAfter=6)

    elements.append(Paragraph("RAG Evaluation Report - Traditional Vector RAG", title_style))
    elements.append(Paragraph(f"Model: {model_name}", body_style))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}", body_style))
    elements.append(Spacer(1, 0.5 * inch))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
    elements.append(Spacer(1, 0.5 * inch))

    elements.append(Paragraph("System Efficiency Metrics", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * inch))

    summary = f"""
    <b>Embedding & Indexing</b><br/>
    M1 Embedding Time : {metrics_summary.get('m1_embedding_time', 0):.4f} seconds<br/>
    M2 Index Size : {metrics_summary.get('m2_index_size', 0)} vectors<br/><br/>

    <b>Retrieval Quality</b><br/>
    M3 Retrieval Latency : {metrics_summary.get('m3_retrieval_latency', 0):.4f} seconds<br/>
    M4 Cosine Similarity (Text) : {metrics_summary.get('m4_cosine_similarity', 0):.4f}<br/>
    M4 Cosine Similarity (Image) : {metrics_summary.get('m4_cosine_similarity_image', 0):.4f}<br/>
    M5 Top-k Page Coverage : {metrics_summary.get('m5_top_k_accuracy', 0):.2f} %<br/><br/>

    <b>ROUGE Scores</b><br/>
    M6 ROUGE-1 : {metrics_summary.get('m6_rouge1', 0):.4f}<br/>
    M7 ROUGE-2 : {metrics_summary.get('m7_rouge2', 0):.4f}<br/>
    M8 ROUGE-L : {metrics_summary.get('m8_rougeL', 0):.4f}<br/><br/>

    <b>Context & Generation</b><br/>
    M9 Context Length : {metrics_summary.get('m9_context_length', 0):.2f} characters<br/>
    M15 Context Utilization : {metrics_summary.get('m15_context_coverage', 0):.2f} %<br/><br/>

    <b>Performance</b><br/>
    M16 E2E Latency : {metrics_summary.get('m16_e2e_latency', 0):.4f} seconds<br/>
    M17 Throughput : {metrics_summary.get('m17_throughput', 0):.3f} queries/sec<br/>
    M18 CPU Usage : {metrics_summary.get('m18_cpu_usage', 0):.2f} %<br/>
    M19 RAM Usage : {metrics_summary.get('m19_ram_usage', 0):.3f} GB<br/>
    GPU Usage : {metrics_summary.get('gpu_usage', 0):.2f} %
    """
    elements.append(Paragraph(summary, metrics_style))
    elements.append(PageBreak())

    for idx, item in enumerate(results, 1):
        query = escape(item.get("query", ""))
        context = escape(item.get("context", "")).replace("\n", "<br/>")
        response_text = escape(item.get("response_text", "")).replace("\n", "<br/>")
        elements.append(Paragraph(f"Query {idx}", styles["Heading2"]))
        elements.append(Spacer(1, 0.2 * inch))
        elements.append(Paragraph("User Query", section_style))
        elements.append(Paragraph(query, body_style))
        elements.append(Paragraph("Retrieved Context", section_style))
        elements.append(Paragraph(context, body_style))
        elements.append(Paragraph("Model Answer", section_style))
        elements.append(Paragraph(response_text, body_style))
        metrics_text = f"""
        Inference Time : {item.get('inference_time_sec',0):.4f} sec<br/>
        E2E Latency : {item.get('e2e_latency_sec',0):.4f} sec<br/>
        CPU : {item.get('avg_cpu_percent',0):.1f} %<br/>
        RAM : {item.get('avg_ram_gb',0):.3f} GB<br/>
        GPU : {item.get('avg_gpu_percent',0):.1f} %<br/>
        Images Used : {item.get('num_images',0)}
        """
        elements.append(Paragraph(metrics_text, metrics_style))
        if item.get("images"):
            image_section = [Spacer(1, 0.3 * inch), Paragraph("Retrieved Image(s)", section_style), Spacer(1, 0.2 * inch)]
            for img_obj in item["images"]:
                pil_img = img_obj.get("image")
                caption = escape(img_obj.get("caption", ""))
                if pil_img is None:
                    continue
                img_buffer = io.BytesIO()
                pil_img.save(img_buffer, format="PNG")
                img_buffer.seek(0)
                rl_img = RLImage(img_buffer)
                scale = min(4.8 * inch / pil_img.size[0], 3.5 * inch / pil_img.size[1])
                rl_img.drawWidth = pil_img.size[0] * scale
                rl_img.drawHeight = pil_img.size[1] * scale
                rl_img.hAlign = "CENTER"
                image_section.append(rl_img)
                if caption:
                    image_section.append(Paragraph(f"<i>{caption}</i>", ParagraphStyle("CaptionStyle", parent=styles["Normal"], fontSize=9, textColor=colors.grey, alignment=1)))
                image_section.append(Spacer(1, 0.35 * inch))
            elements.append(KeepTogether(image_section))
        elements.append(PageBreak())

    def add_page_number(canvas_obj, doc):
        canvas_obj.setFont("Helvetica", 9)
        canvas_obj.drawRightString(A4[0] - 40, 20, f"Page {doc.page}")

    doc.build(elements, onLaterPages=add_page_number)
    print(f"\n  ✓ Saved evaluation report: {filename}")


# In[73]:


def main(test_questions):
    cfg.validate()
    print("\n" + "="*100)
    print("PHASE 1: LOADING DOCUMENTS & CHUNKING")
    print("="*100)
    pages = loading_pdf(dir_path=cfg.pdf_dir, images_dir=cfg.images_dir)
    chunks = bbox_chunker(pages, max_tokens=cfg.chunk_max_tokens, overlap_tokens=cfg.chunk_overlap_tokens)
    image_objects = build_image_objects(pages)

    print("\n" + "="*100)
    print("PHASE 2: EMBEDDING & VECTOR STORE INITIALIZATION")
    print("="*100)
    text_embedder = TextEmbeddingModel(model_name=cfg.text_embed_model, batch_size=cfg.text_embed_batch_size)
    image_embedder = ImageEmbeddingModel(model_name=cfg.image_embed_model, pretrained=cfg.image_embed_pretrained, caption_image_weight=cfg.image_caption_image_weight, batch_size=cfg.image_embed_batch_size)
    text_embeddings, text_time, text_stats = text_embedder.embed_documents(chunks)
    image_embeddings, image_time, image_stats = image_embedder.embed_image(image_objects)
    text_db = VectorStore(collection_name=cfg.text_collection_name, directory=cfg.database_dir, silent=True)
    image_db = VectorStore(collection_name=cfg.image_collection_name, directory=cfg.database_dir, silent=True)
    text_db.add_documents(chunks, text_embeddings)
    image_db.add_documents(image_objects, image_embeddings)

    print("\n" + "="*100)
    print("PHASE 3: RETRIEVAL SETUP")
    print("="*100)
    retriever = RetrievalRag(image_embedder=image_embedder, text_embedder=text_embedder, image_vectordb=image_db, text_vectordb=text_db, use_hybrid=cfg.use_hybrid, adaptive_weighting=cfg.adaptive_weighting, score_fusion=cfg.score_fusion, use_reranker=cfg.use_reranker, bm25_weight=cfg.bm25_weight, semantic_weight=cfg.semantic_weight)
    is_hybrid = retriever.use_hybrid
    formatter = ContextFormatter(max_text_chunks=cfg.max_text_chunks, max_images=cfg.max_images, text_distance_threshold=cfg.text_distance_threshold, image_distance_threshold=cfg.image_distance_threshold, use_percentile_filtering=True, percentile_cutoff=cfg.percentile_cutoff)
    models = [cfg.llm_model]

    for model_name in models:
        print("\n" + "="*100)
        print(f"PHASE 4: EVALUATING MODEL - {model_name}")
        print("="*100)
        llm = LocalLLM(model_name=model_name, text_embedder=text_embedder)
        formatted_output, retrieval_times, cosine_sims_text, cosine_sims_image, retrieval_for_m5, raw_retrieval_results = [], [], [], [], [], []
        print(f"\n  Retrieving context for {len(test_questions)} questions...")
        for q in test_questions:
            start = time.perf_counter()
            out = retriever.retrieve(q["question"], text_k=cfg.text_k, image_k=cfg.image_k, rerank_k=cfg.rerank_k)
            retrieval_times.append(time.perf_counter() - start)
            cosine_sims_text.append(out.cosine_sim_text)
            cosine_sims_image.append(out.cosine_sim_image)
            legacy_result = out.to_legacy_dict()
            formatted = formatter.format(legacy_result)
            formatted["query"] = q["question"]
            formatted_output.append(formatted)
            retrieval_for_m5.append({"id": q.get("id"), "result": legacy_result})
            raw_retrieval_results.append({"id": q.get("id"), "query": q["question"], "result": legacy_result})

        per_query_results = llm_response(llm=llm, formatted_output=formatted_output, test_questions=test_questions, stream=True)

        print("\n" + "="*100)
        print("PHASE 5: CALCULATING METRICS")
        print("="*100)

        metrics_summary = {}

        metrics_summary["m1_embedding_time"] = compute_embedding_time(text_time, image_time)
        metrics_summary["m2_index_size"] = compute_index_size(text_db, image_db)
        metrics_summary["m3_retrieval_latency"] = compute_retrieval_latency(retrieval_times)
        metrics_summary["m4_cosine_similarity"] = compute_cosine_similarity(cosine_sims_text, label="M4 Cosine Similarity (Text)")
        metrics_summary["m4_cosine_similarity_image"] = compute_cosine_similarity(cosine_sims_image, label="M4 Cosine Similarity (Image)")
        metrics_summary["m5_top_k_accuracy"] = compute_top_k_accuracy(retrieval_for_m5, test_questions, k=cfg.top_k_accuracy_k)
        metrics_summary["m6_rouge1"] = compute_rouge1(per_query_results, k=cfg.rouge_top_k_chunks)
        metrics_summary["m7_rouge2"] = compute_rouge2(per_query_results, k=cfg.rouge_top_k_chunks)
        metrics_summary["m8_rougeL"] = compute_rougeL(per_query_results, k=cfg.rouge_top_k_chunks)
        metrics_summary["m15_context_coverage"] = compute_context_coverage(per_query_results)
        metrics_summary["m9_context_length"] = compute_context_length(formatted_output)
        metrics_summary["m16_e2e_latency"] = compute_e2e_latency(per_query_results)
        metrics_summary["m17_throughput"] = compute_throughput(per_query_results)
        metrics_summary["m18_cpu_usage"] = compute_cpu_usage(per_query_results)
        metrics_summary["m19_ram_usage"] = compute_ram_usage(per_query_results)
        metrics_summary["gpu_usage"] = compute_gpu_usage(per_query_results)

        if is_hybrid:
            hybrid_stats = compute_hybrid_stats(raw_retrieval_results)
            fusion_signal = compute_fusion_effectiveness(raw_retrieval_results)
            metrics_summary["hybrid"] = hybrid_stats
            metrics_summary["fusion_signal_agreement"] = fusion_signal
        else:
            hybrid_stats = None
            fusion_signal = None

        print("\n" + "="*100)
        print("PHASE 6: FINAL METRICS SUMMARY")
        print("="*100)
        print(f"\n  ┌{'─'*58}┐")
        print(f"  │{'METRIC':<30}│{'VALUE':>26}│")
        print(f"  ├{'─'*58}┤")
        print(f"  │{'EFFICIENCY':^56}│")
        print(f"  │{'M1 Embedding Time':<30}│{metrics_summary['m1_embedding_time']:>24.4f} s│")
        print(f"  │{'M2 Index Size':<30}│{metrics_summary['m2_index_size']:>26}│")
        print(f"  │{'RETRIEVAL QUALITY':^56}│")
        print(f"  │{'M3 Retrieval Latency':<30}│{metrics_summary['m3_retrieval_latency']:>24.4f} s│")
        print(f"  │{'M4 Cosine Sim (Text)':<30}│{metrics_summary['m4_cosine_similarity']:>26.4f}│")
        print(f"  │{'M4 Cosine Sim (Image)':<30}│{metrics_summary['m4_cosine_similarity_image']:>26.4f}│")
        print(f"  │{'M5 Page Coverage@k':<30}│{metrics_summary['m5_top_k_accuracy']:>25.2f} %│")
        print(f"  │{'GENERATION QUALITY':^56}│")
        print(f"  │{'M6 ROUGE-1':<30}│{metrics_summary['m6_rouge1']:>26.4f}│")
        print(f"  │{'M7 ROUGE-2':<30}│{metrics_summary['m7_rouge2']:>26.4f}│")
        print(f"  │{'M8 ROUGE-L':<30}│{metrics_summary['m8_rougeL']:>26.4f}│")
        print(f"  │{'M15 Context Utilization':<30}│{metrics_summary['m15_context_coverage']:>25.2f} %│")
        print(f"  │{'CONTEXT & PERFORMANCE':^56}│")
        print(f"  │{'M9 Context Length':<30}│{metrics_summary['m9_context_length']:>23.2f} ch│")
        print(f"  │{'M16 E2E Latency':<30}│{metrics_summary['m16_e2e_latency']:>24.4f} s│")
        print(f"  │{'M17 Throughput':<30}│{metrics_summary['m17_throughput']:>25.3f} q/s│")
        print(f"  │{'M18 CPU Usage':<30}│{metrics_summary['m18_cpu_usage']:>25.2f} %│")
        print(f"  │{'M19 RAM Usage':<30}│{metrics_summary['m19_ram_usage']:>24.3f} GB│")
        print(f"  │{'GPU Usage':<30}│{metrics_summary['gpu_usage']:>25.2f} %│")
        print(f"  └{'─'*58}┘")
        if is_hybrid:
            print_hybrid_metrics_summary(hybrid_stats)
            print(f"\n  {'─'*78}")
            print(f"  {'FUSION SIGNAL AGREEMENT':^76}")
            print(f"  {'─'*78}")
            print(f"  {'Avg mixed-signal support:':<50} {fusion_signal['avg_signal_agreement']:.1f}%")
            print(f"  {'Queries with >30% mixed support:':<50} {fusion_signal['mixed_signal_queries']}")
            print(f"  {'Agreement std dev:':<50} {fusion_signal['agreement_std']:.2f}")
            print(f"  {'─'*78}")
        else:
            print(f"\n  {'─'*78}")
            print(f"  {'HYBRID SEARCH':^76}")
            print(f"  {'─'*78}")
            print(f"  {'Status:':<50} Disabled (Semantic Only)")
            print(f"  {'─'*78}")

        print("\n" + "="*100)
        print("PHASE 7: EXPORTING RESULTS")
        print("="*100)
        export_retrieved_results_to_pdf(formatted_output=formatted_output, output_dir=cfg.retrieval_results_dir)
        export_results_to_pdf(results=per_query_results, model_name=model_name, metrics_summary=metrics_summary, output_dir=cfg.results_dir)

    print("\n" + "="*100)
    print("PIPELINE COMPLETED SUCCESSFULLY")
    print("="*100)


# In[74]:


if __name__ == "__main__":
   main(TEST_QUESTIONS)

