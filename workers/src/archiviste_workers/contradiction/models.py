"""Pydantic models for POST /v1/verify-contradiction (CTR-001)."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field

CLAIM_MAX_CHARS: Final = 4096
MAX_CITATIONS: Final = 50
MAX_CHUNK_ORDS: Final = 200

# not_raised: <2 confirmations; the other three mirror ticket_service.TicketAction.
TicketAction = Literal["created", "incremented", "skipped_error", "not_raised"]


class Citation(BaseModel):
    source_path: str
    chunk_ords: Annotated[list[Annotated[int, Field(ge=0)]], Field(max_length=MAX_CHUNK_ORDS)]


class VerifyContradictionRequest(BaseModel):
    claim: Annotated[str, Field(min_length=1, max_length=CLAIM_MAX_CHARS)]
    conversation_id: str
    citations: Annotated[list[Citation], Field(min_length=1, max_length=MAX_CITATIONS)]
    request_id: str


class VerifyContradictionResponse(BaseModel):
    contradiction_confirmed: bool
    confirmations: Annotated[int, Field(ge=0, le=3)]
    ticket_action: TicketAction
    ticket_id: str | None = None
