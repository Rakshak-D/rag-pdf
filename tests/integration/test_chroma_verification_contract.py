from __future__ import annotations

import hashlib
import importlib.metadata
import json
import multiprocessing
from pathlib import Path
import platform
import queue
import shutil
import stat
import sys
import threading
import traceback

import chromadb
import pytest
from chromadb.api.shared_system_client import SharedSystemClient

from backend.config.writer_lease import WriterLease, WriterLeaseError
from backend.engine.retrieval import RetrievalStoreError, retrieve_semantic
from backend.experiments.run_plan import RunPlan, build_run_plan
from backend.vectorstore import ChromaStoreStateError, ChromaVectorStore
from backend.vectorstore.chroma_store import _IndexStoreManifest
from tests.integration.test_retrieval import QueryTextAdapter, make_plan, write_report


BUILD_ID = "c" * 32
TEXT_COLLECTION = "text_vectors"
IMAGE_COLLECTION = "image_vectors"


def _make_plan(tmp_path: Path):
    return make_plan(tmp_path)


def _create_index(storage: Path, plan, *, text_count: int = 2, image_count: int = 1) -> None:
    store = ChromaVectorStore.open(storage, plan)
    chroma_client = store._client
    try:
        if text_count:
            ids = [f"text-{number}" for number in range(text_count)]
            vectors = [[1.0, 0.0] if number == 0 else [0.0, 1.0] for number in range(text_count)]
            store.upsert_text(
                ids,
                vectors,
                [f"synthetic text {number}" for number in range(text_count)],
                [{"fixture": "phase-6.12c", "ordinal": number} for number in range(text_count)],
            )
        if image_count:
            ids = [f"image-{number}" for number in range(image_count)]
            vectors = [[0.5, 0.5] for _ in range(image_count)]
            store.upsert_image(
                ids,
                vectors,
                [{"image_id": identifier, "image_path": f"synthetic/{identifier}.png"} for identifier in ids],
            )
    finally:
        try:
            store.close()
        finally:
            # Chroma 1.5.0 has no public client.close(). The fixture runs in a
            # disposable child and stops its internal System so the baseline is
            # quiescent before another process measures open_existing().
            chroma_client._system.stop()


def _child_create_index(storage_text, plan_json, text_count, image_count, result_queue):
    """Create fixtures in a disposable process so Windows releases Chroma handles."""

    try:
        plan = RunPlan.from_serializable_json(plan_json)
        _create_index(Path(storage_text), plan, text_count=text_count, image_count=image_count)
        result_queue.put({"stage": "created"})
    except BaseException as exc:
        result_queue.put({"stage": "create_failed", "error_type": type(exc).__name__,
                          "traceback": traceback.format_exc()})


def _create_index_isolated(storage: Path, plan, *, text_count: int = 2, image_count: int = 1) -> None:
    process, result_queue = _start_child(
        _child_create_index,
        (str(storage), plan.to_serializable_json(), text_count, image_count),
    )
    try:
        outcome = _receive(result_queue, "synthetic index creation", timeout=60, process=process)
        process.join(30)
        assert not process.is_alive(), "synthetic index creator did not exit"
        assert process.exitcode == 0, outcome
        assert outcome.get("stage") == "created", outcome
    finally:
        _finish_process(process, result_queue)


def _prepared(tmp_path: Path, *, text_count: int = 2, image_count: int = 1):
    config, plan = _make_plan(tmp_path)
    storage = tmp_path / "owned-storage"
    storage.mkdir()
    _create_index_isolated(storage, plan, text_count=text_count, image_count=image_count)
    report = write_report(
        storage,
        plan,
        build_id=BUILD_ID,
        text_count=text_count,
        image_count=image_count,
    )
    return config, plan, storage, report


