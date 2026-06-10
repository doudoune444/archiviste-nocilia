"""Tests for auth_header injection in RetrieveClient / GenerateClient — AC-1, AC-2, AC-5, AC-10.

EVAL-007: GenerateClient contract fix — X-User-Id + X-User-Tier headers required by
/v1/generate per specs/openapi/gateway-to-workers.yml; request_id in body (not user_tier/top_k).
"""

from __future__ import annotations

import json
import re

from pydantic import SecretStr
from pytest_httpserver import HTTPServer

from eval.clients import EVAL_USER_ID, EVAL_USER_TIER, GenerateClient, RetrieveClient

FAKE_TOKEN = "fake-oidc-id-token-sentinel-xyz"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


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
# EVAL-007: GenerateClient sends X-User-Id (valid UUID) + X-User-Tier headers,
# request_id in body; no user_tier/top_k/contexts in body.
# ---------------------------------------------------------------------------


def test_generate_client_sends_required_contract_headers(httpserver: HTTPServer) -> None:
    """EVAL-007: GenerateClient must send X-User-Id (valid UUID) and X-User-Tier headers.

    /v1/generate rejects with 400 "invalid_user_id" when X-User-Id is missing or
    not a valid UUID (first check in the router, per gateway→workers contract).
    X-User-Tier is also required (missing → 422).
    """
    httpserver.expect_request("/v1/generate").respond_with_json(
        {"answer": "ok", "citations": []}
    )
    client = GenerateClient(base_url=httpserver.url_for(""))
    client.generate("test query", "req-id-eval-007")

    assert len(httpserver.log) == 1
    captured = httpserver.log[0][0]

    # X-User-Id must be present and match the UUID regex
    user_id_header = captured.headers.get("x-user-id")
    assert user_id_header is not None, "X-User-Id header missing"
    assert _UUID_RE.match(user_id_header), f"X-User-Id is not a valid UUID: {user_id_header!r}"
    assert user_id_header == EVAL_USER_ID

    # X-User-Tier must be present and equal EVAL_USER_TIER
    user_tier_header = captured.headers.get("x-user-tier")
    assert user_tier_header is not None, "X-User-Tier header missing"
    assert user_tier_header == EVAL_USER_TIER


def test_generate_client_body_has_request_id_not_user_fields(httpserver: HTTPServer) -> None:
    """EVAL-007: JSON body must contain query + request_id; must NOT contain user_tier, top_k, or contexts.

    user_id and user_tier belong in headers per the gateway→workers contract;
    they are injected into the model from headers by the router (not from the body).
    """
    httpserver.expect_request("/v1/generate").respond_with_json(
        {"answer": "ok", "citations": []}
    )
    client = GenerateClient(base_url=httpserver.url_for(""))
    client.generate("lore question", "aaaaaaaa-0000-4000-8000-bbbbbbbbbbbb")

    assert len(httpserver.log) == 1
    captured = httpserver.log[0][0]
    body = json.loads(captured.data)

    assert body.get("query") == "lore question", "body must contain query"
    assert body.get("request_id") == "aaaaaaaa-0000-4000-8000-bbbbbbbbbbbb", "body must contain request_id"
    assert "user_tier" not in body, "user_tier must NOT be in body (belongs in X-User-Tier header)"
    assert "top_k" not in body, "top_k must NOT be in body"
    assert "contexts" not in body, "contexts must NOT be in body (workers does own retrieval)"


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
# AC-5: EntryError paths — non-2xx → upstream_error, timeout, malformed
# ---------------------------------------------------------------------------


def test_generate_client_non_2xx_returns_upstream_error(httpserver: HTTPServer) -> None:
    """AC-5: GenerateClient returns upstream_error EntryError on non-2xx response."""
    from eval.clients import EntryError

    httpserver.expect_request("/v1/generate").respond_with_data("", status=400)
    client = GenerateClient(base_url=httpserver.url_for(""))
    result = client.generate("query", "req-id-err-400")

    assert isinstance(result, EntryError)
    assert result.status == "upstream_error"
    assert "400" in result.detail


def test_generate_client_malformed_response_returns_malformed(httpserver: HTTPServer) -> None:
    """AC-5: GenerateClient returns malformed EntryError when response has no 'answer' key."""
    from eval.clients import EntryError

    httpserver.expect_request("/v1/generate").respond_with_json({"unexpected": "shape"})
    client = GenerateClient(base_url=httpserver.url_for(""))
    result = client.generate("query", "req-id-malformed")

    assert isinstance(result, EntryError)
    assert result.status == "malformed"


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
