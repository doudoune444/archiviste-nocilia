"""Mistral embeddings wrapper. Batched encode, dimension assertion.

Uses `langchain_mistralai.MistralAIEmbeddings` (mistral-embed, dim 1024).
Fallback BAAI/bge-m3 self-host = V2 (cf vision.md Q7).
"""

from __future__ import annotations

import os
from typing import Final

from langchain_mistralai import MistralAIEmbeddings

EMBEDDING_DIM: Final = 1024
DEFAULT_MODEL_NAME: Final = "mistral-embed"
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
    """Wrapper around MistralAIEmbeddings(model='mistral-embed').

    AC-10: LLM_API_KEY (env var) is shared between LLM calls and embeddings
    (same Mistral API key — cf vision.md Q7).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL_NAME,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        # api_key defaults to MISTRAL_API_KEY / LLM_API_KEY env (langchain_mistralai picks it up).
        kwargs: dict[str, object] = {"model": model}
        if api_key is not None:
            kwargs["mistral_api_key"] = api_key
        if base_url is not None:
            kwargs["endpoint"] = base_url + "/v1"
        self._model_name = model
        self._client = MistralAIEmbeddings(**kwargs)

    @property
    def model_name(self) -> str:
        return self._model_name

    def encode_batch(self, texts: list[str], batch_size: int) -> list[list[float]]:
        """Encode `texts` in batches. Each vector has length EMBEDDING_DIM."""
        if not texts:
            return []
        if batch_size <= 0:
            msg = f"batch_size must be > 0, got {batch_size}"
            raise ValueError(msg)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            batch_vectors = self._client.embed_documents(chunk)
            vectors.extend(batch_vectors)
        for vector in vectors:
            if len(vector) != EMBEDDING_DIM:
                msg = f"expected embedding dim {EMBEDDING_DIM}, got {len(vector)}"
                raise RuntimeError(msg)
        return vectors
