"""POST /v1/generate canon mode (AC-1..AC-25). Internal-only — not mounted on gateway."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from archiviste_workers.generate.injection_filter import detect_injection
from archiviste_workers.generate.models import (
    GenerateRequest,
    GenerateResponse,
    Usage,
    is_valid_uuid,
)
from archiviste_workers.generate.parser import extract_citations
from archiviste_workers.generate.pricing import compute_cost_eur
from archiviste_workers.generate.prompt import build_messages
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.llm import (
    LlmClient,
    LlmTimeoutError,
    LlmUpstreamError,
    extract_usage,
)
from archiviste_workers.services.query_log import QueryLogRepository, QueryLogRow
from archiviste_workers.services.retrieve_client import RetrieveClient, RetrieveError

router = APIRouter(prefix="/v1", tags=["generate"])
logger = structlog.get_logger()

TOP_K = 5


def _http_error(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code})


def _parse_request(payload: dict[str, Any]) -> GenerateRequest:
    if (
        "user_id" in payload
        and isinstance(payload["user_id"], str)
        and not is_valid_uuid(payload["user_id"])
    ):
        raise _http_error(400, "invalid_user_id")
    if "request_id" not in payload or not isinstance(payload.get("request_id"), str):
        raise _http_error(400, "invalid_request_id")
    if not is_valid_uuid(payload["request_id"]):
        raise _http_error(400, "invalid_request_id")
    if (
        payload.get("conversation_id") is not None
        and isinstance(payload["conversation_id"], str)
        and not is_valid_uuid(payload["conversation_id"])
    ):
        raise _http_error(400, "invalid_conversation_id")
    try:
        return GenerateRequest.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc", ())
            if loc and loc[0] == "user_tier":
                raise _http_error(422, "invalid_user_tier") from exc
            if loc and loc[0] == "user_id":
                raise _http_error(400, "invalid_user_id") from exc
            if loc and loc[0] == "request_id":
                raise _http_error(400, "invalid_request_id") from exc
            if loc and loc[0] == "query":
                raise _http_error(400, "invalid_query") from exc
        raise _http_error(400, "invalid_query") from exc


@router.post("/generate", response_model=GenerateResponse)
async def post_generate(request: Request, payload: dict[str, Any]) -> GenerateResponse:
    parsed = _parse_request(payload)
    conversation_id = parsed.conversation_id or str(uuid.uuid4())
    suspected = detect_injection(parsed.query)
    if suspected:
        logger.warning(
            "prompt_injection_suspected", request_id=parsed.request_id, pattern=suspected
        )

    retrieve_client: RetrieveClient = request.app.state.retrieve_client
    llm_client: LlmClient = request.app.state.llm_client
    conversation_client: ConversationClient = request.app.state.conversation_client
    query_log_repo: QueryLogRepository = request.app.state.query_log_repo

    started = time.perf_counter()

    retrieve_started = time.perf_counter()
    try:
        chunks = await retrieve_client.search(
            query=parsed.query,
            top_k=TOP_K,
            user_tier=parsed.user_tier,
            request_id=parsed.request_id,
        )
    except RetrieveError as exc:
        logger.error("retrieve_failed", request_id=parsed.request_id, error=str(exc))
        raise _http_error(502, "retrieve_failed") from exc
    retrieve_ms = int((time.perf_counter() - retrieve_started) * 1000)

    messages = build_messages(parsed.query, chunks, suspected_injection=bool(suspected))

    llm_started = time.perf_counter()
    try:
        ai_message = await llm_client.invoke(messages)
    except LlmTimeoutError:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=conversation_id,
                query_text=parsed.query,
                mode="canon",
                status_code=504,
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                cost_eur=None,
            )
        )
        raise _http_error(504, "llm_timeout") from None
    except LlmUpstreamError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        await query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=conversation_id,
                query_text=parsed.query,
                mode="canon",
                status_code=502,
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                cost_eur=None,
            )
        )
        raise _http_error(502, "llm_upstream") from exc
    llm_ms = int((time.perf_counter() - llm_started) * 1000)

    answer = str(ai_message.content) if ai_message.content is not None else ""
    citations = extract_citations(answer, chunks)
    if not citations and chunks:
        logger.warning("llm_no_citation", request_id=parsed.request_id)

    usage = extract_usage(ai_message, llm_client.provider)
    cost_eur = compute_cost_eur(llm_client.model, usage.prompt_tokens, usage.completion_tokens)
    if cost_eur is None and usage.prompt_tokens is not None:
        logger.warning("unknown_model_pricing", model=llm_client.model)
    usage = Usage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_eur=cost_eur,
    )

    now = datetime.now(UTC)
    await conversation_client.append_message(
        conversation_id=conversation_id,
        role="user",
        content=parsed.query,
        timestamp=now,
        user_id=parsed.user_id,
    )
    await conversation_client.append_message(
        conversation_id=conversation_id,
        role="assistant",
        content=answer,
        timestamp=datetime.now(UTC),
        user_id=parsed.user_id,
    )

    latency_ms = int((time.perf_counter() - started) * 1000)
    await query_log_repo.insert(
        QueryLogRow(
            request_id=parsed.request_id,
            user_id=parsed.user_id,
            conversation_id=conversation_id,
            query_text=parsed.query,
            mode="canon",
            status_code=200,
            latency_ms=latency_ms,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_eur=usage.cost_eur,
        )
    )

    logger.info(
        "generate",
        request_id=parsed.request_id,
        conversation_id=conversation_id,
        mode="canon",
        status=200,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
        chunks=len(chunks),
        citations=len(citations),
    )
    return GenerateResponse(
        answer=answer,
        citations=citations,
        mode="canon",
        conversation_id=conversation_id,
        request_id=parsed.request_id,
        usage=usage,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
    )