def _snapshot(root: Path) -> dict[str, dict[str, object]]:
    """Capture names, types, sizes, mtimes, and digests without following links."""

    result: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*")):
        try:
            info = path.lstat()
        except OSError as exc:
            result[path.relative_to(root).as_posix()] = {"type": "unavailable", "error": type(exc).__name__}
            continue
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(info.st_mode):
            result[relative] = {"type": "symlink", "size": info.st_size, "mtime_ns": info.st_mtime_ns}
        elif stat.S_ISDIR(info.st_mode):
            result[relative] = {"type": "directory", "mtime_ns": info.st_mtime_ns}
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            readable = True
            try:
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
            except OSError:
                readable = False
            result[relative] = {
                "type": "file",
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "sha256": digest.hexdigest() if readable else None,
                "readable": readable,
            }
        else:
            result[relative] = {"type": "other", "size": info.st_size, "mtime_ns": info.st_mtime_ns}
    return result


def _delta(before: dict[str, dict[str, object]], after: dict[str, dict[str, object]]) -> dict[str, list[str]]:
    old, new = set(before), set(after)
    added, removed = sorted(new - old), sorted(old - new)
    content_changed: list[str] = []
    size_changed: list[str] = []
    digest_changed: list[str] = []
    metadata_only_changed: list[str] = []
    for name in sorted(old & new):
        left, right = before[name], after[name]
        if left.get("type") != right.get("type"):
            content_changed.append(name)
        elif left.get("type") == "file":
            file_content_changed = False
            if left.get("size") != right.get("size"):
                size_changed.append(name)
                file_content_changed = True
            if left.get("sha256") != right.get("sha256"):
                digest_changed.append(name)
                file_content_changed = True
            if file_content_changed:
                content_changed.append(name)
            elif left.get("mtime_ns") != right.get("mtime_ns"):
                metadata_only_changed.append(name)
        elif left.get("mtime_ns") != right.get("mtime_ns"):
            metadata_only_changed.append(name)
    return {
        "added": added,
        "removed": removed,
        "content_or_type_changed": content_changed,
        "size_changed": size_changed,
        "digest_changed": digest_changed,
        "metadata_only_changed": metadata_only_changed,
    }


def _classify_files(snapshot: dict[str, dict[str, object]], fingerprint: str) -> dict[str, list[str]]:
    prefix = f"indexes/{fingerprint}/chroma/"
    chroma_files = sorted(name for name, item in snapshot.items() if name.startswith(prefix) and item.get("type") == "file")
    sqlite = [name for name in chroma_files if name.lower().endswith((".sqlite", ".sqlite3", ".db"))]
    sidecars = [name for name in chroma_files if any(suffix in name.lower() for suffix in ("-wal", "-shm", "-journal"))]
    hnsw = [name for name in chroma_files if "hnsw" in name.lower() or "index_metadata.pickle" in name.lower() or name.lower().endswith(("header.bin", "data_level0.bin", "length.bin", "link_lists.bin"))]
    temporary = [name for name in chroma_files if name.lower().endswith((".tmp", ".temp", ".wal", ".shm", ".journal"))]
    lease = sorted(name for name in snapshot if name.startswith(f".writer-leases/{fingerprint}"))
    return {"chroma_files": chroma_files, "sqlite_files": sqlite, "sqlite_sidecars": sidecars,
            "hnsw_and_segment_files": hnsw, "temporary_or_journal_files": temporary,
            "fingerprint_lease_paths": lease}


def _system_is_cached(system) -> bool:
    return any(system is item for item in SharedSystemClient._identifier_to_system.values())


def _system_cache_entries_under(root: Path):
    resolved_root = root.resolve(strict=False)
    entries = []
    for identifier, system in list(SharedSystemClient._identifier_to_system.items()):
        try:
            Path(identifier).resolve(strict=False).relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        entries.append((identifier, system))
    return entries


