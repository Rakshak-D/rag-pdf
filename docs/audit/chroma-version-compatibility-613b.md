# Chroma version compatibility — Phase 6.13B

- **Source revision:** `bf8951f1a3ba701e80eb2faae87f7d64d64c1014`
- **Updated:** 2026-11-02
- **Technical classification:** A — candidate technically feasible for further testing.
- **Security:** not cleared for deployment.
- **Migration:** not approved.
- **HNSW reliability:** unresolved.
- **Phase 6.12F:** incomplete. **Unattended deployment:** NO-GO.

This is an isolated compatibility study. It does not upgrade the project,
approve a migration, fix the HNSW incident, or authorize deployment.

## Original interrupted checkpoint

At that checkpoint the technical result was **C — inconclusive**. The first
acquisition attempt for the Chroma 1.5.0 wheel timed out while
reading the HTTPS response. The retained observation says the destination file
was zero bytes when checked. No local digest was computed; neither experiment
environment received a Chroma installation; and zero compatibility trials
were run. The partial file and the original download observation were
preserved in the disposable temporary workspace. The previous cache query did
not find a complete Chroma wheel.

The initial root-wheel acquisition attempt is therefore still a failed
attempt. The later successful acquisition below is a distinct attempt and
does not change that history.

## Release and artifact verification

