"""Settings fail-fast for GCS_BUCKET (AC-12)."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from archiviste_workers.conversation.gcs_storage import build_client
from archiviste_workers.settings import Settings


@pytest.mark.usefixtures("reset_gcs_bucket_env")
def test_settings_fails_fast_without_gcs_bucket() -> None:
    """AC-12: missing GCS_BUCKET raises ValidationError at boot."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_emulator_host_targets_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-12: GCS_EMULATOR_HOST set -> client points at emulator endpoint."""
    monkeypatch.setenv("GCS_BUCKET", "archiviste-conversations-test")
    client = build_client(emulator_host="http://localhost:4443")
    assert client.api_endpoint == "http://localhost:4443"


def test_default_settings_in_test_env_loads() -> None:
    """Sanity: with GCS_BUCKET set (conftest autouse), Settings boots."""
    assert os.environ.get("GCS_BUCKET")
    settings = Settings(_env_file=None)
    assert settings.gcs_bucket == os.environ["GCS_BUCKET"]
