"""Tests for eval/baseline_skip.py — AC-17."""

from __future__ import annotations

import subprocess
from pathlib import Path

from eval.baseline_skip import should_skip_gate_b


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one initial commit (simulates origin/main)."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        check=True, capture_output=True, cwd=str(tmp_path),
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        check=True, capture_output=True, cwd=str(tmp_path),
    )
    # Initial commit (acts as origin/main branch tip)
    readme = tmp_path / "README.md"
    readme.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], check=True, capture_output=True, cwd=str(tmp_path))
    subprocess.run(
        ["git", "commit", "-m", "chore: initial"],
        check=True, capture_output=True, cwd=str(tmp_path),
    )
    # Create a local remote ref so merge-base can resolve origin/main
    subprocess.run(
        ["git", "branch", "origin/main"],
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


# AC-17 case (b): message matches + other file also changed in same commit → do NOT skip
def test_should_not_skip_gate_b_when_other_files_changed(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    # Commit both baseline.json and another file in the same commit
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


# AC-17: message case-insensitive match
def test_should_skip_gate_b_case_insensitive(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    _commit_file(repo, "eval/baseline.json", '{"metrics": {}}', "CHORE(EVAL): BUMP BASELINE")
    assert should_skip_gate_b(repo) is True


# AC-17 security: multi-commit attack — malicious commit buried before final baseline-bump.
# The full PR diff (merge-base..HEAD) reveals both files → gate B must NOT be skipped.
def test_should_not_skip_gate_b_multicommit_attack(tmp_path: Path) -> None:
    """AC-17: PR with a non-baseline commit followed by baseline-bump must NOT skip gate B."""
    repo = _make_git_repo(tmp_path)
    # Commit 1: some non-baseline change (simulates malicious or legitimate code change)
    _commit_file(repo, "eval/extra_module.py", "# extra module\n", "feat: add extra module")
    # Commit 2: final commit is a valid baseline-bump by message and single-file diff
    _commit_file(repo, "eval/baseline.json", '{"metrics": {}}', "chore(eval): bump baseline")
    # Full PR diff (origin/main..HEAD) contains both extra_module.py and baseline.json → NOT skip
    assert should_skip_gate_b(repo) is False
