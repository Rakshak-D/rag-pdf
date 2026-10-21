# Versioned local Chroma index store

Phase 3.1 adds `backend/vectorstore/chroma_store.py` as a storage primitive.
`ChromaVectorStore.open(storage_root, run_plan)` verifies the complete
`RunPlan` before creating or opening persistent storage. The index directory is
derived only from the verified full `RunPlan.index_fingerprint`:

```text
<storage_root>/
  indexes/<index-fingerprint>/
    index-manifest.json
    chroma/                 # Chroma PersistentClient data
  snapshots/<document-manifest-hash>/
```

Each index has separate `text_vectors` and `image_vectors` collections. Both
use cosine distance, explicit caller-supplied vectors, and
`embedding_function=None`; this adapter does not load models, contact Ollama,
or access the network. Text and image collections may contain the same record
ID because they are distinct Chroma collections.

`index-manifest.json` records the store schema version, index fingerprint,
document-manifest hash, index schema version, and modality collection names.
It is written through a same-directory temporary file and atomic replacement.
Existing manifests and collection metadata are checked before use. Missing,
extra, or mismatched collections, unknown metadata, malformed manifests, and
incomplete directories raise explicit errors. Existing data is never reset,
deleted, silently repaired, or replaced by this adapter.

Persistent storage is not active-index promotion. There is no registry,
active pointer, transaction spanning ingestion and indexing, or recovery
protocol for arbitrary process crashes. Those belong to a later persistence
phase. The tested guarantees cover local manifest/collection identity checks,
versioned path isolation, supplied-vector persistence, and safe rejection of
incomplete or incompatible stores.
