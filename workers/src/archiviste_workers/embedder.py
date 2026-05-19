"""Mistral embeddings wrapper. Batched encode, dimension assertion.

Uses `langchain_mistralai.MistralAIEmbeddings` (mistral-embed, dim 1024).
Fallback BAAI/bge-m3 self-host = V2 (cf vision.md Q7).

CI offline mode: set `EMBEDDER_PROVIDER=fake` to use FakeEmbedder (no API call).
Production default: `EMBEDDER_PROVIDER=mistral` (requires MISTRAL_API_KEY).
"""

from __future__ import annotations

import hashlib
import os
from typing import Final

import numpy as np
from langchain_mistralai import MistralAIEmbeddings
from pydantic import SecretStr

EMBEDDING_DIM: Final = 1024
DEFAULT_MODEL_NAME: Final = "mistral-embed"
DEFAULT_BATCH_SIZE_ENV: Final = "EMBED_BATCH_SIZE"
DEFAULT_BATCH_SIZE: Final = 32
# security.md A04: 30 s hard cap on external calls. Mirrors LLM_TIMEOUT_S in services/llm.py.
EMBED_TIMEOUT_S: Final = 30
_EMBED_MAX_RETRIES: Final = 3

EMBEDDER_PROVIDER_ENV: Final = "EMBEDDER_PROVIDER"
_PROVIDER_MISTRAL: Final = "mistral"
_PROVIDER_FAKE: Final = "fake"
_VALID_PROVIDERS: Final = frozenset({_PROVIDER_MISTRAL, _PROVIDER_FAKE})


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
    security.md A09: api_key wrapped in SecretStr to prevent leakage in logs/repr.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL_NAME,
        api_key: SecretStr | None = None,
        base_url: str | None = None,
    ) -> None:
        # api_key defaults to MISTRAL_API_KEY / LLM_API_KEY env (langchain_mistralai picks it up).
        # security.md A04: timeout + retries on every external call.
        kwargs: dict[str, object] = {
            "model": model,
            "timeout": EMBED_TIMEOUT_S,
            "max_retries": _EMBED_MAX_RETRIES,
        }
        if api_key is not None:
            kwargs["mistral_api_key"] = SecretStr(api_key.get_secret_value())
        if base_url is not None:
            kwargs["endpoint"] = base_url + "/v1"
        self._model_name = model
        self._client = MistralAIEmbeddings(**kwargs)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def client_timeout(self) -> int:
        """Timeout configured on the underlying Mistral client (seconds)."""
        return self._client.timeout

    @property
    def client_max_retries(self) -> int:
        """Max retries configured on the underlying Mistral client (>= 1)."""
        retries = self._client.max_retries
        return retries if retries is not None else 0

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


class FakeEmbedder:
    """Deterministic embedder for CI offline eval — no API call, no credentials required.

    CI-only: opt-in via EMBEDDER_PROVIDER=fake. Never used as a silent fallback.
    Algorithm: SHA-256 digest of text → uint32 seed → numpy RNG → float32 vector
    L2-normalised to unit length. Same text always yields the same vector.
    """

    _MODEL_NAME: Final = "fake-embedder-ci"

    @property
    def model_name(self) -> str:
        return self._MODEL_NAME

    def encode_batch(self, texts: list[str], batch_size: int) -> list[list[float]]:
        """Return deterministic unit vectors of length EMBEDDING_DIM for each text."""
        if not texts:
            return []
        if batch_size <= 0:
            msg = f"batch_size must be > 0, got {batch_size}"
            raise ValueError(msg)
        return [self._encode_one(text) for text in texts]

    def _encode_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        # Use the first 4 bytes as a uint32 seed — deterministic per text.
        seed = int.from_bytes(digest[:4], byteorder="big")
        rng = np.random.default_rng(seed)
        vector = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        norm: float = float(np.linalg.norm(vector))
        # norm == 0 is theoretically impossible for a 1024-dim gaussian, but guard it.
        if norm == 0.0:
            vector[0] = np.float32(1.0)
            norm = 1.0
        normalised: list[float] = (vector / norm).tolist()
        return normalised


def build_embedder(provider: str | None = None) -> Embedder | FakeEmbedder:
    """Factory that reads EMBEDDER_PROVIDER and returns the correct embedder.

    Valid values: "mistral" (default, requires MISTRAL_API_KEY) or "fake" (CI only).
    Any other value raises ValueError immediately at boot (fail-fast).
    """
    raw = (
        provider
        if provider is not None
        else os.environ.get(EMBEDDER_PROVIDER_ENV, _PROVIDER_MISTRAL)
    )
    if raw not in _VALID_PROVIDERS:
        msg = (
            f"{EMBEDDER_PROVIDER_ENV}={raw!r} is not a valid provider. "
            f"Valid values: {sorted(_VALID_PROVIDERS)}"
        )
        raise ValueError(msg)
    if raw == _PROVIDER_FAKE:
        return FakeEmbedder()
    return Embedder()
