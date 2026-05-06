"""Unit tests for `archiviste_workers.embedder` (AC-8, AC-9)."""

from __future__ import annotations

import huggingface_hub
import pytest

from archiviste_workers.embedder import EMBEDDING_DIM, Embedder, default_batch_size


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return Embedder()


def test_encode_batch_returns_1024_dim_vectors(embedder: Embedder) -> None:
    # AC-8: dim exactly 1024.
    vectors = embedder.encode_batch(["alpha", "bravo", "charlie"], batch_size=2)
    assert len(vectors) == 3
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIM


def test_encode_empty_batch_returns_empty_list(embedder: Embedder) -> None:
    assert embedder.encode_batch([], batch_size=4) == []


def test_invalid_batch_size_rejected(embedder: Embedder) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        embedder.encode_batch(["x"], batch_size=0)


def test_no_network_after_warmup(embedder: Embedder, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-9: after warm-up the second encode never triggers an HF download."""
    embedder.encode_batch(["warmup"], batch_size=1)

    def explode(*_args: object, **_kwargs: object) -> None:
        msg = "network call attempted after warmup"
        raise AssertionError(msg)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", explode, raising=False)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", explode, raising=False)

    second = embedder.encode_batch(["after warmup"], batch_size=1)
    assert len(second) == 1
    assert len(second[0]) == EMBEDDING_DIM


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
