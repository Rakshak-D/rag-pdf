# First-run index bootstrap

Use a complete resolved configuration JSON object, including every legacy
configuration field and both workbench policy flags. Set
`reset_collections_on_start` and both destructive policy flags to false.
Provide absolute, trusted, disjoint PDF source and index-storage roots. The
tool rejects symlinked path components, malformed or duplicate JSON keys,
non-finite values, empty corpora, and incomplete configurations.

Preview without loading models, extracting PDFs, or creating storage:

```powershell
.\rag-env\Scripts\python.exe scripts/index_bootstrap.py --config <absolute-config-json> --source <absolute-pdf-root> --storage-root <absolute-index-root>
```

Preview displays the current manifest hash, document count, index fingerprint,
and whether its fingerprint-owned path exists. It is a point-in-time observation,
not a reservation. To build, confirm both hashes from a current preview:

```powershell
.\rag-env\Scripts\python.exe scripts/index_bootstrap.py --config <absolute-config-json> --source <absolute-pdf-root> --storage-root <absolute-index-root> --execute --expected-manifest-hash <manifest-sha256> --expected-index-fingerprint <index-sha256>
```

Execution rechecks the manifest and RunPlan before and after acquiring writer
ownership. The builder stages its existing verified snapshot for extraction,
uses the existing pipeline, and rechecks the source before publishing success.
The CLI checks it again before reporting success. These checks are point-in-time
observations; files can change afterward. Actual execution may load embedding
models, download weights according to their configured loaders, and consume
substantial CPU, GPU, memory, and disk resources.

## Writer ownership and failure

Cooperating repository builders and writable `ChromaVectorStore.open()` calls
acquire an atomic reservation under `.writer-leases/<index-fingerprint>` in
the canonical storage root. A reservation is exclusive across processes on
the same filesystem and is held through store close and report publication.
Each operation receives a process- and thread-bound capability; a second thread
cannot reuse it. Different fingerprints use separate reservations.
`open_existing()` is read-only and rejects upserts, but also holds the same
exclusive reservation while its Chroma client is open because the client's
own filesystem effects are not established as read-only. Consequently,
concurrent opens of the same fingerprint fail rather than waiting; operators
may need to retry a rejected operation explicitly after the owner closes it.
A fresh build creates its
index directory exclusively and never opens existing collections for reuse.

An existing directory, incomplete index, symlink, or occupied reservation is
rejected. A process crash leaves the reservation in place: no PID-based or
automatic takeover occurs. After fresh index creation, an incomplete marker
also blocks later opens until the successful report has been published. Failed
or partial indexes and reports are preserved for investigation; they are not
deleted, repaired, or automatically retried. Investigate stale reservations
and partial data offline before any operator-controlled recovery. Do not remove
a marker merely because a recorded process appears dead.

This protocol coordinates callers using this repository version. It cannot
prevent an older workbench process, arbitrary Chroma client, or other external
writer from ignoring the reservation; stop such processes before a build and
keep the storage root under trusted local control. The filesystem must provide
atomic directory creation. No distributed or network-filesystem coordination
is claimed.

## Reports and runtime

The command reports the actual build ID, status, page and record counts,
failure stage, partial-persistence flag, and report location. A failed or
partially persisted build is not a successful index. If report publication
succeeds but finalization fails, the incomplete marker remains and the CLI
reports failure; do not treat the report alone as proof of usability.

After a successful build, manually configure the existing runtime variables,
including `RAG_WORKBENCH_INDEX_STORAGE_ROOT` and
`RAG_WORKBENCH_BUILD_REPORT_PATH`, then launch `python -m backend.app` using
the project environment. Inspect the current manifest, prepare a RunPlan,
check report compatibility, and submit experiments explicitly. The CLI does
not select a report or launch the runtime. Report compatibility does not prove
stored-index usability or future retrieval success; the retrieval boundary
remains authoritative.

For an authenticated operator workflow around the same fresh-only builder,
see [bootstrap HTTP operations](bootstrap-http-operations.md). The bootstrap
server is separate from the experiment runtime and does not activate a report.