@pytest.fixture(autouse=True)
def stop_temporary_chroma_systems(tmp_path):
    """Stop only test-owned Chroma systems so the suite retains no open handles."""

    yield
    failures = []
    for identifier, system in _system_cache_entries_under(tmp_path):
        try:
            system.stop()
        except Exception as exc:  # pragma: no cover - diagnostic cleanup failure
            failures.append(type(exc).__name__)
        else:
            if SharedSystemClient._identifier_to_system.get(identifier) is system:
                SharedSystemClient._identifier_to_system.pop(identifier, None)
    assert not failures, f"test-owned Chroma systems failed to stop: {failures}"
    assert not _system_cache_entries_under(tmp_path), "test-owned Chroma systems remained cached after cleanup"


def _emit_snapshot_trial(trial: int, before, during, after_close, after_process, fingerprint: str, stages) -> None:
    evidence = {
        "trial": trial,
        "chroma_file_count": len(_classify_files(before, fingerprint)["chroma_files"]),
        "sqlite_and_sidecars": {
            "before": _classify_files(before, fingerprint)["sqlite_files"] + _classify_files(before, fingerprint)["sqlite_sidecars"],
            "during": _classify_files(during, fingerprint)["sqlite_files"] + _classify_files(during, fingerprint)["sqlite_sidecars"],
        },
        "lease_present_during": bool(_classify_files(during, fingerprint)["fingerprint_lease_paths"]),
        "lease_present_after_close": bool(_classify_files(after_close, fingerprint)["fingerprint_lease_paths"]),
        "open_transition": _delta(before, during),
        "open_call_only": stages["open"],
        "count_call_only": stages["count"],
        "query_call_only": stages["query"],
        "close_transition": _delta(during, after_close),
        "process_exit_transition": _delta(after_close, after_process),
    }
    print("CHROMA_OPEN_EXISTING_TRIAL=" + json.dumps(evidence, sort_keys=True))


def _child_open_hold(storage_text, plan_json, ready, release, result_queue):
    store = None
    stage = "child_entry"
    outcome: dict[str, object] = {"stage": "child_entry"}
    try:
        plan = RunPlan.from_serializable_json(plan_json)
        stage = outcome["stage"] = "run_plan_reconstructed"
        stage = outcome["stage"] = "snapshot_before_open"
        before_open = _snapshot(Path(storage_text))
        store = ChromaVectorStore.open_existing(storage_text, plan)
        stage = outcome["stage"] = "open_existing_returned"
        chroma_client = store._client
        system = chroma_client._system
        after_open = _snapshot(Path(storage_text))
        stage = outcome["stage"] = "reading_counts"
        outcome["counts"] = {"text": store.count("text"), "image": store.count("image")}
        stage = outcome["stage"] = "counts_read"
        after_count = _snapshot(Path(storage_text))
        stage = outcome["stage"] = "querying_text_collection"
        query = store.query_text([1.0, 0.0], n_results=2)
        stage = outcome["stage"] = "query_completed"
        after_query = _snapshot(Path(storage_text))
        outcome["query_ids"] = query.get("ids", [[]])[0]
        outcome["open_delta"] = _delta(before_open, after_open)
        outcome["count_delta"] = _delta(after_open, after_count)
        outcome["query_delta"] = _delta(after_count, after_query)
        outcome["client_has_close_method"] = callable(getattr(store._client, "close", None))
        outcome["system_cached"] = _system_is_cached(system)
    except BaseException as exc:
        outcome = {
            "stage": stage,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "error_code": getattr(exc, "code", str(exc) if isinstance(exc, WriterLeaseError) else "unclassified"),
            "traceback": traceback.format_exc(),
            "failure_snapshot": _snapshot(Path(storage_text)),
        }
        result_queue.put(outcome)
        ready.set()
        if store is not None:
            try:
                store.close()
            except BaseException:
                pass
        return
    result_queue.put(outcome)
    ready.set()
    release.wait(60)
    try:
        store.close()
        result_queue.put({"stage": "closed", "snapshot": _snapshot(Path(storage_text)),
                          "system_cached_after_wrapper_close": _system_is_cached(system),
                          "client_object_close_method": callable(getattr(chroma_client, "close", None))})
    except BaseException as exc:
        result_queue.put({"stage": "close_failed", "error_type": type(exc).__name__,
                          "error_code": getattr(exc, "code", "unclassified"), "traceback": traceback.format_exc()})


