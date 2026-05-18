"""Unit tests for `archiviste_workers.embedder` with Mistral embeddings backend.

AC-10: workers use mistral-embed (dim 1024) as default embedder.
       encode_batch returns vectors of length EMBEDDING_DIM.
       RuntimeError raised if API returns wrong dimension.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from archiviste_workers.embedder import (
    DEFAULT_MODEL_NAME,
    EMBEDDING_DIM,
    Embedder,
    default_batch_size,
)


def _mistral_embed_response(count: int) -> Response:
    """Build a Mistral embeddings API response with `count` 1024-dim zero vectors."""
    payload: dict[str, Any] = {
        "id": "embd-test",
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": [0.0] * 1024} for i in range(count)
        ],
        "model": "mistral-embed",
        "usage": {"prompt_tokens": count, "total_tokens": count},
    }
    return Response(json.dumps(payload), status=200, content_type="application/json")


def _mistral_wrong_dim_response(count: int, dim: int) -> Response:
    """Mistral response with wrong dimension to exercise RuntimeError path."""
    payload: dict[str, Any] = {
        "id": "embd-test",
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": [0.0] * dim} for i in range(count)
        ],
        "model": "mistral-embed",
        "usage": {"prompt_tokens": count, "total_tokens": count},
    }
    return Response(json.dumps(payload), status=200, content_type="application/json")


@pytest.fixture
def embedder(httpserver: HTTPServer) -> Embedder:
    """AC-10: Embedder backed by a local mock HTTP server (no real Mistral call)."""
    httpserver.expect_request("/v1/embeddings", method="POST").respond_with_handler(
        lambda req: _mistral_embed_response(len(json.loads(req.data)["input"]))
    )
    return Embedder(api_key="test-key", base_url=httpserver.url_for(""))


def test_default_model_name_is_mistral_embed() -> None:
    # AC-10: default model must be mistral-embed, not BAAI/bge-m3.
    assert DEFAULT_MODEL_NAME == "mistral-embed"


def test_encode_batch_returns_1024_dim_vectors(embedder: Embedder) -> None:
    # AC-10: dim exactly 1024 for each returned vector.
    vectors = embedder.encode_batch(["alpha", "bravo", "charlie"], batch_size=2)
    assert len(vectors) == 3
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIM


def test_encode_empty_batch_returns_empty_list(embedder: Embedder) -> None:
    # AC-10: empty input → empty output, no API call.
    assert embedder.encode_batch([], batch_size=4) == []


def test_invalid_batch_size_rejected(embedder: Embedder) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        embedder.encode_batch(["x"], batch_size=0)


def test_batch_size_param_respected(httpserver: HTTPServer) -> None:
    # AC-10: encode_batch splits input into chunks of batch_size and calls API per chunk.
    call_sizes: list[int] = []

    def handler(req: Request) -> Response:
        body = json.loads(req.data)
        call_sizes.append(len(body["input"]))
        return _mistral_embed_response(len(body["input"]))

    httpserver.expect_request("/v1/embeddings", method="POST").respond_with_handler(handler)
    embedder = Embedder(api_key="test-key", base_url=httpserver.url_for(""))

    embedder.encode_batch(["a", "b", "c", "d", "e"], batch_size=2)
    # 5 texts with batch_size=2 → calls of sizes [2, 2, 1]
    assert call_sizes == [2, 2, 1]


def test_wrong_dim_raises_runtime_error(httpserver: HTTPServer) -> None:
    # AC-10: RuntimeError if API returns dimension != 1024.
    httpserver.expect_request("/v1/embeddings", method="POST").respond_with_handler(
        lambda req: _mistral_wrong_dim_response(len(json.loads(req.data)["input"]), 768)
    )
    embedder = Embedder(api_key="test-key", base_url=httpserver.url_for(""))
    with pytest.raises(RuntimeError, match="expected embedding dim"):
        embedder.encode_batch(["x"], batch_size=1)


def test_default_batch_size_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_BATCH_SIZE", "8")
    assert default_batch_size() == 8


def test_default_batch_size_rejects_non_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_BATCH_SIZE", "0")
    with pytest.raises(ValueError, match="EMBED_BATCH_SIZE"):
        default_batch_size()


def test_default_batch_size_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBED_BATCH_SIZE", raising=False)
    assert default_batch_size() == 32
