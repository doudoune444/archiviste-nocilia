"""Test configuration and AC-14 guard: no Drive API imports in production code.

ING-013 amendment: Drive API imports are authorized ONLY in auth.py and drive_client.py.
All other modules in gdrive_export/ remain firewalled.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# AC-14 / ING-013: Drive API imports allowed only in auth.py and drive_client.py.
# All other gdrive_export/ files must not import these packages.
_DRIVE_API_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*(import|from)\s+(requests|httpx|urllib3?|aiohttp|googleapiclient|google\.auth|google\.oauth2|httplib2)\b",
    re.MULTILINE,
)
_GDRIVE_EXPORT_DIR: Path = Path(__file__).parent.parent / "gdrive_export"

# Files authorized to import Drive API packages (by filename, not full path).
_DRIVE_API_ALLOWED_FILENAMES: frozenset[str] = frozenset({"auth.py", "drive_client.py"})


def _find_drive_api_violations() -> list[str]:
    """Return '<file>:<line>' strings where Drive API imports appear in non-allowed files."""
    violations: list[str] = []
    for py_file in _GDRIVE_EXPORT_DIR.rglob("*.py"):
        if py_file.name in _DRIVE_API_ALLOWED_FILENAMES:
            continue
        text = py_file.read_text(encoding="utf-8")
        for match in _DRIVE_API_PATTERN.finditer(text):
            lineno = text[: match.start()].count("\n") + 1
            violations.append(f"{py_file.relative_to(_GDRIVE_EXPORT_DIR)}:{lineno}")
    return violations


def pytest_sessionstart(session: pytest.Session) -> None:
    """AC-14: Fail fast if any Drive API import is found in non-allowed gdrive_export/ files."""
    violations = _find_drive_api_violations()
    if violations:
        msg = (
            "AC-14 VIOLATED — Drive API imports detected in gdrive_export/ "
            "(only auth.py and drive_client.py may import these):\n"
            + "\n".join(violations)
        )
        pytest.fail(msg)
