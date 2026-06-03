"""Health endpoints: /healthz (liveness) and /readyz (readiness)."""

from __future__ import annotations

import asyncio
from typing import Literal

import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from archiviste_workers import __version__

router = APIRouter()

# OPS-003: short timeout for readiness DB probe — prevents slow DB from
# holding up Cloud Run startup_probe beyond its failure_threshold.
READINESS_PROBE_TIMEOUT_SECONDS = 2.0


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str


# `/health` aliases `/healthz`: Cloud Run's public frontend reserves the literal
# `/healthz` path and 404s it before it reaches the container, so the gateway's
# internal liveness probe targets `/health` instead. Both paths share one handler.
@router.get("/healthz", response_model=Health)
@router.get("/health", response_model=Health)
async def healthz() -> Health:
    """Liveness probe — always returns ok. No DB dependency."""
    return Health(status="ok", version=__version__)


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness probe — verifies DB reachability via SELECT 1.

    OPS-003: returns 200/ok when the pool acquires a connection and SELECT 1
    succeeds within READINESS_PROBE_TIMEOUT_SECONDS. Returns 503/degraded on
    any asyncpg error, network error, or timeout so Cloud Run withholds traffic
    from a revision whose DB path is broken.
    """
    pool = request.app.state.db_pool
    payload_ok = {"status": "ok", "version": __version__}
    payload_degraded = {"status": "degraded", "version": __version__}

    try:
        async with pool.acquire() as conn:
            await asyncio.wait_for(
                conn.fetchval("SELECT 1"),
                timeout=READINESS_PROBE_TIMEOUT_SECONDS,
            )
        return JSONResponse(status_code=200, content=payload_ok)
    except (TimeoutError, asyncpg.PostgresError, OSError):
        return JSONResponse(status_code=503, content=payload_degraded)
