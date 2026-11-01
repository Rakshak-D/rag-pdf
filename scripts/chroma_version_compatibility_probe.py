"""Bounded, synthetic Chroma 1.5.0/1.5.9 compatibility probe.

The probe is deliberately a local research harness. It writes only beneath a
caller-supplied temporary output root and never opens an existing user index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
from typing import Any


SCHEMA_VERSION = 1
MANIFEST_HASH = "a" * 64
TEXT_COLLECTION = "text_vectors"
IMAGE_COLLECTION = "image_vectors"
APPLICATION_STORAGE = "adapter-storage"
STANDALONE_STORAGE = "standalone-chroma"
TEXT_ROWS = (
    ("text-1", [1.0, 0.0, 0.0, 0.0], "synthetic text one", {"fixture": "synthetic", "ordinal": 1}),
    ("text-2", [0.0, 1.0, 0.0, 0.0], "synthetic text two", {"fixture": "synthetic", "ordinal": 2}),
)
IMAGE_ROWS = (
    ("image-1", [0.0, 0.0, 1.0, 0.0], {"fixture": "synthetic", "ordinal": 1}),
)
TEXT_QUERY = [1.0, 0.0, 0.0, 0.0]
IMAGE_QUERY = [0.0, 0.0, 1.0, 0.0]
KNOWN_HNSW_ERROR = "Error creating hnsw segment reader: Nothing found on disk"
_NETWORK_ATTEMPTS: list[str] = []


def _event(root: Path, role: str, stage: str, **fields: Any) -> None:
    record = {
        "role": role,
        "stage": stage,
        "monotonic_ns": time.monotonic_ns(),
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        **fields,
    }
    with (root / f"{role}-events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _write_json(path: Path, record: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _install_external_network_guard() -> None:
    """Reject non-loopback socket destinations and record attempted access."""

    import ipaddress

    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(host, str) and host.lower() in {"localhost", "127.0.0.1", "::1"}:
            return original_getaddrinfo(host, *args, **kwargs)
        try:
            address = ipaddress.ip_address(host)
        except (TypeError, ValueError):
            _NETWORK_ATTEMPTS.append("dns")
            raise OSError("external networking disabled by compatibility probe")
        if not address.is_loopback:
            _NETWORK_ATTEMPTS.append("dns")
            raise OSError("external networking disabled by compatibility probe")
        return original_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address:
            host = address[0]
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except (TypeError, ValueError):
                loopback = str(host).lower() == "localhost"
            if not loopback:
                _NETWORK_ATTEMPTS.append("connect")
                raise OSError("external networking disabled by compatibility probe")
        return original_connect(sock, address)

    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.socket.connect = guarded_connect  # type: ignore[assignment]


def _load_application_adapter(project_root: Path) -> tuple[Any, Any, Any, Any, Any]:
    """Load the exact adapter source without eager optional package exports.

    ``backend.experiments.__init__`` imports the full engine/evaluation stack.
    A namespace-package shim skips only that eager export initializer; the
    actual RunPlan, WriterLease, config, and ChromaVectorStore modules remain
    the repository's committed source files.
    """

    sys.path.insert(0, str(project_root))
    experiments = types.ModuleType("backend.experiments")
    experiments.__path__ = [str(project_root / "backend" / "experiments")]  # type: ignore[attr-defined]
    experiments.__package__ = "backend.experiments"
    sys.modules["backend.experiments"] = experiments

    from backend.config import ResolvedRunConfig
    from backend.config.writer_lease import WriterLease
    from backend.experiments.run_plan import RunPlan, build_run_plan
    from backend.vectorstore import ChromaVectorStore
    from notebooks.config import Config

    return ResolvedRunConfig, WriterLease, RunPlan, build_run_plan, (ChromaVectorStore, Config)


def _make_plan(root: Path, adapter_types: tuple[Any, Any]) -> tuple[Any, Path]:
    ResolvedRunConfig, build_run_plan = adapter_types
    paths = {
        "pdf_dir": root / "sources",
        "images_dir": root / "image-source",
        "database_dir": root / "database",
        "results_dir": root / "results",
        "model_comparison_results_dir": root / "model-comparison",
        "retrieval_results_dir": root / "retrieval-results",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    from notebooks.config import Config

    legacy = Config(**{key: str(value) for key, value in paths.items()})
    legacy.reset_collections_on_start = False
    legacy.text_embed_model = "synthetic-text-adapter"
    legacy.image_embed_model = "synthetic-image-adapter"
    legacy.image_embed_pretrained = "synthetic-fixture-v1"
    resolved = ResolvedRunConfig.from_legacy(legacy)
    plan = build_run_plan(resolved, MANIFEST_HASH)
    return plan, paths["pdf_dir"]


def _client_settings() -> Any:
    from chromadb.config import Settings

    return Settings(anonymized_telemetry=False)


def _worker_failure(root: Path, role: str, stage: str, exc: BaseException) -> None:
    record = {
        "schema_version": SCHEMA_VERSION,
        "role": role,
        "status": "failed",
        "stage": stage,
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        "chroma_version": _installed_chroma_version(),
        "exception_type": type(exc).__name__,
        "exception_message": str(exc)[:2000],
        "traceback": traceback.format_exc(),
        "network_attempts": list(_NETWORK_ATTEMPTS),
        "writer_reservation_present": _reservation_state(root),
    }
    try:
        _write_json(root / f"{role}-result.json", record)
    except BaseException as diagnostic_error:
        print(
            f"diagnostic result write failed: {type(diagnostic_error).__name__}",
            file=sys.stderr,
        )


def _installed_chroma_version() -> str:
    try:
        from importlib.metadata import version

        return version("chromadb")
    except Exception:
        return "unknown"


def _reservation_state(root: Path, fingerprint: str | None = None) -> bool:
    if fingerprint is None:
        marker = root / APPLICATION_STORAGE / ".writer-leases"
        return marker.exists() and any(marker.iterdir())
    return (root / APPLICATION_STORAGE / ".writer-leases" / fingerprint).exists()


def _storage_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Snapshot both isolated stores after their Chroma owner has exited."""

    result: dict[str, dict[str, Any]] = {}
    for label in (APPLICATION_STORAGE, STANDALONE_STORAGE):
        for relative, details in _file_snapshot(root / label).items():
            result[f"{label}/{relative}"] = details
    return result


