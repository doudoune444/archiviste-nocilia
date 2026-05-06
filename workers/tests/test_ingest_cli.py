"""CLI-level tests: argument validation and exit codes (AC-14, AC-17)."""

from __future__ import annotations

from pathlib import Path

import pytest

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
