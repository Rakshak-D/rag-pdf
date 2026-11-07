"""Loopback-only same-origin gateway for the local browser application.

The browser never receives either backend bearer token. This gateway injects a
fixed ordinary or bootstrap credential only after an explicit route match.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ipaddress
import os
import re
from typing import Any
from urllib.parse import quote, unquote_to_bytes, urlsplit

import httpx
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


_MAX_REQUEST_BYTES = 24 * 1024 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_MAX_PDF_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_PDF_ERROR_BYTES = 64 * 1024
_JSON_TYPES = {"application/json", "application/problem+json"}
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
_ORDINARY_EXACT: dict[str, set[str]] = {
    "/health": {"GET"},
    "/api/v1/documents/manifest": {"GET"},
    "/api/v1/index/readiness": {"POST"},
    "/api/v1/index/selection": {"GET", "POST"},
    "/api/v1/ask": {"POST"},
    "/api/v1/chat": {"POST"},
    "/api/v1/chat/models": {"GET"},
    "/api/v1/run-plans": {"POST"},
    "/api/v1/evaluations": {"POST"},
    "/api/v1/ablations": {"POST"},
    "/api/v1/ablations/variants": {"GET"},
    "/api/v1/runtime/models": {"GET"},
    "/api/v1/jobs/evaluations": {"POST"},
    "/api/v1/jobs/ablations": {"POST"},
    "/api/v1/jobs": {"GET"},
    "/api/v1/artifacts": {"GET"},
}
_ORDINARY_DYNAMIC = (
    (re.compile(r"^/api/v1/ask/images/[0-9a-f]{32}$"), {"GET"}),
    (re.compile(r"^/api/v1/jobs/[0-9a-f-]{36}$"), {"GET"}),
    (re.compile(r"^/api/v1/jobs/[0-9a-f-]{36}/cancel$"), {"POST"}),
    (re.compile(r"^/api/v1/artifacts/[A-Za-z0-9_-]{1,128}$"), {"GET"}),
    (re.compile(r"^/api/v1/artifacts/[A-Za-z0-9_-]{1,128}/metrics$"), {"GET"}),
    (re.compile(r"^/api/v1/artifacts/[A-Za-z0-9_-]{1,128}/comparisons$"), {"GET"}),
    (re.compile(r"^/api/v1/artifacts/[A-Za-z0-9_-]{1,128}/result$"), {"GET"}),
)
_BOOTSTRAP_EXACT: dict[str, set[str]] = {
    "/health": {"GET"},
    "/api/v1/bootstrap/status": {"GET"},
    "/api/v1/bootstrap/configuration": {"GET"},
    "/api/v1/bootstrap/documents": {"GET", "POST"},
    "/api/v1/bootstrap/preview": {"POST"},
    "/api/v1/bootstrap/index-inventory": {"POST"},
    "/api/v1/bootstrap/jobs": {"GET", "POST"},
}
_BOOTSTRAP_DYNAMIC = (
    (re.compile(r"^/api/v1/bootstrap/jobs/[0-9a-f-]{36}$"), {"GET"}),
    (re.compile(r"^/api/v1/bootstrap/documents/doc_[0-9a-f]{64}/content$"), {"GET"}),
)
_FORWARDED_REQUEST_HEADERS = ("accept", "content-type", "x-document-filename")


class GatewayConfigurationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _valid_token(value: object) -> bool:
    return isinstance(value, str) and 32 <= len(value) <= 4096 and not any(char.isspace() for char in value)


def _loopback_origin(value: object) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise GatewayConfigurationError("gateway_origin_invalid")
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise GatewayConfigurationError("gateway_origin_invalid") from exc
    if parsed.scheme != "http" or not address.is_loopback or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or port is None:
        raise GatewayConfigurationError("gateway_origin_invalid")
    return f"http://{address.compressed}:{port}"


def _loopback_target(value: object) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise GatewayConfigurationError("gateway_upstream_invalid")
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise GatewayConfigurationError("gateway_upstream_invalid") from exc
    if parsed.scheme != "http" or not address.is_loopback or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or port is None:
        raise GatewayConfigurationError("gateway_upstream_invalid")
    return f"http://{address.compressed}:{port}"


def _host_header(value: object) -> str:
    if not isinstance(value, str) or len(value) > 255 or value.strip() != value:
        raise GatewayConfigurationError("gateway_host_invalid")
    parsed = urlsplit(f"http://{value}")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise GatewayConfigurationError("gateway_host_invalid") from exc
    if not address.is_loopback or port is None or parsed.path or parsed.query or parsed.fragment:
        raise GatewayConfigurationError("gateway_host_invalid")
    return f"{address.compressed}:{port}"


@dataclass(frozen=True)
class LocalGatewaySettings:
    ordinary_token: str = field(repr=False)
    bootstrap_token: str = field(repr=False)
    allowed_origin: str
    allowed_host: str
    ordinary_target: str = "http://127.0.0.1:8000"
    bootstrap_target: str = "http://127.0.0.1:8010"
    host: str = "127.0.0.1"
    port: int = 8020

    def __post_init__(self) -> None:
        if not _valid_token(self.ordinary_token) or not _valid_token(self.bootstrap_token) or self.ordinary_token == self.bootstrap_token:
            raise GatewayConfigurationError("gateway_tokens_invalid")
        object.__setattr__(self, "allowed_origin", _loopback_origin(self.allowed_origin))
        object.__setattr__(self, "allowed_host", _host_header(self.allowed_host))
        object.__setattr__(self, "ordinary_target", _loopback_target(self.ordinary_target))
        object.__setattr__(self, "bootstrap_target", _loopback_target(self.bootstrap_target))
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise GatewayConfigurationError("gateway_host_invalid") from exc
        if not address.is_loopback or isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise GatewayConfigurationError("gateway_bind_invalid")

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "LocalGatewaySettings":
        values = os.environ if environ is None else environ
        try:
            return cls(
                ordinary_token=values["RAG_LOCAL_ORDINARY_TOKEN"],
                bootstrap_token=values["RAG_LOCAL_BOOTSTRAP_TOKEN"],
                allowed_origin=values.get("RAG_LOCAL_APP_ORIGIN", "http://127.0.0.1:5173"),
                allowed_host=values.get("RAG_LOCAL_APP_HOST", "127.0.0.1:5173"),
                ordinary_target=values.get("RAG_LOCAL_ORDINARY_TARGET", "http://127.0.0.1:8000"),
                bootstrap_target=values.get("RAG_LOCAL_BOOTSTRAP_TARGET", "http://127.0.0.1:8010"),
                host=values.get("RAG_LOCAL_GATEWAY_HOST", "127.0.0.1"),
                port=int(values.get("RAG_LOCAL_GATEWAY_PORT", "8020")),
            )
        except (KeyError, ValueError) as exc:
            if isinstance(exc, GatewayConfigurationError):
                raise
            raise GatewayConfigurationError("gateway_configuration_invalid") from exc


class GatewayBoundaryMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_host: str, allowed_origin: str) -> None:
        super().__init__(app)
        self._allowed_host = allowed_host
        self._allowed_origin = allowed_origin

    async def dispatch(self, request: Request, call_next):
        if request.headers.get("host", "").lower() != self._allowed_host.lower():
            return _error("gateway_host_rejected", 403)
        origin = request.headers.get("origin")
        if origin is not None and origin != self._allowed_origin:
            return _error("gateway_origin_rejected", 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if origin != self._allowed_origin or request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}:
                return _error("gateway_csrf_rejected", 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def _error(code: str, status: int) -> JSONResponse:
    response = JSONResponse({"error": {"code": code}}, status_code=status)
    response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
    return response


def _path_is_safe(request: Request) -> bool:
    raw = request.scope.get("raw_path", b"")
    if not isinstance(raw, bytes) or len(raw) > 4096 or any(marker in raw.lower() for marker in (b"%2f", b"%5c", b"%2e", b"\\", b"//")):
        return False
    return all(part not in {".", ".."} for part in request.url.path.split("/"))


def _allowed(mapping: dict[str, set[str]], dynamic: tuple[tuple[re.Pattern[str], set[str]], ...], path: str, method: str) -> bool:
    if method in mapping.get(path, set()):
        return True
    return any(method in methods and pattern.fullmatch(path) is not None for pattern, methods in dynamic)


_PDF_SOURCE_PATH = re.compile(r"^/api/v1/bootstrap/documents/(doc_[0-9a-f]{64})/content$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ENCODED_RELATIVE_NAME = re.compile(r"^(?:[A-Za-z0-9_.~/-]|%[0-9A-Fa-f]{2}){1,1024}$", re.ASCII)


def _pdf_route_identity(request: Request, upstream_path: str) -> tuple[str, str] | None:
    match = _PDF_SOURCE_PATH.fullmatch(upstream_path)
    if match is None or request.method != "GET":
        return None
    query = request.query_params.multi_items()
    if (len(query) != 1 or query[0][0] != "manifest_hash" or
            not _SHA256.fullmatch(query[0][1])):
        return None
    return match.group(1), query[0][1]


def _encoded_relative_name_valid(value: str) -> bool:
    if not _ENCODED_RELATIVE_NAME.fullmatch(value):
        return False
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return False
    parts = decoded.split("/")
    return (bool(decoded) and len(decoded) <= 1024 and quote(decoded, safe="/") == value and
            not decoded.startswith("/") and "\\" not in decoded and
            not any(part in {"", ".", ".."} for part in parts) and
            not any(ord(char) < 32 or ord(char) == 127 for char in decoded))


class _GatewayProxy:
    def __init__(self, settings: LocalGatewaySettings, *, client_factory=None) -> None:
        self.settings = settings
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(120.0), follow_redirects=False))

    async def gateway_health(self, _request: Request) -> Response:
        return JSONResponse({"status": "ok", "service": "local-gateway", "api_version": "v1"})

    async def health(self, request: Request) -> Response:
        return await self._forward(request, role="ordinary")

    async def ordinary(self, request: Request) -> Response:
        return await self._forward(request, role="ordinary")

    async def bootstrap(self, request: Request) -> Response:
        return await self._forward(request, role="bootstrap")

    async def _forward(self, request: Request, *, role: str) -> Response:
        if not _path_is_safe(request):
            return _error("gateway_path_rejected", 400)
        path = request.url.path
        pdf_identity: tuple[str, str] | None = None
        if role == "ordinary":
            if not _allowed(_ORDINARY_EXACT, _ORDINARY_DYNAMIC, path, request.method):
                return _error("gateway_route_rejected", 404)
            target = self.settings.ordinary_target
            upstream_path = path
            token = self.settings.ordinary_token
        else:
            if not path.startswith("/bootstrap-api/"):
                return _error("gateway_route_rejected", 404)
            upstream_path = "/health" if path == "/bootstrap-api/health" else f"/api{path.removeprefix('/bootstrap-api')}"
            if not _allowed(_BOOTSTRAP_EXACT, _BOOTSTRAP_DYNAMIC, upstream_path, request.method):
                return _error("gateway_route_rejected", 404)
            if _PDF_SOURCE_PATH.fullmatch(upstream_path):
                pdf_identity = _pdf_route_identity(request, upstream_path)
                if pdf_identity is None:
                    return _error("gateway_route_rejected", 404)
                if request.headers.get("range") is not None or request.headers.get("if-range") is not None:
                    return _error("document_range_unsupported", 416)
            target = self.settings.bootstrap_target
            token = self.settings.bootstrap_token

        content_length = request.headers.get("content-length")
        if content_length is not None and (not content_length.isdecimal() or int(content_length) > _MAX_REQUEST_BYTES):
            return _error("request_body_too_large", 413)
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_REQUEST_BYTES:
                    return _error("request_body_too_large", 413)
                chunks.append(chunk)
            body = b"".join(chunks)
            headers = {name: request.headers[name] for name in _FORWARDED_REQUEST_HEADERS if name in request.headers}
            if request.method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
                headers["Authorization"] = f"Bearer {token}"
            url = f"{target}{upstream_path}"
            if request.url.query:
                url = f"{url}?{request.url.query}"
            async with self.client_factory() as client:
                async with client.stream(request.method, url, headers=headers, content=body) as upstream:
                    media_type = upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if pdf_identity is not None:
                        allowed_pdf_response = media_type == "application/pdf" and upstream.status_code == 200
                        allowed_pdf_error = media_type in _JSON_TYPES and upstream.status_code >= 400
                        if not (allowed_pdf_response or allowed_pdf_error):
                            return _error("gateway_response_type_rejected", 502)
                        declared_length = upstream.headers.get("content-length")
                        limit = _MAX_PDF_RESPONSE_BYTES if allowed_pdf_response else _MAX_PDF_ERROR_BYTES
                        if declared_length is not None and (not declared_length.isdecimal() or int(declared_length) > limit):
                            return _error("gateway_response_too_large", 502)
                    elif media_type not in _JSON_TYPES | _IMAGE_TYPES:
                        return _error("gateway_response_type_rejected", 502)
                    result: list[bytes] = []
                    size = 0
                    async for chunk in upstream.aiter_bytes():
                        size += len(chunk)
                        response_limit = limit if pdf_identity is not None else _MAX_RESPONSE_BYTES
                        if size > response_limit:
                            return _error("gateway_response_too_large", 502)
                        result.append(chunk)
                    if pdf_identity is not None and media_type == "application/pdf":
                        route_document_id, route_manifest = pdf_identity
                        document_id = upstream.headers.get("x-rag-document-id", "")
                        document_digest = upstream.headers.get("x-rag-document-sha256", "")
                        manifest_hash = upstream.headers.get("x-rag-manifest-hash", "")
                        encoded_name = upstream.headers.get("x-rag-document-name", "")
                        page_count = upstream.headers.get("x-rag-page-count", "")
                        try:
                            page_total = int(page_count)
                        except ValueError:
                            page_total = 0
                        if (document_id != route_document_id or manifest_hash != route_manifest or
                                not _SHA256.fullmatch(document_digest) or not 1 <= page_total <= 200 or
                                not _encoded_relative_name_valid(encoded_name)):
                            return _error("gateway_response_identity_rejected", 502)
                    response = Response(b"".join(result), status_code=upstream.status_code, media_type=media_type)
                    response.headers["Cache-Control"] = "no-store"
                    response.headers["X-Content-Type-Options"] = "nosniff"
                    response.headers["Referrer-Policy"] = "no-referrer"
                    if media_type == "application/pdf" and pdf_identity is not None:
                        response.headers.update({
                            "Content-Security-Policy": "default-src 'none'; sandbox",
                            "Cross-Origin-Resource-Policy": "same-origin",
                            "X-Frame-Options": "DENY",
                            "X-RAG-Document-ID": document_id,
                            "X-RAG-Document-SHA256": document_digest,
                            "X-RAG-Manifest-Hash": manifest_hash,
                            "X-RAG-Document-Name": encoded_name,
                            "X-RAG-Page-Count": str(page_total),
                        })
                    elif media_type in _IMAGE_TYPES:
                        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
                    return response
        except httpx.TimeoutException:
            return _error("gateway_upstream_timeout", 504)
        except Exception:
            return _error("gateway_upstream_unavailable", 502)


def create_local_gateway(settings: LocalGatewaySettings, *, client_factory=None) -> Starlette:
    if not isinstance(settings, LocalGatewaySettings):
        raise GatewayConfigurationError("gateway_settings_invalid")
    proxy = _GatewayProxy(settings, client_factory=client_factory)

    @asynccontextmanager
    async def lifespan(_app):
        yield

    app = Starlette(routes=[
        Route("/health", proxy.health, methods=["GET"]),
        Route("/gateway-health", proxy.gateway_health, methods=["GET"]),
        Route("/api/{path:path}", proxy.ordinary, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        Route("/bootstrap-api/{path:path}", proxy.bootstrap, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
    ], lifespan=lifespan)
    app.add_middleware(GatewayBoundaryMiddleware, allowed_host=settings.allowed_host, allowed_origin=settings.allowed_origin)
    return app


def main() -> int:
    try:
        import uvicorn
        settings = LocalGatewaySettings.from_environment()
        uvicorn.run(create_local_gateway(settings), host=settings.host, port=settings.port, access_log=False, log_level="warning")
    except GatewayConfigurationError as exc:
        print(f"Local gateway configuration error: {exc.code}", file=__import__("sys").stderr)
        return 2
    except Exception:
        print("Local gateway startup failed: gateway_startup_failed", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
