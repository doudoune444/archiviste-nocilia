"""Pydantic models for POST /v1/generate (AC-1, AC-17/18/19)."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

UserTier = Literal["anonymous", "members", "author_only"]
Mode = Literal["canon", "off_topic"]

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
QUERY_MAX_BYTES = 4 * 1024


def is_valid_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


class GenerateRequest(BaseModel):
    query: Annotated[str, Field(min_length=1)]
    conversation_id: str | None = None
    user_id: str
    user_tier: UserTier
    request_id: str

    @field_validator("query")
    @classmethod
    def _query_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > QUERY_MAX_BYTES:
            raise ValueError("query_too_large")
        return value


class Citation(BaseModel):
    source_path: str
    chunk_ords: list[int]


class Usage(BaseModel):
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_eur: Decimal | None = None


class GenerateResponse(BaseModel):
    answer: str
    citations: list[Citation]
    mode: Mode
    conversation_id: str
    request_id: str
    usage: Usage
    retrieve_ms: int
    llm_ms: int


class Chunk(BaseModel):
    source_path: str
    ord: int
    text: str
