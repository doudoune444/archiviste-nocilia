"""Unit tests for gdrive_export.auth — AC-7."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gdrive_export.auth import load_service_account_credentials

# Minimal SA JSON shape (private_key not validated by mocked builder).
_FAKE_SA: dict[str, Any] = {
    "type": "service_account",
    "project_id": "test-project",
    "private_key_id": "key-id",
    "private_key": "fake-private-key-placeholder",
    "client_email": "test@test-project.iam.gserviceaccount.com",
    "client_id": "123456789",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/test",
}

_MOCK_CREDS = MagicMock()


class TestLoadServiceAccountCredentials:
    """AC-7: credentials loading matrix."""

    def test_no_env_vars_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # AC-7: fail-fast if neither GDRIVE_SA_KEY_JSON nor GOOGLE_APPLICATION_CREDENTIALS set.
        monkeypatch.delenv("GDRIVE_SA_KEY_JSON", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            load_service_account_credentials()
        assert exc_info.value.code != 0

    def test_sa_key_json_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # AC-7: GDRIVE_SA_KEY_JSON (inline JSON) → credentials loaded successfully.
        monkeypatch.setenv("GDRIVE_SA_KEY_JSON", json.dumps(_FAKE_SA))
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        with patch(
            "gdrive_export.auth.service_account.Credentials.from_service_account_info",
            return_value=_MOCK_CREDS,
        ):
            creds = load_service_account_credentials()
        assert creds is _MOCK_CREDS

    def test_gac_file_valid(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # AC-7: GOOGLE_APPLICATION_CREDENTIALS (file path) → credentials loaded successfully.
        sa_file = tmp_path / "sa.json"
        sa_file.write_text(json.dumps(_FAKE_SA), encoding="utf-8")
        monkeypatch.delenv("GDRIVE_SA_KEY_JSON", raising=False)
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(sa_file))
        with patch(
            "gdrive_export.auth.service_account.Credentials.from_service_account_info",
            return_value=_MOCK_CREDS,
        ):
            creds = load_service_account_credentials()
        assert creds is _MOCK_CREDS

    def test_both_set_uses_sa_key_json_and_logs_conflict(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # AC-7: both set → GDRIVE_SA_KEY_JSON takes precedence, warning log emitted once.
        sa_file = tmp_path / "sa.json"
        sa_file.write_text(json.dumps(_FAKE_SA), encoding="utf-8")
        monkeypatch.setenv("GDRIVE_SA_KEY_JSON", json.dumps(_FAKE_SA))
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(sa_file))
        with patch(
            "gdrive_export.auth.service_account.Credentials.from_service_account_info",
            return_value=_MOCK_CREDS,
        ):
            creds = load_service_account_credentials()
        assert creds is _MOCK_CREDS
        captured = capsys.readouterr()
        assert "gdrive_sync.creds_conflict" in captured.out
        assert "GDRIVE_SA_KEY_JSON" in captured.out

    def test_sa_key_json_invalid_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # AC-7: invalid JSON in GDRIVE_SA_KEY_JSON → fail-fast (SystemExit).
        monkeypatch.setenv("GDRIVE_SA_KEY_JSON", "not-valid-json{")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            load_service_account_credentials()
        assert exc_info.value.code != 0

    def test_gac_file_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # AC-7: GAC path points to non-existent file → fail-fast (SystemExit).
        monkeypatch.delenv("GDRIVE_SA_KEY_JSON", raising=False)
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent/path/sa.json"
        )
        with pytest.raises(SystemExit) as exc_info:
            load_service_account_credentials()
        assert exc_info.value.code != 0

    def test_conflict_log_emitted_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # AC-7: conflict log must be emitted exactly once (not repeated).
        sa_file = tmp_path / "sa.json"
        sa_file.write_text(json.dumps(_FAKE_SA), encoding="utf-8")
        monkeypatch.setenv("GDRIVE_SA_KEY_JSON", json.dumps(_FAKE_SA))
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(sa_file))
        with patch(
            "gdrive_export.auth.service_account.Credentials.from_service_account_info",
            return_value=_MOCK_CREDS,
        ):
            load_service_account_credentials()
        captured = capsys.readouterr()
        assert captured.out.count("gdrive_sync.creds_conflict") == 1
