# Typed configuration and index identity

Phase 2.1 adds a pure configuration boundary under `backend/config/`; it does
not replace `notebooks/config.py` or execute `notebooks/rag_pipeline.py`.

`LegacyConfigSchema` is generated from the 74 actual dataclass fields in
`notebooks.config.Config`. Its defaults are copied from an actual `Config()`
instance, unexpected keys are forbidden, `fusion_type` remains derived from
`use_weighted_fusion`, and conversion never calls the legacy side-effecting
`Config.validate()`. `ResolvedRunConfig` adds a separate
`WorkbenchSafetyPolicy`. The research default
`reset_collections_on_start=True` is preserved for compatibility, while a
future workbench must explicitly opt into destructive reset through the policy
before execution.

Validation is pure: it creates no directories, collections, models, network
connections, or artifacts. The path helper in `backend/config/paths.py`
resolves paths, including existing symlinks, before checking that a candidate
is inside an application-owned root. It is not an upload filename policy.

## Index fingerprints

`backend/config/fingerprint.py` produces a SHA-256 over canonical UTF-8 JSON
with sorted keys and compact separators. The definition contains the caller's
validated document-manifest SHA-256, an explicit schema version, and the
allowlisted settings below:

- chunk size, overlap, vertical gap, minimum text length, and tokenizer;
- text embedding model identity;
- image embedding model and pretrained configuration;
- image/caption vector blending;
- image extraction and quality thresholds; and
- text/image embedding batch sizes, retained as reproducibility and resource
  identity inputs.

Retrieval `top-k`, reranker choice, output paths, LLM temperature/output
limits, and evaluation verbosity are deliberately runtime or output settings;
changing them does not change this index fingerprint. A model name is only an
identity label: it does not prove immutable model weights or revisions. Exact
artifact provenance remains a later persistence concern.

The schema has not yet been wired into ingestion, Chroma initialization,
retrieval, generation, or experiment persistence. Those boundaries remain
unverified until behavior-preserving adapters and integration fixtures are
implemented.
