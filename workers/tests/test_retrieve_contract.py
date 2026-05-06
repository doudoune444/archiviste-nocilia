"""Schemathesis contract test for `POST /v1/retrieve` (RET-001 AC-17).

Mounts the on-disk OpenAPI spec as a static route on a FastAPI app, then runs
schemathesis against it via ASGI in-process. Scoped to `/v1/retrieve` only
(`/v1/generate` is a stub unrelated to this ticket — see plan §risks).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import schemathesis
import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from hypothesis import settings as hypothesis_settings
from schemathesis.checks import not_a_server_error

from archiviste_workers.retrieve.router import router as retrieve_router

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "specs" / "openapi" / "gateway-to-workers.yml"


class _StubEmbedder:
    model_name = "stub"

    def encode_batch(self, texts: list[str], batch_size: int) -> list[list[float]]:
        del batch_size
        return [[0.0] * 1023 + [1.0] for _ in texts]


class _OutagePool:
    def acquire(self) -> Any:
        raise OSError("schemathesis stub outage")


def _load_spec() -> dict[str, Any]:
    with OPENAPI_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(retrieve_router)
    app.state.embedder = _StubEmbedder()
    app.state.db_pool = _OutagePool()
    spec = _load_spec()

    @app.get("/openapi-spec.json", include_in_schema=False)
    async def _serve_spec() -> JSONResponse:
        return JSONResponse(spec)

    return app


_app = _build_app()
schema = schemathesis.openapi.from_asgi("/openapi-spec.json", app=_app)


@schema.include(path_regex=r"/v1/retrieve").parametrize()
@hypothesis_settings(derandomize=True, max_examples=25, deadline=None)
def test_retrieve_contract(case: Any) -> None:
    """AC-17: schemathesis explores `/v1/retrieve` against the OpenAPI contract.

    503 (`embedder_unavailable` / `database_unavailable`) is a documented response
    in the spec, so we exclude the built-in `not_a_server_error` check that would
    flag any 5xx as a violation.
    """
    response = case.call()
    case.validate_response(response, excluded_checks=[not_a_server_error])


def test_openapi_file_present() -> None:
    """Sanity: spec file exists at the expected path."""
    assert OPENAPI_PATH.exists()


def test_schema_loaded() -> None:
    """Sanity: spec parsed and contains `/v1/retrieve`."""
    assert "/v1/retrieve" in schema.raw_schema["paths"]
