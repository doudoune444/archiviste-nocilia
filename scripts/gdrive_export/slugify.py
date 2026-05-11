"""Slug generation from arbitrary Drive file names."""

import re
import unicodedata

MAX_LEN: int = 80


def slugify(text: str, fallback_id: str) -> str:
    """Convert *text* to a URL-safe slug; fall back to ``file-<fallback_id[:8]>`` if empty.

    Pipeline: NFKD → drop combining marks → lower → non-alnum→'-' → collapse → strip → cap 80.
    Invariant: result matches ``^[a-z0-9-]+$`` or ``^file-[0-9a-f]{8}$``.
    """
    normalized = unicodedata.normalize("NFKD", text)
    without_combining = "".join(
        ch for ch in normalized if unicodedata.category(ch) != "Mn"
    )
    lowered = without_combining.lower()
    replaced = re.sub(r"[^a-z0-9]+", "-", lowered)
    collapsed = re.sub(r"-{2,}", "-", replaced)
    stripped = collapsed.strip("-")
    # Cap to MAX_LEN then re-strip: the cap may land mid-hyphen-run, leaving a
    # trailing '-' that would violate the idempotence invariant (AC-3).
    capped = stripped[:MAX_LEN].strip("-")
    if not capped:
        return f"file-{fallback_id[:8]}"
    return capped