def _child_try_open(storage_text, plan_json, result_queue):
    store = None
    try:
        plan = RunPlan.from_serializable_json(plan_json)
        store = ChromaVectorStore.open_existing(storage_text, plan)
        result_queue.put({"stage": "opened", "counts": [store.count("text"), store.count("image")]})
    except BaseException as exc:
        result_queue.put({"stage": "rejected", "error_type": type(exc).__name__,
                          "error_code": getattr(exc, "code", str(exc) if isinstance(exc, WriterLeaseError) else "unclassified")})
    finally:
        if store is not None:
            store.close()


def _start_child(target, args):
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(target=target, args=(*args, result_queue))
    process.start()
    return process, result_queue


def _receive(queue_obj, label: str, timeout: float = 20, process=None):
    try:
        return queue_obj.get(timeout=timeout)
    except queue.Empty:
        pytest.fail(
            f"child did not report checkpoint {label!r}; "
            f"alive={process.is_alive() if process is not None else 'unknown'}; "
            f"exitcode={process.exitcode if process is not None else 'unknown'}"
        )


def _finish_process(process, queue_obj):
    if process.is_alive():
        process.terminate()
        process.join(15)
    if process.is_alive():
        pytest.fail("spawned Chroma child could not be terminated within the bounded cleanup window")
    process.close()
    queue_obj.close()
    queue_obj.join_thread()


def test_open_existing_two_cold_process_trials_capture_filesystem_effects(tmp_path):
    _, plan, storage, report = _prepared(tmp_path)
    fingerprint = plan.index_fingerprint
    print("CHROMA_CONTRACT_ENV=" + json.dumps({
        "python": sys.version,
        "os": platform.platform(),
        "chromadb": importlib.metadata.version("chromadb"),
        "chroma_client_has_close": callable(getattr(chromadb.Client, "close", None)),
        "pytest_config": "repository rag-env; real PersistentClient; temporary fixture",
    }, sort_keys=True))
    report_before = report.read_bytes()
    index_manifest_path = storage / "indexes" / fingerprint / "index-manifest.json"
    index_manifest_before = index_manifest_path.read_bytes()

    # Two planned cold-process trials avoid mistaking the parent's shared-system
    # cache for a fresh PersistentClient initialization.
    for trial in (1, 2):
        before = _snapshot(storage)
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        process, result_queue = _start_child(
            _child_open_hold,
            (str(storage), plan.to_serializable_json(), ready, release),
        )
        opened = None
        during = None
        try:
            if not ready.wait(60):
                diagnostic = None
                try:
                    diagnostic = result_queue.get_nowait()
                except queue.Empty:
                    pass
                pytest.fail(f"child did not reach open_existing_returned; exitcode={process.exitcode}; diagnostic={diagnostic}")
            opened = _receive(result_queue, "open/count/query complete", process=process)
            assert opened.get("stage") == "query_completed", opened
            assert opened["counts"] == {"text": 2, "image": 1}
            assert len(opened["query_ids"]) == 2
            assert any(
                path.endswith("/chroma/chroma.sqlite3")
                for path in opened["open_delta"]["digest_changed"]
            ), "PersistentClient initialization did not reproduce the observed SQLite byte change"
            assert any(
                path.lower().endswith(("data_level0.bin", "length.bin"))
                for path in opened["count_delta"]["digest_changed"]
            ), "post-open/count checkpoint did not reproduce the observed segment-file byte change"
            during = _snapshot(storage)
            opening_delta = _delta(before, during)
            lease_prefix = f".writer-leases/{fingerprint}"
            assert opening_delta["added"] == [lease_prefix, f"{lease_prefix}/owner"]
            assert not opening_delta["removed"]
            assert not opened["count_delta"]["added"] and not opened["count_delta"]["removed"]
            assert not opened["query_delta"]["added"] and not opened["query_delta"]["removed"]
            assert not opened["query_delta"]["content_or_type_changed"]
            assert not opened["query_delta"]["metadata_only_changed"]
            before_classified = _classify_files(before, fingerprint)
            during_classified = _classify_files(during, fingerprint)
            assert len(before_classified["chroma_files"]) == len(during_classified["chroma_files"]) == 9
            assert not before_classified["sqlite_sidecars"] and not during_classified["sqlite_sidecars"]
            assert not before_classified["temporary_or_journal_files"]
            assert not during_classified["temporary_or_journal_files"]
            lease = storage / ".writer-leases" / fingerprint
            assert lease.is_dir() and (lease / "owner").is_file()
            assert opened["client_has_close_method"] is False
            assert opened["system_cached"] is True

            release.set()
            closed = _receive(result_queue, "store close", timeout=30, process=process)
            assert closed.get("stage") == "closed", closed
            assert closed["system_cached_after_wrapper_close"] is True
            assert closed["client_object_close_method"] is False
            after_close = closed["snapshot"]
            close_delta = _delta(during, after_close)
            lease_prefix = f".writer-leases/{fingerprint}"
            assert close_delta["removed"] == [lease_prefix, f"{lease_prefix}/owner"]
            assert not close_delta["added"]
            assert not any(
                path.startswith(f"indexes/{fingerprint}/chroma/")
                for key in ("content_or_type_changed", "metadata_only_changed")
                for path in close_delta[key]
            )
            process.join(30)
            assert not process.is_alive(), "child remained alive after normal store close"
            assert process.exitcode == 0
            after_process = _snapshot(storage)
            exit_delta = _delta(after_close, after_process)
            assert not exit_delta["added"]
            assert not exit_delta["removed"]
            assert not exit_delta["content_or_type_changed"]
            assert not lease.exists(), "clean close must remove only the child-owned fingerprint reservation"
            _emit_snapshot_trial(
                trial, before, during, after_close, after_process, fingerprint,
                {"open": opened["open_delta"], "count": opened["count_delta"], "query": opened["query_delta"]},
            )
        finally:
            release.set()
            _finish_process(process, result_queue)
        assert report.read_bytes() == report_before
        assert index_manifest_path.read_bytes() == index_manifest_before


