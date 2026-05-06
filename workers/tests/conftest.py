"""Shared pytest fixtures for ingest tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

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
def _silence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSFORMERS_VERBOSITY", "error")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
