"""Regression tests for the production lifespan pool wiring.

RET-001 review HIGH finding: the production `lifespan(app)` previously called
`asyncpg.create_pool` directly, which skipped `pgvector.asyncpg.register_vector`.
The retrieve SQL binds `$1` as `vector`, so a 1024-dim `list[float]` would fail
to encode at runtime. The fix routes through `archiviste_workers.db.create_pool`
which installs the codec on every connection. This test boots the real lifespan
against Postgres and asserts the prod-path pool can encode a 1024-dim vector.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI

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

    app = FastAPI()
    try:
        async with lifespan(app):
            pool = app.state.db_pool
            sample = [0.0] * _VECTOR_DIM
            roundtrip = await pool.fetchval("SELECT $1::vector", sample)
            # pgvector returns a numpy.ndarray; comparing element-wise via list().
            assert list(roundtrip) == sample
    except (OSError, ConnectionError, RuntimeError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")