@pytest.mark.parametrize(
    ("case", "expected_message"),
    [
        ("missing_chroma", "incomplete index store"),
        ("missing_manifest", "incomplete index store"),
        ("malformed_manifest", "invalid index manifest"),
        ("wrong_fingerprint", "identity mismatch"),
        ("wrong_manifest_hash", "identity mismatch"),
        ("incomplete_marker", "fresh index build is incomplete"),
    ],
)
def test_open_existing_fails_closed_on_index_hierarchy_and_identity(tmp_path, case, expected_message):
    _, plan, storage, report = _prepared(tmp_path)
    index_root = storage / "indexes" / plan.index_fingerprint
    manifest_path = index_root / "index-manifest.json"
    chroma_path = index_root / "chroma"
    marker = index_root / ".fresh-build-incomplete"
    original_manifest = manifest_path.read_bytes()
    original_chroma_names = sorted(path.relative_to(chroma_path).as_posix() for path in chroma_path.rglob("*"))
    if case == "missing_chroma":
        shutil.rmtree(chroma_path)
    elif case == "missing_manifest":
        manifest_path.unlink()
    elif case == "malformed_manifest":
        manifest_path.write_bytes(b"{malformed")
    elif case == "wrong_fingerprint":
        manifest_path.write_text(
            _IndexStoreManifest(index_fingerprint="0" * 64,
                                 document_manifest_hash=plan.document_manifest_hash).canonical_json(),
            encoding="utf-8",
        )
    elif case == "wrong_manifest_hash":
        manifest_path.write_text(
            _IndexStoreManifest(index_fingerprint=plan.index_fingerprint,
                                 document_manifest_hash="0" * 64).canonical_json(),
            encoding="utf-8",
        )
    elif case == "incomplete_marker":
        marker.write_text(plan.index_fingerprint, encoding="ascii")

    before_index = _snapshot(index_root)
    with pytest.raises(ChromaStoreStateError, match=expected_message):
        ChromaVectorStore.open_existing(storage, plan)
    after_index = _snapshot(index_root)
    assert after_index == before_index, f"open_existing changed evidence for {case}"
    assert not (storage / ".writer-leases" / plan.index_fingerprint).exists()
    assert report.is_file()
    if case == "malformed_manifest":
        assert manifest_path.read_bytes() == b"{malformed"
    elif case in {"wrong_fingerprint", "wrong_manifest_hash"}:
        assert manifest_path.read_bytes() != original_manifest
    elif case == "incomplete_marker":
        assert marker.read_text(encoding="ascii") == plan.index_fingerprint
    elif case == "missing_chroma":
        assert not chroma_path.exists()
    elif case == "missing_manifest":
        assert not manifest_path.exists()
    assert original_chroma_names or case == "missing_chroma"


