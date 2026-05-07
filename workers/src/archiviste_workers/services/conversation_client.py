"""Internal HTTP client to ING-003 conversation logger (AC-14). Best-effort: never raises."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx
import structlog

logger = structlog.get_logger()

_HTTP_OK_CLASS = 2


@dataclass(frozen=True)
class AppendResult:
    ok: bool
    status_code: int | None


class ConversationClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def append_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        timestamp: datetime,
        user_id: str,
    ) -> AppendResult:
        url = f"{self._base_url}/v1/conversations/{conversation_id}/messages"
        body = {
            "role": role,
            "content": content,
            "timestamp": timestamp.isoformat(),
            "user_id": user_id,
        }
        try:
            response = await self._client.post(url, json=body)
        except (httpx.HTTPError, OSError) as exc:
            logger.error(
                "conversation_log_failed",
                conversation_id=conversation_id,
                stage=role,
                status=None,
                error=str(exc),
            )
            return AppendResult(ok=False, status_code=None)
        if response.status_code // 100 == _HTTP_OK_CLASS:
            return AppendResult(ok=True, status_code=response.status_code)
        logger.error(
            "conversation_log_failed",
            conversation_id=conversation_id,
            stage=role,
            status=response.status_code,
        )
        return AppendResult(ok=False, status_code=response.status_code)
