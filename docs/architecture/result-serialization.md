# Evaluation result serialization

`backend/experiments/serialization.py` provides a versioned, canonical JSON
boundary for completed `EvaluationRunResult` and `AblationRunResult` values.
The current serialization schema version is `1`; evaluation and ablation
artifacts use distinct artifact types. JSON is sorted, compact, UTF-8-safe,
and emitted with `allow_nan=False`, so repeated serialization of the same
immutable result is deterministic.

Deserialization treats input as untrusted. It rejects duplicate JSON keys,
unexpected fields, unsupported versions, malformed statuses and hashes,
non-finite numbers, duplicate trace/metric identities, inconsistent report
counts, and altered nested content. Trace fingerprints are recomputed with
the existing trace contract, and evaluation fingerprints are recomputed with
the existing report contract before objects are returned.

An ablation artifact requires the accepted validated `AblationPlan`. Every
variant is checked against its plan run fingerprint, shared manifest and index
identity. Nested evaluation results must agree on benchmark identity and case
order. Comparison rows are recomputed from the reconstructed variant reports;
missing aggregates remain missing, and M4 remains explicitly not comparable
across retrieval score families.

The format preserves computed zero values, skipped/failed/unavailable metric
statuses, aggregate counts, runner-level failure codes and redacted traces.
It never adds prompts, answers, passages, captions, image bytes, model
objects, clients, or local paths. `SerializationError` exposes only stable,
bounded messages while retaining lower-level exceptions as internal causes.

This is an integrity and consistency boundary, not persistence. The functions
do not write files or create a repository, and unkeyed content fingerprints do
not prove authenticity, authorship, execution, or tamper-proof storage.
