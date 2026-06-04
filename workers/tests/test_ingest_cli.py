"""CLI-level tests: argument validation and exit codes (AC-6, AC-14, AC-17)."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import archiviste_workers.ingest.cli as _cli_module
from archiviste_workers.ingest import cli
from archiviste_workers.ingest.pipeline import ProcessResult, ProcessStatus


# AC-6: --path outside --root → non-zero exit + exact error message preserved
def test_path_outside_root_rejected(tmp_path: Path) -> None:
    """AC-6: --path outside --root → non-zero exit, message 'path must be relative to repo root'."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: T\n---\nbody")

    exit_code = cli.main(["--root", str(root), "--path", str(outside)])
    assert exit_code != 0


# AC-6: resolve_target accepts path inside --root
def test_resolve_target_accepts_path_inside_root(tmp_path: Path) -> None:
    """AC-6: path inside root → resolved without error."""
    root = tmp_path / "root"
    root.mkdir()
    inner = root / "lore" / "doc.md"
    inner.parent.mkdir()
    inner.write_text("ok")

    resolved = cli.resolve_target(str(inner), root.resolve())
    assert resolved == inner.resolve()


# AC-6: resolve_target rejects path outside root with exact message
def test_resolve_target_rejects_outside_root(tmp_path: Path) -> None:
    """AC-6: path outside root → ValueError, message 'path must be relative to repo root'."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "other.md"
    outside.write_text("ok")

    with pytest.raises(ValueError, match="path must be relative to repo root"):
        cli.resolve_target(str(outside), root.resolve())


# AC-6: source_path computed relative to --root (not .git/)
def test_source_path_relative_to_root(tmp_path: Path) -> None:
    """AC-6: --root /x --path /x/lore/foo.md → source_path == lore/foo.md."""
    root = tmp_path / "root"
    root.mkdir()
    lore = root / "lore"
    lore.mkdir()
    doc = lore / "foo.md"
    doc.write_text("ok")

    resolved = cli.resolve_target(str(doc), root.resolve())
    source_path = resolved.relative_to(root.resolve())
    assert str(source_path) == "lore/foo.md" or source_path.as_posix() == "lore/foo.md"


# AC-6: --root missing dir → non-zero exit
def test_missing_root_dir_exits_nonzero(tmp_path: Path) -> None:
    """AC-6: --root pointing to nonexistent dir → non-zero exit."""
    missing = tmp_path / "does_not_exist"
    exit_code = cli.main(["--root", str(missing), "--path", str(missing / "lore.md")])
    assert exit_code != 0


def test_iter_markdown_files_lists_only_md(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("c")
    files = list(cli.iter_markdown_files(tmp_path))
    names = sorted(file.name for file in files)
    assert names == ["a.md", "c.md"]


def test_run_counters_aggregates_results() -> None:
    counters = cli.RunCounters()
    counters.record(ProcessResult("a.md", ProcessStatus.INSERTED, chunks_count=2))
    counters.record(ProcessResult("b.md", ProcessStatus.UPDATED, chunks_count=3))
    counters.record(ProcessResult("c.md", ProcessStatus.SKIPPED, reason="unchanged"))
    counters.record(ProcessResult("d.md", ProcessStatus.ERROR, reason="boom"))
    assert counters.total == 4
    assert counters.inserted == 1
    assert counters.updated == 1
    assert counters.skipped == 1
    assert counters.errors == 1


# AC-6: no .git/ or find_repo_root reference in cli.py
def test_no_find_repo_root_in_cli_source() -> None:
    """AC-6: find_repo_root must not exist in cli.py."""
    source = inspect.getsource(_cli_module)
    assert "find_repo_root" not in source
    assert ".git" not in source


# OPS-005 AC-2 / AC-7: ingest CLI must wire the IAM token provider exactly like
# main.py lifespan (SEC-005) so the Cloud Run Job can authenticate to Cloud SQL.
# cloud_sql_iam_auth=True  → SqlTokenProvider constructed, non-None token_provider to create_pool.
# cloud_sql_iam_auth=False → no SqlTokenProvider, token_provider=None to create_pool.


def _make_fake_root(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal fake root dir with one lore markdown file."""
    root = tmp_path / "root"
    root.mkdir()
    lore = root / "lore"
    lore.mkdir()
    (lore / "doc.md").write_text("---\ntitle: T\n---\nbody")
    return root, lore


