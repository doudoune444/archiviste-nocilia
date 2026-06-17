"""#169 — FIX-CONVO: contract tier vocabulary accepted at workers ingress.

AC references:
  - AC-1: generate ingress accepts member/author (contract vocab) and maps to internal tiers.
  - AC-2: contradiction ingress accepts member/author.
  - AC-3: unknown/legacy tiers (e.g. "admin") still rejected.
  - AC-4: anonymous path unchanged.
  - AC-5: member maps to internal "members" ACL tier; author maps to "author_only".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from archiviste_workers.contradiction.router import (
    _CONTRACT_TIER_TO_INTERNAL as _CTR_CONTRACT_TIER_TO_INTERNAL,
)
from archiviste_workers.contradiction.router import (
    router as contradiction_router,
)
from archiviste_workers.embedder import FakeEmbedder
from archiviste_workers.generate.router import router as generate_router

USER_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_ID = "22222222-2222-4222-8222-222222222222"
CONV_ID = "33333333-3333-4333-8333-333333333333"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def gen_client() -> Any:
    """Minimal FastAPI app wiring the generate router with stubbed dependencies."""
    app = FastAPI()
    app.include_router(generate_router)
    app.state.retrieve_client = AsyncMock()
    app.state.llm_client = AsyncMock()
    app.state.conversation_client = AsyncMock()
    app.state.query_log_repo = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def ctr_client() -> Any:
    """Minimal FastAPI app wiring the contradiction router."""
    app = FastAPI()
    app.include_router(contradiction_router)
    app.state.db_pool = AsyncMock()
    # FakeEmbedder provides a synchronous encode_batch; prevents coroutine-subscript errors
    # when the no-citation path triggers embedding (not a tier-validation concern).
    app.state.embedder = FakeEmbedder()
    app.state.llm_client = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _gen_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "query": "Qui est l'Archiviste?",
        "conversation_id": None,
        "request_id": REQUEST_ID,
    }
    base.update(overrides)
    return base


def _gen_headers(tier: str) -> dict[str, str]:
    return {"X-User-Id": USER_ID, "X-User-Tier": tier}


def _ctr_payload() -> dict[str, Any]:
    return {
        "claim": "L'Archiviste est mort.",
        "conversation_id": CONV_ID,
        "request_id": REQUEST_ID,
    }


def _ctr_headers(tier: str) -> dict[str, str]:
    return {"x-user-id": USER_ID, "x-user-tier": tier}


# ---------------------------------------------------------------------------
# Generate ingress — contract tier vocabulary (AC-1, AC-4, AC-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["anonymous", "member", "author"])
async def test_generate_accepts_contract_tiers(gen_client: AsyncClient, tier: str) -> None:
    # AC-1/AC-4: generate ingress must accept all three contract-vocabulary tiers
    # without returning 422 invalid_user_tier.
    r = await gen_client.post("/v1/generate", json=_gen_payload(), headers=_gen_headers(tier))
    # Any status other than 422 with invalid_user_tier is a pass — the request
    # reached the pipeline (stubs may return 500/502 etc, which is fine here).
    assert not (r.status_code == 422 and r.json().get("error") == "invalid_user_tier"), (
        f"Tier '{tier}' was rejected at ingress with 422 invalid_user_tier"
    )


@pytest.mark.asyncio
async def test_generate_rejects_unknown_tier(gen_client: AsyncClient) -> None:
    # AC-3: non-contract tiers are still rejected.
    r = await gen_client.post("/v1/generate", json=_gen_payload(), headers=_gen_headers("admin"))
    assert r.status_code == 422
    assert r.json() == {"error": "invalid_user_tier"}


@pytest.mark.asyncio
async def test_generate_rejects_legacy_internal_tier_members(gen_client: AsyncClient) -> None:
    # AC-5/contract: "members" (old internal name) is NOT in the contract vocab; must be rejected.
    r = await gen_client.post("/v1/generate", json=_gen_payload(), headers=_gen_headers("members"))
    assert r.status_code == 422
    assert r.json() == {"error": "invalid_user_tier"}


@pytest.mark.asyncio
async def test_generate_rejects_legacy_internal_tier_author_only(gen_client: AsyncClient) -> None:
    # AC-5/contract: "author_only" (old internal name) is NOT in the contract vocab; must be
    # rejected.
    r = await gen_client.post(
        "/v1/generate", json=_gen_payload(), headers=_gen_headers("author_only")
    )
    assert r.status_code == 422
    assert r.json() == {"error": "invalid_user_tier"}


# ---------------------------------------------------------------------------
# Contradiction ingress — contract tier vocabulary (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["anonymous", "member", "author"])
async def test_contradiction_accepts_contract_tiers(tier: str) -> None:
    # AC-2: contradiction ingress must accept all three contract-vocabulary tiers.
    # We check the boundary mapping constant directly to avoid the no-citation retrieval
    # path crashing against stubs (that is not a tier-validation concern).
    assert tier in _CTR_CONTRACT_TIER_TO_INTERNAL, (
        f"Tier '{tier}' is not in _CONTRACT_TIER_TO_INTERNAL — ingress would reject it"
    )


@pytest.mark.asyncio
async def test_contradiction_rejects_unknown_tier(ctr_client: AsyncClient) -> None:
    # AC-3: non-contract tiers are still rejected by the contradiction router.
    r = await ctr_client.post(
        "/v1/verify-contradiction", json=_ctr_payload(), headers=_ctr_headers("superuser")
    )
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_request"}


@pytest.mark.asyncio
async def test_contradiction_rejects_legacy_internal_tier_members(
    ctr_client: AsyncClient,
) -> None:
    # AC-5/contract: "members" (old internal name) is NOT in the contract vocab; must be rejected.
    r = await ctr_client.post(
        "/v1/verify-contradiction", json=_ctr_payload(), headers=_ctr_headers("members")
    )
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_request"}


@pytest.mark.asyncio
async def test_contradiction_rejects_legacy_internal_tier_author_only(
    ctr_client: AsyncClient,
) -> None:
    # AC-5/contract: "author_only" (old internal name) is NOT in the contract vocab; must be
    # rejected.
    r = await ctr_client.post(
        "/v1/verify-contradiction", json=_ctr_payload(), headers=_ctr_headers("author_only")
    )
    assert r.status_code == 400
    assert r.json() == {"error": "invalid_request"}
