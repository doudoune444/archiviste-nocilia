"""GEN-001 citation parser unit tests (AC-12, AC-13)."""

from __future__ import annotations

import structlog
from structlog.testing import capture_logs

from archiviste_workers.generate.models import Chunk
from archiviste_workers.generate.parser import extract_citations


def _setup_structlog() -> None:
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )


def test_extract_citations_groups_chunk_ords() -> None:
    # AC-12: same source_path collapses to one citation with all known ords.
    chunks = [
        Chunk(source_path="a/b.md", ord=3, text="t1"),
        Chunk(source_path="a/b.md", ord=5, text="t2"),
        Chunk(source_path="c.md", ord=1, text="t3"),
    ]
    citations = extract_citations("texte [a/b.md] et [c.md]", chunks)
    assert [c.source_path for c in citations] == ["a/b.md", "c.md"]
    assert citations[0].chunk_ords == [3, 5]
    assert citations[1].chunk_ords == [1]


def test_extract_citations_filters_unknown_source() -> None:
    # AC-12: unknown source_path -> filtered + log warn.
    chunks = [Chunk(source_path="a/b.md", ord=1, text="t1")]
    with capture_logs() as captured:
        citations = extract_citations("citation [d.md] et [a/b.md]", chunks)
    assert [c.source_path for c in citations] == ["a/b.md"]
    assert any(
        log.get("event") == "citation_unknown_source" and log.get("source_path") == "d.md"
        for log in captured
    )


def test_extract_citations_no_brackets() -> None:
    # AC-13: answer without brackets -> empty list (caller logs llm_no_citation).
    chunks = [Chunk(source_path="a/b.md", ord=1, text="t1")]
    assert extract_citations("plain answer no citations", chunks) == []
