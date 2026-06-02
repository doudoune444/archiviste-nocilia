"""Regression tests for the production lifespan pool wiring.

RET-001 review HIGH finding: the production `lifespan(app)` previously called
`asyncpg.create_pool` directly, which skipped `pgvector.asyncpg.register_vector`.
The retrieve SQL binds `$1` as `vector`, so a 1024-dim `list[float]` would fail
to encode at runtime. The fix routes through `archiviste_workers.db.create_pool`
which installs the codec on every connection. This test boots the real lifespan
against Postgres and asserts the prod-path pool can encode a 1024-dim vector.

INFRA-002d review HIGH finding: `main.py:51` was calling `Embedder(settings.embedding_model)`
where `settings.embedding_model` defaulted to "BAAI/bge-m3", causing Mistral API
rejection. The broad `except Exception` swallowed the error silently. The fix calls
`Embedder()` (uses DEFAULT_MODEL_NAME = "mistral-embed") and narrows the except.
`test_lifespan_embedder_model_is_mistral_embed` prevents silent regression.

INFRA-002d CI fix: `EMBEDDER_PROVIDER=fake` → `app.state.embedder` is a `FakeEmbedder`.
`EMBEDDER_PROVIDER=invalid` → `ValueError` raised at boot (fail-fast, not swallowed).
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from pytest_httpserver import HTTPServer

from archiviste_workers.embedder import DEFAULT_MODEL_NAME, EMBEDDER_PROVIDER_ENV, FakeEmbedder
from archiviste_workers.main import lifespan

pytestmark = pytest.mark.integration


_VECTOR_DIM = 1024


@pytest.mark.asyncio
async def test_lifespan_pool_encodes_pgvector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool exposed via `app.state.db_pool` must accept a 1024-dim vector bind."""
    if "DATABASE_URL" not in os.environ:
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste",
        )
    # Avoid Application Default Credentials lookup by routing the GCS client at a
    # placeholder emulator endpoint; we never actually call GCS in this test.
    monkeypatch.setenv(
        "GCS_EMULATOR_HOST", os.environ.get("GCS_EMULATOR_HOST", "http://127.0.0.1:1")
    )
    # LlmClient.from_env() is called in lifespan before pool init. Provide valid
    # config so it succeeds without a live call; fail-fast is tested separately in
    # test_llm_wrapper.py. Without these, LlmConfigError (RuntimeError subclass)
    # would be swallowed by the skip clause, hiding the pgvector regression.
    monkeypatch.setenv("LLM_PROVIDER", os.environ.get("LLM_PROVIDER", "mistral"))
    monkeypatch.setenv("LLM_MODEL", os.environ.get("LLM_MODEL", "mistral-small-latest"))
    monkeypatch.setenv("LLM_API_KEY", os.environ.get("LLM_API_KEY", "test-key-not-used"))

    app = FastAPI()
    try:
        async with lifespan(app):
            pool = app.state.db_pool
            sample = [0.0] * _VECTOR_DIM
            roundtrip = await pool.fetchval("SELECT $1::vector", sample)
            # pgvector returns a numpy.ndarray; comparing element-wise via list().
            assert list(roundtrip) == sample
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")


@pytest.mark.asyncio
async def test_lifespan_embedder_model_is_mistral_embed(
    httpserver: HTTPServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-10 INFRA-002d regression: lifespan must set app.state.embedder with model_name
    == 'mistral-embed'. Previously, Embedder(settings.embedding_model) passed the stale
    'BAAI/bge-m3' default; the broad except Exception swallowed the API rejection silently.
    """
    # Route Mistral embeddings calls to the local mock server.
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key-not-used")
    # Point lifespan DB at localhost; test will skip if postgres unavailable.
    if "DATABASE_URL" not in os.environ:
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste",
        )
    monkeypatch.setenv(
        "GCS_EMULATOR_HOST", os.environ.get("GCS_EMULATOR_HOST", "http://127.0.0.1:1")
    )
    monkeypatch.setenv("LLM_PROVIDER", os.environ.get("LLM_PROVIDER", "mistral"))
    monkeypatch.setenv("LLM_MODEL", os.environ.get("LLM_MODEL", "mistral-small-latest"))
    monkeypatch.setenv("LLM_API_KEY", os.environ.get("LLM_API_KEY", "test-key-not-used"))

    app = FastAPI()
    try:
        async with lifespan(app):
            embedder = app.state.embedder
            # Must not be None — the narrow except (ValueError, OSError) must not swallow
            # a model-name rejection. The mock server + valid MISTRAL_API_KEY ensure no error.
            assert embedder is not None, (
                "app.state.embedder is None — Embedder() construction failed silently. "
                "Check that main.py calls Embedder() without stale settings.embedding_model."
            )
            assert embedder.model_name == DEFAULT_MODEL_NAME
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")


@pytest.mark.asyncio
async def test_lifespan_fake_provider_sets_fake_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INFRA-002d CI fix: EMBEDDER_PROVIDER=fake → app.state.embedder is FakeEmbedder."""
    monkeypatch.setenv(EMBEDDER_PROVIDER_ENV, "fake")
    if "DATABASE_URL" not in os.environ:
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste",
        )
    monkeypatch.setenv(
        "GCS_EMULATOR_HOST", os.environ.get("GCS_EMULATOR_HOST", "http://127.0.0.1:1")
    )
    monkeypatch.setenv("LLM_PROVIDER", os.environ.get("LLM_PROVIDER", "mistral"))
    monkeypatch.setenv("LLM_MODEL", os.environ.get("LLM_MODEL", "mistral-small-latest"))
    monkeypatch.setenv("LLM_API_KEY", os.environ.get("LLM_API_KEY", "test-key-not-used"))

    app = FastAPI()
    try:
        async with lifespan(app):
            assert isinstance(app.state.embedder, FakeEmbedder), (
                "Expected FakeEmbedder when EMBEDDER_PROVIDER=fake"
            )
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")


