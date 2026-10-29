from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from threading import Event

import pytest

from backend.config.writer_lease import WriterLeaseError
from backend.engine.index_builder import IndexBuildError, build_index
from backend.engine.retrieval import _validate_build_report
from backend.experiments.run_plan import RunPlan
from backend.vectorstore import ChromaStoreStateError, ChromaVectorStore
from tests.integration.test_index_builder import _plan_with_text_and_optional_image, _valid_image_adapter, _valid_text_adapter


def test_fresh_build_persists_and_refuses_reuse(tmp_path):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    result = build_index(plan, source, storage, text_adapter=_valid_text_adapter(config), creation_policy="fresh_only")
    assert result.status == "completed" and result.text_records_persisted == 1
    report_bytes = result.report_path.read_bytes()
    report = _validate_build_report(storage, result.report_path, plan)
    assert report.text_records_persisted == 1
    store = ChromaVectorStore.open_existing(storage, plan)
    try:
        original = store.ids("text")
        with pytest.raises(ChromaStoreStateError, match="read-only"):
            store.upsert_text(["other"], [[1.0, 0.0]], ["other"])
    finally:
        store.close()
    with pytest.raises(IndexBuildError, match="index_already_exists"):
        build_index(plan, source, storage, text_adapter=_valid_text_adapter(config), creation_policy="fresh_only")
    assert result.report_path.read_bytes() == report_bytes
    store = ChromaVectorStore.open_existing(storage, plan)
    try:
        assert store.ids("text") == original
    finally:
        store.close()


def test_incomplete_existing_index_is_not_repaired(tmp_path):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    index = storage / "indexes" / plan.index_fingerprint
    index.mkdir(parents=True)
    sentinel = index / "sentinel"
    sentinel.write_bytes(b"untouched")
    with pytest.raises(IndexBuildError, match="index_already_exists"):
        build_index(plan, source, storage, text_adapter=_valid_text_adapter(config), creation_policy="fresh_only")
    assert sentinel.read_bytes() == b"untouched"
    assert not (storage / "build-artifacts").exists()


def test_fresh_build_persists_text_and_image_collections(tmp_path):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path, with_image=True)
    storage = tmp_path / "owned"
    result = build_index(plan, source, storage, text_adapter=_valid_text_adapter(config), image_adapter=_valid_image_adapter(config), creation_policy="fresh_only")
    assert result.status == "completed"
    assert result.text_records_persisted == 1 and result.image_records_persisted == 1
    report = _validate_build_report(storage, result.report_path, plan)
    assert report.text_records_persisted == 1 and report.image_records_persisted == 1
    store = ChromaVectorStore.open_existing(storage, plan)
    try:
        assert store.count("text") == 1 and store.count("image") == 1
    finally:
        store.close()