def _writer(root: Path, project_root: Path, expected_version: str) -> dict[str, Any]:
    role = "writer"
    stage = "worker_started"
    store = None
    lease = None
    store_closed = False
    try:
        _install_external_network_guard()
        import chromadb

        if chromadb.__version__ != expected_version:
            raise RuntimeError(f"unexpected Chroma version: {chromadb.__version__}")
        _event(root, role, "fixture_created", chroma_version=chromadb.__version__)

        ResolvedRunConfig, WriterLease, RunPlan, build_run_plan, values = _load_application_adapter(project_root)
        ChromaVectorStore, Config = values
        plan, _ = _make_plan(root, (ResolvedRunConfig, build_run_plan))
        storage = root / APPLICATION_STORAGE
        storage.mkdir(parents=True, exist_ok=True)
        index_root = storage / "indexes" / plan.index_fingerprint
        _write_json(root / "plan.json", json.loads(plan.to_serializable_json()))
        stage = "writer_lease_acquire_started"
        lease = WriterLease.acquire(storage, plan.index_fingerprint)
        _event(root, role, "writer_lease_acquired", fingerprint=plan.index_fingerprint[:12])

        stage = "fresh_only_store_open_started"
        store = ChromaVectorStore.create_new(storage, plan, _lease=lease)
        _event(root, role, "fresh_only_store_opened", reservation_present=True)

        stage = "text_upsert_started"
        store.upsert_text(
            [row[0] for row in TEXT_ROWS],
            [row[1] for row in TEXT_ROWS],
            [row[2] for row in TEXT_ROWS],
            [row[3] for row in TEXT_ROWS],
        )
        _event(root, role, "text_upsert_completed", record_ids=[row[0] for row in TEXT_ROWS])

        stage = "image_upsert_started"
        store.upsert_image([IMAGE_ROWS[0][0]], [IMAGE_ROWS[0][1]], [IMAGE_ROWS[0][2]])
        _event(root, role, "image_upsert_completed", record_ids=[IMAGE_ROWS[0][0]])

        stage = "writer_counts_started"
        counts = {"text": store.count("text"), "image": store.count("image")}
        if counts != {"text": 2, "image": 1}:
            raise AssertionError(f"unexpected writer counts: {counts}")
        adapter_text = store.query_text(TEXT_QUERY, 2)
        adapter_image = store.query_image(IMAGE_QUERY, 1)
        if list(adapter_text["ids"][0]) != ["text-1", "text-2"] or list(adapter_image["ids"][0]) != ["image-1"]:
            raise AssertionError("application adapter query returned unexpected fixture records")
        adapter_text_distances = [float(value) for value in adapter_text["distances"][0]]
        adapter_image_distances = [float(value) for value in adapter_image["distances"][0]]
        if (
            not all(math.isfinite(value) for value in adapter_text_distances + adapter_image_distances)
            or len(adapter_text_distances) != 2
            or len(adapter_image_distances) != 1
            or abs(adapter_text_distances[0]) > 1e-5
            or abs(adapter_text_distances[1] - 1.0) > 1e-5
            or abs(adapter_image_distances[0]) > 1e-5
        ):
            raise AssertionError("application adapter returned unexpected cosine distances")
        manifest_path = index_root / "index-manifest.json"
        manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        _event(root, role, "writer_counts_observed", counts=counts)

        stage = "writer_wrapper_close_started"
        store.close()
        store_closed = True
        _event(root, role, "writer_wrapper_closed", reservation_present=True)
        lease.release()
        lease = None
        _event(root, role, "writer_lease_released", reservation_present=_reservation_state(root, plan.index_fingerprint))

        # This is a separate low-level Chroma fixture. It exercises public
        # PersistentClient/Collection.add semantics without falsely clearing
        # the adapter's fresh-build marker in the absence of a genuine build
        # report. The application adapter fixture remains deliberately marked
        # incomplete because this experiment does not run the PDF builder.
        stage = "standalone_client_create_started"
        standalone_path = root / STANDALONE_STORAGE
        standalone_path.mkdir(parents=True, exist_ok=False)
        client = chromadb.PersistentClient(path=str(standalone_path), settings=_client_settings())
        text_collection = client.create_collection(
            TEXT_COLLECTION,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )
        image_collection = client.create_collection(
            IMAGE_COLLECTION,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )
        text_collection.add(
            ids=[row[0] for row in TEXT_ROWS],
            embeddings=[row[1] for row in TEXT_ROWS],
            documents=[row[2] for row in TEXT_ROWS],
            metadatas=[row[3] for row in TEXT_ROWS],
        )
        image_collection.add(
            ids=[row[0] for row in IMAGE_ROWS],
            embeddings=[row[1] for row in IMAGE_ROWS],
            metadatas=[row[2] for row in IMAGE_ROWS],
        )
        standalone_counts = {"text": text_collection.count(), "image": image_collection.count()}
        if standalone_counts != counts:
            raise AssertionError(f"standalone and application adapter counts differ: {standalone_counts} != {counts}")
        collection_info = {
            "text": {"id": str(text_collection.id), "name": text_collection.name, "metadata": dict(text_collection.metadata or {})},
            "image": {"id": str(image_collection.id), "name": image_collection.name, "metadata": dict(image_collection.metadata or {})},
            "list_collections_item_types": [type(value).__name__ for value in client.list_collections()],
            "public_close_available": callable(getattr(client, "close", None)),
        }
        text_collection = None
        image_collection = None
        client = None
        adapter_marker_present = (index_root / ".fresh-build-incomplete").is_file()
        if not adapter_marker_present:
            raise AssertionError("adapter fixture marker unexpectedly absent without a genuine build report")

        if _NETWORK_ATTEMPTS:
            raise RuntimeError("external networking was attempted by the child")
        result = {
            "schema_version": SCHEMA_VERSION,
            "role": role,
            "status": "passed",
            "stage": "writer_completed",
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "chroma_version": chromadb.__version__,
            "index_fingerprint": plan.index_fingerprint,
            "document_manifest_hash": plan.document_manifest_hash,
            "counts": counts,
            "standalone_counts": standalone_counts,
            "application_adapter_operations": ["create_new", "upsert_text", "upsert_image", "count", "query_text", "query_image", "close"],
            "application_adapter_manifest_sha256": manifest_digest,
            "application_adapter_fresh_marker_retained": adapter_marker_present,
            "application_adapter_query_distances": {
                "text": adapter_text_distances,
                "image": adapter_image_distances,
            },
            "collection_info": collection_info,
            "expected_record_ids": {"text": [row[0] for row in TEXT_ROWS], "image": [row[0] for row in IMAGE_ROWS]},
            "manifest_sha256": manifest_digest,
            "fresh_only": True,
            "writer_reservation_present_after_close": _reservation_state(root, plan.index_fingerprint),
            "network_attempts": list(_NETWORK_ATTEMPTS),
            "process_boundary_required_for_client_quiescence": True,
        }
        _write_json(root / "writer-result.json", result)
        print(json.dumps(result, sort_keys=True))
        return result
    except BaseException as exc:
        if store is not None and not store_closed:
            try:
                store.close()
                store_closed = True
            except BaseException as cleanup_error:
                _event(root, role, "store_close_failed", exception_type=type(cleanup_error).__name__)
        if lease is not None and (store_closed or store is None):
            try:
                lease.release()
            except BaseException as cleanup_error:
                _event(root, role, "lease_release_failed", exception_type=type(cleanup_error).__name__)
        _event(root, role, "worker_failed", stage=stage, exception_type=type(exc).__name__)
        _worker_failure(root, role, stage, exc)
        raise