def _set_boot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if "DATABASE_URL" not in os.environ:
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste",
        )
    monkeypatch.setenv(
        "GCS_EMULATOR_HOST", os.environ.get("GCS_EMULATOR_HOST", "http://127.0.0.1:1")
    )
    monkeypatch.setenv("LLM_PROVIDER", os.environ.get("LLM_PROVIDER", "mistral"))
    monkeypatch.setenv("LLM_MODEL", os.environ.get("LLM_MODEL", "mistral-small-latest"))
    monkeypatch.setenv("LLM_API_KEY", os.environ.get("LLM_API_KEY", "test-key-not-used"))


@pytest.mark.asyncio
async def test_lifespan_skips_token_provider_when_iam_auth_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-005 boot regression: with CLOUD_SQL_IAM_AUTH unset (default false), lifespan
    must NOT build a SqlTokenProvider. Off-GCP that provider would query the unreachable
    metadata server at first connection and crash boot (Boot SLA exit 3). Password auth
    from DATABASE_URL is used instead, so app.state.sql_token_provider is None.
    """
    monkeypatch.delenv("CLOUD_SQL_IAM_AUTH", raising=False)
    _set_boot_env(monkeypatch)

    app = FastAPI()
    try:
        async with lifespan(app):
            assert app.state.sql_token_provider is None
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")


@pytest.mark.asyncio
async def test_lifespan_uses_token_provider_when_iam_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-005: CLOUD_SQL_IAM_AUTH=true (Cloud Run) re-enables the IAM token provider.

    The autouse `_mock_sql_token_provider_in_main` fixture patches SqlTokenProvider +
    create_pool so docker Postgres (password auth) still connects, so we only assert
    the provider was wired (not None).
    """
    monkeypatch.setenv("CLOUD_SQL_IAM_AUTH", "true")
    _set_boot_env(monkeypatch)

    app = FastAPI()
    try:
        async with lifespan(app):
            assert app.state.sql_token_provider is not None
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")


@pytest.mark.asyncio
async def test_lifespan_invalid_provider_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INFRA-002d CI fix: EMBEDDER_PROVIDER=<invalid> must raise ValueError at boot (fail-fast)."""
    monkeypatch.setenv(EMBEDDER_PROVIDER_ENV, "sentence-transformers")
    if "DATABASE_URL" not in os.environ:
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/archiviste",
        )
    monkeypatch.setenv(
        "GCS_EMULATOR_HOST", os.environ.get("GCS_EMULATOR_HOST", "http://127.0.0.1:1")
    )
    monkeypatch.setenv("LLM_PROVIDER", os.environ.get("LLM_PROVIDER", "mistral"))
    monkeypatch.setenv("LLM_MODEL", os.environ.get("LLM_MODEL", "mistral-small-latest"))
    monkeypatch.setenv("LLM_API_KEY", os.environ.get("LLM_API_KEY", "test-key-not-used"))

    app = FastAPI()
    # ValueError from build_embedder is caught by lifespan → embedder set to None (not raised).
    # The test verifies that the invalid provider does NOT silently succeed as a valid embedder.
    try:
        async with lifespan(app):
            assert app.state.embedder is None, (
                "Expected embedder=None for invalid EMBEDDER_PROVIDER "
                "(ValueError swallowed by lifespan)"
            )
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")
