"""Test configuration and AC-14 guard: no Drive API imports in production code."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# AC-14: gdrive_export/ must never import Drive API modules or HTTP client libs.
# Pattern catches both bare-module forms (`import requests`) and
# from-module forms (`from httpx import ...`) for all forbidden packages.
_DRIVE_API_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*(import|from)\s+(requests|httpx|urllib3?|aiohttp|googleapiclient|google\.auth|httplib2)\b",
    re.MULTILINE,
)
_GDRIVE_EXPORT_DIR: Path = Path(__file__).parent.parent / "gdrive_export"


def _find_drive_api_imports() -> list[str]:
    """Return a list of '<file>:<line>' strings where Drive API imports appear."""
    violations: list[str] = []
    for py_file in _GDRIVE_EXPORT_DIR.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for match in _DRIVE_API_PATTERN.finditer(text):
            lineno = text[: match.start()].count("\n") + 1
            violations.append(f"{py_file.relative_to(_GDRIVE_EXPORT_DIR)}:{lineno}")
    return violations


def pytest_sessionstart(session: pytest.Session) -> None:
    """AC-14: Fail fast if any Drive API import is found in gdrive_export/."""
    violations = _find_drive_api_imports()
    if violations:
        msg = (
            "AC-14 VIOLATED — Drive API imports detected in gdrive_export/:\n"
            + "\n".join(violations)
        )
        pytest.fail(msg)
