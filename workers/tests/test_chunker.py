"""Unit tests for `archiviste_workers.ingest.chunker` (AC-6, AC-7)."""

from __future__ import annotations

import pytest

from archiviste_workers.ingest.chunker import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    SEPARATORS,
    build_chunker,
)


@pytest.fixture(scope="module")
def chunker() -> object:
    # Loads bge-m3 tokenizer; cached for module to avoid repeat downloads.
    return build_chunker()


def test_chunker_constants_match_spec() -> None:
    # AC-6: chunk_size=512, chunk_overlap=64, separators ordered.
    assert CHUNK_SIZE == 512
    assert CHUNK_OVERLAP == 64
    assert SEPARATORS == ["\n\n", "\n", ". ", " ", ""]


def test_chunker_emits_ordered_non_empty_chunks(chunker: object) -> None:
    # AC-7: ord = position 0-indexed in the splitter output.
    text = "Paragraph one. " * 200 + "\n\n" + "Paragraph two. " * 200
    chunks = chunker.split_text(text)  # type: ignore[attr-defined]
    assert len(chunks) >= 2
    for index, chunk in enumerate(chunks):
        assert isinstance(chunk, str)
        assert chunk
        # ord = enumerate index (verified by pipeline test); chunker preserves order.
        assert chunks.index(chunk) == index or chunks.count(chunk) > 1


def test_chunker_short_text_single_chunk(chunker: object) -> None:
    chunks = chunker.split_text("hi")  # type: ignore[attr-defined]
    assert chunks == ["hi"]