def test_fresh_partial_persistence_remains_unusable(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    original_create = ChromaVectorStore.create_new

    class FailAfterWrite:
        def __init__(self, store):
            self.store = store

        def upsert_text(self, *args, **kwargs):
            self.store.upsert_text(*args, **kwargs)
            raise RuntimeError("controlled failure")

        def close(self):
            self.store.close()

    monkeypatch.setattr(ChromaVectorStore, "create_new", lambda *args, **kwargs: FailAfterWrite(original_create(*args, **kwargs)))
    result = build_index(plan, source, storage, text_adapter=_valid_text_adapter(config), creation_policy="fresh_only")
    assert result.status == "failed" and result.partial_persistence is True
    assert result.failure_stage == "vector_persistence" and result.report_path.is_file()
    assert (storage / "indexes" / plan.index_fingerprint / ".fresh-build-incomplete").is_file()
    with pytest.raises(ChromaStoreStateError, match="incomplete"):
        ChromaVectorStore.open(storage, plan)
    with pytest.raises(IndexBuildError, match="index_already_exists"):
        build_index(plan, source, storage, text_adapter=_valid_text_adapter(config), creation_policy="fresh_only")


def test_source_change_during_build_does_not_publish_success(tmp_path, monkeypatch):
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    adapter = _valid_text_adapter(config)
    original = adapter.embed_documents

    def mutate_after_embedding(chunks):
        result = original(chunks)
        next(source.glob("*.pdf")).write_bytes(b"changed during build")
        return result

    monkeypatch.setattr(adapter, "embed_documents", mutate_after_embedding)
    result = build_index(plan, source, storage, text_adapter=adapter, creation_policy="fresh_only")
    assert result.status == "failed" and result.failure_stage == "source_verification"
    assert result.report_path.is_file()
    with pytest.raises(ChromaStoreStateError, match="incomplete"):
        ChromaVectorStore.open_existing(storage, plan)


def test_creation_collision_after_preflight_never_reuses_index(tmp_path, monkeypatch):
    import backend.engine.index_builder as module
    source, config, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    original = module.extract_pdfs
    index = storage / "indexes" / plan.index_fingerprint

    def create_competing_index(*args, **kwargs):
        result = original(*args, **kwargs)
        index.mkdir(parents=True)
        (index / "sentinel").write_bytes(b"preserve")
        return result

    monkeypatch.setattr(module, "extract_pdfs", create_competing_index)
    result = build_index(plan, source, storage, text_adapter=_valid_text_adapter(config), creation_policy="fresh_only")
    assert result.status == "failed" and result.failure_stage == "vector_store_open"
    assert (index / "sentinel").read_bytes() == b"preserve"
    assert not (index / "chroma").exists()


@pytest.mark.parametrize(("held_policy", "competing_policy"), [("legacy", "fresh_only"), ("fresh_only", "legacy"), ("fresh_only", "fresh_only")])
def test_builder_excludes_other_builders_and_direct_writers(tmp_path, monkeypatch, held_policy, competing_policy):
    source, _, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    entered, release = Event(), Event()

    def blocked(*_args, **_kwargs):
        entered.set()
        if not release.wait(20):
            raise RuntimeError("test timeout")
        raise RuntimeError("controlled extraction failure")

    monkeypatch.setattr("backend.engine.index_builder.extract_pdfs", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(build_index, plan, source, storage, creation_policy=held_policy)
        try:
            assert entered.wait(20)
            with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
                build_index(plan, source, storage, creation_policy=competing_policy)
            with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
                ChromaVectorStore.open(storage, plan)
        finally:
            release.set()
        result = pending.result(timeout=20)
    assert result.status == "failed" and result.failure_stage == "extraction"
    assert not (storage / "indexes" / plan.index_fingerprint).exists()


def test_existing_store_open_cannot_bypass_a_legacy_builder_lease(tmp_path, monkeypatch):
    source, _, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    store = ChromaVectorStore.open(storage, plan)
    store.close()
    entered, release = Event(), Event()

    def blocked(*_args, **_kwargs):
        entered.set()
        if not release.wait(20):
            raise RuntimeError("test timeout")
        raise RuntimeError("controlled extraction failure")

    monkeypatch.setattr("backend.engine.index_builder.extract_pdfs", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(build_index, plan, source, storage)
        try:
            assert entered.wait(20)
            with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
                ChromaVectorStore.open_existing(storage, plan)
        finally:
            release.set()
        assert pending.result(timeout=20).status == "failed"


def _hold_builder(plan_json, source, storage, policy, entered, release):
    import backend.engine.index_builder as module

    def blocked(*_args, **_kwargs):
        entered.set()
        if not release.wait(20):
            raise RuntimeError("test timeout")
        raise RuntimeError("controlled failure")

    module.extract_pdfs = blocked
    result = module.build_index(RunPlan.from_serializable_json(plan_json), source, storage, creation_policy=policy)
    if result.status != "failed":
        raise AssertionError("expected controlled failure")


def _hold_fresh_store(plan_json, storage, ready, release):
    from backend.config.writer_lease import WriterLease
    plan = RunPlan.from_serializable_json(plan_json)
    lease = WriterLease.acquire(storage, plan.index_fingerprint)
    store = ChromaVectorStore.create_new(storage, plan, _lease=lease)
    ready.set()
    release.wait(30)
    store.close()
    lease.release()


def test_process_termination_during_fresh_store_keeps_index_unavailable(tmp_path):
    source, _, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_fresh_store, args=(plan.to_serializable_json(), storage, ready, release))
    process.start()
    try:
        assert ready.wait(20), "child did not create the fresh store"
        process.terminate()
        process.join(10)
        assert not process.is_alive()
        index = storage / "indexes" / plan.index_fingerprint
        assert (index / ".fresh-build-incomplete").is_file()
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            ChromaVectorStore.open(storage, plan)
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            ChromaVectorStore.open_existing(storage, plan)
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            build_index(plan, source, storage, creation_policy="fresh_only")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)


@pytest.mark.parametrize(("held_policy", "competing_policy"), [("legacy", "fresh_only"), ("fresh_only", "legacy"), ("fresh_only", "fresh_only")])
def test_builder_ownership_excludes_other_processes(tmp_path, held_policy, competing_policy):
    source, _, plan = _plan_with_text_and_optional_image(tmp_path)
    storage = tmp_path / "owned"
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    process = context.Process(target=_hold_builder, args=(plan.to_serializable_json(), source, storage, held_policy, entered, release))
    process.start()
    try:
        assert entered.wait(20), "child did not enter the build"
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            build_index(plan, source, storage, creation_policy=competing_policy)
        with pytest.raises(WriterLeaseError, match="writer_lease_busy"):
            ChromaVectorStore.open(storage, plan)
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(10)
    assert process.exitcode == 0
