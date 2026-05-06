"""POST /v1/retrieve router (RET-001).

Sequence per AC-1..AC-16 :
    parse body -> validate (AC-2..AC-5) -> embedder check (AC-13)
        -> embed query (AC-6/15) -> ACL search (AC-7/8/14) -> response (AC-9..AC-12)
    -> emit one redacted JSON log (AC-16).
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from archiviste_workers.embedder import Embedder
from archiviste_workers.retrieve.schemas import (
    ALLOWED_TIERS_BY_USER_TIER,
    ALLOWED_USER_TIERS,
    MAX_QUERY_BYTES,
    TOP_K_DEFAULT,
    TOP_K_MAX,
    TOP_K_MIN,
    RetrievedChunk,
    RetrieveResponse,
)
from archiviste_workers.retrieve.search import DatabaseUnavailableError, search

router = APIRouter(prefix="/v1", tags=["retrieve"])
logger = structlog.get_logger()

_JSON_MEDIA_TYPE = "application/json; charset=utf-8"


class _ValidationError(Exception):
    """Carries an error code mapped 1:1 to AC-2..AC-5 wire format."""

    def __init__(self, code: str) -> None:
        self.code = code


def _json(body: dict[str, Any], status_code: int) -> Response:
    return JSONResponse(content=body, status_code=status_code, media_type=_JSON_MEDIA_TYPE)


def _validate_body(payload: object) -> tuple[str, int, str]:
    """Return `(query, top_k, user_tier)` or raise `_ValidationError`. AC-2/3/4/5."""
    if not isinstance(payload, dict):
        raise _ValidationError("invalid_request")
    if "query" not in payload or "user_tier" not in payload:
        raise _ValidationError("invalid_request")

    query = _validate_query(payload["query"])
    top_k = _validate_top_k(payload.get("top_k"))
    user_tier = _validate_user_tier(payload["user_tier"])
    return query, top_k, user_tier


def _validate_query(value: object) -> str:
    if not isinstance(value, str):
        raise _ValidationError("invalid_query")
    stripped = value.strip()
    if not stripped:
        raise _ValidationError("invalid_query")
    if len(value.encode("utf-8")) > MAX_QUERY_BYTES:
        raise _ValidationError("invalid_query")
    return value


def _validate_top_k(value: object) -> int:
    if value is None:
        return TOP_K_DEFAULT
    # Reject bool (Python bool is int subclass — would silently pass `int` check).
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ValidationError("invalid_top_k")
    if value < TOP_K_MIN or value > TOP_K_MAX:
        raise _ValidationError("invalid_top_k")
    return value


def _validate_user_tier(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_USER_TIERS:
        raise _ValidationError("invalid_user_tier")
    return value


async def _read_payload(request: Request) -> object:
    try:
        return await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise _ValidationError("invalid_request") from exc


@router.post("/retrieve")
async def post_retrieve(request: Request) -> Response:
    """Embed `query`, run ACL-filtered cosine top-K, return ordered chunks."""
    log_fields: dict[str, Any] = {
        "query_len": 0,
        "top_k": 0,
        "user_tier": "",
        "results": 0,
        "embedding_ms": 0,
        "search_ms": 0,
    }
    status: str = "ok"
    response: Response

    try:
        raw = await _read_payload(request)
        query, top_k, user_tier = _validate_body(raw)
        log_fields["query_len"] = len(query.encode("utf-8"))
        log_fields["top_k"] = top_k
        log_fields["user_tier"] = user_tier

        embedder: Embedder | None = getattr(request.app.state, "embedder", None)
        if embedder is None:
            status = "embedder_unavailable"
            response = _json({"error": status}, status_code=503)
        else:
            response = await _execute_search(
                request=request,
                embedder=embedder,
                query=query,
                top_k=top_k,
                user_tier=user_tier,
                log_fields=log_fields,
            )
            if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
                status = "database_unavailable"
    except _ValidationError as exc:
        status = exc.code
        response = _json({"error": exc.code}, status_code=400)
    finally:
        logger.info("retrieve", status=status, **log_fields)
    return response


async def _execute_search(
    *,
    request: Request,
    embedder: Embedder,
    query: str,
    top_k: int,
    user_tier: str,
    log_fields: dict[str, Any],
) -> Response:
    embedding_started = time.perf_counter()
    [vector] = embedder.encode_batch([query], batch_size=1)
    log_fields["embedding_ms"] = _elapsed_ms(embedding_started)

    pool = request.app.state.db_pool
    allowed_tiers = ALLOWED_TIERS_BY_USER_TIER[user_tier]

    search_started = time.perf_counter()
    try:
        chunks: list[RetrievedChunk] = await search(pool, vector, allowed_tiers, top_k)
    except DatabaseUnavailableError:
        log_fields["search_ms"] = _elapsed_ms(search_started)
        return _json({"error": "database_unavailable"}, status_code=503)
    log_fields["search_ms"] = _elapsed_ms(search_started)
    log_fields["results"] = len(chunks)

    body = RetrieveResponse(
        chunks=chunks,
        embedding_ms=log_fields["embedding_ms"],
        search_ms=log_fields["search_ms"],
    )
    return _json(body.model_dump(), status_code=200)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
