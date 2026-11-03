# Phase 6.16C — Single Ablation Publisher Diagnostic

**Review date:** 2026-11-03
**Starting checkpoint:** `b9a564bd4516ee160b469e7d3b29ef7d2bebf3f8`
**Branch:** `production-evaluation-workbench`
**Outcome:** **A — `PASSED_INCONCLUSIVE`**

## Scope and preflight

One separately approved diagnostic invocation was authorized for the existing
concurrent ablation publisher test. The approval excluded retries, alternative
scenarios, the diagnostic matrix runner, source/test edits, dependency changes,
native work, and deployment. The invocation used `_run_attempt()` directly;
the runner's `main()` was not called.

Before launch, local HEAD and remote feature HEAD both matched
`b9a564bd4516ee160b469e7d3b29ef7d2bebf3f8`, with parent
`f9c7cea055d68e67e0ace78b1c59b245c22feb9b`. Protected `main` was
`7e9522618296af6a7f25652cc8161009d9a92685`; the fixture blob was
`25331e9324182002eb0333a5c56034e684e3c018`. The only worktree change was the
unstaged requirements candidate at SHA-256
`EF3095DBC1A1657CCB5D5FCBA4D2DF245DD5EC9D04B6C2D237823FDFE472734F`; nothing
was staged.

The interpreter was CPython 3.10.11, 64-bit Windows AMD64. Read-only installed
metadata reported Chroma 1.5.0, OmegaConf 2.3.0, and async-timeout 5.0.1. The
target node existed, the opt-in diagnostic path was supported, and the
reviewed `_run_attempt()` implementation launched one pytest child with the
90-second timeout, bounded output, and bounded cleanup. The baseline verifier
passed before execution: **9 protected research files unchanged**.

The historical ablation attribution remains **USER_REPORT_ONLY** from Phase
6.16B. This execution was a candidate investigation, not an independently
verified reproduction of the historical incident.

## Invocation and result

The PowerShell wrapper called `_run_attempt(base_root, "ablation", node, 1)`
once after asserting that the `ablation` entry in `SCENARIOS` matched the
target. It created a fresh evidence root under
`%LOCALAPPDATA%\Temp\rag-pdf-616c-ablation-qizllpzu`. The exact PowerShell
wrapper was:

```powershell
@'
import json
import sys
import tempfile
from pathlib import Path

from scripts.diagnose_artifact_publisher import (
    SCENARIOS,
    _run_attempt,
)

node = (
    "tests/unit/test_artifact_store.py::"
    "test_concurrent_ablation_saves_are_idempotent_and_plan_bound"
)

assert dict(SCENARIOS).get("ablation") == node, (
    "Pinned diagnostic scenario does not match expected node"
)

base_root = Path(
    tempfile.mkdtemp(prefix="rag-pdf-616c-ablation-")
)

print(
    "RAG_PDF_616C_EVIDENCE_ROOT=" + str(base_root),
    flush=True,
)

result = _run_attempt(
    base_root,
    "ablation",
    node,
    1,
)

print(
    "RAG_PDF_616C_RESULT="
    + json.dumps(result, sort_keys=True),
    flush=True,
)

sys.exit(0 if result["classification"] == "passed" else 1)
'@ | & .\rag-env\Scripts\python.exe -
```

It passed this pytest argument vector (with the local evidence-root prefix
abbreviated):

```text
<rag-env>\Scripts\python.exe -m pytest -p no:cacheprovider -q
  --basetemp=<EVIDENCE_ROOT>\ablation-01-7b41ae8cc02d\pytest-temp
  tests/unit/test_artifact_store.py::test_concurrent_ablation_saves_are_idempotent_and_plan_bound
```

| Observation | Result |
|---|---|
| `_run_attempt()` calls | 1 |
| Pytest child invocations | 1 |
| Attempt ID / scenario | `ablation-01-7b41ae8cc02d` / `ablation` |
| Test node | `tests/unit/test_artifact_store.py::test_concurrent_ablation_saves_are_idempotent_and_plan_bound` |
| Pytest result | **1 passed in 7.58s**; 0 failed, 0 skipped, 0 errors |
| Pytest exit code | 0 |
| Runner elapsed time | 10.141 seconds |
| Timeout / cleanup error | No / none |
| Popen PID | 27924 (may identify a Windows launcher) |
| Child interpreter PID | 1652, from the validated child success record |
| Runner classification | `passed` |
| Publisher failure evidence | Not present; runner reported `false` |

