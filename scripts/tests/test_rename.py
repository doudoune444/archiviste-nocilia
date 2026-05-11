"""Integration tests for gdrive_export.rename — AC-8, AC-9."""

import subprocess
from pathlib import Path

import pytest

from gdrive_export.rename import rename_local_file

# AC-8: Path.rename() pure, no subprocess in prod code.
# AC-9: git status --porcelain shows R after rename (similarity >= 50%).


def _git(args: list[str], cwd: Path) -> str:
    """Run a git command read-only and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> None:
    """Initialize a git repo with a committed file for rename testing."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )


@pytest.mark.integration
class TestRenameLocalFile:
    def test_basic_rename(self, tmp_path: Path) -> None:
        old = tmp_path / "old.md"
        new = tmp_path / "new.md"
        old.write_text("content", encoding="utf-8")
        rename_local_file(old, new)
        assert not old.exists()
        assert new.exists()
        assert new.read_text(encoding="utf-8") == "content"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        old = tmp_path / "old.md"
        new = tmp_path / "sub" / "dir" / "new.md"
        old.write_text("content", encoding="utf-8")
        rename_local_file(old, new)
        assert new.exists()

    def test_raises_if_old_absent(self, tmp_path: Path) -> None:
        old = tmp_path / "nonexistent.md"
        new = tmp_path / "new.md"
        with pytest.raises(FileNotFoundError):
            rename_local_file(old, new)

    def test_raises_if_new_exists(self, tmp_path: Path) -> None:
        old = tmp_path / "old.md"
        new = tmp_path / "new.md"
        old.write_text("old content", encoding="utf-8")
        new.write_text("existing content", encoding="utf-8")
        with pytest.raises(FileExistsError):
            rename_local_file(old, new)

    def test_git_detects_rename_as_r(self, tmp_path: Path) -> None:
        """AC-9: git status --porcelain shows 'R' after Path.rename() on tracked file."""
        _init_repo(tmp_path)
        old = tmp_path / "original.md"
        # Write enough content so similarity >= 50% is guaranteed
        old.write_text("# Title\n\nThis is a document.\n" * 10, encoding="utf-8")
        subprocess.run(["git", "add", "original.md"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add file"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        new = tmp_path / "renamed.md"
        rename_local_file(old, new)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        status = _git(["status", "--porcelain"], tmp_path)
        lines = status.strip().splitlines()
        rename_lines = [line for line in lines if line.startswith("R")]
        assert rename_lines, (
            f"Expected 'R' rename line in git status, got:\n{status}"
        )