@pytest.mark.asyncio
async def test_run_async_passes_token_provider_when_iam_auth_enabled(
    tmp_path: Path,
) -> None:
    """OPS-005 AC-2/AC-7: cloud_sql_iam_auth=True → SqlTokenProvider built and passed."""
    root, target = _make_fake_root(tmp_path)

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()
    mock_provider = AsyncMock()
    mock_provider.aclose = AsyncMock()

    captured: dict[str, object] = {}

    async def fake_create_pool(
        database_url: str,
        *,
        token_provider: object = None,
        **_kwargs: object,
    ) -> object:
        captured["token_provider"] = token_provider
        return mock_pool

    async def fake_process_file(*_args: object, **_kwargs: object) -> ProcessResult:
        return ProcessResult("lore/doc.md", ProcessStatus.SKIPPED, reason="unchanged")

    with (
        patch("archiviste_workers.ingest.cli.Settings") as mock_settings_cls,
        patch("archiviste_workers.ingest.cli.SqlTokenProvider", return_value=mock_provider),
        patch("archiviste_workers.ingest.cli.create_pool", side_effect=fake_create_pool),
        patch("archiviste_workers.ingest.cli.Embedder", return_value=MagicMock()),
        patch("archiviste_workers.ingest.cli.build_chunker", return_value=MagicMock()),
        patch("archiviste_workers.ingest.cli.process_file", side_effect=fake_process_file),
    ):
        mock_settings = MagicMock()
        mock_settings.cloud_sql_iam_auth = True
        mock_settings.database_url = "postgresql+asyncpg://sa@localhost/archiviste"
        mock_settings_cls.return_value = mock_settings

        exit_code = await _cli_module._run_async(target, root, 32)

    assert exit_code == cli.EXIT_OK
    assert captured["token_provider"] is mock_provider, (
        "token_provider must be the SqlTokenProvider instance when cloud_sql_iam_auth=True"
    )
    mock_provider.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_async_passes_none_token_provider_when_iam_auth_disabled(
    tmp_path: Path,
) -> None:
    """OPS-005 AC-2/AC-7: cloud_sql_iam_auth=False → no SqlTokenProvider, None passed."""
    root, target = _make_fake_root(tmp_path)

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    captured: dict[str, object] = {}

    async def fake_create_pool(
        database_url: str,
        *,
        token_provider: object = None,
        **_kwargs: object,
    ) -> object:
        captured["token_provider"] = token_provider
        return mock_pool

    async def fake_process_file(*_args: object, **_kwargs: object) -> ProcessResult:
        return ProcessResult("lore/doc.md", ProcessStatus.SKIPPED, reason="unchanged")

    with (
        patch("archiviste_workers.ingest.cli.Settings") as mock_settings_cls,
        patch("archiviste_workers.ingest.cli.SqlTokenProvider") as mock_sql_token_provider_cls,
        patch("archiviste_workers.ingest.cli.create_pool", side_effect=fake_create_pool),
        patch("archiviste_workers.ingest.cli.Embedder", return_value=MagicMock()),
        patch("archiviste_workers.ingest.cli.build_chunker", return_value=MagicMock()),
        patch("archiviste_workers.ingest.cli.process_file", side_effect=fake_process_file),
    ):
        mock_settings = MagicMock()
        mock_settings.cloud_sql_iam_auth = False
        mock_settings.database_url = "postgresql+asyncpg://postgres:postgres@localhost/archiviste"
        mock_settings_cls.return_value = mock_settings

        exit_code = await _cli_module._run_async(target, root, 32)

    assert exit_code == cli.EXIT_OK
    assert captured["token_provider"] is None, (
        "token_provider must be None when cloud_sql_iam_auth=False"
    )
    mock_sql_token_provider_cls.assert_not_called()
