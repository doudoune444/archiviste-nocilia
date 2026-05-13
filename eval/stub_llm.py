"""Deterministic offline stub replacing the LLM generate call.

Rule is fixed (AC-4): no I/O, no randomness.
answer = keywords joined by space + double newline + chunks text[:200] joined by double newline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievedChunk:
    """Minimal chunk representation returned by /v1/retrieve."""

    source_path: str
    text: str


def build_stub_answer(keywords: list[str], chunks: list[RetrievedChunk]) -> str:
    """Build a deterministic answer from keywords and retrieved chunks (AC-4).

    Rule: keywords joined by space, then double newline, then each chunk's
    first 200 chars joined by double newline.
    Two calls with identical inputs produce byte-identical output.
    """
    keyword_part = " ".join(keywords)
    chunk_parts = "\n\n".join(chunk.text[:200] for chunk in chunks)
    return f"{keyword_part}\n\n{chunk_parts}"
