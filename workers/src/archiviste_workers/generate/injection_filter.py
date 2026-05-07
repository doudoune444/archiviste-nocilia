"""Prompt-injection pattern detector (AC-20). Sanitize-prefix only, never reject."""

from __future__ import annotations

import re

_INJECTION_RE = re.compile(
    r"(ignore (all )?(prior|previous) instructions"
    r"|new instructions"
    r"|system\s*:"
    r"|disregard (the )?above)",
    re.IGNORECASE,
)


def detect_injection(query: str) -> str | None:
    """Return the matched pattern (verbatim, lowercased context preserved), or None."""
    match = _INJECTION_RE.search(query)
    return match.group(0) if match else None
