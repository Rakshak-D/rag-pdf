from __future__ import annotations

import hashlib
import threading
import traceback
from time import monotonic
from pathlib import Path

import pytest

from backend.config import ResolvedRunConfig
from backend.experiments import (
    AblationRunResult,
    AblationVariantResult,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStoreError,
    ExperimentArtifactStore,
    build_ablation_plan,
    deserialize_evaluation_result,
)
from backend.experiments.ablation_runner import _comparison
import backend.experiments.artifact_store as artifact_store_module
from backend.experiments.serialization import serialize_ablation_result, serialize_evaluation_result
from notebooks.config import Config
from tests.unit.test_experiment_serialization import _evaluation


def _failed_ablation(plan) -> AblationRunResult:
    variants = tuple(AblationVariantResult(item.variant_id, item.run_fingerprint, "failed", None, "ablation_variant_failed") for item in plan.variants)
    return AblationRunResult("1", "failed", plan.document_manifest_hash, plan.shared_index_fingerprint, "synthetic", "v1", variants, tuple(_comparison(f"M{number}", variants) for number in range(1, 20)))


def test_evaluation_save_load_is_content_addressed_and_listed(tmp_path):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_evaluation_result(result)
    serialized = serialize_evaluation_result(result).encode("utf-8")
    assert reference.artifact_id == hashlib.sha256(serialized).hexdigest()
    assert store.save_evaluation_result(result) == reference
    assert store.load_evaluation_result(reference.artifact_id).report.evaluation_fingerprint == result.report.evaluation_fingerprint
    assert store.list_artifacts() == (reference,)
    assert store.list_artifacts("evaluation_result") == (reference,)
    with pytest.raises(ArtifactNotFoundError):
        store.load_evaluation_result("0" * 64)


def test_ablation_save_load_is_bound_to_the_supplied_plan(tmp_path):
    base = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False))
    plan = build_ablation_plan(base, "c" * 64)
    result = _failed_ablation(plan)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_ablation_result(result, ablation_plan=plan)
    restored = store.load_ablation_result(reference.artifact_id, ablation_plan=plan)
    assert restored.status == "failed"
    assert restored.variants[0].error_code == "ablation_variant_failed"
    with pytest.raises(ArtifactStoreError):
        store.load_ablation_result(reference.artifact_id, ablation_plan=object())


def test_corruption_is_rejected_and_never_repaired(tmp_path):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    reference = store.save_evaluation_result(result)
    path = tmp_path / "artifacts" / Path(reference.relative_path)
    path.write_bytes(b"corrupt")
    with pytest.raises(ArtifactIntegrityError):
        store.load_evaluation_result(reference.artifact_id)
    with pytest.raises(ArtifactIntegrityError):
        store.save_evaluation_result(result)
    assert path.read_bytes() == b"corrupt"
    with pytest.raises(ArtifactStoreError):
        store.load_evaluation_result("../" + "0" * 60)


def test_size_limit_is_validated_before_publication(tmp_path):
    with pytest.raises(ArtifactStoreError):
        ExperimentArtifactStore(tmp_path, max_artifact_size=0)
    store = ExperimentArtifactStore(tmp_path / "artifacts", max_artifact_size=10)
    with pytest.raises(ArtifactStoreError):
        store.save_evaluation_result(_evaluation(tmp_path))
    assert not (tmp_path / "artifacts").exists()


