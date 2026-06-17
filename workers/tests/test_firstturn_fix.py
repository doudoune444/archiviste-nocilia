"""Tests for first-turn race fix and self-heal on missing GCS object.

Covers:
- AC-2 amendment: created_at = first message timestamp (not DB NOW()),
  eliminating the app-clock vs DB-clock 422 false-positive on turn 1.
- Self-heal: append_block on an orphaned conversation (row exists, GCS object
  absent) recreates the object and succeeds instead of 503.
- AC-9 regression: timestamp_in_future still 422 (unchanged).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from archiviste_workers.conversation.models import (
    ConversationAlreadyExistsError,
    ConversationObjectMissingError,
)

VALID_ID = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
USER_ID = "00000000-0000-0000-0000-000000000001"


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "role": "user",
        "content": "hello nocilia",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": USER_ID,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Part A: first-turn race — created_at == message.timestamp so ts < created_at
# is always False for a brand-new conversation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_turn_does_not_422_when_timestamp_equals_created_at(
    worker_client: AsyncClient, fake_repo: Any, fake_storage: Any
) -> None:
    """AC-2 amendment: new conversation created_at = message.timestamp → no 422.

    Regression guard: previously create_if_absent returned DB NOW() (a few ms
    LATER than the app-clock `now`), so ts < created_at was True → 422.
    With the fix, the caller passes created_at=message.timestamp and the DB
    INSERT uses that value; returned created_at == message.timestamp → equal,
    not less-than → _check_timestamp passes.
    """
    ts = datetime.now(UTC) - timedelta(milliseconds=5)
    # Simulates the race: created_at == message.timestamp (the fix).
    fake_repo.create_if_absent = AsyncMock(return_value=(True, ts))

    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages",
        json=_payload(timestamp=ts.isoformat()),
    )
    # Must NOT 422 — the first turn must persist.
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_create_if_absent_receives_created_at_param(
    worker_client: AsyncClient, fake_repo: Any
) -> None:
    """AC-2 amendment: router passes created_at=message.timestamp to create_if_absent.

    Verifies the call-site wiring: the repository must be called with the
    message timestamp so the DB INSERT can use it as conversations.created_at.
    """
    ts = datetime.now(UTC)
    fake_repo.create_if_absent = AsyncMock(return_value=(True, ts))

    await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages",
        json=_payload(timestamp=ts.isoformat()),
    )

    fake_repo.create_if_absent.assert_called_once()
    _, kwargs = fake_repo.create_if_absent.call_args
    assert "created_at" in kwargs, "create_if_absent must receive created_at kwarg"
    # The value must be the message's own timestamp (same instant, tz-normalised).
    passed = kwargs["created_at"]
    assert abs((passed - ts).total_seconds()) < 1.0


# ---------------------------------------------------------------------------
# Part B: self-heal — missing GCS object is recreated on append.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_heal_on_missing_gcs_object(
    worker_client: AsyncClient, fake_repo: Any, fake_storage: Any
) -> None:
    """Orphaned conversation (row exists, GCS absent): append heals and succeeds.

    Scenario: is_new=False (row exists), but the GCS object is gone.
    append_block raises ConversationObjectMissingError on first call, then
    succeeds on retry after create_conversation_object recreates it.
    Expect 201, not 503.
    """
    ts = datetime.now(UTC) - timedelta(seconds=60)
    fake_repo.create_if_absent = AsyncMock(return_value=(False, ts))

    # First call raises missing; second call succeeds.
    fake_storage.append_block = AsyncMock(side_effect=[ConversationObjectMissingError(), (5, 256)])
    fake_storage.create_conversation_object = AsyncMock(return_value=1)

    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages",
        json=_payload(timestamp=ts.isoformat()),
    )

    assert response.status_code == 201, response.text
    # create_conversation_object must have been called exactly once to heal.
    fake_storage.create_conversation_object.assert_called_once()


@pytest.mark.asyncio
async def test_self_heal_ignores_already_exists_on_concurrent_create(
    worker_client: AsyncClient, fake_repo: Any, fake_storage: Any
) -> None:
    """If create_conversation_object raises ConversationAlreadyExistsError during
    heal (object appeared concurrently), ignore it and retry the append.

    Expect 201, not 409.
    """
    ts = datetime.now(UTC) - timedelta(seconds=30)
    fake_repo.create_if_absent = AsyncMock(return_value=(False, ts))

    fake_storage.append_block = AsyncMock(side_effect=[ConversationObjectMissingError(), (7, 300)])
    fake_storage.create_conversation_object = AsyncMock(
        side_effect=ConversationAlreadyExistsError()
    )

    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages",
        json=_payload(timestamp=ts.isoformat()),
    )

    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# AC-9 regression: timestamp_in_future still 422 (unchanged).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timestamp_in_future_still_422_after_fix(
    worker_client: AsyncClient, fake_repo: Any
) -> None:
    """AC-9 regression: fix must not remove the future-timestamp guard."""
    ts = datetime.now(UTC)
    fake_repo.create_if_absent = AsyncMock(return_value=(True, ts))
    future = (ts + timedelta(minutes=10)).isoformat()

    response = await worker_client.post(
        f"/v1/conversations/{VALID_ID}/messages",
        json=_payload(timestamp=future),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "timestamp_in_future"}}
