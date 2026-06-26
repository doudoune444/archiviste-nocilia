"""Citation extraction (AC-12, AC-13)."""

from __future__ import annotations

import re

import structlog

from archiviste_workers.generate.models import Chunk, Citation

# AC-12 OQ-6: permissive regex; semantic filtering happens against known chunk source_paths.
_CITATION_RE = re.compile(r"\[([^\]\s][^\]]*)\]")
logger = structlog.get_logger()

# #354: sentinel that separates the answer body from the structured follow-up block.
# The canon SYSTEM_PROMPT instructs the model to emit it verbatim (prompt.py owns the
# instruction text); extract_followups splits on it here. A test pins the marker into
# the prompt so the two stay in sync.
FOLLOWUP_MARKER = "---SUIVI---"
_FOLLOWUP_LINE_PREFIX = "- "


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


def extract_followups(answer: str) -> tuple[str, list[str]]:
    """Split the answer on FOLLOWUP_MARKER → (body_without_block, follow_up_questions).

    Mirror of extract_citations: a post-hoc parse of the LLM output. The canon prompt
    asks the model to end with the marker then one dash-prefixed question per line.
    When the marker is absent the body is returned unchanged with an empty list.
    """
    marker_index = answer.find(FOLLOWUP_MARKER)
    if marker_index == -1:
        return answer, []

    body = answer[:marker_index].rstrip()
    block = answer[marker_index + len(FOLLOWUP_MARKER) :]
    followups: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(_FOLLOWUP_LINE_PREFIX):
            line = line[len(_FOLLOWUP_LINE_PREFIX) :].strip()
        followups.append(line)
    return body, followups
