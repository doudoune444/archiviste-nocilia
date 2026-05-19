"""POST /v1/generate — Mode 1 canon + Mode 2 off_topic (GEN-001/GEN-003). Internal-only."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
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
from archiviste_workers.generate.prompt import build_messages, build_off_topic_messages
from archiviste_workers.services.conversation_client import ConversationClient
from archiviste_workers.services.intent import IntentResult, classify_intent
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


class _GenerateError(Exception):
    # Internal-only sentinel: caught by post_generate to emit a JSON body of exactly
    # {"error": code} (spec AC-11/17/18/19/21/22/23). Avoids FastAPI's HTTPException
    # which wraps detail under {"detail": ...}.
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _error_response(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


def _parse_request(payload: dict[str, Any]) -> GenerateRequest:
    if (
        "user_id" in payload
        and isinstance(payload["user_id"], str)
        and not is_valid_uuid(payload["user_id"])
    ):
        raise _GenerateError(400, "invalid_user_id")
    if "request_id" not in payload or not isinstance(payload.get("request_id"), str):
        raise _GenerateError(400, "invalid_request_id")
    if not is_valid_uuid(payload["request_id"]):
        raise _GenerateError(400, "invalid_request_id")
    if (
        payload.get("conversation_id") is not None
        and isinstance(payload["conversation_id"], str)
        and not is_valid_uuid(payload["conversation_id"])
    ):
        raise _GenerateError(400, "invalid_conversation_id")
    try:
        return GenerateRequest.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc", ())
            if loc and loc[0] == "user_tier":
                raise _GenerateError(422, "invalid_user_tier") from exc
            if loc and loc[0] == "user_id":
                raise _GenerateError(400, "invalid_user_id") from exc
            if loc and loc[0] == "request_id":
                raise _GenerateError(400, "invalid_request_id") from exc
            if loc and loc[0] == "query":
                raise _GenerateError(400, "invalid_query") from exc
        raise _GenerateError(400, "invalid_query") from exc


@dataclass(frozen=True)
class _RequestContext:
    parsed: GenerateRequest
    conversation_id: str
    suspected: str | None
    retrieve_client: RetrieveClient
    llm_client: LlmClient
    conversation_client: ConversationClient
    query_log_repo: QueryLogRepository
    started: float


@router.post("/generate", response_model=GenerateResponse)
async def post_generate(
    request: Request, payload: dict[str, Any]
) -> GenerateResponse | JSONResponse:
    try:
        return await _handle_generate(request, payload)
    except _GenerateError as exc:
        return _error_response(exc.status, exc.code)


async def _handle_generate(request: Request, payload: dict[str, Any]) -> GenerateResponse:
    parsed = _parse_request(payload)
    conversation_id = parsed.conversation_id or str(uuid.uuid4())
    suspected = detect_injection(parsed.query)
    if suspected:
        # AC-17: one log per request.
        logger.warning(
            "prompt_injection_suspected", request_id=parsed.request_id, pattern=suspected
        )

    ctx = _RequestContext(
        parsed=parsed,
        conversation_id=conversation_id,
        suspected=suspected,
        retrieve_client=request.app.state.retrieve_client,
        llm_client=request.app.state.llm_client,
        conversation_client=request.app.state.conversation_client,
        query_log_repo=request.app.state.query_log_repo,
        started=time.perf_counter(),
    )

    # AC-2: classify intent before any retrieve/LLM call.
    intent_result = await classify_intent(
        llm_client=ctx.llm_client,
        query=parsed.query,
        suspected_injection=bool(suspected),
        request_id=parsed.request_id,
    )

    if intent_result.intent == "off_topic":
        return await _run_off_topic(ctx, intent_result)
    return await _run_canon(ctx, intent_result)


async def _run_canon(ctx: _RequestContext, intent_result: IntentResult) -> GenerateResponse:
    """Pipeline canon (Mode 1 — retrieve → LLM → parse → log)."""
    parsed = ctx.parsed

    retrieve_started = time.perf_counter()
    try:
        chunks = await ctx.retrieve_client.search(
            query=parsed.query,
            top_k=TOP_K,
            user_tier=parsed.user_tier,
            request_id=parsed.request_id,
        )
    except RetrieveError as exc:
        logger.error("retrieve_failed", request_id=parsed.request_id, error=str(exc))
        raise _GenerateError(502, "retrieve_failed") from exc
    retrieve_ms = int((time.perf_counter() - retrieve_started) * 1000)

    messages = build_messages(parsed.query, chunks, suspected_injection=bool(ctx.suspected))

    llm_started = time.perf_counter()
    try:
        ai_message = await ctx.llm_client.invoke(messages)
    except LlmTimeoutError:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="canon",
                intent=intent_result.intent,
                status_code=504,
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                cost_eur=None,
            )
        )
        raise _GenerateError(504, "llm_timeout") from None
    except LlmUpstreamError as exc:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="canon",
                intent=intent_result.intent,
                status_code=502,
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                cost_eur=None,
            )
        )
        raise _GenerateError(502, "llm_upstream") from exc
    llm_ms = int((time.perf_counter() - llm_started) * 1000)

    answer = str(ai_message.content) if ai_message.content is not None else ""
    citations = extract_citations(answer, chunks)
    if not citations and chunks:
        logger.warning("llm_no_citation", request_id=parsed.request_id)

    usage = extract_usage(ai_message, ctx.llm_client.provider)
    cost_eur = compute_cost_eur(ctx.llm_client.model, usage.prompt_tokens, usage.completion_tokens)
    if cost_eur is None and usage.prompt_tokens is not None:
        logger.warning("unknown_model_pricing", model=ctx.llm_client.model)
    canon_usage = Usage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_eur=cost_eur,
    )

    now = datetime.now(UTC)
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="user",
        content=parsed.query,
        timestamp=now,
        user_id=parsed.user_id,
    )
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="assistant",
        content=answer,
        timestamp=datetime.now(UTC),
        user_id=parsed.user_id,
    )

    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    await ctx.query_log_repo.insert(
        QueryLogRow(
            request_id=parsed.request_id,
            user_id=parsed.user_id,
            conversation_id=ctx.conversation_id,
            query_text=parsed.query,
            mode="canon",
            intent=intent_result.intent,
            status_code=200,
            latency_ms=latency_ms,
            prompt_tokens=canon_usage.prompt_tokens,
            completion_tokens=canon_usage.completion_tokens,
            cost_eur=canon_usage.cost_eur,
        )
    )

    logger.info(
        "generate",
        request_id=parsed.request_id,
        conversation_id=ctx.conversation_id,
        intent=intent_result.intent,
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
        conversation_id=ctx.conversation_id,
        request_id=parsed.request_id,
        usage=canon_usage,
        retrieve_ms=retrieve_ms,
        llm_ms=llm_ms,
    )


async def _run_off_topic(ctx: _RequestContext, intent_result: IntentResult) -> GenerateResponse:
    """Mode 2 off_topic — LLM refusal, no retrieve, no canon LLM (AC-3/AC-7)."""
    parsed = ctx.parsed

    refusal_messages = build_off_topic_messages(
        parsed.query, suspected_injection=bool(ctx.suspected)
    )
    refusal_started = time.perf_counter()
    try:
        refusal_ai = await ctx.llm_client.invoke(refusal_messages)
    except LlmTimeoutError:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        # AC-15: partial query_log with classifier usage only.
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="off_topic",
                intent="off_topic",
                status_code=504,
                latency_ms=latency_ms,
                prompt_tokens=intent_result.prompt_tokens,
                completion_tokens=intent_result.completion_tokens,
                cost_eur=intent_result.cost_eur,
            )
        )
        raise _GenerateError(504, "llm_timeout") from None
    except LlmUpstreamError as exc:
        latency_ms = int((time.perf_counter() - ctx.started) * 1000)
        logger.error("llm_upstream", request_id=parsed.request_id, status=exc.status_code)
        # AC-16: partial query_log with classifier usage only.
        await ctx.query_log_repo.insert(
            QueryLogRow(
                request_id=parsed.request_id,
                user_id=parsed.user_id,
                conversation_id=ctx.conversation_id,
                query_text=parsed.query,
                mode="off_topic",
                intent="off_topic",
                status_code=502,
                latency_ms=latency_ms,
                prompt_tokens=intent_result.prompt_tokens,
                completion_tokens=intent_result.completion_tokens,
                cost_eur=intent_result.cost_eur,
            )
        )
        raise _GenerateError(502, "llm_upstream") from exc
    refusal_ms = int((time.perf_counter() - refusal_started) * 1000)

    answer = str(refusal_ai.content) if refusal_ai.content is not None else ""

    # AC-10: aggregate usage from classifier + refusal LLM.
    refusal_raw_usage = extract_usage(refusal_ai, ctx.llm_client.provider)
    refusal_cost = compute_cost_eur(
        ctx.llm_client.model,
        refusal_raw_usage.prompt_tokens,
        refusal_raw_usage.completion_tokens,
    )

    combined_prompt = _add_optional(intent_result.prompt_tokens, refusal_raw_usage.prompt_tokens)
    combined_completion = _add_optional(
        intent_result.completion_tokens, refusal_raw_usage.completion_tokens
    )
    combined_cost = _add_decimal_optional(intent_result.cost_eur, refusal_cost)

    if combined_cost is None and combined_prompt is not None:
        logger.warning("unknown_model_pricing", model=ctx.llm_client.model)

    off_topic_usage = Usage(
        prompt_tokens=combined_prompt,
        completion_tokens=combined_completion,
        cost_eur=combined_cost,
    )

    # AC-9: retrieve_ms=0, llm_ms = classifier + refusal.
    llm_ms = intent_result.latency_ms + refusal_ms

    # AC-11: persist conversation messages.
    now = datetime.now(UTC)
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="user",
        content=parsed.query,
        timestamp=now,
        user_id=parsed.user_id,
    )
    await ctx.conversation_client.append_message(
        conversation_id=ctx.conversation_id,
        role="assistant",
        content=answer,
        timestamp=datetime.now(UTC),
        user_id=parsed.user_id,
    )

    # AC-12: insert query_log.
    latency_ms = int((time.perf_counter() - ctx.started) * 1000)
    await ctx.query_log_repo.insert(
        QueryLogRow(
            request_id=parsed.request_id,
            user_id=parsed.user_id,
            conversation_id=ctx.conversation_id,
            query_text=parsed.query,
            mode="off_topic",
            intent="off_topic",
            status_code=200,
            latency_ms=latency_ms,
            prompt_tokens=off_topic_usage.prompt_tokens,
            completion_tokens=off_topic_usage.completion_tokens,
            cost_eur=off_topic_usage.cost_eur,
        )
    )

    # AC-20: log INFO with intent, chunks=0, citations=0.
    logger.info(
        "generate",
        request_id=parsed.request_id,
        conversation_id=ctx.conversation_id,
        intent="off_topic",
        mode="off_topic",
        status=200,
        retrieve_ms=0,
        llm_ms=llm_ms,
        chunks=0,
        citations=0,
    )
    return GenerateResponse(
        answer=answer,
        citations=[],
        mode="off_topic",
        conversation_id=ctx.conversation_id,
        request_id=parsed.request_id,
        usage=off_topic_usage,
        retrieve_ms=0,
        llm_ms=llm_ms,
    )


def _add_optional(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return a + b


def _add_decimal_optional(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return a + b
