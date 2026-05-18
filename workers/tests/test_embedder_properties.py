"""Property test for INV-2: embedding dimension constant across documents.

Uses hypothesis with a mock Mistral HTTP server so no real API call is made.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from archiviste_workers.embedder import EMBEDDING_DIM, Embedder


def _make_embed_handler() -> object:
    def handler(req: Request) -> Response:
        body = json.loads(req.data)
        count = len(body["input"])
        payload = {
            "id": "prop-test",
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": [0.0] * 1024} for i in range(count)
            ],
            "model": "mistral-embed",
            "usage": {"prompt_tokens": count, "total_tokens": count},
        }
        return Response(json.dumps(payload), status=200, content_type="application/json")

    return handler


@pytest.fixture
def embedder(httpserver: HTTPServer) -> Embedder:
    """Function-scoped embedder backed by a mock server always returning 1024-dim vectors."""
    httpserver.expect_request("/v1/embeddings", method="POST").respond_with_handler(
        _make_embed_handler()
    )
    return Embedder(api_key="test-key", base_url=httpserver.url_for(""))


@given(
    texts=st.lists(
        st.text(
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
            min_size=1,
            max_size=100,
        ),
        min_size=1,
        max_size=4,
    )
)
@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_embeddings_share_constant_dim(embedder: Embedder, texts: list[str]) -> None:
    # INV-2: every embedding has length EMBEDDING_DIM regardless of input.
    vectors = embedder.encode_batch(texts, batch_size=2)
    assert len(vectors) == len(texts)
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIM
