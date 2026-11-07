from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from backend.local_gateway import (
    GatewayConfigurationError,
    LocalGatewaySettings,
    create_local_gateway,
)


ORDINARY = "ordinary-private-token-0123456789-0123456789"
BOOTSTRAP = "bootstrap-private-token-0123456789-0123456789"
HOST = "127.0.0.1:5173"
ORIGIN = "http://127.0.0.1:5173"


def _settings(**kwargs):
    return LocalGatewaySettings(ORDINARY, BOOTSTRAP, ORIGIN, HOST, **kwargs)


def _app(handler):
    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return create_local_gateway(_settings(), client_factory=factory)


def test_gateway_injects_only_the_ordinary_server_credential_and_does_not_forward_cookies():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"availability": "available"}, headers={"Set-Cookie": "secret=never"})

    with TestClient(_app(handler)) as client:
        response = client.get("/api/v1/chat/models", headers={"Host": HOST, "Authorization": "Bearer browser-supplied"})
    assert response.status_code == 200
    assert seen[0].url == "http://127.0.0.1:8000/api/v1/chat/models"
    assert seen[0].headers["authorization"] == f"Bearer {ORDINARY}"
    assert "cookie" not in seen[0].headers
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_bootstrap_route_uses_separate_credential_and_fixed_upstream():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    with TestClient(_app(handler)) as client:
        response = client.get("/bootstrap-api/v1/bootstrap/status", headers={"Host": HOST})
    assert response.status_code == 200
    assert seen[0].url == "http://127.0.0.1:8010/api/v1/bootstrap/status"
    assert seen[0].headers["authorization"] == f"Bearer {BOOTSTRAP}"
    assert seen[0].headers["authorization"] != f"Bearer {ORDINARY}"


def test_trusted_configuration_route_is_bootstrap_only_and_origin_protected():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"schema_version": "1", "resolved_config": {}})

    with TestClient(_app(handler)) as client:
        response = client.get("/bootstrap-api/v1/bootstrap/configuration", headers={"Host": HOST})
        ordinary_attempt = client.get("/api/v1/bootstrap/configuration", headers={"Host": HOST})
        cross_origin = client.get("/bootstrap-api/v1/bootstrap/configuration", headers={"Host": HOST, "Origin": "http://evil.example"})

    assert response.status_code == 200
    assert seen[0].url == "http://127.0.0.1:8010/api/v1/bootstrap/configuration"
    assert seen[0].headers["authorization"] == f"Bearer {BOOTSTRAP}"
    assert seen[0].headers["authorization"] != f"Bearer {ORDINARY}"
    assert ordinary_attempt.status_code == 404
    assert cross_origin.status_code == 403
    assert len(seen) == 1


def test_gateway_allows_manifest_bound_pdf_only_on_bootstrap_source_route():
    document_id = f"doc_{'a' * 64}"
    manifest_hash = "b" * 64
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            content=b"%PDF-1.7\nexample",
            headers={
                "Content-Type": "application/pdf",
                "X-RAG-Document-ID": document_id,
                "X-RAG-Document-SHA256": "c" * 64,
                "X-RAG-Manifest-Hash": manifest_hash,
                "X-RAG-Document-Name": "folder/notes%20one.pdf",
                "X-RAG-Page-Count": "3",
                "Set-Cookie": "must-not-forward=secret",
            },
        )

    path = f"/bootstrap-api/v1/bootstrap/documents/{document_id}/content?manifest_hash={manifest_hash}"
    with TestClient(_app(handler)) as client:
        response = client.get(path, headers={"Host": HOST, "Authorization": "Bearer browser-value"})
        wrong_method = client.post(path, headers={"Host": HOST, "Origin": ORIGIN}, content=b"")
        duplicate_manifest = client.get(path + "&manifest_hash=" + manifest_hash, headers={"Host": HOST})
        malformed_document = client.get(f"/bootstrap-api/v1/bootstrap/documents/doc_%2e%2e/content?manifest_hash={manifest_hash}", headers={"Host": HOST})
        range_request = client.get(path, headers={"Host": HOST, "Range": "bytes=0-5"})
        cross_origin = client.get(path, headers={"Host": HOST, "Origin": "http://evil.example"})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["x-rag-document-name"] == "folder/notes%20one.pdf"
    assert "set-cookie" not in response.headers
    assert seen[0].url == f"http://127.0.0.1:8010/api/v1/bootstrap/documents/{document_id}/content?manifest_hash={manifest_hash}"
    assert seen[0].headers["authorization"] == f"Bearer {BOOTSTRAP}"
    assert "browser-value" not in response.text
    assert wrong_method.status_code == 404
    assert duplicate_manifest.status_code == 404
    assert malformed_document.status_code in {400, 404}
    assert range_request.status_code == 416
    assert cross_origin.status_code == 403
    assert len(seen) == 1


