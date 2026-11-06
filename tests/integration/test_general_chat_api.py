from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from backend.app.runtime import LocalRuntimeSettings, build_runtime
from backend.services.general_chat import GeneralChatService


TOKEN = "general-chat-api-test-token-0123456789"


@dataclass
class ModelRecord:
    model: str


class FakeOllama:
    def __init__(self, *, fail_list: bool = False):
        self.fail_list = fail_list
        self.requests: list[dict] = []

    def list(self):
        if self.fail_list:
            raise ConnectionError("private host information")
        return {"models": [ModelRecord("gemma4:e2b"), ModelRecord("qwen2.5:3b")]}

    def chat(self, **request):
        self.requests.append(request)
        return {"message": {"content": "A real service boundary with deterministic test output."}}


def _runtime(tmp_path: Path, chat_service: GeneralChatService, *, body_limit: int = 24 * 1024 * 1024):
    source = tmp_path / "source"
    storage = tmp_path / "storage"
    state = tmp_path / "state"
    for path in (source, storage, state):
        path.mkdir(parents=True)
    settings = LocalRuntimeSettings(
        api_token=TOKEN,
        data_dir=source,
        artifact_store_dir=state / "artifacts",
        catalog_db_path=state / "catalog.sqlite3",
        jobs_db_path=state / "jobs.sqlite3",
        index_storage_root=storage,
        max_request_body_bytes=body_limit,
    )
    return build_runtime(settings, chat_service=chat_service)


def test_authenticated_chat_api_uses_local_chat_and_never_opens_retrieval(tmp_path: Path, monkeypatch):
    ollama = FakeOllama()
    service = GeneralChatService("gemma4:e2b", client_factory=lambda: ollama)
    runtime = _runtime(tmp_path, service)
    assert runtime.ask_service is not None
    monkeypatch.setattr(runtime.ask_service, "ask", lambda *args, **kwargs: pytest.fail("General Chat must not invoke AskService"))
    with TestClient(runtime.app) as client:
        assert client.get("/api/v1/chat/models").status_code == 401
        catalog = client.get("/api/v1/chat/models", headers={"Authorization": f"Bearer {TOKEN}"})
        assert catalog.status_code == 200
        assert catalog.json()["available_models"] == ["gemma4:e2b", "qwen2.5:3b"]

        denied = client.post("/api/v1/chat", json={"question": "Hello"})
        assert denied.status_code == 401
        response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {TOKEN}"}, json={
            "question": "What should I remember?",
            "history": [{"user": "Earlier question", "assistant": "Earlier answer"}],
            "model": "qwen2.5:3b",
            "generation": {"temperature": 0.35, "max_output_tokens": 768},
        })
        assert response.status_code == 200
        assert response.json()["mode"] == "general"
        assert response.json()["answer"] == "A real service boundary with deterministic test output."
        assert set(response.json()) == {"mode", "answer", "model"}
        assert response.headers["cache-control"] == "no-store"
        assert ollama.requests[0]["model"] == "qwen2.5:3b"
        assert ollama.requests[0]["options"] == {"temperature": 0.35, "num_predict": 768}
        assert ollama.requests[0]["messages"][1:3] == [
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
        ]
        assert runtime.model_registry.snapshot().components == ()
        assert runtime.ask_service is not None

        defaulted = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {TOKEN}"}, json={"question": "Defaults?"})
        assert defaulted.status_code == 200
        assert ollama.requests[1]["options"] == {"temperature": 0.7, "num_predict": 512}


def test_chat_api_rejects_invalid_and_oversized_requests_before_ollama(tmp_path: Path):
    ollama = FakeOllama()
    runtime = _runtime(tmp_path, GeneralChatService("gemma4:e2b", client_factory=lambda: ollama), body_limit=2048)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(runtime.app) as client:
        assert client.post("/api/v1/chat", headers=headers, json={"question": "  "}).status_code == 400
        assert client.post("/api/v1/chat", headers=headers, json={"question": "Hello", "model": "C:\\secret\\model"}).status_code == 400
        assert client.post("/api/v1/chat", headers=headers, json={"question": "Hello", "system": "override"}).status_code == 400
        assert client.post("/api/v1/chat", headers=headers, json={"question": "Hello", "generation": {
            "temperature": True, "max_output_tokens": 512,
        }}).status_code == 400
        assert client.post("/api/v1/chat", headers=headers, json={"question": "Hello", "generation": {
            "temperature": 0.5, "max_output_tokens": 512, "tools": ["read_file"],
        }}).status_code == 400
        too_many_turns = [{"user": "q", "assistant": "a"}] * 9
        assert client.post("/api/v1/chat", headers=headers, json={"question": "Hello", "history": too_many_turns}).status_code == 400
        oversized = client.post("/api/v1/chat", headers={**headers, "Content-Type": "application/json"}, content=b"{" + b" " * 2100)
        assert oversized.status_code == 413
    assert ollama.requests == []


def test_chat_api_returns_safe_unavailable_and_missing_model_codes(tmp_path: Path):
    offline = GeneralChatService("gemma4:e2b", client_factory=lambda: FakeOllama(fail_list=True))
    runtime = _runtime(tmp_path / "offline", offline)
    with TestClient(runtime.app) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        assert client.get("/api/v1/chat/models", headers=headers).json()["error_code"] == "ollama_unavailable"
        response = client.post("/api/v1/chat", headers=headers, json={"question": "Hello"})
        assert response.status_code == 503
        assert response.json() == {"error": {"code": "ollama_unavailable"}}
        assert "private host" not in response.text

    available = FakeOllama()
    runtime2 = _runtime(tmp_path / "missing", GeneralChatService("gemma4:e2b", client_factory=lambda: available))
    with TestClient(runtime2.app) as client:
        response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {TOKEN}"}, json={"question": "Hello", "model": "unknown:latest"})
        assert response.status_code == 409
        assert response.json() == {"error": {"code": "chat_model_unavailable"}}
        assert available.requests == []
