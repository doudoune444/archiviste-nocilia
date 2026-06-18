"""Pydantic models for POST /v1/verify-contradiction (CTR-001 / #162)."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field

CLAIM_MAX_CHARS: Final = 4096
MAX_CITATIONS: Final = 50
MAX_CHUNK_ORDS: Final = 200

# not_raised: winning verdict is present, or no verdict reaches >=2 votes.
# The other three mirror ticket_service.TicketAction.
TicketAction = Literal["created", "incremented", "skipped_error", "not_raised"]

# Four-way verdict enum (design decision #1).
Verdict = Literal["present", "absent", "contradiction", "unclear"]

# Typed signal outcome (#172).
# confirmed: judges confirmed absent/contradiction majority → ticket raised.
# refused: judges confirmed present majority → lore is consistent, no ticket.
# indecisive: unclear majority, no majority, or no sources → judges could not decide.
# A force=True ticket never reports confirmed (it is the untrusted judges_not_passed path).
Outcome = Literal["confirmed", "refused", "indecisive"]

# Verdicts that trigger a ticket when they win with >=2 votes (design decision #4).
# #172: "unclear" removed — unclear majority is now indecisive, no ticket raised.
TICKET_TRIGGERING_VERDICTS: Final[frozenset[str]] = frozenset({"absent", "contradiction"})

# Retrieval fallback top-k when no citations are provided (design decision #5).
RETRIEVAL_TOP_K: Final = 5


class Citation(BaseModel):
    source_path: str
    chunk_ords: Annotated[list[Annotated[int, Field(ge=0)]], Field(max_length=MAX_CHUNK_ORDS)]


class VerifyContradictionRequest(BaseModel):
    claim: Annotated[str, Field(min_length=1, max_length=CLAIM_MAX_CHARS)]
    conversation_id: str
    # citations is now optional (design decision #5 — no-citation retrieval path).
    citations: Annotated[list[Citation], Field(default_factory=list, max_length=MAX_CITATIONS)]
    request_id: str
    # force=True allows a human to raise a ticket even when judges did not confirm (#163).
    force: bool = False


class VerifyContradictionResponse(BaseModel):
    # Clean-break response shape (design decision #6).
    verdict: Verdict
    reason: str
    ticket_action: TicketAction
    ticket_id: str | None = None
    # Explicit typed outcome (#172): confirmed/refused/indecisive.
    outcome: Outcome