def _publish_concurrently(monkeypatch, publish, count=4):
    original_os = artifact_store_module.os
    original_link = original_os.link
    start = threading.Barrier(count)
    gate_release = threading.Event()
    peer_failed = threading.Event()
    lock = threading.Lock()
    arrivals = 0
    results = [None] * count
    errors = [None] * count
    original_errors = [None] * count
    first_failure = [None]
    checkpoints = ["created"] * count
    staged_names = []
    started = monotonic()

    def coordinated_link(source, destination, *args, **kwargs):
        nonlocal arrivals
        index = int(threading.current_thread().name.rsplit("-", 1)[1])
        with lock:
            arrivals += 1
            staged_names.append(Path(source))
            checkpoints[index] = "final_link"
            if arrivals == count:
                gate_release.set()
        if not gate_release.wait(10):
            raise TimeoutError("all publishers did not reach the final link boundary")
        return original_link(source, destination, *args, **kwargs)

    class OsProxy:
        """Redirect only this module's link call; leave process-wide os untouched."""
        link = staticmethod(coordinated_link)

        def __getattr__(self, name):
            return getattr(original_os, name)

    monkeypatch.setattr(artifact_store_module, "os", OsProxy())

    def run(index):
        try:
            checkpoints[index] = "started"
            start.wait(timeout=5)
            checkpoints[index] = "start_barrier_passed"
            checkpoints[index] = "publication_entered"
            results[index] = publish()
            checkpoints[index] = "completed"
        except BaseException as exc:
            failure = {
                "index": index,
                "thread": threading.current_thread().name,
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "cause": repr(exc.__cause__) if exc.__cause__ is not None else None,
                "context": repr(exc.__context__) if exc.__context__ is not None else None,
                "checkpoint": checkpoints[index],
                "elapsed": monotonic() - started,
            }
            with lock:
                errors[index] = failure
                original_errors[index] = exc
                if first_failure[0] is None:
                    first_failure[0] = index
                checkpoints[index] = f"raised_after_{checkpoints[index]}"
                failure["link_arrivals"] = arrivals
            peer_failed.set()
            gate_release.set()

    threads = [threading.Thread(target=run, args=(index,), name=f"artifact-publisher-{index}") for index in range(count)]
    timed_out = False
    try:
        for thread in threads:
            thread.start()
        timed_out = not gate_release.wait(10) and not peer_failed.is_set()
    finally:
        gate_release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=10)
        monkeypatch.setattr(artifact_store_module, "os", original_os)
    if first_failure[0] is not None:
        first_index = first_failure[0]
        first_error = errors[first_index]
        raise AssertionError(
            f"publisher failure: {first_error}; arrivals={arrivals}/{count}; "
            f"checkpoints={checkpoints}; alive={[thread.is_alive() for thread in threads]}; "
            f"temporary_files={[path.name for path in staged_names if path.exists()]}; "
            f"elapsed={monotonic() - started:.3f}s"
        ) from original_errors[first_index]
    if timed_out:
        raise AssertionError(
            f"publishers at final link={arrivals}/{count}; checkpoints={checkpoints}; "
            f"alive={[thread.is_alive() for thread in threads]}; "
            f"errors={errors}; "
            f"staged_files={[path.name for path in staged_names if path.exists()]}; "
            f"elapsed={monotonic() - started:.1f}s"
        )
    assert all(not thread.is_alive() for thread in threads), "artifact publication thread did not terminate"
    assert errors == [None] * count
    assert all(item == results[0] for item in results)
    return results[0]


def test_concurrent_publication_helper_preserves_first_prelink_exception(tmp_path, monkeypatch):
    base = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False))
    plan = build_ablation_plan(base, "d" * 64)
    result = _failed_ablation(plan)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    original_os = artifact_store_module.os
    original_serialize = artifact_store_module.serialize_ablation_result

    def fail_selected(result_value):
        if threading.current_thread().name == "artifact-publisher-2":
            raise ArtifactIntegrityError("controlled pre-link diagnostic fault")
        return original_serialize(result_value)

    monkeypatch.setattr(artifact_store_module, "serialize_ablation_result", fail_selected)
    with pytest.raises(AssertionError, match="publisher failure") as caught:
        _publish_concurrently(
            monkeypatch,
            lambda: store.save_ablation_result(result, ablation_plan=plan),
        )
    diagnostic = str(caught.value)
    assert "artifact-publisher-2" in diagnostic
    assert "ArtifactIntegrityError" in diagnostic
    assert "controlled pre-link diagnostic fault" in diagnostic
    assert "Traceback (most recent call last)" in diagnostic
    assert "arrivals=" in diagnostic and "alive=[False, False, False, False]" in diagnostic
    assert isinstance(caught.value.__cause__, ArtifactIntegrityError)
    assert artifact_store_module.os is original_os
    assert not list((tmp_path / "artifacts").rglob(".artifact-*.tmp"))


