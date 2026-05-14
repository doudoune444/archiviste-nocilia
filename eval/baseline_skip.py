"""Determine if gate B should be skipped for a baseline-bump commit (AC-17).

Security: inspects the FULL PR diff (merge-base..HEAD), not just the last commit.
An attacker cannot hide a malicious commit behind a final baseline-bump commit
because ALL files changed across the entire PR must be evaluated.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

BUMP_PATTERN = re.compile(r"^chore\(eval\): bump baseline$", re.IGNORECASE)
BASELINE_FILE = "eval/baseline.json"


def should_skip_gate_b(repo_path: Path) -> bool:
    """Return True only when ALL PR commits form an exclusive baseline bump (AC-17).

    Conditions (both required):
    (a) HEAD commit message matches ^chore(eval): bump baseline$ (case-insensitive).
    (b) Full PR diff (merge-base(origin/main, HEAD)..HEAD) touches ONLY eval/baseline.json.

    Using merge-base prevents the multi-commit attack: a malicious commit buried
    earlier in the PR cannot hide behind a final baseline-bump HEAD commit.
    """
    head_msg = _get_commit_message(repo_path, "HEAD")
    if not BUMP_PATTERN.match(head_msg.strip()):
        return False

    merge_base = _get_merge_base(repo_path)
    changed_files = _get_changed_files_since(repo_path, merge_base)
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


def _get_merge_base(repo_path: Path) -> str:
    """Return the merge-base SHA between origin/main and HEAD."""
    try:
        result = subprocess.run(
            ["git", "merge-base", "origin/main", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            check=True,
            timeout=10,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        # Fallback for repos without origin/main (local tests use HEAD^)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD^"],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            check=True,
            timeout=10,
        )
        return result.stdout.strip()


def _get_changed_files_since(repo_path: Path, base_sha: str) -> list[str]:
    """Return all files changed between base_sha and HEAD (full PR diff)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", base_sha, "HEAD"],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=True,
        timeout=10,
    )
    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
