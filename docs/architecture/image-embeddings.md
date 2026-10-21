# Lazy image embedding adapter

`backend/embeddings/image.py` provides `ImageEmbeddingAdapter` for the image
objects produced by `backend.engine.ingestion.build_image_objects()`. It
returns image vectors aligned with the successful image-object tuple, timing
statistics, failure counts, caption-fallback counts, and UI-safe per-item
issues. It does not mutate the caller's image sequence.

Construction and import are side-effect-free. OpenCLIP is loaded only by
`load()` or the first operation that needs it. The default loader uses
`image_embed_model`, `image_embed_pretrained`, and
`torch.cuda.is_available()` to select the configured model and CPU/CUDA device;
it creates the model/transforms, tokenizer, moves the model to the device,
sets evaluation mode, and reads the model embedding dimension. A failed load
identifies the requested model/pretrained pair without exposing a stack trace
and preserves the original exception as its cause. Injected loaders and fake
components keep tests independent of model downloads.

Images are opened with PIL, converted to RGB, preprocessed, and encoded in
configured batches under `torch.no_grad()`. Each image vector and caption
vector is L2-normalized. A usable caption is fused as
`image_weight * image_vector + (1 - image_weight) * caption_vector` using
`image_caption_image_weight`; the fused vector is deliberately not normalized
again, matching the research implementation. Caption failures retain the
normalized image-only vector and produce a caption issue. Missing, unreadable,
or unsupported images produce image-load issues while valid images continue.
Queries use the same OpenCLIP tokenizer and normalized text encoder.

The adapter performs no Chroma writes, retrieval, orchestration, or model
fallback. Tests use local PIL images, deterministic fake components, and a
real temporary Chroma store. Model-weight provenance and immutable model
revisions are not yet enforced; model and pretrained names alone do not prove
the exact weights used.
