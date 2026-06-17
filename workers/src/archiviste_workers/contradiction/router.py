"""POST /v1/verify-contradiction — 4-way verdict judging with retrieval fallback (#162)."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from archiviste_workers.contradiction.models import (
    VerifyContradictionRequest,
    VerifyContradictionResponse,
)
from archiviste_workers.contradiction.service import verify_contradiction
from archiviste_workers.generate.models import is_valid_uuid

router = APIRouter(prefix="/v1", tags=["contradiction"])
logger = structlog.get_logger()

# Contract vocabulary (specs/openapi/gateway-to-workers.yml X-User-Tier enum) mapped to the
# internal ACL tier names used by retrieve/schemas.py and services/acl.py.
# Translation happens at this boundary only — internal code keeps its own vocabulary.
_CONTRACT_TIER_TO_INTERNAL: dict[str, str] = {
    "anonymous": "anonymous",
    "member": "members",
    "author": "author_only",
}


class _VerifyError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _error_response(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


def _parse_request(
    payload: dict[str, Any], headers: Any
) -> tuple[VerifyContradictionRequest, str, str]:
    raw_user_id = headers.get("x-user-id")
    if not raw_user_id or not is_valid_uuid(raw_user_id):
        raise _VerifyError(400, "invalid_request")
    raw_user_tier = headers.get("x-user-tier")
    if not raw_user_tier or raw_user_tier not in _CONTRACT_TIER_TO_INTERNAL:
        raise _VerifyError(400, "invalid_request")

    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not is_valid_uuid(request_id):
        raise _VerifyError(400, "invalid_request")
    conversation_id = payload.get("conversation_id")
    if not isinstance(conversation_id, str) or not is_valid_uuid(conversation_id):
        raise _VerifyError(400, "invalid_conversation_id")

    try:
        parsed = VerifyContradictionRequest.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc", ())
            if loc and loc[0] == "claim":
                raise _VerifyError(400, "invalid_claim") from exc
            if loc and loc[0] == "citations":
                raise _VerifyError(400, "invalid_citations") from exc
        raise _VerifyError(400, "invalid_request") from exc
    return parsed, _CONTRACT_TIER_TO_INTERNAL[raw_user_tier], raw_user_id


@router.post("/verify-contradiction", response_model=VerifyContradictionResponse)
async def post_verify_contradiction(
    request: Request, payload: dict[str, Any]
) -> VerifyContradictionResponse | JSONResponse:
    try:
        parsed, user_tier, user_id = _parse_request(payload, request.headers)
    except _VerifyError as exc:
        return _error_response(exc.status, exc.code)

    result = await verify_contradiction(
        pool=request.app.state.db_pool,
        embedder=request.app.state.embedder,
        llm=request.app.state.llm_client,
        claim=parsed.claim,
        conversation_id=parsed.conversation_id,
        citations=parsed.citations,
        user_tier=user_tier,
        request_id=parsed.request_id,
        force=parsed.force,
    )
    logger.info(
        "verify_contradiction",
        request_id=parsed.request_id,
        user_id=user_id,
        conversation_id=parsed.conversation_id,
        verdict=result.verdict,
        ticket_action=result.ticket_action,
    )
    return VerifyContradictionResponse(
        verdict=result.verdict,
        reason=result.reason,
        ticket_action=result.ticket_action,
        ticket_id=result.ticket_id,
    )
