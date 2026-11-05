from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading

import pymupdf
import pytest
from starlette.testclient import TestClient

from backend.app.runtime import LocalRuntimeSettings, build_runtime
from backend.config import ResolvedRunConfig
from backend.engine.context import ContextFormatter
from backend.engine.generation import LocalGenerationAdapter
from backend.engine.manifest import build_document_manifest
from backend.engine.retrieval import HybridHit, HybridRetrievalResult, query_id_for
from backend.experiments.run_plan import build_run_plan
from backend.services.ask_service import AskError, AskImageReferenceRegistry, AskService
from backend.engine.context import SelectedImageEvidence
from notebooks.config import Config


TOKEN = "ask-integration-token"
BUILD_A = "a" * 32
BUILD_B = "b" * 32
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "3ioAAAAASUVORK5CYII="
)


class WordEncoder:
    def encode(self, value: str) -> list[int]:
        return list(range(len(value.split())))


class FakeOllama:
    def __init__(self, answer: str = "The supplied passage supports this answer [T1] [I1] [T99].", fail: bool = False, failure: Exception | None = None):
        self.answer = answer
        self.fail = fail
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def chat(self, **request):
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        if self.fail:
            raise RuntimeError("synthetic local generation failure")
        return {"message": {"content": self.answer}}


def _pdf_bytes() -> bytes:
    document = pymupdf.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), "A test-owned source document.")
        return document.tobytes()
    finally:
        document.close()


def _fixture(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "manual.pdf").write_bytes(_pdf_bytes())
    storage = tmp_path / "index-storage"
    storage.mkdir()
    images = tmp_path / "images"
    images.mkdir()
    image_path = images / "figure.png"
    image_path.write_bytes(PNG)
    config = ResolvedRunConfig.from_legacy(Config(
        pdf_dir=str(source), images_dir=str(images), database_dir=str(tmp_path / "database"),
        results_dir=str(tmp_path / "results"), model_comparison_results_dir=str(tmp_path / "comparison"),
        retrieval_results_dir=str(tmp_path / "retrieval-results"), retrieval_mode="hybrid",
        adaptive_weighting=False, use_weighted_fusion=False, use_reranker=False,
        text_k=5, image_k=5, max_context_tokens=1000, max_images=4,
        reset_collections_on_start=False, llm_retries=0, llm_fail_fast=True,
    ))
    manifest = build_document_manifest(source)
    for build_id in (BUILD_A, BUILD_B):
        artifact = storage / "build-artifacts" / build_id
        artifact.mkdir(parents=True)
        report = artifact / "build-report.json"
        plan = build_run_plan(config, manifest.content_hash)
        report.write_text(json.dumps({
            "report_schema_version": "1", "build_id": build_id,
            "artifact_root": str(artifact), "report_path": str(report),
            "index_fingerprint": plan.index_fingerprint,
            "document_manifest_hash": manifest.content_hash,
            "status": "completed", "partial_persistence": False,
            "text_records_persisted": 1, "image_records_persisted": 1,
        }, sort_keys=True), encoding="utf-8")
    state = tmp_path / "runtime-state"
    state.mkdir()
    settings = LocalRuntimeSettings(
        api_token=TOKEN, data_dir=source,
        artifact_store_dir=state / "artifacts", catalog_db_path=state / "catalog.sqlite3",
        jobs_db_path=state / "jobs.sqlite3", index_storage_root=storage,
    )
    return settings, config, manifest, image_path


def _select_payload(config: ResolvedRunConfig, manifest_hash: str, build_id: str, revision: int = 0):
    plan = build_run_plan(config, manifest_hash)
    return {
        "resolved_config": config.model_dump(mode="json"),
        "expected_manifest_hash": manifest_hash,
        "expected_index_fingerprint": plan.index_fingerprint,
        "build_id": build_id,
        "expected_revision": revision,
        "operation_id": ("1" if build_id == BUILD_A else "2") * 32,
    }


