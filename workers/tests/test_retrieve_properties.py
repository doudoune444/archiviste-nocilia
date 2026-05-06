"""Property tests for `POST /v1/retrieve` (RET-001).

INV-3 : top-K retrieval is deterministic for a fixed query + index state.
INV-9 : every returned `score` is in `[0.0, 1.0]`.
INV-10: `len(chunks) <= top_k` and all `chunk_id`s are unique within a response.

Requires Postgres + pgvector + bge-m3 weights (real `Embedder`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from archiviste_workers.embedder import Embedder
from archiviste_workers.retrieve.router import router as retrieve_router

if TYPE_CHECKING:
    import asyncpg

pytestmark = [pytest.mark.integration, pytest.mark.slow]


@pytest.fixture(scope="module")
def shared_embedder() -> Embedder:
    return Embedder()


@pytest_asyncio.fixture
async def seeded_client(
    clean_db: asyncpg.Pool, shared_embedder: Embedder
) -> AsyncIterator[AsyncClient]:
    texts = [f"document number {idx} about the archiviste of nocilia" for idx in range(8)]
    vectors = shared_embedder.encode_batch(texts, batch_size=4)
    async with clean_db.acquire() as conn:
        for index, (text, vec) in enumerate(zip(texts, vectors, strict=True)):
            doc_id = await conn.fetchval(
                """
                INSERT INTO documents (source_path, title, tags, access_tier, content_hash)
                VALUES ($1, $1, '{}'::text[], 'public', $2)
                RETURNING id
                """,
                f"prop-doc-{index}.md",
                f"hash-{index}",
            )
            await conn.execute(
                "INSERT INTO chunks (document_id, ord, text, embedding) VALUES ($1, 0, $2, $3)",
                doc_id,
                text,
                vec,
            )
    app = FastAPI()
    app.include_router(retrieve_router)
    app.state.db_pool = clean_db
    app.state.embedder = shared_embedder
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
@given(top_k=st.integers(min_value=1, max_value=20))
@settings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_invariants_score_bounds_and_top_k(seeded_client: AsyncClient, top_k: int) -> None:
    """INV-9 + INV-10: score in [0,1], len ≤ top_k, chunk_ids unique."""
    response = await seeded_client.post(
        "/v1/retrieve",
        json={"query": "archiviste", "top_k": top_k, "user_tier": "anonymous"},
    )
    assert response.status_code == 200
    chunks = response.json()["chunks"]
    assert len(chunks) <= top_k
    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    assert len(chunk_ids) == len(set(chunk_ids))
    for chunk in chunks:
        assert 0.0 <= chunk["score"] <= 1.0


@pytest.mark.asyncio
@given(top_k=st.integers(min_value=1, max_value=10))
@settings(
    max_examples=4,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_invariant_deterministic_topk(seeded_client: AsyncClient, top_k: int) -> None:
    """INV-3: same query + same index → same ordered chunk_ids."""
    payload = {"query": "archiviste", "top_k": top_k, "user_tier": "anonymous"}
    first, second = await asyncio.gather(
        seeded_client.post("/v1/retrieve", json=payload),
        seeded_client.post("/v1/retrieve", json=payload),
    )
    first_ids = [chunk["chunk_id"] for chunk in first.json()["chunks"]]
    second_ids = [chunk["chunk_id"] for chunk in second.json()["chunks"]]
    assert first_ids == second_ids
