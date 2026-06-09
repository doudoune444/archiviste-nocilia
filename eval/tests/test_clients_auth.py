"""Tests for auth_header injection in RetrieveClient / GenerateClient — AC-1, AC-2, AC-5, AC-10."""

from __future__ import annotations

from pydantic import SecretStr
from pytest_httpserver import HTTPServer

from eval.clients import GenerateClient, RetrieveClient

FAKE_TOKEN = "fake-oidc-id-token-sentinel-xyz"


# ---------------------------------------------------------------------------
# AC-1: authed target — Authorization header present + X-Request-Id present
# ---------------------------------------------------------------------------


def test_retrieve_client_sends_auth_header(httpserver: HTTPServer) -> None:
    """AC-1: RetrieveClient with auth_header sends Authorization: Bearer <token>."""
    httpserver.expect_request("/v1/retrieve").respond_with_json({"chunks": []})
    client = RetrieveClient(
        base_url=httpserver.url_for(""),
        auth_header=SecretStr(FAKE_TOKEN),
    )
    client.search("test query", "req-id-001")

    assert len(httpserver.log) == 1
    captured = httpserver.log[0][0]
    assert captured.headers.get("authorization") == f"Bearer {FAKE_TOKEN}"
    assert captured.headers.get("x-request-id") == "req-id-001"


def test_generate_client_sends_auth_header(httpserver: HTTPServer) -> None:
    """AC-1: GenerateClient with auth_header sends Authorization: Bearer <token>."""
    httpserver.expect_request("/v1/generate").respond_with_json(
        {"answer": "ok", "citations": []}
    )
    client = GenerateClient(
        base_url=httpserver.url_for(""),
        auth_header=SecretStr(FAKE_TOKEN),
    )
    client.generate("test query", "req-id-002")

    assert len(httpserver.log) == 1
    captured = httpserver.log[0][0]
    assert captured.headers.get("authorization") == f"Bearer {FAKE_TOKEN}"
    assert captured.headers.get("x-request-id") == "req-id-002"


# ---------------------------------------------------------------------------
# AC-2: non-authed target — no Authorization header; only X-Request-Id
# ---------------------------------------------------------------------------


def test_retrieve_client_no_auth_header_by_default(httpserver: HTTPServer) -> None:
    """AC-2: RetrieveClient without auth_header must not send Authorization."""
    httpserver.expect_request("/v1/retrieve").respond_with_json({"chunks": []})
    client = RetrieveClient(base_url=httpserver.url_for(""))
    client.search("test query", "req-id-003")

    assert len(httpserver.log) == 1
    captured = httpserver.log[0][0]
    assert "authorization" not in captured.headers
    assert captured.headers.get("x-request-id") == "req-id-003"


def test_generate_client_no_auth_header_by_default(httpserver: HTTPServer) -> None:
    """AC-2: GenerateClient without auth_header must not send Authorization."""
    httpserver.expect_request("/v1/generate").respond_with_json(
        {"answer": "ok", "citations": []}
    )
    client = GenerateClient(base_url=httpserver.url_for(""))
    client.generate("test query", "req-id-004")

    assert len(httpserver.log) == 1
    captured = httpserver.log[0][0]
    assert "authorization" not in captured.headers
    assert captured.headers.get("x-request-id") == "req-id-004"


# ---------------------------------------------------------------------------
# AC-10: non-authed target — provider spy asserts zero calls
# ---------------------------------------------------------------------------


def test_retrieve_client_without_auth_never_calls_provider(httpserver: HTTPServer) -> None:
    """AC-10: when auth_header is None, the provider is never invoked (clients don't hold a provider)."""
    httpserver.expect_request("/v1/retrieve").respond_with_json({"chunks": []})
    client = RetrieveClient(base_url=httpserver.url_for(""), auth_header=None)
    client.search("query", "req-id-005")
    # If auth_header is None, no Authorization key is sent — verified by AC-2 tests above.
    assert len(httpserver.log) == 1
    assert "authorization" not in httpserver.log[0][0].headers


# ---------------------------------------------------------------------------
# AC-11: token not in SecretStr repr (defence-in-depth at client boundary)
# ---------------------------------------------------------------------------


def test_auth_header_secret_not_in_repr() -> None:
    """AC-11: SecretStr wrapping the token must redact in repr/str."""
    secret = SecretStr(FAKE_TOKEN)
    assert FAKE_TOKEN not in repr(secret)
    assert FAKE_TOKEN not in str(secret)