def test_pdf_mime_and_headers_are_not_permitted_on_unrelated_routes(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"%PDF-fake", headers={"Content-Type": "application/pdf"})

    monkeypatch.setattr("backend.local_gateway._MAX_PDF_RESPONSE_BYTES", 4)
    with TestClient(_app(handler)) as client:
        unrelated = client.get("/api/v1/chat/models", headers={"Host": HOST})
        oversize = client.get(
            f"/bootstrap-api/v1/bootstrap/documents/doc_{'d' * 64}/content?manifest_hash={'e' * 64}",
            headers={"Host": HOST},
        )
    assert unrelated.status_code == 502
    assert unrelated.json()["error"]["code"] == "gateway_response_type_rejected"
    assert oversize.status_code == 502
    assert oversize.json()["error"]["code"] == "gateway_response_too_large"
    assert len(calls) == 2


def test_gateway_enforces_host_origin_csrf_and_fixed_route_map():
    calls = []
    app = _app(lambda request: calls.append(request) or httpx.Response(200, json={"ok": True}))
    with TestClient(app) as client:
        assert client.get("/api/v1/chat/models", headers={"Host": "evil.example"}).status_code == 403
        assert client.post("/api/v1/chat", headers={"Host": HOST, "Origin": "http://evil.example"}, json={"question": "x"}).json()["error"]["code"] == "gateway_origin_rejected"
        assert client.post("/api/v1/chat", headers={"Host": HOST, "Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"}, json={"question": "x"}).json()["error"]["code"] == "gateway_csrf_rejected"
        assert client.get("/api/v1/private-proxy", headers={"Host": HOST}).status_code == 404
        assert client.get("/bootstrap-api/api/v1/chat/models", headers={"Host": HOST}).status_code == 404
    assert calls == []


def test_gateway_rejects_remote_or_credentialed_upstream_and_ambiguous_tokens():
    try:
        LocalGatewaySettings(ORDINARY, BOOTSTRAP, ORIGIN, HOST, ordinary_target="https://example.com")
    except GatewayConfigurationError as error:
        assert error.code == "gateway_upstream_invalid"
    else:
        raise AssertionError("remote upstream was accepted")
    try:
        LocalGatewaySettings(ORDINARY, ORDINARY, ORIGIN, HOST)
    except GatewayConfigurationError as error:
        assert error.code == "gateway_tokens_invalid"
    else:
        raise AssertionError("shared privilege token was accepted")


def test_gateway_rejects_unsafe_path_oversized_body_and_unsafe_response(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/chat"):
            return httpx.Response(200, content=b"raw", headers={"Content-Type": "application/octet-stream"})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("backend.local_gateway._MAX_REQUEST_BYTES", 8)
    with TestClient(_app(handler)) as client:
        body = client.post("/api/v1/chat", headers={"Host": HOST, "Origin": ORIGIN}, content=b"123456789")
        unsafe = client.post("/api/v1/chat", headers={"Host": HOST, "Origin": ORIGIN}, json={"q": 1})
        path = client.get("/api/v1/%2e%2e/private", headers={"Host": HOST})
    assert body.status_code == 413
    assert unsafe.status_code == 502
    assert unsafe.json()["error"]["code"] == "gateway_response_type_rejected"
    assert path.status_code in {400, 404}
    assert len(calls) == 1


def test_settings_repr_never_contains_backend_tokens():
    settings = _settings()
    assert ORDINARY not in repr(settings)
    assert BOOTSTRAP not in repr(settings)
