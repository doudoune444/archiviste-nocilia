"""Post-retrieval ACL filter — pure module, no I/O (AC-3, AC-20, D-4).

Mapping (AC-3, byte-for-byte per spec):
  anonymous   → {"public"}
  members     → {"public", "members"}
  author_only → {"public", "members", "author_only"}

Fail-closed on unknown chunk tier or unknown user_tier (D-4, security.md §A01).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import structlog

from archiviste_workers.generate.models import Chunk

logger = structlog.get_logger()

ALLOWED_ACCESS_TIERS_BY_USER_TIER: Final[dict[str, frozenset[str]]] = {
    "anonymous": frozenset({"public"}),
    "members": frozenset({"public", "members"}),
    "author_only": frozenset({"public", "members", "author_only"}),
}


@dataclass
class FilterResult:
    """Result of ACL filtering — visible chunks + count of blocked ones."""

    visible: list[Chunk] = field(default_factory=list)
    blocked_count: int = 0


def filter_chunks_by_tier(chunks: list[Chunk], user_tier: str) -> FilterResult:
    """Return chunks visible to user_tier; increment blocked_count for the rest.

    Fail-closed (D-4): unknown chunk tier or unknown user_tier → chunk treated
    as blocked, never leaked. Logs acl_unknown_tier with chunk_id only (AC-18).
    """
    allowed = ALLOWED_ACCESS_TIERS_BY_USER_TIER.get(user_tier, frozenset())
    visible: list[Chunk] = []
    blocked_count = 0

    for chunk in chunks:
        chunk_tier = chunk.access_tier
        if chunk_tier not in {"public", "members", "author_only"}:
            # D-4: fail-closed — unknown tier is never allowed.
            logger.error("acl_unknown_tier", chunk_id=chunk.source_path)
            blocked_count += 1
            continue
        if chunk_tier in allowed:
            visible.append(chunk)
        else:
            blocked_count += 1

    return FilterResult(visible=visible, blocked_count=blocked_count)
