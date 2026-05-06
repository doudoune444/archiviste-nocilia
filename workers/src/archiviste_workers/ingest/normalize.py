"""NFKC normalization + control-char stripping + SHA-256 content hashing."""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Final

_PRESERVED_CONTROL: Final = frozenset({"\n", "\t"})
_C0_MAX_CODEPOINT: Final = 0x1F
_DEL_CODEPOINT: Final = 0x7F


def normalize_body(text: str) -> str:
    """NFKC-normalize and strip C0 control chars except `\\n` and `\\t`."""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        char for char in normalized if char in _PRESERVED_CONTROL or not _is_stripped_control(char)
    )


def _is_stripped_control(char: str) -> bool:
    code = ord(char)
    return code <= _C0_MAX_CODEPOINT or code == _DEL_CODEPOINT


def compute_content_hash(body: str) -> str:
    """SHA-256 hex digest of the UTF-8 encoded normalized body."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
