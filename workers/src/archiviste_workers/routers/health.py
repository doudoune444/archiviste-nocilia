"""Health endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from archiviste_workers import __version__

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str


@router.get("/healthz", response_model=Health)
async def healthz() -> Health:
    return Health(status="ok", version=__version__)