def test_missing_index_directory_is_rejected_before_reservation(tmp_path):
    _, plan = _make_plan(tmp_path)
    storage = tmp_path / "empty-storage"
    storage.mkdir()
    with pytest.raises(ChromaStoreStateError, match="does not exist"):
        ChromaVectorStore.open_existing(storage, plan)
    assert not (storage / ".writer-leases").exists()
    assert not (storage / "indexes").exists()


@pytest.mark.parametrize("case", ["wrong_collection_metadata", "missing_modality", "extra_modality"])
def test_open_existing_checks_exact_collection_set_and_metadata_without_repair(tmp_path, case):
    _, plan, storage, report = _prepared(tmp_path)
    chroma_path = storage / "indexes" / plan.index_fingerprint / "chroma"
    client = chromadb.PersistentClient(path=str(chroma_path))
    if case == "wrong_collection_metadata":
        collection = client.get_collection(TEXT_COLLECTION, embedding_function=None)
        changed = dict(collection.metadata)
        changed["index_fingerprint"] = "0" * 64
        changed.pop("hnsw:space", None)
        collection.modify(metadata=changed)
        expected_message = "metadata mismatch"
    elif case == "missing_modality":
        client.delete_collection(IMAGE_COLLECTION)
        expected_message = "collections mismatch"
    else:
        client.create_collection(name="unexpected_modality", embedding_function=None)
        expected_message = "collections mismatch"

    with pytest.raises(ChromaStoreStateError, match=expected_message):
        ChromaVectorStore.open_existing(storage, plan)
    names = {collection.name for collection in client.list_collections()}
    if case == "wrong_collection_metadata":
        assert client.get_collection(TEXT_COLLECTION, embedding_function=None).metadata["index_fingerprint"] == "0" * 64
        assert names == {TEXT_COLLECTION, IMAGE_COLLECTION}
    elif case == "missing_modality":
        assert names == {TEXT_COLLECTION}
    else:
        assert names == {TEXT_COLLECTION, IMAGE_COLLECTION, "unexpected_modality"}
    assert report.is_file()
    assert not (storage / ".writer-leases" / plan.index_fingerprint).exists()


def test_report_count_mismatch_belongs_to_retrieval_not_open_existing(tmp_path):
    config, plan, storage, report = _prepared(tmp_path)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["text_records_persisted"] = 99
    report.write_text(json.dumps(payload), encoding="utf-8")

    store = ChromaVectorStore.open_existing(storage, plan)
    try:
        assert store.count("text") == 2
        assert store.count("image") == 1
    finally:
        store.close()
    with pytest.raises(RetrievalStoreError, match="text collection count"):
        retrieve_semantic(
            "synthetic query",
            plan,
            storage,
            build_report_path=report,
            modalities=("text",),
            text_adapter=QueryTextAdapter(config, []),
        )
    assert not (storage / ".writer-leases" / plan.index_fingerprint).exists()
    assert json.loads(report.read_text(encoding="utf-8"))["text_records_persisted"] == 99