def test_concurrent_evaluation_saves_publish_one_complete_canonical_artifact(tmp_path, monkeypatch):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    expected_bytes = serialize_evaluation_result(result).encode("utf-8")

    reference = _publish_concurrently(monkeypatch, lambda: store.save_evaluation_result(result))

    path = tmp_path / "artifacts" / Path(reference.relative_path)
    assert path.read_bytes() == expected_bytes
    assert len(store.list_artifacts("evaluation_result")) == 1
    assert store.load_evaluation_result(reference.artifact_id).report.evaluation_fingerprint == result.report.evaluation_fingerprint
    assert not list((tmp_path / "artifacts").rglob(".artifact-*.tmp"))


def test_concurrent_ablation_saves_are_idempotent_and_plan_bound(tmp_path, monkeypatch):
    base = ResolvedRunConfig.from_legacy(Config(reset_collections_on_start=False))
    plan = build_ablation_plan(base, "d" * 64)
    result = _failed_ablation(plan)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    expected_bytes = serialize_ablation_result(result).encode("utf-8")

    reference = _publish_concurrently(
        monkeypatch,
        lambda: store.save_ablation_result(result, ablation_plan=plan),
    )

    path = tmp_path / "artifacts" / Path(reference.relative_path)
    assert path.read_bytes() == expected_bytes
    assert len(store.list_artifacts("ablation_result")) == 1
    restored = store.load_ablation_result(reference.artifact_id, ablation_plan=plan)
    assert tuple(item.variant_id for item in restored.variants) == tuple(item.variant_id for item in plan.variants)
    assert serialize_ablation_result(restored).encode("utf-8") == expected_bytes
    assert not list((tmp_path / "artifacts").rglob(".artifact-*.tmp"))


def test_listing_ignores_temporary_file_before_atomic_publication(tmp_path, monkeypatch):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    original_link = artifact_store_module.os.link
    link_reached = threading.Event()
    release_link = threading.Event()
    result_box = []
    error_box = []

    def paused_link(source, destination, *args, **kwargs):
        link_reached.set()
        if not release_link.wait(10):
            raise AssertionError("test did not release final publication")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(artifact_store_module.os, "link", paused_link)

    def publish():
        try:
            result_box.append(store.save_evaluation_result(result))
        except BaseException as exc:
            error_box.append(exc)

    thread = threading.Thread(target=publish, name="paused-artifact-publisher")
    try:
        thread.start()
        assert link_reached.wait(10), "publisher did not reach the final link boundary"
        assert store.list_artifacts() == ()
        assert list((tmp_path / "artifacts").rglob(".artifact-*.tmp"))
    finally:
        release_link.set()
        if thread.ident is not None:
            thread.join(timeout=10)
        monkeypatch.setattr(artifact_store_module.os, "link", original_link)
    assert not thread.is_alive(), "paused publisher did not terminate"
    assert error_box == []
    assert len(result_box) == 1
    assert store.list_artifacts() == (result_box[0],)


def test_publication_failure_cleans_temp_file_and_retry_succeeds(tmp_path, monkeypatch):
    result = _evaluation(tmp_path)
    store = ExperimentArtifactStore(tmp_path / "artifacts")
    original_link = artifact_store_module.os.link

    def fail_link(_source, _destination, *args, **kwargs):
        raise OSError("controlled publication failure")

    monkeypatch.setattr(artifact_store_module.os, "link", fail_link)
    with pytest.raises(ArtifactStoreError, match="filesystem does not support safe no-overwrite publication"):
        store.save_evaluation_result(result)
    assert store.list_artifacts() == ()
    assert not list((tmp_path / "artifacts").rglob(".artifact-*.tmp"))

    monkeypatch.setattr(artifact_store_module.os, "link", original_link)
    reference = store.save_evaluation_result(result)
    assert store.load_evaluation_result(reference.artifact_id).report.evaluation_fingerprint == result.report.evaluation_fingerprint
    assert store.list_artifacts() == (reference,)
