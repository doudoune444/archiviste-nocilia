"""Tests for retry-with-backoff in RetrieveClient / GenerateClient — EVAL-008."""

from __future__ import annotations

import pytest
from pytest_httpserver import HTTPServer

import eval.clients as clients_module
from eval.clients import EntryError, GenerateClient, RetrieveClient


def _make_sleep_recorder() -> tuple[list[float], object]:
    """Return (calls_list, callable) for monkeypatching _sleep."""
    calls: list[float] = []

    def record(seconds: float) -> None:
        calls.append(seconds)

    return calls, record


# ---------------------------------------------------------------------------
# EVAL-008 (a): 429 then 200 → success (retried)
# ---------------------------------------------------------------------------


def test_generate_client_retries_on_429_then_succeeds(
    httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL-008 (a): 429 on first attempt → retry → 200 returns GenerateResponse."""
    from eval.clients import GenerateResponse

    sleep_calls, sleep_fn = _make_sleep_recorder()
    monkeypatch.setattr(clients_module, "_sleep", sleep_fn)

    httpserver.expect_ordered_request("/v1/generate").respond_with_data("", status=429)
    httpserver.expect_ordered_request("/v1/generate").respond_with_json(
        {"answer": "retried ok", "citations": []}
    )

    client = GenerateClient(base_url=httpserver.url_for(""))
    result = client.generate("query", "req-retry-001")

    assert isinstance(result, GenerateResponse), f"expected GenerateResponse, got {result!r}"
    assert result.answer == "retried ok"
    assert len(sleep_calls) == 1, "exactly one sleep between attempts"


def test_retrieve_client_retries_on_429_then_succeeds(
    httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL-008 (a): RetrieveClient 429 then 200 → RetrieveResponse returned."""
    from eval.clients import RetrieveResponse

    _, sleep_fn = _make_sleep_recorder()
    monkeypatch.setattr(clients_module, "_sleep", sleep_fn)

    httpserver.expect_ordered_request("/v1/retrieve").respond_with_data("", status=429)
    httpserver.expect_ordered_request("/v1/retrieve").respond_with_json({"chunks": []})

    client = RetrieveClient(base_url=httpserver.url_for(""))
    result = client.search("query", "req-retry-002")

    assert isinstance(result, RetrieveResponse)


# ---------------------------------------------------------------------------
# EVAL-008 (b): 4 consecutive 429 → EntryError upstream_error after 4 attempts
# ---------------------------------------------------------------------------


def test_generate_client_exhausts_retries_on_repeated_429(
    httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL-008 (b): 4 consecutive 429 responses → EntryError(upstream_error)."""
    sleep_calls, sleep_fn = _make_sleep_recorder()
    monkeypatch.setattr(clients_module, "_sleep", sleep_fn)

    for _ in range(4):
        httpserver.expect_ordered_request("/v1/generate").respond_with_data("", status=429)

    client = GenerateClient(base_url=httpserver.url_for(""))
    result = client.generate("query", "req-exhaust-003")

    assert isinstance(result, EntryError)
    assert result.status == "upstream_error"
    # 4 attempts → 3 sleeps (no sleep after the last attempt — avoids unnecessary wait)
    assert len(sleep_calls) == 3


# ---------------------------------------------------------------------------
# EVAL-008 (c): non-retryable 400 → immediate EntryError, no retry
# ---------------------------------------------------------------------------


def test_generate_client_non_retryable_400_no_retry(
    httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL-008 (c): 400 is not in retry set → immediate EntryError, sleep never called."""
    sleep_calls, sleep_fn = _make_sleep_recorder()
    monkeypatch.setattr(clients_module, "_sleep", sleep_fn)

    httpserver.expect_ordered_request("/v1/generate").respond_with_data("", status=400)

    client = GenerateClient(base_url=httpserver.url_for(""))
    result = client.generate("query", "req-noretry-004")

    assert isinstance(result, EntryError)
    assert result.status == "upstream_error"
    assert "400" in result.detail
    assert len(sleep_calls) == 0, "must not sleep on non-retryable 400"
    assert len(httpserver.log) == 1, "must not retry: exactly one request sent"


def test_retrieve_client_non_retryable_400_no_retry(
    httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL-008 (c): RetrieveClient 400 → immediate EntryError, no retry."""
    sleep_calls, sleep_fn = _make_sleep_recorder()
    monkeypatch.setattr(clients_module, "_sleep", sleep_fn)

    httpserver.expect_ordered_request("/v1/retrieve").respond_with_data("", status=400)

    client = RetrieveClient(base_url=httpserver.url_for(""))
    result = client.search("query", "req-noretry-005")

    assert isinstance(result, EntryError)
    assert result.status == "upstream_error"
    assert len(sleep_calls) == 0
    assert len(httpserver.log) == 1


# ---------------------------------------------------------------------------
# EVAL-008 (d): Retry-After header honored
# ---------------------------------------------------------------------------


def test_generate_client_honors_retry_after_header(
    httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL-008 (d): 429 with Retry-After: 5 → _sleep called with 5.0 (not backoff)."""
    sleep_calls, sleep_fn = _make_sleep_recorder()
    monkeypatch.setattr(clients_module, "_sleep", sleep_fn)

    httpserver.expect_ordered_request("/v1/generate").respond_with_data(
        "", status=429, headers={"Retry-After": "5"}
    )
    httpserver.expect_ordered_request("/v1/generate").respond_with_json(
        {"answer": "after retry-after", "citations": []}
    )

    client = GenerateClient(base_url=httpserver.url_for(""))
    result = client.generate("query", "req-retryafter-006")

    from eval.clients import GenerateResponse

    assert isinstance(result, GenerateResponse)
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 5.0, (
        f"Retry-After: 5 must override backoff; _sleep called with {sleep_calls[0]}"
    )
