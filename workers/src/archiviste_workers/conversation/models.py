"""Pydantic models and domain exceptions for the conversation logger (ING-003)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, field_validator

Role = Literal["user", "assistant"]

# AC-6: strict lowercase hex UUIDv4-shape filename source.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# AC-8: hard cap on UTF-8 byte length of `content`.
MAX_CONTENT_BYTES = 16 * 1024


def is_valid_conversation_id(value: str) -> bool:
    return bool(_UUID_RE.match(value))


class MessageIn(BaseModel):
    role: Role
    content: Annotated[str, StringConstraints(min_length=1)]
    timestamp: datetime
    user_id: str

    @field_validator("content")
    @classmethod
    def _content_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_CONTENT_BYTES:
            # Signalled via dedicated exception so router can map to 413.
            raise ContentTooLargeError
        return value


class MessageOut(BaseModel):
    conversation_id: str
    message_count: int
    gcs_uri: str
    generation: int


class ConversationLoggerError(Exception):
    """Base class for domain errors raised by the conversation logger."""


class UnknownUserError(ConversationLoggerError):
    """FK violation on conversations.user_id (Postgres 23503)."""


class ConversationAlreadyExistsError(ConversationLoggerError):
    """GCS object already present at create time (ifGenerationMatch=0 conflict)."""


class ConcurrentWriteError(ConversationLoggerError):
    """All retries exhausted on append read-modify-write."""


class StorageUnavailableError(ConversationLoggerError):
    """Non-recoverable GCS error (5xx, timeout, connection)."""


class ConversationObjectMissingError(ConversationLoggerError):
    """GCS object absent at append time (blob download returned NotFound).

    Raised by _read_modify_write when the object does not exist yet, so the
    router can self-heal by recreating it before retrying the append.
    """


class ContentTooLargeError(ValueError):
    """Content exceeds MAX_CONTENT_BYTES (mapped to HTTP 413)."""

    def __init__(self) -> None:
        super().__init__("content_too_large")
