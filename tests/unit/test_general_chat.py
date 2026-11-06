from __future__ import annotations

from dataclasses import dataclass
import threading

import httpx
import pytest

from backend.services.general_chat import GeneralChatError, GeneralChatGenerationOptions, GeneralChatService


@dataclass
class ModelRecord:
    model: str


@dataclass
class Message:
    content: str


@dataclass
class ChatResult:
    message: Message


class FakeOllama:
    def __init__(self, answer: str = "A general answer.", *, on_chat=None, failure=None):
        self.answer = answer
        self.on_chat = on_chat
        self.failure = failure
        self.chat_requests: list[dict] = []
        self.list_count = 0

    def list(self):
        self.list_count += 1
        return {"models": [ModelRecord("gemma4:e2b"), ModelRecord("qwen2.5:3b"), ModelRecord("bad model name")]}

    def chat(self, **request):
        self.chat_requests.append(request)
        if self.on_chat:
            self.on_chat()
        if self.failure:
            raise self.failure
        return ChatResult(Message(self.answer))


def test_model_catalog_is_bounded_and_reports_configured_availability():
    client = FakeOllama()
    service = GeneralChatService("gemma4:e2b", client_factory=lambda: client)
    assert service.models() == {
        "availability": "available",
        "configured_model": "gemma4:e2b",
        "configured_model_available": True,
        "available_models": ["gemma4:e2b", "qwen2.5:3b"],
        "error_code": None,
    }


def test_chat_uses_server_prompt_model_parameters_and_validated_history():
    client = FakeOllama("A clear answer.")
    service = GeneralChatService("gemma4:e2b", client_factory=lambda: client)
    result = service.chat("Follow up?", [{"user": "Question one", "assistant": "Answer one"}], "qwen2.5:3b")
    request = client.chat_requests[0]
    assert result["mode"] == "general"
    assert result["answer"] == "A clear answer."
    assert request["model"] == "qwen2.5:3b"
    assert request["options"] == {"temperature": 0.7, "num_predict": 512}
    assert request["messages"][0]["role"] == "system"
    assert "not reading the user's documents" in request["messages"][0]["content"]
    assert request["messages"][1:] == [
        {"role": "user", "content": "Question one"},
        {"role": "assistant", "content": "Answer one"},
        {"role": "user", "content": "Follow up?"},
    ]
    assert "citation" in request["messages"][0]["content"]


def test_request_scoped_generation_options_are_forwarded_without_shared_state_leakage():
    client = FakeOllama()
    service = GeneralChatService("gemma4:e2b", client_factory=lambda: client)
    first = GeneralChatGenerationOptions(temperature=0.2, max_output_tokens=768)
    second = GeneralChatGenerationOptions(temperature=1.4, max_output_tokens=128)

    service.chat("First", [], generation=first)
    service.chat("Second", [], generation=second)
    service.chat("Default", [])

    assert [item["options"] for item in client.chat_requests] == [
        {"temperature": 0.2, "num_predict": 768},
        {"temperature": 1.4, "num_predict": 128},
        {"temperature": 0.7, "num_predict": 512},
    ]
    assert service._default_generation == GeneralChatGenerationOptions()
    with pytest.raises((AttributeError, TypeError)):
        first.temperature = 0.8


@pytest.mark.parametrize("temperature", [True, "0.5", float("nan"), float("inf"), -0.01, 2.01])
def test_generation_temperature_is_finite_numeric_and_bounded(temperature):
    with pytest.raises(ValueError, match="chat_temperature_invalid"):
        GeneralChatGenerationOptions(temperature=temperature)


@pytest.mark.parametrize("output_tokens", [True, 31, 2049, 32.0, "512"])
def test_generation_output_tokens_are_integers_and_bounded(output_tokens):
    with pytest.raises(ValueError, match="chat_output_limit_invalid"):
        GeneralChatGenerationOptions(max_output_tokens=output_tokens)


def test_unavailable_ollama_and_missing_model_have_stable_errors():
    unavailable = GeneralChatService("gemma4:e2b", client_factory=lambda: (_ for _ in ()).throw(ConnectionError("private endpoint")))
    assert unavailable.models()["error_code"] == "ollama_unavailable"
    with pytest.raises(GeneralChatError, match="ollama_unavailable") as error:
        unavailable.chat("Hello", [])
    assert error.value.status_code == 503

    service = GeneralChatService("gemma4:e2b", client_factory=FakeOllama)
    with pytest.raises(GeneralChatError, match="chat_model_unavailable") as error:
        service.chat("Hello", [], "not-installed:latest")
    assert error.value.status_code == 409


def test_single_active_chat_rejects_concurrent_request_without_evicting_first():
    entered = threading.Event()
    release = threading.Event()

    def on_chat():
        entered.set()
        assert release.wait(3)

    service = GeneralChatService("gemma4:e2b", client_factory=lambda: FakeOllama(on_chat=on_chat))
    result: list[object] = []
    worker = threading.Thread(target=lambda: result.append(service.chat("First", [])))
    worker.start()
    assert entered.wait(2)
    with pytest.raises(GeneralChatError, match="chat_capacity_exhausted"):
        service.chat("Second", [])
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert result[0]["answer"] == "A general answer."


def test_connection_failure_does_not_expose_exception_text():
    service = GeneralChatService("gemma4:e2b", client_factory=lambda: (_ for _ in ()).throw(RuntimeError("C:\\Users\\secret\\token=abc")))
    result = service.models()
    assert result["error_code"] == "ollama_unavailable"
    assert "secret" not in repr(result)
    with pytest.raises(GeneralChatError) as error:
        service.chat("Hello", [])
    assert str(error.value) == "ollama_unavailable"


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_status"),
    [
        (httpx.ConnectError("http://user:secret@127.0.0.1/private"), "ollama_unavailable", 503),
        (httpx.ReadTimeout("private request details"), "chat_generation_timeout", 504),
    ],
)
def test_transport_failures_have_stable_codes_without_leaking_details(failure, expected_code, expected_status):
    service = GeneralChatService("gemma4:e2b", client_factory=lambda: FakeOllama(failure=failure))
    with pytest.raises(GeneralChatError) as error:
        service.chat("Hello", [])
    assert error.value.code == expected_code
    assert error.value.status_code == expected_status
    assert str(error.value) == expected_code


def test_client_creation_does_not_load_models():
    calls: list[str] = []
    service = GeneralChatService("gemma4:e2b", client_factory=lambda: calls.append("client") or FakeOllama())
    assert calls == []
    assert service.configured_model == "gemma4:e2b"
