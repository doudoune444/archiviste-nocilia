"""Body-text normalization for ingested Markdown content."""

import re
import unicodedata

# C0 control chars (0x00-0x1F) except TAB (0x09) and LF (0x0A).
_STRIP_PATTERN: re.Pattern[str] = re.compile(r"[\x00-\x08\x0b-\x1f]")


def normalize_body(text: str) -> str:
    """Normalize *text* to NFKC and strip dangerous C0 control characters.

    Preserves ``\\n`` and ``\\t``; removes all other C0 control chars (0x00-0x1F).
    Invariant: result contains no bytes in 0x00-0x08 or 0x0B-0x1F.
    """
    nfkc = unicodedata.normalize("NFKC", text)
    return _STRIP_PATTERN.sub("", nfkc)
