"""Tests for FakeEmbedder — CI offline deterministic embedder.

Design (from INFRA-002d CI fix mission):
- (a) Determinism: same text → same vector across calls.
- (b) Dimension: output vectors have length EMBEDDING_DIM (1024).
- (c) L2-normalised: ||v|| == 1.0 within float tolerance.
- (d) Non-trivial: different texts produce different vectors.
"""

from __future__ import annotations

import math

import pytest

from archiviste_workers.embedder import (
    EMBEDDER_PROVIDER_ENV,
    EMBEDDING_DIM,
    FakeEmbedder,
    build_embedder,
)


@pytest.fixture
def fake() -> FakeEmbedder:
    return FakeEmbedder()


def test_determinism(fake: FakeEmbedder) -> None:
    # (a) Same text must yield identical vector on repeated calls.
    text = "Nocilia est une archiviste qui veille sur les lore-gaps."
    first = fake.encode_batch([text], batch_size=1)
    second = fake.encode_batch([text], batch_size=1)
    assert first == second


def test_dimension(fake: FakeEmbedder) -> None:
    # (b) Each vector must have exactly EMBEDDING_DIM (1024) components.
    vectors = fake.encode_batch(["alpha", "bravo", "charlie"], batch_size=8)
    assert len(vectors) == 3
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIM


def test_l2_normalised(fake: FakeEmbedder) -> None:
    # (c) ||v||_2 must equal 1.0 within float32 rounding tolerance.
    vectors = fake.encode_batch(["unit norm check"], batch_size=1)
    norm = math.sqrt(sum(x * x for x in vectors[0]))
    assert abs(norm - 1.0) < 1e-5


def test_distinct_texts_produce_distinct_vectors(fake: FakeEmbedder) -> None:
    # (d) Different input strings must not produce the same vector.
    vectors = fake.encode_batch(["hello world", "goodbye world"], batch_size=2)
    assert vectors[0] != vectors[1]


def test_empty_input_returns_empty(fake: FakeEmbedder) -> None:
    assert fake.encode_batch([], batch_size=4) == []


def test_invalid_batch_size_rejected(fake: FakeEmbedder) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        fake.encode_batch(["x"], batch_size=0)


def test_model_name(fake: FakeEmbedder) -> None:
    assert fake.model_name == "fake-embedder-ci"


def test_build_embedder_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # build_embedder with EMBEDDER_PROVIDER=fake returns a FakeEmbedder instance.
    monkeypatch.setenv(EMBEDDER_PROVIDER_ENV, "fake")
    embedder = build_embedder()
    assert isinstance(embedder, FakeEmbedder)


def test_build_embedder_invalid_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Any value outside {"mistral", "fake"} must raise ValueError at boot.
    monkeypatch.setenv(EMBEDDER_PROVIDER_ENV, "sentence-transformers")
    with pytest.raises(ValueError, match=EMBEDDER_PROVIDER_ENV):
        build_embedder()