def _retrieval_for(*, include_text: bool = True, include_image: bool = True, on_call=None):
    def retrieve(question, plan, _storage_root, **kwargs):
        if on_call is not None:
            on_call(question, plan, kwargs)
        modalities = ("text", "image")
        config = plan.resolved_config()
        text = (HybridHit("chunk-1", 1, 0.8, {"source": "manual.pdf", "page_num": 1},
                          document="The source describes a verified test finding.", semantic_distance=0.1),) if include_text else ()
        image = (HybridHit("image-1", 1, 0.7, {
            "source": "manual.pdf", "page_num": 1, "image_id": "image-1",
            "image_path": str(Path(config.images_dir) / "figure.png"), "caption_text": "A test figure",
        }, caption="A test figure", semantic_distance=0.2),) if include_image else ()
        return HybridRetrievalResult(
            query_id=query_id_for(question, plan, modalities, config.text_k, config.image_k),
            index_fingerprint=plan.index_fingerprint, document_manifest_hash=plan.document_manifest_hash,
            modalities=modalities, effective_text_k=config.text_k, effective_image_k=config.image_k,
            text_hits=text, image_hits=image, fusion_strategy="rrf", effective_weights=(0.5, 0.5),
            diagnostics={}, modality_timings=(), total_elapsed_seconds=0.0, query_text=question,
        )
    return retrieve


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _build(settings, ollama: FakeOllama, retrieval, *, max_request_body_bytes: int | None = None):
    if max_request_body_bytes is not None:
        settings = replace(settings, max_request_body_bytes=max_request_body_bytes)
    encoder = WordEncoder()
    runtime = build_runtime(
        settings,
        context_formatter=ContextFormatter(encoder=encoder),
        generation_adapter=LocalGenerationAdapter(client_factory=lambda _host: ollama, encoder=encoder),
        ask_retrieval_callable=retrieval,
    )
    return runtime


def _select(client: TestClient, config: ResolvedRunConfig, manifest_hash: str, build_id: str = BUILD_A):
    response = client.post("/api/v1/index/selection", headers=_auth(), json=_select_payload(config, manifest_hash, build_id))
    assert response.status_code == 200, response.text
    return response.json()


def test_ask_auth_strict_input_unselected_and_body_limit(tmp_path: Path):
    settings, _config, _manifest, _image = _fixture(tmp_path)
    called = []
    runtime = _build(settings, FakeOllama(), _retrieval_for(on_call=lambda *_: called.append(True)), max_request_body_bytes=96)
    with TestClient(runtime.app) as client:
        assert client.post("/api/v1/ask", json={"question": "hello"}).status_code == 401
        assert client.get("/api/v1/ask/images/" + "a" * 32).status_code == 401
        empty = client.post("/api/v1/ask", headers=_auth(), json={"question": "  "})
        assert empty.status_code == 400 and empty.json()["error"]["code"] == "request_invalid"
        duplicate = client.post("/api/v1/ask", headers={**_auth(), "Content-Type": "application/json"}, content=b'{"question":"a","question":"b"}')
        assert duplicate.status_code == 400 and duplicate.json()["error"]["code"] == "request_invalid"
        unsupported = client.post("/api/v1/ask", headers={**_auth(), "Content-Type": "text/plain"}, content=b"question")
        assert unsupported.status_code == 415
        unsupported_field = client.post("/api/v1/ask", headers=_auth(), json={"question": "hello", "model": "untrusted-model"})
        assert unsupported_field.status_code == 400
        oversized = client.post("/api/v1/ask", headers=_auth(), json={"question": "x" * 160})
        assert oversized.status_code == 413 and oversized.json()["error"]["code"] == "request_body_too_large"
        no_selection = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is stated?"})
        assert no_selection.status_code == 409 and no_selection.json()["error"]["code"] == "index_not_selected"
        assert called == []


