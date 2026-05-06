"""POST /v1/conversations/{conversation_id}/messages router (ING-003)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import asyncpg
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from archiviste_workers.conversation.gcs_storage import GcsConversationStorage
from archiviste_workers.conversation.models import (
    ConcurrentWriteError,
    ContentTooLargeError,
    ConversationAlreadyExistsError,
    MessageIn,
    MessageOut,
    StorageUnavailableError,
    UnknownUserError,
    is_valid_conversation_id,
)
from archiviste_workers.conversation.repository import ConversationRepository

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
logger = structlog.get_logger()

_FUTURE_TOLERANCE = timedelta(minutes=5)


def _error(http_status: int, code: str) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"error": code})


def _parse_body(raw: dict[str, object]) -> MessageIn:
    try:
        return MessageIn.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc", ())
            if loc and loc[0] == "role":
                raise _error(422, "invalid_role") from exc
            if loc and loc[0] == "content":
                if isinstance(err.get("ctx", {}).get("error"), ContentTooLargeError):
                    raise _error(413, "content_too_large") from exc
                raise _error(422, "empty_content") from exc
            if loc and loc[0] == "timestamp":
                raise _error(422, "invalid_timestamp") from exc
        raise _error(422, "invalid_payload") from exc


def _check_timestamp(timestamp: datetime, *, created_at: datetime) -> None:
    now = datetime.now(UTC)
    ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
    if ts > now + _FUTURE_TOLERANCE:
        raise _error(422, "timestamp_in_future")
    if ts < created_at:
        raise _error(422, "timestamp_before_conversation")


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    conversation_id: str, request: Request, payload: dict[str, object]
) -> MessageOut:
    if not is_valid_conversation_id(conversation_id):
        logger.info("invalid_conversation_id", conversation_id=conversation_id)
        raise _error(400, "invalid_conversation_id")

    message = _parse_body(payload)
    started = time.perf_counter()

    repository: ConversationRepository = request.app.state.conversation_repo
    storage: GcsConversationStorage = request.app.state.gcs_storage
    gcs_uri = storage.gcs_uri(conversation_id)

    try:
        is_new, created_at = await repository.create_if_absent(
            conversation_id=conversation_id, user_id=message.user_id, gcs_uri=gcs_uri
        )
    except UnknownUserError as exc:
        raise _error(422, "unknown_user") from exc

    _check_timestamp(message.timestamp, created_at=created_at)

    if is_new:
        try:
            await storage.create_conversation_object(
                conversation_id=conversation_id,
                user_id=message.user_id,
                created_at=created_at,
            )
            logger.info(
                "conversation_created",
                conversation_id=conversation_id,
                user_id=message.user_id,
            )
        except ConversationAlreadyExistsError as exc:
            raise _error(409, "conversation_already_exists") from exc
        except StorageUnavailableError as exc:
            raise _error(503, "storage_unavailable") from exc

    try:
        generation, _ = await storage.append_block(
            conversation_id=conversation_id,
            role=message.role,
            content=message.content,
            timestamp=message.timestamp,
        )
    except ConcurrentWriteError as exc:
        logger.warning("concurrent_write_exhausted", conversation_id=conversation_id)
        raise _error(409, "concurrent_write") from exc
    except StorageUnavailableError as exc:
        raise _error(503, "storage_unavailable") from exc

    try:
        message_count = await repository.increment_message_count(conversation_id)
    except (asyncpg.PostgresError, OSError, TimeoutError):
        # AC-5: DB failure post-GCS is non-fatal; surface ALERT log, return 201.
        logger.error(
            "inconsistency_db_after_gcs",
            conversation_id=conversation_id,
            gcs_generation=generation,
        )
        message_count = -1

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "conversation_message",
        conversation_id=conversation_id,
        role=message.role,
        bytes=len(message.content.encode("utf-8")),
        latency_ms=latency_ms,
        gcs_generation=generation,
    )
    return MessageOut(
        conversation_id=conversation_id,
        message_count=message_count,
        gcs_uri=gcs_uri,
        generation=generation,
    )
