"""AC-2 placeholder: workers tests pass."""

from fastapi.testclient import TestClient

from archiviste_workers.main import app


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["version"], str)
