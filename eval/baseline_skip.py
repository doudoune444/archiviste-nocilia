"""Determine if gate B should be skipped for a baseline-bump commit (AC-17)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

BUMP_PATTERN = re.compile(r"^chore\(eval\): bump baseline$", re.IGNORECASE)
BASELINE_FILE = "eval/baseline.json"


def should_skip_gate_b(repo_path: Path) -> bool:
    """Return True only when commit is an exclusive baseline bump (AC-17).

    Conditions (both required):
    (a) HEAD commit message matches ^chore(eval): bump baseline$ (case-insensitive).
    (b) Commit diff touches ONLY eval/baseline.json.

    PR_HEAD_SHA env var is injected by CI (OQ-2 resolution).
    Falls back to HEAD if env var absent (local runs).
    """
    head_sha = os.environ.get("PR_HEAD_SHA", "HEAD")

    commit_msg = _get_commit_message(repo_path, head_sha)
    if not BUMP_PATTERN.match(commit_msg.strip()):
        return False

    changed_files = _get_changed_files(repo_path, head_sha)
    return changed_files == [BASELINE_FILE]


def _get_commit_message(repo_path: Path, sha: str) -> str:
    result = subprocess.run(
        ["git", "show", "-s", "--format=%s", sha],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=True,
        timeout=10,
    )
    return result.stdout.strip()


def _get_changed_files(repo_path: Path, sha: str) -> list[str]:
    parent = f"{sha}^"
    result = subprocess.run(
        ["git", "diff", "--name-only", parent, sha],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=True,
        timeout=10,
    )
    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