def _reader(root: Path, project_root: Path, expected_version: str, expected_fingerprint: str) -> dict[str, Any]:
    role = "reader"
    stage = "worker_started"
    client = None
    text_collection = None
    image_collection = None
    try:
        _install_external_network_guard()
        import chromadb

        if chromadb.__version__ != expected_version:
            raise RuntimeError(f"unexpected Chroma version: {chromadb.__version__}")
        standalone_path = root / STANDALONE_STORAGE
        stage = "persistent_client_reopen_started"
        client = chromadb.PersistentClient(
            path=str(standalone_path),
            settings=_client_settings(),
        )
        listed = client.list_collections()
        names = [value.name if hasattr(value, "name") else str(value) for value in listed]
        if sorted(names) != [IMAGE_COLLECTION, TEXT_COLLECTION]:
            raise AssertionError(f"unexpected reopened collection names: {names}")
        text_collection = client.get_collection(TEXT_COLLECTION, embedding_function=None)
        image_collection = client.get_collection(IMAGE_COLLECTION, embedding_function=None)
        collection_info = {
            "names": sorted(names),
            "list_collections_item_types": [type(value).__name__ for value in listed],
            "text": {"id": str(text_collection.id), "name": text_collection.name, "metadata": dict(text_collection.metadata or {})},
            "image": {"id": str(image_collection.id), "name": image_collection.name, "metadata": dict(image_collection.metadata or {})},
            "public_close_available": callable(getattr(client, "close", None)),
        }

        stage = "persisted_counts_started"
        counts = {"text": text_collection.count(), "image": image_collection.count()}
        if counts != {"text": 2, "image": 1}:
            raise AssertionError(f"report-like expected counts do not match: {counts}")
        _event(root, role, "persisted_counts_verified", counts=counts)

        stage = "query_text_started"
        first_text = text_collection.query(
            query_embeddings=[TEXT_QUERY], n_results=2,
            include=["metadatas", "documents", "distances"],
        )
        text_ids = list(first_text["ids"][0])
        text_distances = [float(value) for value in first_text["distances"][0]]
        if text_ids != ["text-1", "text-2"] or not all(math.isfinite(v) for v in text_distances):
            raise AssertionError(f"unexpected text query: ids={text_ids}, distances={text_distances}")
        if abs(text_distances[0]) > 1e-5 or abs(text_distances[1] - 1.0) > 1e-5:
            raise AssertionError(f"unexpected cosine text distances: {text_distances}")
        _event(root, role, "query_text_completed", record_ids=text_ids, distances=text_distances)

        stage = "query_image_started"
        image_result = image_collection.query(
            query_embeddings=[IMAGE_QUERY], n_results=1,
            include=["metadatas", "distances"],
        )
        image_ids = list(image_result["ids"][0])
        image_distances = [float(value) for value in image_result["distances"][0]]
        if image_ids != ["image-1"] or not all(math.isfinite(v) for v in image_distances):
            raise AssertionError(f"unexpected image query: ids={image_ids}, distances={image_distances}")
        if abs(image_distances[0]) > 1e-5:
            raise AssertionError(f"unexpected cosine image distance: {image_distances}")
        _event(root, role, "query_image_completed", record_ids=image_ids, distances=image_distances)

        stage = "query_text_repeated_started"
        repeated_text = text_collection.query(
            query_embeddings=[TEXT_QUERY], n_results=2,
            include=["metadatas", "documents", "distances"],
        )
        repeated_ids = list(repeated_text["ids"][0])
        repeated_distances = [float(value) for value in repeated_text["distances"][0]]
        if repeated_ids != text_ids or any(abs(a - b) > 1e-6 for a, b in zip(text_distances, repeated_distances)):
            raise AssertionError("repeated text query differed from the first query")
        _event(root, role, "query_text_repeated_completed", record_ids=repeated_ids, distances=repeated_distances)

        writer_record_path = root / "writer-result.json"
        writer_record = json.loads(writer_record_path.read_text(encoding="utf-8")) if writer_record_path.exists() else None
        manifest_path = root / APPLICATION_STORAGE / "indexes" / expected_fingerprint / "index-manifest.json"
        adapter_manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
        if writer_record is not None and adapter_manifest_digest != writer_record.get("application_adapter_manifest_sha256"):
            raise AssertionError("application index manifest changed after writer process exit")
        adapter_marker = manifest_path.parent / ".fresh-build-incomplete"
        if writer_record is not None and not adapter_marker.is_file():
            raise AssertionError("application adapter fixture marker changed unexpectedly")
        client = None
        text_collection = None
        image_collection = None

        if _NETWORK_ATTEMPTS:
            raise RuntimeError("external networking was attempted by the child")
        result = {
            "schema_version": SCHEMA_VERSION,
            "role": role,
            "status": "passed",
            "stage": "reader_completed",
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "chroma_version": chromadb.__version__,
            "index_fingerprint": expected_fingerprint,
            "document_manifest_hash": MANIFEST_HASH,
            "counts": counts,
            "collection_info": collection_info,
            "text_query": {"ids": text_ids, "distances": text_distances},
            "image_query": {"ids": image_ids, "distances": image_distances},
            "repeated_text_query": {"ids": repeated_ids, "distances": repeated_distances},
            "application_adapter_manifest_sha256": adapter_manifest_digest,
            "application_adapter_fresh_marker_retained": adapter_marker.is_file(),
            "application_adapter_writer_reservation_present": _reservation_state(root, expected_fingerprint),
            "standalone_client_close_available": collection_info["public_close_available"],
            "network_attempts": list(_NETWORK_ATTEMPTS),
        }
        # Client references were released before creating the result. The
        # installed releases do not expose a public close method; process exit
        # is the lifecycle boundary for the persistent system.
        _write_json(root / "reader-result.json", result)
        print(json.dumps(result, sort_keys=True))
        return result
    except BaseException as exc:
        _event(root, role, "worker_failed", stage=stage, exception_type=type(exc).__name__)
        _worker_failure(root, role, stage, exc)
        raise


