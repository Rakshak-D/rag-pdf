"""Session-scoped general conversation through the configured local Ollama."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import re
import threading
from typing import Any, Callable

import httpx

from backend.engine.generation import create_local_ollama_client


_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_MAX_MODELS = 128
_MAX_ANSWER_CHARS = 16_000
_SYSTEM_MESSAGE = (
    "You are RAG-PDF General Chat. Answer clearly and helpfully using the "
    "conversation provided. You are not reading the user's documents. Do not "
    "claim document grounding or invent citations. If unsure, say so."
)


@dataclass(frozen=True)
class GeneralChatGenerationOptions:
    """Validated request-scoped options for General Chat inference."""

    temperature: float = 0.7
    max_output_tokens: int = 512

    def __post_init__(self) -> None:
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not math.isfinite(self.temperature)
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError("chat_temperature_invalid")
        if (
            isinstance(self.max_output_tokens, bool)
            or not isinstance(self.max_output_tokens, int)
            or not 32 <= self.max_output_tokens <= 2048
        ):
            raise ValueError("chat_output_limit_invalid")


class GeneralChatError(RuntimeError):
    """Stable, safe public error for General Chat."""

    def __init__(self, code: str, status_code: int) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code or ""):
            code = "chat_failed"
        self.code = code
        self.status_code = status_code if 400 <= status_code <= 599 else 500
        super().__init__(self.code)


class GeneralChatService:
    """A no-retrieval chat service with one active Ollama operation."""

    def __init__(
        self,
        configured_model: str,
        *,
        client_factory: Callable[[], Any] | None = None,
        max_concurrent: int = 1,
        temperature: float = 0.7,
        output_tokens: int = 512,
    ) -> None:
        if not isinstance(configured_model, str) or not _MODEL_NAME.fullmatch(configured_model):
            raise ValueError("chat_model_invalid")
        if isinstance(max_concurrent, bool) or max_concurrent != 1:
            raise ValueError("chat_concurrency_invalid")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise ValueError("chat_temperature_invalid")
        if isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or not 32 <= output_tokens <= 2048:
            raise ValueError("chat_output_limit_invalid")
        self.configured_model = configured_model
        self._client_factory = client_factory or create_local_ollama_client
        self._default_generation = GeneralChatGenerationOptions(float(temperature), output_tokens)
        self._capacity = threading.BoundedSemaphore(max_concurrent)

    @staticmethod
    def _model_names(response: Any) -> list[str]:
        raw = response.get("models") if isinstance(response, Mapping) else getattr(response, "models", None)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return []
        names: list[str] = []
        for item in raw[:_MAX_MODELS]:
            if isinstance(item, Mapping):
                value = item.get("model", item.get("name"))
            else:
                value = getattr(item, "model", None) or getattr(item, "name", None)
            if isinstance(value, str) and _MODEL_NAME.fullmatch(value) and value not in names:
                names.append(value)
        return names

    def models(self) -> dict[str, Any]:
        try:
            names = self._model_names(self._client_factory().list())
            return {
                "availability": "available",
                "configured_model": self.configured_model,
                "configured_model_available": self.configured_model in names,
                "available_models": names,
                "error_code": None,
            }
        except Exception:
            return {
                "availability": "unavailable",
                "configured_model": self.configured_model,
                "configured_model_available": False,
                "available_models": [],
                "error_code": "ollama_unavailable",
            }

    def chat(
        self,
        question: str,
        history: Sequence[Mapping[str, str]],
        model: str | None = None,
        generation: GeneralChatGenerationOptions | None = None,
    ) -> dict[str, Any]:
        if not self._capacity.acquire(blocking=False):
            raise GeneralChatError("chat_capacity_exhausted", 429)
        try:
            options = self._default_generation if generation is None else generation
            if not isinstance(options, GeneralChatGenerationOptions):
                raise GeneralChatError("chat_generation_invalid", 400)
            selected = self.configured_model if model is None else model
            if not isinstance(selected, str) or not _MODEL_NAME.fullmatch(selected):
                raise GeneralChatError("chat_model_invalid", 400)
            try:
                client = self._client_factory()
                installed = self._model_names(client.list())
            except Exception as exc:
                raise GeneralChatError("ollama_unavailable", 503) from exc
            if selected not in installed:
                raise GeneralChatError("chat_model_unavailable", 409)

            messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_MESSAGE}]
            for turn in history:
                messages.append({"role": "user", "content": turn["user"]})
                messages.append({"role": "assistant", "content": turn["assistant"]})
            messages.append({"role": "user", "content": question})
            try:
                response = client.chat(
                    model=selected,
                    messages=messages,
                    stream=False,
                    options={
                        "temperature": options.temperature,
                        "num_predict": options.max_output_tokens,
                    },
                )
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status == 404:
                    raise GeneralChatError("chat_model_unavailable", 409) from exc
                if status in {429, 503}:
                    raise GeneralChatError("ollama_capacity_unavailable", 503) from exc
                if isinstance(exc, (ConnectionError, httpx.ConnectError)):
                    raise GeneralChatError("ollama_unavailable", 503) from exc
                if isinstance(exc, httpx.TimeoutException):
                    raise GeneralChatError("chat_generation_timeout", 504) from exc
                raise GeneralChatError("chat_generation_failed", 502) from exc

            message = response.get("message") if isinstance(response, Mapping) else getattr(response, "message", None)
            answer = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", None)
            if not isinstance(answer, str) or not answer.strip() or len(answer) > _MAX_ANSWER_CHARS:
                raise GeneralChatError("chat_response_invalid", 502)
            return {
                "mode": "general",
                "answer": answer.strip(),
                "model": selected,
            }
        finally:
            self._capacity.release()


__all__ = ["GeneralChatService", "GeneralChatError", "GeneralChatGenerationOptions"]
