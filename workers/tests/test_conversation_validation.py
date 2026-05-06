"""Unit tests for conversation message validation (ING-003 AC-6/7/8/9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

VALID_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = "00000000-0000-0000-0000-000000000000"


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "role": "user",
        "content": "hello",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": USER_ID,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "bad_id",
    [
        "abc",
        "11111111-2222-3333-4444-55555555555",  # 35 chars
        "ABCDEF12-3456-7890-ABCD-EF1234567890",  # uppercase
        "11111111-2222-3333-4444-zzzzzzzzzzzz",  # non-hex
    ],
)
@pytest.mark.asyncio
async def test_invalid_conversation_id_returns_400(worker_client: AsyncClient, bad_id: str) -> None:
    """AC-6: non-UUID conversation_id rejected before any DB/GCS call."""
    response = await worker_client.post(f"/v1/conversations/{bad_id}/messages", json=_payload())
    assert response.status_code == 400
    assert response.json() == {"detail": {"error": "invalid_conversation_id"}}


@pytest.mark.asyncio
async def test_invalid_role_returns_422(worker_client: AsyncClient) -> None:
    """AC-7: role outside {user, assistant} -> 422 invalid_role."""
    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages", json=_payload(role="system")
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "invalid_role"}}


@pytest.mark.asyncio
async def test_empty_content_returns_422(worker_client: AsyncClient) -> None:
    """AC-8: empty content -> 422 empty_content."""
    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages", json=_payload(content="")
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "empty_content"}}


@pytest.mark.asyncio
async def test_oversized_content_returns_413(worker_client: AsyncClient) -> None:
    """AC-8: content > 16 KiB UTF-8 -> 413 content_too_large."""
    too_big = "x" * (16 * 1024 + 1)
    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages", json=_payload(content=too_big)
    )
    assert response.status_code == 413
    assert response.json() == {"detail": {"error": "content_too_large"}}


@pytest.mark.asyncio
async def test_invalid_timestamp_format_returns_422(worker_client: AsyncClient) -> None:
    """AC-9: non-ISO8601 timestamp -> 422 invalid_timestamp."""
    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages", json=_payload(timestamp="yesterday")
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "invalid_timestamp"}}


@pytest.mark.asyncio
async def test_timestamp_in_future_returns_422(worker_client: AsyncClient, fake_repo: Any) -> None:
    """AC-9: timestamp > now+5min -> 422 timestamp_in_future."""
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    fake_repo.create_if_absent = AsyncMock(return_value=(False, created_at))
    future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages", json=_payload(timestamp=future)
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "timestamp_in_future"}}


@pytest.mark.asyncio
async def test_timestamp_before_created_at_returns_422(
    worker_client: AsyncClient, fake_repo: Any
) -> None:
    """AC-9: timestamp < conversation.created_at -> 422 timestamp_before_conversation."""
    created_at = datetime.now(UTC)
    fake_repo.create_if_absent = AsyncMock(return_value=(False, created_at))
    too_old = (created_at - timedelta(hours=1)).isoformat()
    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages", json=_payload(timestamp=too_old)
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "timestamp_before_conversation"}}