def test_authenticated_ask_returns_pinned_text_and_image_evidence(tmp_path: Path, monkeypatch):
    from backend.vectorstore.chroma_store import ChromaVectorStore

    monkeypatch.setattr(ChromaVectorStore, "open_existing", lambda *_a, **_k: pytest.fail("deterministic Ask test must not open Chroma"))
    settings, config, manifest, _image = _fixture(tmp_path)
    fake = FakeOllama()
    retrieval_calls = []
    runtime = _build(settings, fake, _retrieval_for(on_call=lambda q, p, kw: retrieval_calls.append((q, p, kw))))
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What finding is described?"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["outcome"] == "answered"
        assert payload["answer"] == fake.answer
        assert payload["referenced_citations"] == ["T1", "I1"]
        assert "unsupported_citation_label" in payload["warnings"]
        assert payload["text_evidence"][0]["label"] == "T1"
        assert payload["text_evidence"][0]["source_path"] == "manual.pdf"
        assert payload["text_evidence"][0]["page_number"] == 1
        assert payload["text_evidence"][0]["passage"] == "The source describes a verified test finding."
        image = payload["image_evidence"][0]
        assert image["label"] == "I1" and image["source_path"] == "manual.pdf" and image["page_number"] == 1
        assert image["content_type"] == "image/png" and image["size_bytes"] == len(PNG)
        assert str(tmp_path) not in response.text
        assert len(retrieval_calls) == 1
        assert retrieval_calls[0][2]["build_report_path"].name == "build-report.json"
        assert retrieval_calls[0][2]["build_report_path"].parent.name == BUILD_A
        assert fake.calls and fake.calls[0]["messages"][1]["images"] == [base64.b64encode(PNG).decode("ascii")]
        reference = image["image_reference"]
        unauthorized_image = client.get(f"/api/v1/ask/images/{reference}")
        assert unauthorized_image.status_code == 401
        image_response = client.get(f"/api/v1/ask/images/{reference}", headers=_auth())
        assert image_response.status_code == 200
        assert image_response.content == PNG
        assert image_response.headers["cache-control"] == "no-store"
        assert image_response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/api/v1/jobs", headers=_auth()).json()["jobs"] == []
        assert client.get("/api/v1/artifacts", headers=_auth()).json()["artifacts"] == []


def test_no_usable_evidence_skips_generation(tmp_path: Path):
    settings, config, manifest, _image = _fixture(tmp_path)
    fake = FakeOllama()
    runtime = _build(settings, fake, _retrieval_for(include_text=False, include_image=False))
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "insufficient_evidence"
        assert response.json()["failure_code"] == "no_usable_evidence"
        assert response.json()["answer"] is None
        assert response.json()["text_evidence"] == [] and response.json()["image_evidence"] == []
        assert fake.calls == []


def test_token_limit_and_retrieval_identity_fail_before_generation(tmp_path: Path):
    settings, config, manifest, _image = _fixture(tmp_path)
    fake = FakeOllama()
    calls = []
    runtime = _build(settings, fake, _retrieval_for(on_call=lambda *_: calls.append(True)))
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        too_many_tokens = client.post("/api/v1/ask", headers=_auth(), json={"question": "word " * 513})
        assert too_many_tokens.status_code == 413
        assert too_many_tokens.json()["error"]["code"] == "question_too_long"
        assert calls == [] and fake.calls == []

        def mismatched(_question, plan, storage_root, **kwargs):
            result = _retrieval_for()(_question, plan, storage_root, **kwargs)
            return replace(result, index_fingerprint="f" * 64)

        runtime.app.state.ask_service = AskService(
            settings.data_dir, settings.index_storage_root, runtime.index_selection_service,
            runtime.model_registry, runtime.context_formatter, runtime.generation_adapter,
            retrieval_callable=mismatched,
        )
        invalid = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert invalid.status_code == 502
        assert invalid.json()["error"]["code"] == "retrieval_identity_mismatch"
        assert fake.calls == []


