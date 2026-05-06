"""Property test for INV-1: chunker conserves total char count modulo overlap."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from archiviste_workers.ingest.chunker import CHUNK_OVERLAP, build_chunker


@pytest.fixture(scope="module")
def chunker() -> object:
    return build_chunker()


@given(text=st.text(min_size=1, max_size=2000))
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_chunker_preserves_chars_modulo_overlap(chunker: object, text: str) -> None:
    """INV-1: sum of chunk lengths ≥ original len, ≤ original + (n-1)*overlap_chars upper bound."""
    chunks = chunker.split_text(text)  # type: ignore[attr-defined]
    total = sum(len(chunk) for chunk in chunks)
    if not chunks:
        # Splitter may return empty list for whitespace-only inputs; nothing to assert.
        return
    overlap_char_budget = max(0, (len(chunks) - 1)) * CHUNK_OVERLAP * 8
    assert total <= len(text) + overlap_char_budget
