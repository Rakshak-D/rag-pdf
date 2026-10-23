# Immutable experiment artifact storage

`ExperimentArtifactStore` is a small local filesystem store for the canonical
evaluation and ablation JSON produced by `serialization.py`. The caller must
provide the store root; it is never inferred from the working directory or
environment. The layout is:

```text
<store-root>/<artifact-type>/<first-two-hex-digits>/<sha256>.json
```

The artifact ID is the SHA-256 digest of the exact UTF-8 bytes stored. Public
`StoredArtifact` references contain only the supported type, digest, portable
store-relative path, byte length and serialization schema version. They never
expose an absolute path.

Writes validate and round-trip through the existing serializers before making
the destination visible. Bytes are flushed and fsynced to a same-directory
temporary file, then published with an exclusive hard-link operation. The
final path is never overwritten. Repeating an identical write verifies and
returns the existing bytes; a mismatch is an integrity error. Temporary
files are cleaned up on failure. Filesystems without the required safe
no-overwrite primitive fail rather than falling back to replacement.

Loads derive paths from a validated lowercase digest, enforce containment and
the no-symlink internal-component policy, apply a default 64 MiB bounded read,
verify the digest, decode strict UTF-8, and invoke the existing typed
deserializer. Ablation loads additionally require the accepted validated
`AblationPlan`. Listing verifies layout and content digests and returns stable
references without reconstructing trusted result objects; full typed
validation occurs on load.

The store does not create a database, index file, API or job system. Directory
metadata durability can vary by platform—especially on Windows—and a local
filesystem write is not a cryptographic authorship or authenticity guarantee.
The unkeyed digest detects mismatch against the referenced content only.
Stored JSON retains the existing privacy boundary: prompts, answers,
passages, captions, image bytes, runtime clients and absolute paths are not
added by this layer.
