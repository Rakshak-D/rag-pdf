# Lazy text embedding adapter

`backend/embeddings/text.py` provides `TextEmbeddingAdapter`, which accepts a
validated `ResolvedRunConfig` and the LangChain-compatible Document objects
returned by `backend.engine.ingestion.bbox_chunker()`. It returns validated
NumPy document batches with elapsed time, count, dimension, and average-time
statistics, or one validated NumPy vector for a query.

Construction and module import are side-effect-free. The configured
`text_embed_model` is loaded only by `load()` or the first embedding call.
The default loader imports SentenceTransformers and selects `cuda` when
`torch.cuda.is_available()` is true, otherwise `cpu`. A failed load or model
call raises a typed adapter error with the configured model identity while
preserving the original exception as its cause; it does not silently select a
different model. Injected loaders and deterministic fake models keep tests
offline and independent of model weights.

Document content is trimmed, kept in input order, and encoded with the
configured `text_embed_batch_size`, NumPy conversion, progress disabled, and
`normalize_embeddings=True`, matching the protected research implementation.
Queries use the same normalization. When the configured model name contains
`bge` case-insensitively, query text receives the research prefix
`Represent this sentence for searching relevant passages: `; document text
never receives that prefix.

The adapter validates caller inputs and model output shapes, dimensions, and
finite values without silently dropping or repairing records. It does not
load image models, build indexes, invoke Chroma, contact Ollama, or perform
retrieval. Model-weight provenance and immutable model revisions are not yet
enforced; the configured model name alone is not proof of immutable weights.