def test_stale_source_is_rejected_before_retrieval(tmp_path: Path):
    settings, config, manifest, _image = _fixture(tmp_path)
    called = []
    runtime = _build(settings, FakeOllama(), _retrieval_for(on_call=lambda *_: called.append(True)))
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        (settings.data_dir / "manual.pdf").write_bytes(_pdf_bytes() + b"changed")
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] in {"index_selection_stale", "manifest_changed"}
        assert called == []


def test_selection_change_during_retrieval_does_not_redirect_pinned_query(tmp_path: Path):
    settings, config, manifest, _image = _fixture(tmp_path)
    holder = {}
    observations = []

    def switch_selection(_question, plan, kwargs):
        observations.append((plan.index_fingerprint, kwargs["build_report_path"].parent.name))
        holder["runtime"].index_selection_service.select(
            config=config, expected_manifest_hash=manifest.content_hash,
            expected_index_fingerprint=plan.index_fingerprint, build_id=BUILD_B,
            expected_revision=1, operation_id="2" * 32,
        )

    runtime = _build(settings, FakeOllama(), _retrieval_for(on_call=switch_selection))
    holder["runtime"] = runtime
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert response.status_code == 200, response.text
        assert response.json()["selected_build_id"] == BUILD_A
        assert observations == [(response.json()["index_fingerprint"], BUILD_A)]
        state = client.get("/api/v1/index/selection", headers=_auth()).json()
        assert state["selection"]["build_id"] == BUILD_B


def test_generation_failure_is_stable_and_does_not_leak_runtime_paths(tmp_path: Path):
    settings, config, manifest, _image = _fixture(tmp_path)
    runtime = _build(settings, FakeOllama(fail=True), _retrieval_for())
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert response.status_code == 502
        assert response.json() == {"error": {"code": "generation_failed"}}
        assert str(tmp_path) not in response.text


def test_ollama_connection_failure_has_distinct_unavailable_code(tmp_path: Path):
    settings, config, manifest, _image = _fixture(tmp_path)
    runtime = _build(settings, FakeOllama(failure=ConnectionError("private connection detail")), _retrieval_for())
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert response.status_code == 503
        assert response.json() == {"error": {"code": "ollama_unavailable"}}
        assert "private connection detail" not in response.text


def test_unavailable_retrieval_model_adapter_fails_before_retrieval(tmp_path: Path, monkeypatch):
    from backend.services.model_registry import ModelAdapterRegistry, ModelRuntimeError

    settings, config, manifest, _image = _fixture(tmp_path)
    fake = FakeOllama()
    retrieval_calls = []

    def unavailable(*_args, **_kwargs):
        raise ModelRuntimeError("private model path")

    monkeypatch.setattr(ModelAdapterRegistry, "get_text_adapter", unavailable)
    runtime = _build(settings, fake, _retrieval_for(on_call=lambda *_: retrieval_calls.append(True)))
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert response.status_code == 503
        assert response.json() == {"error": {"code": "model_adapter_unavailable"}}
        assert retrieval_calls == [] and fake.calls == []
        assert str(tmp_path) not in response.text


@pytest.mark.parametrize("unsafe_source", [
    "C:\\private\\manual.pdf",
    "../manual.pdf",
    "nested/manual.pdf",
])
def test_untrusted_retrieval_source_path_is_not_guessed_from_basename(tmp_path: Path, unsafe_source: str):
    settings, config, manifest, _image = _fixture(tmp_path)
    fake = FakeOllama()

    def retrieve_with_unsafe_source(question, plan, storage_root, **kwargs):
        result = _retrieval_for(include_image=False)(question, plan, storage_root, **kwargs)
        hit = result.text_hits[0]
        return replace(result, text_hits=(replace(hit, metadata={**hit.metadata, "source": unsafe_source}),))

    runtime = _build(settings, fake, retrieve_with_unsafe_source)
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert response.status_code == 500
        assert response.json() == {"error": {"code": "evidence_source_unresolved"}}
        assert str(tmp_path) not in response.text
        assert fake.calls == []


