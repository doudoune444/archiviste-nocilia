"""Per-file ingestion pipeline: read → parse → normalize → hash → chunk → embed → upsert."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import asyncpg
from langchain_text_splitters import TextSplitter

from archiviste_workers.embedder import Embedder
from archiviste_workers.ingest.frontmatter import (
    Frontmatter,
    FrontmatterError,
    parse_frontmatter,
)
from archiviste_workers.ingest.normalize import compute_content_hash, normalize_body
from archiviste_workers.ingest.repository import (
    ChunkRecord,
    DocumentRecord,
    fetch_existing,
    insert_document_with_chunks,
    update_document_replace_chunks,
)

MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024


class ProcessStatus(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Outcome of processing a single file (rendered to JSON log by the CLI)."""

    relative_path: str
    status: ProcessStatus
    chunks_count: int = 0
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineDeps:
    """Shared singletons passed into per-file processing."""

    embedder: Embedder
    splitter: TextSplitter
    pool: asyncpg.Pool
    batch_size: int


async def process_file(path: Path, repo_root: Path, deps: PipelineDeps) -> ProcessResult:
    """Drive a single file through the pipeline; never raises, returns a ProcessResult."""
    relative = _relative_path(path, repo_root)
    try:
        size = await asyncio.to_thread(lambda: path.stat().st_size)
    except OSError as exc:
        return ProcessResult(relative, ProcessStatus.ERROR, reason=f"stat failed: {exc}")
    if size > MAX_FILE_SIZE_BYTES:
        return ProcessResult(relative, ProcessStatus.ERROR, reason="file exceeds 1 MiB cap")
    try:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ProcessResult(relative, ProcessStatus.ERROR, reason=f"read failed: {exc}")
    try:
        frontmatter, body = parse_frontmatter(raw)
    except FrontmatterError as exc:
        return ProcessResult(relative, ProcessStatus.ERROR, reason=str(exc))
    normalized = normalize_body(body)
    content_hash = compute_content_hash(normalized)
    return await _persist(relative, frontmatter, normalized, content_hash, deps)


def _relative_path(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root).as_posix()


async def _persist(
    relative: str,
    frontmatter: Frontmatter,
    normalized: str,
    content_hash: str,
    deps: PipelineDeps,
) -> ProcessResult:
    doc = DocumentRecord(
        source_path=relative,
        title=frontmatter.title,
        tags=frontmatter.tags,
        access_tier=frontmatter.access_tier,
        content_hash=content_hash,
    )
    async with deps.pool.acquire() as conn:
        existing = await fetch_existing(conn, relative)
    if existing is not None and existing.content_hash == content_hash:
        return ProcessResult(relative, ProcessStatus.SKIPPED, reason="unchanged")
    chunks = _build_chunk_records(normalized, deps)
    try:
        if existing is None:
            await insert_document_with_chunks(deps.pool, doc, chunks)
            status = ProcessStatus.INSERTED
        else:
            await update_document_replace_chunks(deps.pool, existing.id, doc, chunks)
            status = ProcessStatus.UPDATED
    except (asyncpg.PostgresError, RuntimeError) as exc:
        return ProcessResult(relative, ProcessStatus.ERROR, reason=f"db error: {exc}")
    return ProcessResult(relative, status, chunks_count=len(chunks))


def _build_chunk_records(normalized: str, deps: PipelineDeps) -> list[ChunkRecord]:
    texts = deps.splitter.split_text(normalized)
    if not texts:
        return []
    embeddings = deps.embedder.encode_batch(texts, batch_size=deps.batch_size)
    return [
        ChunkRecord(ord=index, text=text, embedding=embedding)
        for index, (text, embedding) in enumerate(zip(texts, embeddings, strict=True))
    ]
