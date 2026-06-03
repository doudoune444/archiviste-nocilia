"""CLI-level tests: argument validation and exit codes (AC-14, AC-17)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import archiviste_workers.ingest.cli as _cli_module
from archiviste_workers.ingest import cli
from archiviste_workers.ingest.pipeline import ProcessResult, ProcessStatus


def test_path_outside_repo_root_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-14: absolute path outside the resolved repo root → non-zero exit + clear message."""
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: T\n---\nbody")
    exit_code = cli.main(["--path", str(outside)])
    assert exit_code != 0
    out = capsys.readouterr().out
    assert "path must be relative to repo root" in out


def test_resolve_target_accepts_path_inside_repo(tmp_path: Path) -> None:
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    (fake_repo / ".git").write_text("gitdir: ../bare")
    inner = fake_repo / "lore" / "doc.md"
    inner.parent.mkdir()
    inner.write_text("ok")
    repo_root = cli.find_repo_root(fake_repo)
    resolved = cli.resolve_target(str(inner), repo_root)
    assert resolved == inner.resolve()


def test_iter_markdown_files_lists_only_md(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("c")
    files = list(cli.iter_markdown_files(tmp_path))
    names = sorted(file.name for file in files)
    assert names == ["a.md", "c.md"]


def test_find_repo_root_raises_when_no_git(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="repo root"):
        cli.find_repo_root(tmp_path)


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


# OPS-005 AC-2 / AC-7: ingest CLI must wire the IAM token provider exactly like
# main.py lifespan (SEC-005) so the Cloud Run Job can authenticate to Cloud SQL.
# cloud_sql_iam_auth=True  → SqlTokenProvider constructed, non-None token_provider to create_pool.
# cloud_sql_iam_auth=False → no SqlTokenProvider, token_provider=None to create_pool.


def _make_fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo with one lore markdown file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: ../bare")
    lore = repo / "lore"
    lore.mkdir()
    (lore / "doc.md").write_text("---\ntitle: T\n---\nbody")
    return repo


@pytest.mark.asyncio
async def test_run_async_passes_token_provider_when_iam_auth_enabled(
    tmp_path: Path,
) -> None:
    """OPS-005 AC-2/AC-7: cloud_sql_iam_auth=True → SqlTokenProvider built and passed."""
    repo = _make_fake_repo(tmp_path)
    target = repo / "lore"

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

        exit_code = await _cli_module._run_async(target, repo, 32)

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
    repo = _make_fake_repo(tmp_path)
    target = repo / "lore"

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

        exit_code = await _cli_module._run_async(target, repo, 32)

    assert exit_code == cli.EXIT_OK
    assert captured["token_provider"] is None, (
        "token_provider must be None when cloud_sql_iam_auth=False"
    )
    mock_sql_token_provider_cls.assert_not_called()