def _file_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Hash a quiescent, test-owned tree; caller must ensure child exit."""

    snapshot: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            snapshot[path.relative_to(root).as_posix()] = {"kind": "symlink"}
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = {
                "kind": "file",
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": digest.hexdigest(),
            }
    return snapshot


def _classify_file(relative_path: str) -> str:
    lower = relative_path.lower()
    name = lower.rsplit("/", 1)[-1]
    if name.startswith("chroma.sqlite3") or name.endswith((".sqlite", ".sqlite3", ".db")):
        return "sqlite"
    if name in {"header.bin", "data_level0.bin", "length.bin", "link_lists.bin", "index_metadata.pickle"} or "hnsw" in lower:
        return "hnsw_or_segment"
    if name == "index-manifest.json":
        return "application_manifest"
    return "other"


def _snapshot_diff(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, Any]:
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    size_changed: list[str] = []
    digest_changed: list[str] = []
    timestamp_only: list[str] = []
    for name in sorted(before.keys() & after.keys()):
        left, right = before[name], after[name]
        if left.get("size") != right.get("size"):
            size_changed.append(name)
        if left.get("sha256") != right.get("sha256"):
            digest_changed.append(name)
        elif left.get("mtime_ns") != right.get("mtime_ns"):
            timestamp_only.append(name)
    files = sorted(set(added + removed + size_changed + digest_changed + timestamp_only))
    by_category = {
        category: [name for name in files if _classify_file(name) == category]
        for category in ("sqlite", "hnsw_or_segment", "application_manifest", "other")
    }
    return {
        "added": added,
        "removed": removed,
        "size_changed": size_changed,
        "digest_changed": digest_changed,
        "timestamp_only": timestamp_only,
        "changed_by_category": by_category,
    }


def _run_child(
    *,
    interpreter: Path,
    mode: str,
    root: Path,
    expected_version: str,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[1]
    role = "writer" if mode == "write" else "reader"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(project_root),
            "ANONYMIZED_TELEMETRY": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "HF_HOME": str(root / "model-cache"),
            "TRANSFORMERS_CACHE": str(root / "model-cache"),
        }
    )
    command = [str(interpreter), str(Path(__file__).resolve()), "--worker", mode, str(root), str(project_root), expected_version]
    if expected_fingerprint is not None:
        command.append(expected_fingerprint)
    output_path = root / f"process-{role}"
    output_path.mkdir(parents=True, exist_ok=True)
    stdout_path = output_path / "stdout.txt"
    stderr_path = output_path / "stderr.txt"
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        result = {"role": role, "launch_error": type(exc).__name__, "exit_code": None, "actual_python_pid": None}
        _write_json(output_path / "parent-process-result.json", result)
        return result

    launcher_pid = process.pid
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        timed_out = True
        # The known PID is the probe's own Windows child. Kill only that child
        # tree so the venv redirector cannot leave its interpreter running.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(launcher_pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            pass
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
    stdout_path.write_text(stdout or "", encoding="utf-8")
    stderr_path.write_text(stderr or "", encoding="utf-8")
    child_record_path = root / f"{role}-result.json"
    child_record = None
    if child_record_path.is_file():
        try:
            child_record = json.loads(child_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            child_record = {"malformed_result": True}
    result = {
        "role": role,
        "command_launcher_pid": launcher_pid,
        "actual_python_pid": child_record.get("pid") if isinstance(child_record, dict) else None,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "child_result": child_record,
        "stdout_file": stdout_path.name,
        "stderr_file": stderr_path.name,
    }
    _write_json(output_path / "parent-process-result.json", result)
    return result


def _valid_worker_result(record: dict[str, Any] | None, *, role: str, version: str) -> bool:
    if not (
        isinstance(record, dict)
        and record.get("schema_version") == SCHEMA_VERSION
        and record.get("role") == role
        and record.get("status") == "passed"
        and record.get("chroma_version") == version
        and isinstance(record.get("pid"), int)
        and isinstance(record.get("thread_id"), int)
        and record.get("network_attempts") == []
        and isinstance(record.get("index_fingerprint"), str)
        and len(record["index_fingerprint"]) == 64
    ):
        return False
    collection_info = record.get("collection_info")
    if not isinstance(collection_info, dict):
        return False
    for modality, collection_name in (("text", TEXT_COLLECTION), ("image", IMAGE_COLLECTION)):
        details = collection_info.get(modality)
        if not isinstance(details, dict):
            return False
        if details.get("name") != collection_name or not isinstance(details.get("id"), str):
            return False
        metadata = details.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("hnsw:space") != "cosine":
            return False
    expected_counts = {"text": 2, "image": 1}
    expected_ids = {"text": ["text-1", "text-2"], "image": ["image-1"]}
    if role == "writer":
        return bool(
            record.get("stage") == "writer_completed"
            and record.get("counts") == expected_counts
            and record.get("standalone_counts") == expected_counts
            and record.get("expected_record_ids") == expected_ids
            and record.get("fresh_only") is True
            and record.get("application_adapter_fresh_marker_retained") is True
            and record.get("writer_reservation_present_after_close") is False
            and isinstance(record.get("application_adapter_manifest_sha256"), str)
            and len(record["application_adapter_manifest_sha256"]) == 64
            and record.get("application_adapter_operations") == [
                "create_new", "upsert_text", "upsert_image", "count",
                "query_text", "query_image", "close",
            ]
        )
    if role == "reader":
        text_query = record.get("text_query")
        image_query = record.get("image_query")
        repeated = record.get("repeated_text_query")
        if not all(isinstance(value, dict) for value in (text_query, image_query, repeated)):
            return False
        text_distances = text_query.get("distances")
        image_distances = image_query.get("distances")
        repeated_distances = repeated.get("distances")
        if not all(isinstance(value, list) for value in (text_distances, image_distances, repeated_distances)):
            return False
        try:
            all_distances = [float(value) for values in (text_distances, image_distances, repeated_distances) for value in values]
        except (TypeError, ValueError):
            return False
        return bool(
            record.get("stage") == "reader_completed"
            and record.get("counts") == expected_counts
            and collection_info.get("names") == [IMAGE_COLLECTION, TEXT_COLLECTION]
            and isinstance(text_query, dict)
            and text_query.get("ids") == expected_ids["text"]
            and image_query.get("ids") == expected_ids["image"]
            and repeated.get("ids") == expected_ids["text"]
            and len(text_distances) == 2
            and len(image_distances) == 1
            and len(repeated_distances) == 2
            and all(math.isfinite(value) for value in all_distances)
            and abs(all_distances[0]) <= 1e-5
            and abs(all_distances[1] - 1.0) <= 1e-5
            and abs(all_distances[2]) <= 1e-5
            and all(abs(a - b) <= 1e-6 for a, b in zip(text_distances, repeated_distances))
            and record.get("application_adapter_writer_reservation_present") is False
            and (
                record.get("application_adapter_fresh_marker_retained") is True
                or record.get("application_adapter_fresh_marker_retained") is False
            )
        )
    return False


def _run_fresh_trial(
    *,
    root: Path,
    interpreter: Path,
    version: str,
    ordinal: int,
) -> tuple[dict[str, Any], bool]:
    trial = root / f"fresh-{version}-{ordinal}"
    trial.mkdir(parents=True, exist_ok=False)
    writer = _run_child(interpreter=interpreter, mode="write", root=trial, expected_version=version)
    app_storage = trial / APPLICATION_STORAGE
    writer_snapshot = _storage_snapshot(trial)
    writer_record = writer.get("child_result")
    writer_ok = writer.get("exit_code") == 0 and not writer.get("timed_out") and _valid_worker_result(writer_record, role="writer", version=version)
    result: dict[str, Any] = {
        "trial": trial.name,
        "version": version,
        "writer": writer,
        "writer_snapshot_file_count": len(writer_snapshot),
        "writer_snapshot": writer_snapshot,
        "writer_reservation_present_after_exit": bool(list((app_storage / ".writer-leases").glob("*"))) if (app_storage / ".writer-leases").exists() else False,
        "completed": False,
    }
    if not writer_ok:
        result["failure_stage"] = (writer_record or {}).get("stage") if isinstance(writer_record, dict) else "writer_orchestration"
        result["hnsw_error"] = KNOWN_HNSW_ERROR in json.dumps(writer_record, ensure_ascii=False) if writer_record else False
        _write_json(trial / "trial-summary.json", result)
        return result, False

    fingerprint = writer_record["index_fingerprint"]
    reader = _run_child(interpreter=interpreter, mode="read", root=trial, expected_version=version, expected_fingerprint=fingerprint)
    reader_snapshot = _storage_snapshot(trial)
    reader_record = reader.get("child_result")
    reader_ok = reader.get("exit_code") == 0 and not reader.get("timed_out") and _valid_worker_result(reader_record, role="reader", version=version)
    if reader_ok:
        reader_ok = bool(
            reader_record.get("index_fingerprint") == fingerprint
            and reader_record.get("document_manifest_hash") == writer_record.get("document_manifest_hash")
            and reader_record.get("application_adapter_manifest_sha256") == writer_record.get("application_adapter_manifest_sha256")
            and reader_record.get("application_adapter_fresh_marker_retained") is True
        )
    result.update(
        {
            "reader": reader,
            "reader_snapshot_file_count": len(reader_snapshot),
            "reader_snapshot": reader_snapshot,
            "writer_to_reader_diff": _snapshot_diff(writer_snapshot, reader_snapshot),
            "reader_reservation_present_after_exit": bool(list((app_storage / ".writer-leases").glob("*"))) if (app_storage / ".writer-leases").exists() else False,
            "completed": reader_ok,
        }
    )
    if not reader_ok:
        result["failure_stage"] = (reader_record or {}).get("stage") if isinstance(reader_record, dict) else "reader_orchestration"
        result["hnsw_error"] = KNOWN_HNSW_ERROR in json.dumps(reader_record, ensure_ascii=False) if reader_record else False
    _write_json(trial / "trial-summary.json", result)
    return result, reader_ok


def _run_cross_version_trial(root: Path, baseline: Path, candidate: Path) -> dict[str, Any]:
    trial = root / "cross-version-copied-index"
    trial.mkdir(parents=True, exist_ok=False)
    source_root = trial / "baseline-source"
    source_root.mkdir()
    writer = _run_child(interpreter=baseline, mode="write", root=source_root, expected_version="1.5.0")
    source_standalone = source_root / STANDALONE_STORAGE
    source_snapshot = _file_snapshot(source_standalone)
    writer_record = writer.get("child_result")
    result: dict[str, Any] = {
        "trial": trial.name,
        "source_version": "1.5.0",
        "reader_version": "1.5.9",
        "writer": writer,
        "source_snapshot_file_count": len(source_snapshot),
        "source_snapshot": source_snapshot,
        "completed": False,
    }
    if writer.get("exit_code") != 0 or writer.get("timed_out") or not _valid_worker_result(writer_record, role="writer", version="1.5.0"):
        result["failure_stage"] = (writer_record or {}).get("stage") if isinstance(writer_record, dict) else "baseline_writer_orchestration"
        _write_json(trial / "trial-summary.json", result)
        return result

    fingerprint = writer_record["index_fingerprint"]
    copy_root = trial / "candidate-copy"
    copy_root.mkdir()
    copy_storage = copy_root / STANDALONE_STORAGE
    shutil.copytree(source_standalone, copy_storage)
    candidate_root = copy_root
    before_candidate = _file_snapshot(copy_storage)
    reader = _run_child(
        interpreter=candidate,
        mode="read",
        root=candidate_root,
        expected_version="1.5.9",
        expected_fingerprint=fingerprint,
    )
    after_candidate = _file_snapshot(copy_storage)
    reader_record = reader.get("child_result")
    passed = reader.get("exit_code") == 0 and not reader.get("timed_out") and _valid_worker_result(reader_record, role="reader", version="1.5.9")
    if passed:
        passed = bool(
            reader_record.get("index_fingerprint") == fingerprint
            and reader_record.get("application_adapter_manifest_sha256") is None
        )
    result.update(
        {
            "candidate_reader": reader,
            "copy_before_snapshot_file_count": len(before_candidate),
            "copy_before_snapshot": before_candidate,
            "copy_after_snapshot_file_count": len(after_candidate),
            "copy_after_snapshot": after_candidate,
            "source_to_copy_before_diff": _snapshot_diff(source_snapshot, before_candidate),
            "copy_open_to_after_diff": _snapshot_diff(before_candidate, after_candidate),
            "original_source_untouched_by_candidate": _file_snapshot(source_standalone) == source_snapshot,
            "completed": passed,
        }
    )
    if not passed:
        result["failure_stage"] = (reader_record or {}).get("stage") if isinstance(reader_record, dict) else "candidate_reader_orchestration"
        result["hnsw_error"] = KNOWN_HNSW_ERROR in json.dumps(reader_record, ensure_ascii=False) if reader_record else False
    _write_json(trial / "trial-summary.json", result)
    return result


def _validate_temp_path(path: Path, temp_root: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    try:
        resolved.relative_to(temp_root)
    except ValueError as exc:
        raise ValueError("probe environments and output must be beneath the system temporary directory") from exc
    return resolved


def _run_matrix(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise RuntimeError("this compatibility probe is specified for Windows")
    temp_root = Path(tempfile.gettempdir()).resolve()
    baseline = _validate_temp_path(Path(args.baseline_python), temp_root)
    candidate = _validate_temp_path(Path(args.candidate_python), temp_root)
    output_root = _validate_temp_path(Path(args.output_root), temp_root)
    if not baseline.is_file() or not candidate.is_file():
        raise FileNotFoundError("both isolated interpreter executables must exist")
    output_root.mkdir(parents=True, exist_ok=False)
    scenarios: list[dict[str, Any]] = []
    environment = {}
    for version, interpreter in (("1.5.0", baseline), ("1.5.9", candidate)):
        check = subprocess.run(
            [str(interpreter), "-c", "import chromadb,platform,sys; print(chromadb.__version__); print(sys.version.split()[0]); print(platform.machine()); print(sys.prefix)"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        lines = check.stdout.splitlines()
        environment[version] = {
            "exit_code": check.returncode,
            "chroma_version": lines[0] if lines else None,
            "python_version": lines[1] if len(lines) > 1 else None,
            "machine": lines[2] if len(lines) > 2 else None,
            "prefix_is_temp_environment": str(temp_root).lower() in (lines[3].lower() if len(lines) > 3 else ""),
            "stderr": check.stderr[-2000:],
        }
        expected = version
        if check.returncode != 0 or environment[version]["chroma_version"] != expected or environment[version]["python_version"] != "3.10.11" or environment[version]["machine"] != "AMD64":
            raise RuntimeError(f"experimental environment identity failed for Chroma {version}")

    for version, interpreter in (("1.5.0", baseline), ("1.5.9", candidate)):
        trials = []
        for ordinal in (1, 2):
            trial, success = _run_fresh_trial(root=output_root, interpreter=interpreter, version=version, ordinal=ordinal)
            trials.append(trial)
            if not success:
                break
        scenarios.append({"scenario": f"{version}-fresh-cold-reopen", "maximum": 2, "attempts": len(trials), "trials": trials})

    cross = _run_cross_version_trial(output_root, baseline, candidate)
    scenarios.append({"scenario": "1.5.9-open-copy-of-1.5.0-index", "maximum": 1, "attempts": 1, "trial": cross})

    summary = {
        "schema_version": SCHEMA_VERSION,
        "project_revision": "recorded by the caller",
        "fixture": {"text_collection": TEXT_COLLECTION, "image_collection": IMAGE_COLLECTION, "dimension": 4, "text_count": 2, "image_count": 1, "explicit_embeddings": True, "distance": "cosine", "ids": {"text": [row[0] for row in TEXT_ROWS], "image": [row[0] for row in IMAGE_ROWS]}},
        "environment_identity": environment,
        "trial_budget": {"baseline_fresh_max": 2, "candidate_fresh_max": 2, "cross_version_max": 1, "maximum_total": 5},
        "scenarios": scenarios,
        "source_index_mutated_by_candidate_copy_trial": cross.get("original_source_untouched_by_candidate") is False,
    }
    _write_json(output_root / "matrix-summary.json", summary)
    print(json.dumps({"output_root": output_root.name, "environment_identity": environment, "scenarios": [{"scenario": item["scenario"], "attempts": item["attempts"], "completed": all(t.get("completed") for t in item.get("trials", [item.get("trial", {})]))} for item in scenarios]}, indent=2))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        if len(sys.argv) not in {6, 7}:
            raise SystemExit("invalid worker arguments")
        _, mode, root_text, project_text, expected_version, *rest = sys.argv[1:]
        root = Path(root_text).resolve()
        project_root = Path(project_text).resolve()
        if not root.is_dir() or not project_root.is_dir():
            raise SystemExit("worker roots must exist")
        if mode == "write":
            _writer(root, project_root, expected_version)
        elif mode == "read" and rest:
            _reader(root, project_root, expected_version, rest[0])
        else:
            raise SystemExit("invalid worker mode")
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-python", required=True)
    parser.add_argument("--candidate-python", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    return _run_matrix(args)


if __name__ == "__main__":
    raise SystemExit(main())
