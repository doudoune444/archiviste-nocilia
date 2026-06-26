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

# #345 BUG A: mistral-small is unreliable about the exact sentinel. Match the FIRST
# whole line that, ignoring surrounding spaces and -/*/_ runs, reduces to the token
# SUIVI. The WHOLE-LINE anchor is critical: the French word "suivi" mid-sentence must
# never trigger a cut.
_FOLLOWUP_MARKER_RE = re.compile(
    r"^[ \t]*[-*_]*[ \t]*SUIVI[ \t]*[-*_]*[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
# A markdown horizontal rule the model may emit just before the marker line.
_TRAILING_RULE_RE = re.compile(r"\n[ \t]*[-*_]{3,}[ \t]*$")
# Leading bullet (-, *, •) + spaces on a follow-up line.
_FOLLOWUP_BULLET_RE = re.compile(r"^[-*•]+[ \t]*")


def extract_citations(answer: str, chunks: list[Chunk]) -> list[Citation]:
    """Parse `[source_path]` patterns; keep only those matching a retrieved chunk path."""
    known: dict[str, list[int]] = {}
    for chunk in chunks:
        known.setdefault(chunk.source_path, []).append(chunk.ord)

    seen: dict[str, list[int]] = {}
    for raw_match in _CITATION_RE.findall(answer):
        # #345 BUG B: a single bracket may group sources `[a, b]`; split on comma and
        # match each piece. Security unchanged — only retrieved paths count.
        for piece in raw_match.split(","):
            candidate = piece.strip()
            if not candidate:
                continue
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
    Matching is tolerant (#345): the marker is the first whole line reducing to SUIVI.
    When the marker is absent the body is returned unchanged with an empty list.
    """
    match = _FOLLOWUP_MARKER_RE.search(answer)
    if match is None:
        return answer, []

    body = answer[: match.start()].rstrip()
    body = _TRAILING_RULE_RE.sub("", body).rstrip()
    block = answer[match.end() :]
    followups: list[str] = []
    for raw_line in block.splitlines():
        line = _FOLLOWUP_BULLET_RE.sub("", raw_line.strip()).strip()
        if not line:
            continue
        followups.append(line)
    return body, followups
