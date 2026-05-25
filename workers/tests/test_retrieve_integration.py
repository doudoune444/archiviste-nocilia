"""Integration tests for `POST /v1/retrieve` (RET-001).

Covers AC-1, AC-7, AC-8, AC-9, AC-10, AC-11, AC-12, AC-14, AC-15. Requires Postgres
+ pgvector + bge-m3 weights (real `Embedder`).
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from archiviste_workers.db import create_pool
from archiviste_workers.embedder import Embedder
from archiviste_workers.retrieve.router import router as retrieve_router

if TYPE_CHECKING:
    import asyncpg

pytestmark = pytest.mark.integration

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture(scope="module")
def shared_embedder() -> Embedder:
    return Embedder()


async def _seed_doc(
    pool: asyncpg.Pool,
    *,
    source_path: str,
    text: str,
    access_tier: str,
    embedding: list[float],
) -> str:
    async with pool.acquire() as conn:
        doc_id = await conn.fetchval(
            """
            INSERT INTO documents (source_path, title, tags, access_tier, content_hash)
            VALUES ($1, $1, '{}'::text[], $2, $3)
            RETURNING id
            """,
            source_path,
            access_tier,
            f"hash-{source_path}",
        )
        await conn.execute(
            "INSERT INTO chunks (document_id, ord, text, embedding) VALUES ($1, 0, $2, $3)",
            doc_id,
            text,
            embedding,
        )
        return str(doc_id)


@pytest_asyncio.fixture
async def app_client(
    clean_db: asyncpg.Pool, shared_embedder: Embedder
) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(retrieve_router)
    app.state.db_pool = clean_db
    app.state.embedder = shared_embedder
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_empty_corpus_returns_empty_chunks(app_client: AsyncClient) -> None:
    """AC-1 + AC-12: empty table → 200 with empty chunks list, charset present."""
    response = await app_client.post(
        "/v1/retrieve",
        json={"query": "anything", "top_k": 5, "user_tier": "anonymous"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    body = response.json()
    assert body["chunks"] == []
    assert isinstance(body["embedding_ms"], int)
    assert isinstance(body["search_ms"], int)
    assert body["embedding_ms"] >= 0
    assert body["search_ms"] >= 0


@pytest.mark.asyncio
async def test_happy_path_returns_chunk_shape(
    app_client: AsyncClient,
    clean_db: asyncpg.Pool,
    shared_embedder: Embedder,
) -> None:
    """AC-9 + AC-10 + AC-15 + GEN-005-AC-2: returned shape includes access_tier, score in [0,1]."""
    [vec] = shared_embedder.encode_batch(["the archiviste keeps the lore"], batch_size=1)
    await _seed_doc(
        clean_db,
        source_path="lore/archiviste.md",
        text="the archiviste keeps the lore",
        access_tier="public",
        embedding=vec,
    )
    response = await app_client.post(
        "/v1/retrieve",
        json={"query": "archiviste lore", "top_k": 5, "user_tier": "anonymous"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["chunks"]) == 1
    chunk = body["chunks"][0]
    # GEN-005 AC-2: access_tier field now present in response.
    assert set(chunk.keys()) == {
        "chunk_id",
        "document_id",
        "source_path",
        "ord",
        "text",
        "score",
        "access_tier",
    }
    assert _UUID_RE.match(chunk["chunk_id"])
    assert _UUID_RE.match(chunk["document_id"])
    assert chunk["source_path"] == "lore/archiviste.md"
    assert chunk["ord"] == 0
    assert 0.0 <= chunk["score"] <= 1.0
    assert chunk["access_tier"] == "public"


@pytest.mark.asyncio
async def test_retrieve_returns_all_tiers_with_access_tier_field(
    app_client: AsyncClient,
    clean_db: asyncpg.Pool,
    shared_embedder: Embedder,
) -> None:
    """GEN-005 AC-2 + U-4: /v1/retrieve returns all tiers (no SQL ACL filter) + access_tier field.

    D-2: SQL ACL filter removed; filtering is now post-retrieval in services/acl.py.
    user_tier parameter still accepted but ignored for filtering (compat D-2).
    """
    [vec_pub] = shared_embedder.encode_batch(["public chunk"], batch_size=1)
    [vec_mem] = shared_embedder.encode_batch(["members chunk"], batch_size=1)
    [vec_aut] = shared_embedder.encode_batch(["author chunk"], batch_size=1)
    await _seed_doc(
        clean_db, source_path="p.md", text="public", access_tier="public", embedding=vec_pub
    )
    await _seed_doc(
        clean_db, source_path="m.md", text="members", access_tier="members", embedding=vec_mem
    )
    await _seed_doc(
        clean_db, source_path="a.md", text="author", access_tier="author_only", embedding=vec_aut
    )

    # D-2: even as anonymous, retrieve now returns all tiers (ACL moved post-retrieve).
    response = await app_client.post(
        "/v1/retrieve",
        json={"query": "members chunk", "top_k": 10, "user_tier": "anonymous"},
    )
    assert response.status_code == 200
    chunks = response.json()["chunks"]
    paths = {chunk["source_path"] for chunk in chunks}
    # All 3 tiers returned (no SQL filter anymore).
    assert paths == {"p.md", "m.md", "a.md"}
    # access_tier field present and correct on each chunk.
    tier_by_path = {chunk["source_path"]: chunk["access_tier"] for chunk in chunks}
    assert tier_by_path["p.md"] == "public"
    assert tier_by_path["m.md"] == "members"
    assert tier_by_path["a.md"] == "author_only"


@pytest.mark.asyncio
async def test_retrieve_all_tiers_returned_regardless_of_user_tier(
    app_client: AsyncClient,
    clean_db: asyncpg.Pool,
    shared_embedder: Embedder,
) -> None:
    """GEN-005 AC-2 + U-4: all user_tier values return the same unfiltered chunks."""
    [vec] = shared_embedder.encode_batch(["seed"], batch_size=1)
    await _seed_doc(clean_db, source_path="p.md", text="p", access_tier="public", embedding=vec)
    await _seed_doc(clean_db, source_path="m.md", text="m", access_tier="members", embedding=vec)
    await _seed_doc(
        clean_db, source_path="a.md", text="a", access_tier="author_only", embedding=vec
    )

    # Regardless of user_tier, retrieve returns all 3 tiers (D-2 post GEN-005).
    for user_tier in ("anonymous", "members", "author_only"):
        response = await app_client.post(
            "/v1/retrieve",
            json={"query": "seed", "top_k": 10, "user_tier": user_tier},
        )
        paths = {chunk["source_path"] for chunk in response.json()["chunks"]}
        assert paths == {"p.md", "m.md", "a.md"}, f"Failed for user_tier={user_tier}"


@pytest.mark.asyncio
async def test_top_k_caps_results_and_orders_by_score(
    app_client: AsyncClient,
    clean_db: asyncpg.Pool,
    shared_embedder: Embedder,
) -> None:
    """AC-10 + AC-11: len ≤ top_k, scores monotonically non-increasing, ids unique."""
    texts = [f"chunk text number {i} about archiviste" for i in range(6)]
    vectors = shared_embedder.encode_batch(texts, batch_size=4)
    for index, (text, vec) in enumerate(zip(texts, vectors, strict=True)):
        await _seed_doc(
            clean_db,
            source_path=f"doc-{index}.md",
            text=text,
            access_tier="public",
            embedding=vec,
        )
    response = await app_client.post(
        "/v1/retrieve",
        json={"query": "archiviste", "top_k": 3, "user_tier": "anonymous"},
    )
    chunks = response.json()["chunks"]
    assert len(chunks) <= 3
    scores = [chunk["score"] for chunk in chunks]
    assert scores == sorted(scores, reverse=True)
    ids = [chunk["chunk_id"] for chunk in chunks]
    assert len(ids) == len(set(ids))
    for score in scores:
        assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_database_unavailable_returns_503(shared_embedder: Embedder) -> None:
    """AC-14: closed pool → 503 database_unavailable, no DB error string in body."""
    # Use a dedicated pool we own + close, so the shared `clean_db` fixture stays
    # healthy for sibling tests in the module. Mirror conftest.db_pool skip gate
    # so this test (which doesn't consume `clean_db`) doesn't crash CI when
    # Postgres is unreachable.
    dsn = os.environ.get("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/archiviste")
    try:
        pool = await create_pool(dsn)
    except (OSError, RuntimeError, ConnectionError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")
    await pool.close()

    app = FastAPI()
    app.include_router(retrieve_router)
    app.state.db_pool = pool
    app.state.embedder = shared_embedder
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/v1/retrieve",
            json={"query": "x", "top_k": 1, "user_tier": "anonymous"},
        )
    body: dict[str, Any] = response.json()
    assert response.status_code == 503
    assert body == {"error": "database_unavailable"}
