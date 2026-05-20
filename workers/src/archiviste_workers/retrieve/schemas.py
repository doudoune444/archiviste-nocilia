"""Pydantic schemas + constants for `POST /v1/retrieve` (RET-001)."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

MAX_QUERY_BYTES: Final = 4096
TOP_K_DEFAULT: Final = 5
TOP_K_MIN: Final = 1
TOP_K_MAX: Final = 20
SQL_TIMEOUT_SECONDS: Final = 5.0

USER_TIER_ANONYMOUS: Final = "anonymous"
USER_TIER_MEMBERS: Final = "members"
USER_TIER_AUTHOR_ONLY: Final = "author_only"
ALLOWED_USER_TIERS: Final = frozenset(
    {USER_TIER_ANONYMOUS, USER_TIER_MEMBERS, USER_TIER_AUTHOR_ONLY}
)

# user_tier -> set of `documents.access_tier` values eligible for retrieval.
ALLOWED_TIERS_BY_USER_TIER: Final[dict[str, tuple[str, ...]]] = {
    USER_TIER_ANONYMOUS: ("public",),
    USER_TIER_MEMBERS: ("public", "members"),
    USER_TIER_AUTHOR_ONLY: ("public", "members", "author_only"),
}


class RetrievedChunk(BaseModel):
    """Single chunk row returned by `/v1/retrieve` (AC-9)."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    source_path: str
    ord: int
    text: str
    score: float
    # AC-2 (GEN-005): access_tier propagated from documents.access_tier via JOIN.
    # Strict Literal here (production schema); acl.py handles unknown values fail-closed.
    access_tier: Literal["public", "members", "author_only"]


class RetrieveResponse(BaseModel):
    """Body returned on `200 OK` from `/v1/retrieve` (AC-9)."""

    model_config = ConfigDict(extra="forbid")

    chunks: list[RetrievedChunk]
    embedding_ms: int
    search_ms: int
