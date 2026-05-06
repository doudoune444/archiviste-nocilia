"""asyncpg persistence for `documents` + `chunks` tables. One transaction per file."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg
from pgvector.asyncpg import register_vector


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """Document fields persisted to the `documents` table (excluding generated id)."""

    source_path: str
    title: str
    tags: list[str]
    access_tier: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """Chunk text + 1024-dim embedding ready to insert."""

    ord: int
    text: str
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class ExistingDocument:
    """Subset of an existing row used to decide insert/skip/update."""

    id: UUID
    content_hash: str


async def fetch_existing(conn: asyncpg.Connection, source_path: str) -> ExistingDocument | None:
    """Return the matching document or None."""
    row = await conn.fetchrow(
        "SELECT id, content_hash FROM documents WHERE source_path = $1",
        source_path,
    )
    if row is None:
        return None
    return ExistingDocument(id=row["id"], content_hash=row["content_hash"])


async def insert_document_with_chunks(
    pool: asyncpg.Pool, doc: DocumentRecord, chunks: list[ChunkRecord]
) -> UUID:
    """Insert a new document + its chunks atomically. Returns the new document id."""
    async with pool.acquire() as conn:
        await register_vector(conn)
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO documents (source_path, title, tags, access_tier, content_hash)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                doc.source_path,
                doc.title,
                doc.tags,
                doc.access_tier,
                doc.content_hash,
            )
            document_id: UUID = row["id"]
            await _insert_chunks(conn, document_id, chunks)
            return document_id


async def update_document_replace_chunks(
    pool: asyncpg.Pool,
    document_id: UUID,
    doc: DocumentRecord,
    chunks: list[ChunkRecord],
) -> None:
    """Atomically replace chunks for an existing document and refresh metadata."""
    async with pool.acquire() as conn:
        await register_vector(conn)
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM chunks WHERE document_id = $1",
                document_id,
            )
            await conn.execute(
                """
                UPDATE documents
                SET title = $2,
                    tags = $3,
                    access_tier = $4,
                    content_hash = $5,
                    updated_at = NOW()
                WHERE id = $1
                """,
                document_id,
                doc.title,
                doc.tags,
                doc.access_tier,
                doc.content_hash,
            )
            await _insert_chunks(conn, document_id, chunks)


async def _insert_chunks(
    conn: asyncpg.Connection, document_id: UUID, chunks: list[ChunkRecord]
) -> None:
    if not chunks:
        return
    await conn.executemany(
        """
        INSERT INTO chunks (document_id, ord, text, embedding)
        VALUES ($1, $2, $3, $4)
        """,
        [(document_id, chunk.ord, chunk.text, chunk.embedding) for chunk in chunks],
    )
