"""Slug generation from arbitrary Drive file names."""

import re
import unicodedata

MAX_LEN: int = 80

# Unicode dash codepoints to normalise to ASCII '-' before slug pipeline.
# AC-3 ING-011: em-dash U+2014, en-dash U+2013, figure-dash U+2012, minus U+2212.
# Codepoints are given as integers to avoid RUF001 ambiguous-character warnings.
_UNICODE_DASHES = str.maketrans({
    0x2014: "-",  # em-dash
    0x2013: "-",  # en-dash
    0x2012: "-",  # figure-dash
    0x2212: "-",  # minus sign
})


def slugify(text: str, fallback_id: str) -> str:
    """Convert *text* to a URL-safe slug; fall back to ``file-<fallback_id[:8]>`` if empty.

    Pipeline: Unicode dashes -> ASCII '-' -> NFKD -> drop combining marks -> lower
    -> non-alnum->'-' -> collapse -> strip -> cap 80.
    Invariant: result matches ``^[a-z0-9-]+$`` or ``^file-[0-9a-f]{8}$``.
    """
    # AC-3 ING-011: map Unicode dashes before NFKD so they collapse into '-'.
    dash_normalised = text.translate(_UNICODE_DASHES)
    normalized = unicodedata.normalize("NFKD", dash_normalised)
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
