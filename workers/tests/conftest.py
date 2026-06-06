"""Shared pytest fixtures for workers tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

# AC-12 indirectly: keep Settings() boot-compatible across the unit suite.
# GCS_BUCKET is required (no default). We do NOT set GCS_EMULATOR_HOST here
# because integration suites need to control it explicitly.
os.environ.setdefault("GCS_BUCKET", "archiviste-conversations-test")

import archiviste_workers.db as _db_module
from archiviste_workers.conversation.router import router as conversation_router
from archiviste_workers.db import create_pool

if TYPE_CHECKING:
    import asyncpg


FIXTURES_LORE_DIR = Path(__file__).parent / "fixtures" / "lore"
SAMPLE_LORE_DIR = Path(__file__).resolve().parents[2] / "lore" / "sample"


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgres://postgres:postgres@localhost:5432/archiviste",
    )


@pytest_asyncio.fixture
async def db_pool() -> AsyncIterator[asyncpg.Pool]:
    """Create a real asyncpg pool; skip the test if Postgres is unreachable."""
    try:
        pool = await create_pool(_database_url())
    except (OSError, RuntimeError, ConnectionError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def clean_db(db_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Pool]:
    """Truncate documents (CASCADE → chunks) before and after each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("TRUNCATE documents RESTART IDENTITY CASCADE")
    yield db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("TRUNCATE documents RESTART IDENTITY CASCADE")


@pytest.fixture
def fixtures_lore_dir() -> Path:
    # AC-19: integration fixtures live in tests/fixtures/lore/, not lore/sample/.
    assert FIXTURES_LORE_DIR.exists()
    assert FIXTURES_LORE_DIR.resolve() != SAMPLE_LORE_DIR.resolve()
    return FIXTURES_LORE_DIR


@pytest.fixture
def oversize_payload() -> bytes:
    payload = "---\ntitle: Big\n---\n" + ("a" * (1024 * 1024 + 32))
    return payload.encode("utf-8")


@pytest.fixture(autouse=True)
def _mock_sql_token_provider_in_main(request: pytest.FixtureRequest) -> Iterator[None]:
    """Patch SqlTokenProvider and create_pool in main.py for tests using lifespan().

    SEC-005: lifespan() now creates a SqlTokenProvider that fetches from
    metadata.google.internal — unavailable outside Cloud Run. Tests calling
    lifespan() directly need a mock that also strips token_provider from the
    create_pool call so docker-compose Postgres (password auth) still works.
    Tests in test_sql_pool.py create their own SqlTokenProvider instances
    directly (not patched here).

    Tests marked @pytest.mark.real_token_provider bypass this fixture so they
    can exercise the real token_provider= codepath against docker Postgres (AC-12(a) part 3).
    """
    if request.node.get_closest_marker("real_token_provider") is not None:
        yield
        return

    mock_provider = AsyncMock()
    mock_provider.get_or_refresh = AsyncMock(return_value=SecretStr("test-iam-token"))
    mock_provider.aclose = AsyncMock()

    _original_create_pool = _db_module.create_pool

    async def _create_pool_no_token(
        database_url: str,
        *,
        token_provider: object = None,
        min_size: int = 1,
        max_size: int = 2,
    ) -> object:
        # Strip token_provider so docker Postgres (password auth) can connect.
        return await _original_create_pool(database_url, min_size=min_size, max_size=max_size)

    with (
        patch("archiviste_workers.main.SqlTokenProvider", return_value=mock_provider),
        patch("archiviste_workers.main.create_pool", side_effect=_create_pool_no_token),
    ):
        yield


@pytest.fixture
def fake_repo() -> Any:
    repo = AsyncMock()
    repo.create_if_absent = AsyncMock()
    repo.increment_message_count = AsyncMock(return_value=1)
    return repo


@pytest.fixture
def fake_storage() -> Any:
    storage = AsyncMock()
    storage.gcs_uri = lambda cid: f"gs://archiviste-conversations-test/{cid}.md"
    storage.create_conversation_object = AsyncMock(return_value=1)
    storage.append_block = AsyncMock(return_value=(2, 128))
    return storage


@pytest_asyncio.fixture
async def worker_client(fake_repo: Any, fake_storage: Any) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(conversation_router)
    app.state.conversation_repo = fake_repo
    app.state.gcs_storage = fake_storage
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def reset_gcs_bucket_env() -> Iterator[None]:
    """Temporarily remove GCS_BUCKET so Settings() fails fast (AC-12)."""
    saved = os.environ.pop("GCS_BUCKET", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["GCS_BUCKET"] = saved


@pytest_asyncio.fixture
async def db_pool_required() -> AsyncIterator[asyncpg.Pool]:
    """Create a real asyncpg pool; fails the test (no skip) if Postgres is unreachable.

    AC-12 forbids pytest.skip fallback — CI must have docker-compose postgres running.
    """
    pool = await create_pool(_database_url())
    try:
        yield pool
    finally:
        await pool.close()
