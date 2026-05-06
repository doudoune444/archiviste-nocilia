"""Citation extraction (AC-12, AC-13)."""

from __future__ import annotations

import re

import structlog

from archiviste_workers.generate.models import Chunk, Citation

# AC-12 OQ-6: permissive regex; semantic filtering happens against known chunk source_paths.
_CITATION_RE = re.compile(r"\[([^\]\s][^\]]*)\]")
logger = structlog.get_logger()


def extract_citations(answer: str, chunks: list[Chunk]) -> list[Citation]:
    """Parse `[source_path]` patterns; keep only those matching a retrieved chunk path."""
    known: dict[str, list[int]] = {}
    for chunk in chunks:
        known.setdefault(chunk.source_path, []).append(chunk.ord)

    seen: dict[str, list[int]] = {}
    for raw_match in _CITATION_RE.findall(answer):
        candidate = raw_match.strip()
        if candidate not in known:
            logger.warning("citation_unknown_source", source_path=candidate)
            continue
        if candidate not in seen:
            seen[candidate] = list(known[candidate])
    return [Citation(source_path=p, chunk_ords=ords) for p, ords in seen.items()]
