"""bge-m3 embedder wrapper. Singleton model load, batched encode, dimension assertion."""

from __future__ import annotations

import os
from typing import Final

from sentence_transformers import SentenceTransformer

EMBEDDING_DIM: Final = 1024
DEFAULT_MODEL_NAME: Final = "BAAI/bge-m3"
DEFAULT_BATCH_SIZE_ENV: Final = "EMBED_BATCH_SIZE"
DEFAULT_BATCH_SIZE: Final = 32


def default_batch_size() -> int:
    """Read `EMBED_BATCH_SIZE` from env, fall back to 32. Reject non-positive values."""
    raw = os.environ.get(DEFAULT_BATCH_SIZE_ENV)
    if raw is None or raw == "":
        return DEFAULT_BATCH_SIZE
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"{DEFAULT_BATCH_SIZE_ENV} must be an integer, got {raw!r}"
        raise ValueError(msg) from exc
    if value <= 0:
        msg = f"{DEFAULT_BATCH_SIZE_ENV} must be > 0, got {value}"
        raise ValueError(msg)
    return value


class Embedder:
    """Lazy-singleton wrapper around `SentenceTransformer('BAAI/bge-m3')`."""

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)

    @property
    def model_name(self) -> str:
        return self._model_name

    def encode_batch(self, texts: list[str], batch_size: int) -> list[list[float]]:
        """Encode `texts` with the configured model. Each vector has length EMBEDDING_DIM."""
        if not texts:
            return []
        if batch_size <= 0:
            msg = f"batch_size must be > 0, got {batch_size}"
            raise ValueError(msg)
        raw = self._model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vectors: list[list[float]] = [list(map(float, row)) for row in raw]
        for vector in vectors:
            if len(vector) != EMBEDDING_DIM:
                msg = f"expected embedding dim {EMBEDDING_DIM}, got {len(vector)}"
                raise RuntimeError(msg)
        return vectors