def test_incomplete_marker_and_symlinked_index_hierarchy_are_not_repaired(tmp_path):
    _, plan, storage, _ = _prepared(tmp_path)
    index_root = storage / "indexes" / plan.index_fingerprint
    marker = index_root / ".fresh-build-incomplete"
    marker.write_text(plan.index_fingerprint, encoding="ascii")
    with pytest.raises(ChromaStoreStateError, match="incomplete"):
        ChromaVectorStore.open_existing(storage, plan)
    assert marker.read_text(encoding="ascii") == plan.index_fingerprint
    assert not (storage / ".writer-leases" / plan.index_fingerprint).exists()

    other = tmp_path / "outside-index"
    other.mkdir()
    link_root = storage / "symlink-storage"
    link_root.mkdir()
    (link_root / "indexes").mkdir()
    link = link_root / "indexes" / plan.index_fingerprint
    try:
        link.symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Windows symlink creation unavailable for this case: {type(exc).__name__}")
    with pytest.raises(ChromaStoreStateError, match="symlinked index directory"):
        ChromaVectorStore.open_existing(link_root, plan)
    assert not list(other.iterdir())
    assert not (link_root / ".writer-leases").exists()


def test_reader_reservation_blocks_same_fingerprint_and_writer_but_not_another_index(tmp_path):
    config, plan, storage, _ = _prepared(tmp_path)
    second_config = config.model_copy(update={"chunk_overlap_tokens": config.chunk_overlap_tokens + 1})
    second_plan = build_run_plan(second_config, plan.document_manifest_hash)
    _create_index_isolated(storage, second_plan, text_count=1, image_count=0)

    # The same exclusive reservation serializes a writer against a read-only
    # wrapper open as well as serializing two read-only opens.
    writer = WriterLease.acquire(storage, plan.index_fingerprint)
    writer_owner = (storage / ".writer-leases" / plan.index_fingerprint / "owner").read_bytes()
    with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
        ChromaVectorStore.open_existing(storage, plan)
    assert (storage / ".writer-leases" / plan.index_fingerprint / "owner").read_bytes() == writer_owner
    writer.release()

    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process, result_queue = _start_child(
        _child_open_hold,
        (str(storage), plan.to_serializable_json(), ready, release),
    )
    try:
        if not ready.wait(60):
            diagnostic = None
            try:
                diagnostic = result_queue.get_nowait()
            except queue.Empty:
                pass
            pytest.fail(f"reader child did not acquire lease; exitcode={process.exitcode}; diagnostic={diagnostic}")
        opened = _receive(result_queue, "reader lease held", process=process)
        assert opened["stage"] == "query_completed", opened
        assert opened["counts"] == {"text": 2, "image": 1}
        lease = storage / ".writer-leases" / plan.index_fingerprint
        owner_before = (lease / "owner").read_bytes()
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            WriterLease.acquire(storage, plan.index_fingerprint)
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            ChromaVectorStore.open_existing(storage, plan)
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            ChromaVectorStore.open(storage, plan)
        assert (lease / "owner").read_bytes() == owner_before

        # A distinct fingerprint has its own lease and can be inspected while
        # this fingerprint's exclusive reservation is held.
        other = ChromaVectorStore.open_existing(storage, second_plan)
        try:
            assert (other.count("text"), other.count("image")) == (1, 0)
        finally:
            other.close()

        contender_queue = context.Queue()
        contender = context.Process(
            target=_child_try_open,
            args=(str(storage), plan.to_serializable_json(), contender_queue),
        )
        try:
            contender.start()
            contender.join(60)
            assert not contender.is_alive(), "competing runtime process did not finish"
            assert contender.exitcode == 0
            attempted = _receive(contender_queue, "second-process conflict", process=contender)
            assert attempted == {"stage": "rejected", "error_type": "WriterLeaseError", "error_code": "writer_lease_busy"}
        finally:
            _finish_process(contender, contender_queue)

        release.set()
        closed = _receive(result_queue, "reader close", timeout=30, process=process)
        assert closed["stage"] == "closed", closed
        process.join(30)
        assert not process.is_alive() and process.exitcode == 0
        assert not lease.exists()
        with WriterLease.acquire(storage, plan.index_fingerprint):
            pass
        reopened = ChromaVectorStore.open_existing(storage, plan)
        try:
            assert (reopened.count("text"), reopened.count("image")) == (2, 1)
        finally:
            reopened.close()
    finally:
        release.set()
        _finish_process(process, result_queue)


