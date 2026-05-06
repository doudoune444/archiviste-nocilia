"""Property test for INV-2: embedding dimension constant across documents."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from archiviste_workers.embedder import EMBEDDING_DIM, Embedder


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return Embedder()


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