def test_model_reported_abstention_is_distinguished_from_missing_evidence(tmp_path: Path):
    settings, config, manifest, _image = _fixture(tmp_path)
    fake = FakeOllama("Insufficient evidence to answer this question.")
    runtime = _build(settings, fake, _retrieval_for(include_image=False))
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
        response = client.post("/api/v1/ask", headers=_auth(), json={"question": "What is described?"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["outcome"] == "answered"
        assert payload["model_reported_insufficient_evidence"] is True
        assert payload["model_identifier"] is not None
        assert fake.calls


def test_bounded_query_capacity_returns_overload_without_redirecting_work(tmp_path: Path):
    settings, config, manifest, _image = _fixture(tmp_path)
    runtime = _build(settings, FakeOllama(), _retrieval_for())
    with TestClient(runtime.app) as client:
        _select(client, config, manifest.content_hash)
    entered = threading.Event()
    release = threading.Event()

    def blocking_retrieval(question, plan, storage_root, **kwargs):
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("test retrieval release timed out")
        return _retrieval_for()(question, plan, storage_root, **kwargs)

    bounded = AskService(
        settings.data_dir, settings.index_storage_root, runtime.index_selection_service,
        runtime.model_registry, runtime.context_formatter, runtime.generation_adapter,
        retrieval_callable=blocking_retrieval, max_concurrent_queries=1,
    )
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(bounded.ask, "What is described?")
        try:
            assert entered.wait(timeout=3)
            with pytest.raises(AskError, match="ask_capacity_exhausted"):
                bounded.ask("A second question")
        finally:
            release.set()
        assert first.result(timeout=5)["outcome"] == "answered"


def test_image_reference_expiry_and_path_integrity(tmp_path: Path):
    image_path = tmp_path / "images" / "figure.png"
    image_path.parent.mkdir()
    image_path.write_bytes(PNG)
    now = [10.0]
    registry = AskImageReferenceRegistry(clock=lambda: now[0], ttl_seconds=5)
    hit = HybridHit("image-1", 1, 0.5, {})
    image = SelectedImageEvidence("I1", "image-1", "manual.pdf", "1", "figure", image_path, hit)
    registered = registry.register(image_path.parent, image, hashlib.sha256(PNG).hexdigest())
    assert registered is not None
    reference, content_type, size = registered
    assert content_type == "image/png" and size == len(PNG)
    assert registry.read(reference).data == PNG
    now[0] += 6
    with pytest.raises(AskError, match="image_evidence_expired"):
        registry.read(reference)


def test_image_registry_rejects_outside_invalid_type_and_oversized_files(tmp_path: Path):
    root = tmp_path / "owned-images"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    invalid = root / "invalid.png"
    invalid.write_bytes(b"not an image")
    oversized = root / "oversized.png"
    oversized.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (8 * 1024 * 1024))
    registry = AskImageReferenceRegistry()

    def selected(path: Path) -> SelectedImageEvidence:
        hit = HybridHit(path.name, 1, 0.5, {"source": "manual.pdf"})
        return SelectedImageEvidence("I1", path.name, "manual.pdf", "1", "", path, hit)

    assert registry.register(root, selected(outside), hashlib.sha256(PNG).hexdigest()) is None
    invalid_bytes = invalid.read_bytes()
    assert registry.register(root, selected(invalid), hashlib.sha256(invalid_bytes).hexdigest()) is None
    oversized_hash = hashlib.sha256(oversized.read_bytes()).hexdigest()
    assert registry.register(root, selected(oversized), oversized_hash) is None


def test_public_model_identifier_does_not_expose_absolute_paths():
    assert AskService._public_model_identifier(r"C:\private\models\answer.gguf") == "configured_model"
    assert AskService._public_model_identifier("llama3.2:latest") == "llama3.2:latest"
