# Read-only index and build-report inventory

The `IndexInventoryService` discovers build reports for operator inspection.
The bootstrap API exposes it at `POST /api/v1/bootstrap/index-inventory`.
This is a discovery operation only: it does not select or activate a report,
change runtime configuration, run retrieval, or start an experiment.

## Request and trusted scope

The request body contains:

```json
{
  "resolved_config": {},
  "expected_manifest_hash": "<64 lowercase hexadecimal SHA-256 characters>",
  "limit": 25,
  "offset": 0
}
```

The empty object above is a placeholder; a real request must supply every
`ResolvedRunConfig` field. Unknown request properties,
duplicate JSON keys, non-finite JSON numbers, destructive settings, malformed
pagination, and a configuration source that does not match the trusted
bootstrap source root are rejected. The HTTP client cannot supply a storage
root, report path, directory name, or build ID to scan. The service rebuilds
the current manifest and canonical evaluation RunPlan and rejects an empty or
changed source manifest.

Only direct entries within
`<trusted-storage-root>/build-artifacts/<build-id>/build-report.json` are
examined. Build IDs must be 32 lowercase hexadecimal characters. The service
does not recursively search other directories. It checks path containment,
rejects symlinked candidate paths, reads each report with a 1 MiB default
limit, and rejects malformed JSON, duplicate keys, non-finite values,
unsupported report schemas, invalid report hierarchies, and invalid identities.
Report compatibility uses the established retrieval report validator; this
service does not define another build-report schema or fingerprint formula.

## Bounds and ordering

The default inventory considers at most 512 direct entries and returns at
most 100 results per request. Results are ordered lexically by the build
artifact directory name. This is a deterministic ordering for pagination and
does not claim that one report is newer than another. `offset` is limited to
0–512. Each response reports its offset, limit, total candidate count, and
whether enumeration was complete.

When more than the configured entry cap exists, the service returns no
partial candidate list, sets `complete` to false, leaves the total unknown,
and reports `inventory_limit_exceeded`. The API does not silently present a
truncated scan as complete. Separate page requests are not a durable snapshot:
concurrent build publication can change the inventory between requests.

## Candidate classifications

| Classification | Meaning |
|---|---|
| `report_match` | The report passed schema, location, successful-build, persistence, and RunPlan identity checks. |
| `mismatch` | A valid report identifies another index fingerprint or document manifest. |
| `failed` | The report records a failed build. |
| `incomplete` | The report is oversized or partial, or the associated fresh-build incomplete marker is present. |
| `invalid` | The report or candidate path is malformed, unsupported, or unsafe. |
| `unavailable` | A candidate or report could not be read reliably. |

Only a validated `report_match` exposes reported text and image record counts.
Candidate references are relative to the trusted storage root. Invalid
directory names are represented by a short hash identifier and are not echoed
back to the operator.

Every page and candidate states `check_level: "report"` and
`store_checked: false`. A matching report is evidence about the report's
identity and recorded build metadata only. The service does not open Chroma or
inspect persisted collections, so the result does not establish that an index
is currently readable or that a future retrieval will succeed. The ordinary
retrieval path remains authoritative for its own report and store validation.

## Read-only boundary and limitations

Inventory does not create directories, write reports, acquire a fingerprint
writer lease, construct a Chroma client, load embedding or reranking models,
make network calls, submit a build job, repair partial storage, remove
incomplete markers, or activate an index. The bootstrap API limits concurrent
inventory scans to two and performs filesystem work outside the async event
loop.

The manifest check is a point-in-time check. The service verifies the source
manifest before and after scanning, but files can change immediately after
the response. A response is not a reservation and is not a guarantee that a
later build or retrieval will succeed. Inventory does not verify store counts
against collections.

There is no general index catalog and no index selection or activation
operation in this phase. Operators must continue to configure the ordinary
runtime's existing report path manually. The historical Chroma HNSW reader
error and intermittent artifact publisher integrity incident remain
unresolved; this service does not attempt to diagnose or repair either one.
