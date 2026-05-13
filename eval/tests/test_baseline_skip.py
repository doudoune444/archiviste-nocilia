"""Tests for eval/baseline_skip.py — AC-17."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eval.baseline_skip import should_skip_gate_b


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one initial commit."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        check=True, capture_output=True, cwd=str(tmp_path),
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        check=True, capture_output=True, cwd=str(tmp_path),
    )
    # Initial commit
    readme = tmp_path / "README.md"
    readme.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], check=True, capture_output=True, cwd=str(tmp_path))
    subprocess.run(
        ["git", "commit", "-m", "chore: initial"],
        check=True, capture_output=True, cwd=str(tmp_path),
    )
    return tmp_path


def _commit_file(repo: Path, filename: str, content: str, message: str) -> None:
    """Add a file and commit it."""
    filepath = repo / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], check=True, capture_output=True, cwd=str(repo))
    subprocess.run(
        ["git", "commit", "-m", message],
        check=True, capture_output=True, cwd=str(repo),
    )


# AC-17 case (a): message matches + only baseline.json changed → skip gate B
def test_should_skip_gate_b_when_baseline_bump_only(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    _commit_file(repo, "eval/baseline.json", '{"metrics": {}}', "chore(eval): bump baseline")
    assert should_skip_gate_b(repo) is True


# AC-17 case (b): message matches + other file also changed → do NOT skip
def test_should_not_skip_gate_b_when_other_files_changed(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    # Commit both baseline.json and another file
    (repo / "eval").mkdir(parents=True, exist_ok=True)
    (repo / "eval" / "baseline.json").write_text('{"metrics": {}}', encoding="utf-8")
    (repo / "other.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "eval/baseline.json", "other.py"],
        check=True, capture_output=True, cwd=str(repo),
    )
    subprocess.run(
        ["git", "commit", "-m", "chore(eval): bump baseline"],
        check=True, capture_output=True, cwd=str(repo),
    )
    assert should_skip_gate_b(repo) is False


# AC-17 case (c): other message + baseline.json changed → do NOT skip
def test_should_not_skip_gate_b_when_wrong_message(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    _commit_file(repo, "eval/baseline.json", '{"metrics": {}}', "feat(eval): some feature")
    assert should_skip_gate_b(repo) is False


# AC-17 : message case-insensitive match
def test_should_skip_gate_b_case_insensitive(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    _commit_file(repo, "eval/baseline.json", '{"metrics": {}}', "CHORE(EVAL): BUMP BASELINE")
    assert should_skip_gate_b(repo) is True