def test_store_close_is_thread_bound_and_failed_cross_thread_close_keeps_lease(tmp_path):
    _, plan, storage, _ = _prepared(tmp_path)
    store = ChromaVectorStore.open_existing(storage, plan)
    lease_path = storage / ".writer-leases" / plan.index_fingerprint
    owner_before = (lease_path / "owner").read_bytes()

    def close_elsewhere():
        try:
            store.close()
        except WriterLeaseError as exc:
            return str(exc)
        return "closed"

    thread = threading.Thread(target=close_elsewhere, name="wrong-thread-close")
    thread.start()
    thread.join(15)
    assert not thread.is_alive()
    assert store._writer_lease is not None
    assert (lease_path / "owner").read_bytes() == owner_before
    # A close on the acquisition thread can still release the retained token.
    store.close()
    assert not lease_path.exists()


def test_forced_child_termination_retains_reservation_and_index_evidence(tmp_path):
    _, plan, storage, report = _prepared(tmp_path)
    fingerprint = plan.index_fingerprint
    index_root = storage / "indexes" / fingerprint
    report_bytes = report.read_bytes()
    manifest_path = index_root / "index-manifest.json"
    manifest_bytes = manifest_path.read_bytes()

    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process, result_queue = _start_child(
        _child_open_hold,
        (str(storage), plan.to_serializable_json(), ready, release),
    )
    try:
        if not ready.wait(60):
            diagnostic = None
            try:
                diagnostic = result_queue.get_nowait()
            except queue.Empty:
                pass
            pytest.fail(f"crash-test child did not reach opened state; exitcode={process.exitcode}; diagnostic={diagnostic}")
        opened = _receive(result_queue, "child store held before termination", process=process)
        assert opened["stage"] == "query_completed", opened
        before_termination = _snapshot(storage)
        chroma_before = _classify_files(before_termination, fingerprint)
        process.terminate()
        process.join(20)
        assert not process.is_alive(), "forced termination did not stop the owned child"
        assert process.exitcode != 0

        after_termination = _snapshot(storage)
        chroma_after = _classify_files(after_termination, fingerprint)
        print("CHROMA_FORCED_TERMINATION_SNAPSHOT=" + json.dumps({
            "chroma_file_count_before": len(chroma_before["chroma_files"]),
            "chroma_file_count_after": len(chroma_after["chroma_files"]),
            "sqlite_sidecars_before": chroma_before["sqlite_sidecars"],
            "sqlite_sidecars_after": chroma_after["sqlite_sidecars"],
            "transition": _delta(before_termination, after_termination),
        }, sort_keys=True))
        termination_delta = _delta(before_termination, after_termination)
        assert not termination_delta["added"]
        assert not termination_delta["removed"]
        assert not termination_delta["content_or_type_changed"]

        lease = storage / ".writer-leases" / fingerprint
        assert lease.is_dir() and (lease / "owner").is_file()
        assert report.read_bytes() == report_bytes
        assert manifest_path.read_bytes() == manifest_bytes
        assert set(chroma_before["chroma_files"]).issubset(set(chroma_after["chroma_files"])), (
            "forced termination removed existing Chroma files; see printed snapshot"
        )
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            WriterLease.acquire(storage, fingerprint)
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            ChromaVectorStore.open_existing(storage, plan)
        assert lease.is_dir() and (lease / "owner").is_file(), "failed takeover must preserve the crash reservation"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(20)
        process.close()
        result_queue.close()
        result_queue.join_thread()
