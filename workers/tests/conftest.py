"""Shared pytest fixtures for workers tests (ING-003)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# AC-12 indirectly: keep Settings() boot-compatible across the unit suite.
# GCS_BUCKET is required (no default). We do NOT set GCS_EMULATOR_HOST here
# because integration suites need to control it explicitly.
os.environ.setdefault("GCS_BUCKET", "archiviste-conversations-test")

from archiviste_workers.conversation.router import router as conversation_router


@pytest.fixture
def fake_repo() -> Any:
    repo = AsyncMock()
    repo.create_if_absent = AsyncMock()
    repo.increment_message_count = AsyncMock(return_value=1)
    return repo


@pytest.fixture
def fake_storage() -> Any:
    storage = AsyncMock()
    storage.gcs_uri = lambda cid: f"gs://archiviste-conversations-test/{cid}.md"
    storage.create_conversation_object = AsyncMock(return_value=1)
    storage.append_block = AsyncMock(return_value=(2, 128))
    return storage


@pytest_asyncio.fixture
async def worker_client(fake_repo: Any, fake_storage: Any) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(conversation_router)
    app.state.conversation_repo = fake_repo
    app.state.gcs_storage = fake_storage
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def reset_gcs_bucket_env() -> Iterator[None]:
    """Temporarily remove GCS_BUCKET so Settings() fails fast (AC-12)."""
    saved = os.environ.pop("GCS_BUCKET", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["GCS_BUCKET"] = saved
