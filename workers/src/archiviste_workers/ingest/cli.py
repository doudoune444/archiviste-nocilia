"""CLI entrypoint for `python -m archiviste_workers.ingest`."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from archiviste_workers.db import create_pool
from archiviste_workers.embedder import Embedder, default_batch_size
from archiviste_workers.ingest.chunker import build_chunker
from archiviste_workers.ingest.pipeline import (
    PipelineDeps,
    ProcessResult,
    ProcessStatus,
    process_file,
)
from archiviste_workers.logging_config import configure_structlog
from archiviste_workers.settings import Settings

EXIT_OK = 0
EXIT_FILE_ERRORS = 1
EXIT_INIT_FAILURE = 2

_LOGGER = structlog.get_logger("archiviste_workers.ingest")


@dataclass(slots=True)
class RunCounters:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[ProcessResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped + self.errors

    def record(self, result: ProcessResult) -> None:
        self.results.append(result)
        if result.status is ProcessStatus.INSERTED:
            self.inserted += 1
        elif result.status is ProcessStatus.UPDATED:
            self.updated += 1
        elif result.status is ProcessStatus.SKIPPED:
            self.skipped += 1
        else:
            self.errors += 1


def find_repo_root(start: Path) -> Path:
    """Walk up from `start` until a directory containing `.git/` is found."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate.resolve()
    msg = f"could not locate repo root (no .git/ above {start})"
    raise RuntimeError(msg)


def resolve_target(raw_path: str, repo_root: Path) -> Path:
    """Resolve `raw_path` and ensure it stays inside `repo_root`."""
    target = Path(raw_path).resolve(strict=True)
    if not target.is_relative_to(repo_root):
        msg = "path must be relative to repo root"
        raise ValueError(msg)
    return target


def iter_markdown_files(target: Path) -> Iterator[Path]:
    if target.is_file():
        if target.suffix == ".md":
            yield target
        return
    yield from sorted(target.rglob("*.md"))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_structlog()
    try:
        repo_root = find_repo_root(Path.cwd())
        target = resolve_target(args.path, repo_root)
        batch_size = default_batch_size()
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        _LOGGER.error("ingest.fatal", reason=str(exc))
        return EXIT_INIT_FAILURE if isinstance(exc, RuntimeError) else EXIT_FILE_ERRORS
    _LOGGER.info(
        "ingest.start",
        embed_batch_size=batch_size,
        path=target.relative_to(repo_root).as_posix(),
        cwd=str(Path.cwd()),
    )
    return asyncio.run(_run_async(target, repo_root, batch_size))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="archiviste_workers.ingest")
    parser.add_argument("--path", required=True, help="file or directory under repo root")
    return parser.parse_args(argv)


async def _run_async(target: Path, repo_root: Path, batch_size: int) -> int:
    settings = Settings()
    started_at = time.monotonic()
    try:
        embedder = Embedder()
        splitter = build_chunker()
        pool = await create_pool(settings.database_url)
    except (OSError, RuntimeError, ValueError) as exc:
        _LOGGER.error("ingest.fatal", reason=f"init failed: {exc}")
        return EXIT_INIT_FAILURE
    counters = RunCounters()
    try:
        deps = PipelineDeps(embedder=embedder, splitter=splitter, pool=pool, batch_size=batch_size)
        for path in iter_markdown_files(target):
            result = await process_file(path, repo_root, deps)
            _emit_document_log(result)
            counters.record(result)
    finally:
        await pool.close()
    duration_ms = int((time.monotonic() - started_at) * 1000)
    _LOGGER.info(
        "ingest.summary",
        total=counters.total,
        inserted=counters.inserted,
        updated=counters.updated,
        skipped=counters.skipped,
        errors=counters.errors,
        duration_ms=duration_ms,
    )
    return EXIT_FILE_ERRORS if counters.errors else EXIT_OK


def _emit_document_log(result: ProcessResult) -> None:
    fields: dict[str, object] = {
        "path": result.relative_path,
        "status": result.status.value,
        "chunks": result.chunks_count,
    }
    if result.reason is not None:
        fields["reason"] = result.reason
    _LOGGER.info("ingest.document", **fields)


if __name__ == "__main__":
    sys.exit(main())
