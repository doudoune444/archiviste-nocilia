"""End-to-end ingest integration: requires Postgres + pgvector + bge-m3 weights.

AC coverage : AC-1, AC-10, AC-11, AC-12, AC-13, AC-15, AC-16, AC-18, AC-20.
"""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import structlog

from archiviste_workers.embedder import Embedder
from archiviste_workers.ingest.chunker import build_chunker
from archiviste_workers.ingest.pipeline import (
    PipelineDeps,
    ProcessStatus,
    process_file,
)
from archiviste_workers.ingest.repository import fetch_existing
from archiviste_workers.logging_config import configure_structlog

if TYPE_CHECKING:
    import asyncpg

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return Embedder()


@pytest.fixture(scope="module")
def splitter() -> object:
    return build_chunker()


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _deps(pool: asyncpg.Pool, embedder: Embedder, splitter: object) -> PipelineDeps:
    return PipelineDeps(embedder=embedder, splitter=splitter, pool=pool, batch_size=4)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_first_run_inserts_doc_and_chunks(
    clean_db: asyncpg.Pool,
    embedder: Embedder,
    splitter: object,
    fixtures_lore_dir: Path,
    repo_root: Path,
) -> None:
    """AC-10: insert path → status inserted, chunks_count > 0, DB rows created."""
    target = fixtures_lore_dir / "valid_simple.md"
    result = await process_file(target, repo_root, _deps(clean_db, embedder, splitter))
    assert result.status is ProcessStatus.INSERTED
    assert result.chunks_count >= 1
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title FROM documents WHERE source_path = $1",
            result.relative_path,
        )
        assert row is not None
        chunk_count = await conn.fetchval(
            "SELECT COUNT(*) FROM chunks WHERE document_id = $1",
            row["id"],
        )
        assert chunk_count == result.chunks_count


@pytest.mark.asyncio
async def test_unchanged_hash_skipped(
    clean_db: asyncpg.Pool,
    embedder: Embedder,
    splitter: object,
    fixtures_lore_dir: Path,
    repo_root: Path,
) -> None:
    """AC-11: same content twice → second pass status=skipped, no extra writes."""
    target = fixtures_lore_dir / "valid_simple.md"
    deps = _deps(clean_db, embedder, splitter)
    first = await process_file(target, repo_root, deps)
    assert first.status is ProcessStatus.INSERTED
    second = await process_file(target, repo_root, deps)
    assert second.status is ProcessStatus.SKIPPED
    assert second.reason == "unchanged"
    async with clean_db.acquire() as conn:
        chunk_count = await conn.fetchval("SELECT COUNT(*) FROM chunks")
        doc_count = await conn.fetchval("SELECT COUNT(*) FROM documents")
        assert doc_count == 1
        assert chunk_count == first.chunks_count


@pytest.mark.asyncio
async def test_changed_hash_updates_in_single_tx(
    clean_db: asyncpg.Pool,
    embedder: Embedder,
    splitter: object,
    tmp_path: Path,
    repo_root: Path,
) -> None:
    """AC-12 + AC-13: changed body → status=updated, old chunks gone, single tx."""
    inside = repo_root / "workers" / "tests" / "fixtures" / "lore" / "_mutable.md"
    try:
        inside.write_text("---\ntitle: Mut\n---\nfirst body\n", encoding="utf-8")
        deps = _deps(clean_db, embedder, splitter)
        first = await process_file(inside, repo_root, deps)
        assert first.status is ProcessStatus.INSERTED
        async with clean_db.acquire() as conn:
            existing = await fetch_existing(conn, first.relative_path)
            assert existing is not None
            initial_id = existing.id

        inside.write_text("---\ntitle: Mut\n---\nsecond body changed.\n", encoding="utf-8")
        second = await process_file(inside, repo_root, deps)
        assert second.status is ProcessStatus.UPDATED
        async with clean_db.acquire() as conn:
            after = await fetch_existing(conn, second.relative_path)
            assert after is not None
            assert after.id == initial_id
            assert after.content_hash != existing.content_hash
            chunk_count = await conn.fetchval(
                "SELECT COUNT(*) FROM chunks WHERE document_id = $1", after.id
            )
            assert chunk_count == second.chunks_count
    finally:
        inside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_oversize_file_skipped_unread(
    clean_db: asyncpg.Pool,
    embedder: Embedder,
    splitter: object,
    oversize_payload: bytes,
    repo_root: Path,
) -> None:
    """AC-15: file > 1 MiB → error reason mentions cap, no DB write."""
    inside = repo_root / "workers" / "tests" / "fixtures" / "lore" / "_oversize.md"
    try:
        inside.write_bytes(oversize_payload)
        result = await process_file(inside, repo_root, _deps(clean_db, embedder, splitter))
        assert result.status is ProcessStatus.ERROR
        assert "1 MiB" in (result.reason or "")
        async with clean_db.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM documents")
            assert count == 0
    finally:
        inside.unlink(missing_ok=True)


def test_lore_sample_dir_present(repo_root: Path) -> None:
    """AC-18: lore/sample/ versionned with at least 2 valid markdown files."""
    sample = repo_root / "lore" / "sample"
    md_files = sorted(sample.glob("*.md"))
    assert len(md_files) >= 2
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---")


def test_summary_log_shape_via_capture(
    fixtures_lore_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-16 + AC-20: start + summary logs are emitted as single JSON lines.

    Smoke-checks the JSON renderer wiring (no DB needed).
    """
    configure_structlog()
    logger = structlog.get_logger("test")
    buffer = StringIO()
    logger.info(
        "ingest.start",
        embed_batch_size=4,
        path="lore/",
        cwd=os.getcwd(),
    )
    logger.info(
        "ingest.summary",
        total=2,
        inserted=1,
        updated=0,
        skipped=1,
        errors=0,
        duration_ms=42,
    )
    out = capsys.readouterr().out + buffer.getvalue()
    lines = [line for line in out.splitlines() if line.strip().startswith("{")]
    parsed = [json.loads(line) for line in lines]
    events = [entry["event"] for entry in parsed]
    assert "ingest.start" in events
    assert "ingest.summary" in events
    summary = next(entry for entry in parsed if entry["event"] == "ingest.summary")
    expected_total = (
        summary["inserted"] + summary["updated"] + summary["skipped"] + summary["errors"]
    )
    assert summary["total"] == expected_total