No retry or second child was run. The runner's completed `communicate()` call
and zero exit status show that its child was reaped normally; the child
success record independently reports the interpreter PID.

## Success evidence

The runner found exactly one `child-result.json` and no
`failure-evidence.json`. Its schema and required fields were independently
checked against the attempt ID and scenario. The record states:

- Four expected workers completed; all four outcomes were `returned`.
- Artifact integrity was verified.
- Temporary cleanup was verified.
- The success evidence was complete and classified `passed`.
- Artifact ID: `ea6f14f66d73d6adc389b91ff832cb64287116180cce6dcb41759796ce797532`.

The selected test also asserts the shared artifact can be loaded, the ablation
variant ordering matches the plan, serialization remains stable, one artifact
is listed, and no `.artifact-*.tmp` files remain. The artifact was confined to
the test-owned pytest temporary root. Its bytes are not included here.

The normal test helper synchronizes the four workers at its local link proxy;
this is part of the existing test's controlled concurrency behavior, not a
fault injection. The run observed no worker failure. Consequently, there is
no failure-time exception chain or first-failure ordering to inspect.
Per-worker in-memory trace events were not persisted in the success record; no
failure event sequence exists for this attempt. The success record does not
establish an original natural failure's cause or stage, nor does it
independently record link-proxy arrival timing.

## Retained evidence manifest

Files remain in the task-owned temporary directory under the evidence root
above. Paths below are relative to that root. Hashes identify the retained
files; no raw artifact payload or full user-specific absolute path is included
in this report.

| Relative file | Bytes | SHA-256 | Contents |
|---|---:|---|---|
| `ablation-01-7b41ae8cc02d/attempt.json` | 979 | `adcbaf0ddd4951905bad2b6c4145c3ff59126ad931c0db3ecf08f5c78ddfa34a` | Runner command, PIDs, duration, exit and classification metadata |
| `ablation-01-7b41ae8cc02d/pytest-output.log` | 100 | `7bb87829794c7c4d53df5a29d954fc5c1c35afa32a9743bdf91efb47a09c05a9` | Bounded combined pytest output: one pass in 7.58s |
| `ablation-01-7b41ae8cc02d/pytest-temp/test_concurrent_ablation_saves0/artifact-publisher-diagnostics/child-result.json` | 439 | `ea9f515e9e57832511608fbb4c8f994f8d37f7382906371d9f8c7d59daa553af` | Validated child success record |
| `ablation-01-7b41ae8cc02d/pytest-temp/test_concurrent_ablation_saves0/artifacts/ablation_result/ea/ea6f14f66d73d6adc389b91ff832cb64287116180cce6dcb41759796ce797532.json` | 4205 | `ea6f14f66d73d6adc389b91ff832cb64287116180cce6dcb41759796ce797532` | Test-owned canonical artifact; content not reproduced here |

No failure-evidence JSON was created. The actual local records also contain
their local command paths; those are preserved in the temporary root rather
than copied into this committed report.

## Source identities

The test and runner came from the starting checkpoint. Their Git blob IDs were:

| File | Blob ID |
|---|---|
| `backend/experiments/artifact_store.py` | `b7ef0e46307672d74b90b8b50bd13c5e898829c0` |
| `tests/unit/test_artifact_store.py` | `e43dd379cf7160c86b004b712700c16162aa53cd` |
| `tests/diagnostics/artifact_publisher.py` | `572cc7c9b7f324d7859f3d66a00f04510d0c227f` |
| `scripts/diagnose_artifact_publisher.py` | `6457e706cf7fef92c8cc2206ea7a0c8e047a7e93` |

## Interpretation and decision

This is **A — `PASSED_INCONCLUSIVE`**: one bounded concurrent ablation test
passed and emitted a complete, validated success record. It establishes the
selected test's assertions for this invocation only. It does not establish
publisher reliability, a failure probability, or resolution of the historical
incident.

The Phase 6.13A incident remains **C — not reproduced in its bounded matrix**.
The Phase 6.16A decision remains `NO_MATERIAL_CAPTURE_GAP`. Phase 6.16B remains
`USER_REPORT_ONLY`; the retrospective three-of-four account is not upgraded by
this passing run. No additional attempt is authorized or recommended by this
result.

No source, test, diagnostic, dependency, or environment files were changed.
No native build, Chroma query, dependency installation, repair, deployment, or
release qualification occurred. The deferred dependency candidate remains
unstaged and unchanged. HNSW reliability and the natural publisher incident
remain unresolved; dependency adoption is deferred, Chroma migration is
unauthorized, and unattended general-purpose deployment remains **NO-GO**.