The official release pages and PyPI JSON metadata were checked on 2026-11-01.
The [Chroma release listing](https://github.com/chroma-core/chroma/releases)
showed 1.5.9 as the newest stable release; the newer 1.5.10 entries were
development prereleases and were not substituted. Both requested releases
provide a Windows x86-64 `cp39-abi3` wheel, whose stable ABI tag supports the
CPython 3.10 interpreter used here.

| Release | Exact wheel | Published size | Published SHA-256 | Local SHA-256 | Result |
|---|---|---:|---|---|---|
| 1.5.0 | `chromadb-1.5.0-cp39-abi3-win_amd64.whl` | 21,724,723 bytes | `8298cde5ffe448ca5a9794450c8b9700393e824ef8951be425ba2691330e78e6` | `8298cde5ffe448ca5a9794450c8b9700393e824ef8951be425ba2691330e78e6` | Verified |
| 1.5.9 | `chromadb-1.5.9-cp39-abi3-win_amd64.whl` | 23,542,454 bytes | `4fd0b560e56761b7f3cb4d5c6205fd5f20814484b4a3e4e9af9038c2b428fc6c` | `4fd0b560e56761b7f3cb4d5c6205fd5f20814484b4a3e4e9af9038c2b428fc6c` | Verified |

Sources: [PyPI 1.5.0 files](https://pypi.org/project/chromadb/1.5.0/#files) and
[PyPI 1.5.9 files](https://pypi.org/project/chromadb/1.5.9/#files). The exact
filenames, sizes, SHA-256 values, wheel `METADATA` name/version, ZIP structure,
and ABI tags were checked locally. Both root wheels passed `zipfile.testzip()`.
Both were verified before either was installed.

PyPI reports **no provenance attestation** for either release. The checksum
comparison establishes that the downloaded bytes match PyPI's published
artifact metadata. It is not a publisher build attestation, source-to-binary
reproducibility proof, or security audit.

### Acquisition path

The resumed Route A used the official `https://pypi.org/simple/` index with
binary-only, no-dependency, bounded pip downloads. One attempt succeeded for
each release. For 1.5.0, this was the second bounded Route A attempt when the
earlier body-read timeout is included; for 1.5.9 it was the first. The exact
official `files.pythonhosted.org` Route B and the local-cache Route C were not
needed. No untrusted mirror, source distribution, TLS bypass, custom CA, or
system network setting was used.

The earlier failure is classified only as an HTTPS response-read timeout.
The retained observation does not establish whether the server headers had
arrived or whether transfer began before the final zero-byte file check. There
is no evidence of DNS, TLS-handshake, proxy-negotiation, or package-resolution
failure. No proxy-related environment variable or explicit pip index setting
was visible during inspection; no credential values were printed.

## Isolated environments and dependencies

The two previously created disposable environments were inspected before
reuse. They were independent CPython 3.10.11 64-bit Windows virtual
environments, had `include-system-site-packages = false`, and contained no
Chroma installation before the verified offline installs. Both used the same
base interpreter. Their `sys.path` did not include `rag-env`.

| Environment | Python | Architecture | Chroma | Dependency wheels verified | `pip check` |
|---|---|---|---:|---:|---|
| Baseline | CPython 3.10.11 | Windows AMD64 | 1.5.0 | 95 / 95 | Passed |
| Candidate | CPython 3.10.11 | Windows AMD64 | 1.5.9 | 93 / 93 | Passed |

All dependency artifacts were binary wheels downloaded from official PyPI.
The local wheel sets were checked against PyPI release JSON file size and
SHA-256 metadata, then installed offline from hash-locked requirements with
`--no-index --no-deps --require-hashes`. No source build or build-isolation
script was run. Full wheel and final `pip freeze` manifests remain in the
task-owned temporary evidence workspace, outside the repository. Their
manifest digests are:

- Baseline wheel manifest: `d5bc260a38683c3649fb0bf6e1562152247354e7598f406af1b8e068dedbbecd`
- Candidate wheel manifest: `993a07043e1a117ed1cc01ee72fac3a7bf58b00a8c2fb72c22a41b30fb0ed8f3`
- Baseline final environment freeze: `f5e087f76bd2e30f8dff43d4f75098831c6f4664bc45fefb884f6dbeddeb92ad`
- Candidate final environment freeze: `99127c8687c035be508a24e5ae25b837308c544a6cc0cfbe33876e6f425eb4b8`

The baseline closure included `posthog==5.4.0`, `backoff==2.2.1`, and
`distro==1.9.0`; the candidate closure instead included
`pydantic-settings==2.15.0`. Both resolved `pydantic==2.14.2`,
`pydantic-core==2.50.2`, `numpy==2.2.6`, `onnxruntime==1.23.2`,
`grpcio==1.85.1`, `tokenizers==0.23.3`, `huggingface-hub==2.4.0`, and
`httpx2==2.14.0`. The project pin for Pydantic is 2.12.5, so these isolated
dependency closures are not an exact recreation of the repository's complete
production environment. `pip check` confirms installed dependency
consistency only; it is not a vulnerability audit.

To load the committed adapter source in a narrow test-only probe, both
environments also received locally verified wheels for
`PyMuPDF==1.26.7`, `tiktoken==0.12.0`, and `regex==2026.1.15`. Importing the
full `backend.engine.index_builder` still stopped at missing `langchain_core`.
The broader project/model stack was not installed just to force that import.

The repository's `rag-env` was used only to invoke downloads, run local
verification, and run the probe parent. No install/upgrade command targeted
it; it still imports Chroma 1.5.0. This is package-version evidence, not a
byte-for-byte environment comparison. `requirements.txt` and `.python-version`
remain unchanged.

## Application contract and fixture

The inspected application contract is implemented in
[`chroma_store.py`](../../backend/vectorstore/chroma_store.py),
[`writer_lease.py`](../../backend/config/writer_lease.py), and
[`retrieval.py`](../../backend/engine/retrieval.py). It uses
`chromadb.PersistentClient`, `create_collection`, `list_collections`,
`get_collection`, `upsert`, `count`, and `query`; it passes embeddings
explicitly, disables Chroma's default embedding function, requires the
`text_vectors` and `image_vectors` collections, and sets `hnsw:space` to
`cosine`. The application index hierarchy and manifest are maintained beside
Chroma storage. The cooperating `WriterLease` is process- and thread-bound;
it cannot control external writers. The retrieval layer compares persisted
counts with the selected build report before querying.

The disposable fixture used two four-dimensional text vectors and one
four-dimensional image vector, stable IDs (`text-1`, `text-2`, `image-1`),
cosine distance, explicit stored vectors, and explicit query vectors. No PDF,
production model, embedding constructor, Ollama process, remote database, or
user index was used. Child processes set Hugging Face/Transformers offline
flags and reject non-loopback socket resolution/connection attempts. The
captured child records reported no attempted external network access.

The probe ran two related fixture paths per writer:

1. The committed `ChromaVectorStore.create_new()` path performed
   `upsert_text`, `upsert_image`, count and text/image query checks, then closed
   the wrapper and released its lease. It intentionally retained the
   `.fresh-build-incomplete` marker: this compatibility probe did not run the
   index builder or publish a genuine build report, so it did not pretend the
   application index was complete.
2. A separate standalone Chroma fixture used public `PersistentClient`,
   collection creation, `Collection.add`, count, then a distinct cold reader
   process. The cross-version test copied only this standalone Chroma store.

Thus the standalone client contract received cold-process persistence tests.
The existing application adapter's fresh-store write/count/query and lease
release path also ran against each version. The probe did **not** run the
full builder, `ChromaVectorStore.open_existing()`, build-report validation, or
the retrieval/evaluation pipeline. Full adapter reopen coverage was not
claimed because the builder import required `langchain_core`, which was not in
the minimal dependency environment.

## Fixed five-trial matrix

The probe was run sequentially on 2026-11-01 at 23:58 local time. Each writer
and reader was a separate bounded child process; every child exited with code
0 and no timeout. The table distinguishes the Windows subprocess/launcher
PID from the actual Python PID reported inside the child. Collection UUIDs
observed by writer and reader matched within every trial.

| Scenario | Writer launcher / Python PID | Reader launcher / Python PID | Standalone text / image collection UUID | Result |
|---|---:|---:|---|---|
| 1.5.0 fresh trial 1 | 32844 / 31692 | 36752 / 30064 | `0a9f490d-8c6c-4fe6-9b18-71065a459c63` / `4e2f2e4d-f65e-44d9-8907-6c97dba23dbb` | Passed |
| 1.5.0 fresh trial 2 | 29068 / 24660 | 33020 / 11524 | `e0fe50a9-a218-4802-8f15-95d1c24bb7d3` / `9cf28ed8-e4cc-4e27-bc35-ac33ca36ce88` | Passed |
| 1.5.9 fresh trial 1 | 24700 / 4164 | 10832 / 14976 | `ebd38d06-d819-4b5b-ac14-a6cc324afd4e` / `e3426ee4-7d63-455c-8dc0-880f5c847eb3` | Passed |
| 1.5.9 fresh trial 2 | 7128 / 33732 | 29416 / 30952 | `42fd7198-fd6b-4bf0-9b90-d15c215791f2` / `e503f5c8-aee1-46fe-8bd5-73fc2b2cb320` | Passed |
| 1.5.0 source → 1.5.9 disposable copy | 31928 / 33776 | 27016 / 36212 | `92c4c17c-acf1-4271-beae-f37682710662` / `e2f7db55-f5fe-4315-b478-12fbfb1d43f3` | Passed; source unchanged |

Each reader reopened both collections and observed counts `text=2`,
`image=1`. The text query returned `text-1` at distance `0.0` and `text-2` at
distance `1.0`; the image query returned `image-1` at distance `0.0`; and the
repeated text query returned the same IDs and distances. All reported values
were finite. `list_collections()` returned `Collection` objects in both
versions. Collection creation with explicit cosine metadata and `add()` with
explicit vectors succeeded in all standalone writer processes.

There were **2/2** successful 1.5.0 fresh reopen/query trials, **2/2** 1.5.9
fresh trials, and **1/1** 1.5.9 read of a copied 1.5.0 fixture. No HNSW error,
other exception, timeout, retry, or rerun occurred. The run is preserved under
temporary evidence ID `matrix-20261101-2b833dfd05f4484cbc890fbb3f43c7ab`;
its JSON contains the detailed per-process and per-file observations.

## Persistent side effects and lifecycle

Snapshots were taken only after each owning child exited: after the writer,
and again after the reader. Each standalone Chroma store had nine regular
files: `chroma.sqlite3` and four HNSW files for each of two segment directories.
No files were added or removed and no file sizes changed during the reader
interval. All five trials showed the same categories of change:

- SHA-256 changed for `chroma.sqlite3`, and for `data_level0.bin` and
  `length.bin` in each segment: five digest changes.
- `header.bin` and `link_lists.bin` in each segment had timestamp-only
  changes: four timestamp-only changes.
- The four HNSW files and SQLite file had no observed size change.
- The application adapter fixture's manifest SHA-256 matched the value
  recorded by its writer when checked in the later reader process. That
  reader queried only the separate standalone Chroma fixture. The incomplete
  marker remained present and the cooperating writer reservation was absent
  after the adapter's clean close.
- For the copied-index trial, the original 1.5.0 source snapshot remained
  identical after the candidate operated on its separate copy.

These are observed interval changes, not proof of corruption and not proof
that any one `open`, `count`, or `query` call caused a particular write. The
cross-version candidate read also changed bytes in the copy. SQLite contents
were not inspected concurrently or otherwise; no schema migration conclusion
was drawn. Chroma's mutation-capable persistence behavior from the
[Phase 6.12C contract](../architecture/chroma-store-verification-contract.md)
remains in force: these operations are not filesystem-read-only.

All 10 writer/reader child processes completed and were reaped; no probe
process remained running. The application adapter released its lease on the
acquiring thread. The probe deliberately relied on process exit for Chroma
client lifetime and did not invoke private Chroma shutdown APIs. Neither
version ran a Chroma server.

## API and application compatibility assessment

The exercised collection APIs behaved compatibly for this fixture. Both
versions returned `Collection` instances from `list_collections()`, accepted
explicit embeddings and cosine metadata, and reopened/query-read the
persisted records. The application adapter's fresh creation, upsert, count,
query, manifest identity, and lease lifecycle also worked in each isolated
environment. It was not reopened by the reader process.

One concrete lifecycle difference was observed: the 1.5.0 client had no
callable synchronous `close()` or context-manager methods; the 1.5.9 concrete
sync `Client` exposed `close()`, `__enter__`, and `__exit__`. The 1.5.9 method's
docstring says it releases shared-system references and resources. The public
factory return annotation is still `ClientAPI`, which does not declare these
methods; upstream issue [#7885](https://github.com/chroma-core/chroma/issues/7885)
records this interface/type-checking gap. This probe recorded method
availability but did not call `close()`. The repository's wrapper still only
drops its client references and does not use the candidate method. This
surface difference is not evidence that the HNSW incident is fixed or that
the cached-system lifecycle is solved in the application.

No complete application build-report/retrieval/evaluation path was run.
Therefore the result is **standalone API compatibility plus partial
application-adapter compatibility**, not full application or production
pipeline compatibility.

## Security, migration, and HNSW disposition

The latest stable candidate found was 1.5.9, and all four currently identified
Chroma advisories below list it (and the 1.5.0 baseline) as affected with no
patched version listed:

- [GHSA-f4j7-r4q5-qw2c / CVE-2026-45829](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c):
  pre-authentication server code injection involving an untrusted model
  repository and `trust_remote_code`.
- [GHSA-2wm9-hf6c-p5cr / CVE-2026-45830](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr):
  cross-tenant authorization failure for authenticated users.
- [GHSA-xph7-9rjv-w5fr / CVE-2026-45831](https://github.com/advisories/GHSA-xph7-9rjv-w5fr):
  SimpleRBAC permissions are not scoped to tenant, database, or collection.
- [GHSA-36p7-vc44-83pf / CVE-2026-45833](https://github.com/advisories/GHSA-36p7-vc44-83pf):
  authenticated collection-update code injection involving an untrusted model
  repository and `trust_remote_code`.

The experiment used only embedded local clients, synthetic vectors, offline
model settings, and no server or remote access. That narrows the exercised
attack surface but does not clear the packages for deployment. No
comprehensive transitive-dependency vulnerability scan was performed.

**Security disposition:** no deployment approval for either version while
these advisories remain unresolved. **Migration disposition:** no migration
approval. The candidate read one disposable 1.5.0 copy successfully but
changed its SQLite and HNSW file digests; reverse migration, rollback, larger
stores, and crash recovery were not tested. Do not open real research indexes
with 1.5.9 on the basis of this result.

The repository's historical `Error creating hnsw segment reader: Nothing
found on disk` failure was not reproduced. Both versions passed the small
matrix, but five trials do not establish reliability. Upstream [issue
#7946](https://github.com/chroma-core/chroma/issues/7946) is still an open,
related report in a different operating system, Python version, and cache
scenario. It is not evidence that 1.5.9 fixes this repository's incident.
The HNSW root cause and failure policy remain unresolved; see the
[incident record](chroma-hnsw-incident.md).

## Decision

**Technical classification: A — Chroma 1.5.9 is feasible for further isolated
compatibility testing.** This finding is limited to the tested Windows
CPython 3.10.11 wheel, the resolved disposable dependency closure, the small
synthetic fixture, and the five bounded trials. It does not establish
security suitability, complete adapter compatibility, stable migration,
rollback, general retrieval reliability, or production readiness.

The next dependency checkpoint should wait for a stable release with a
reviewed security posture, then repeat compatibility tests in an isolated
environment including the complete application builder, `open_existing`,
report validation, and retrieval contract. Any production dependency change
requires separate compatibility, security, data-migration, and rollback
approval. The HNSW and artifact-publisher incidents remain separately
unresolved; the latter is documented in
[`artifact-publisher-incident.md`](artifact-publisher-incident.md).

## Validation and repository boundary

The compatibility probe compiled and its fixed matrix completed. Both
temporary environments passed `pip check`; the project's Chroma import still
reports 1.5.0. No full Python suite was run. The last established full-suite
result remains **792 passed, 17 skipped, 1 failed** and is not replaced by
these compatibility trials. Baseline verification after the experiment
reported all nine protected research files unchanged.

The probe, this report, and the three previously untracked Phase 6.12F files
are the only local untracked work. No production module, dependency pin,
protected main reference, or research fixture was modified. The probe and
report remain uncommitted and unpushed.

No production source, dependency pin, protected research file, or Phase
6.12F file was changed. Phase 6.13B artifacts remain local and uncommitted.
