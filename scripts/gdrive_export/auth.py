"""Service account credentials loader for Google Drive API.

Reads credentials from environment variables only — no hardcoded paths or defaults.
AC-7: GDRIVE_SA_KEY_JSON (inline JSON) takes precedence over GOOGLE_APPLICATION_CREDENTIALS.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, cast

from google.oauth2 import service_account

_DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_SLIDES_READONLY_SCOPE = "https://www.googleapis.com/auth/presentations.readonly"

_SCOPES = [_DRIVE_READONLY_SCOPE, _SHEETS_READONLY_SCOPE, _SLIDES_READONLY_SCOPE]


def load_service_account_credentials() -> service_account.Credentials:
    """Load Drive service account credentials from environment variables.

    Precedence: GDRIVE_SA_KEY_JSON > GOOGLE_APPLICATION_CREDENTIALS.
    Logs a warning if both are set (AC-7). Calls sys.exit(1) on any failure.
    """
    sa_key_json = os.environ.get("GDRIVE_SA_KEY_JSON")
    gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    if sa_key_json is None and gac_path is None:
        print(  # noqa: T201
            "gdrive credentials missing: set GDRIVE_SA_KEY_JSON or GOOGLE_APPLICATION_CREDENTIALS",
            file=sys.stderr,
        )
        sys.exit(1)

    if sa_key_json is not None and gac_path is not None:
        # AC-7: both set → log conflict warning once, use GDRIVE_SA_KEY_JSON.
        _log_conflict()

    if sa_key_json is not None:
        return _load_from_json_string(sa_key_json)
    assert gac_path is not None  # both-None branch already exited at L33
    return _load_from_file(gac_path)


def _log_conflict() -> None:
    """Emit the AC-7 credentials conflict warning to stdout."""
    print(json.dumps({"event": "gdrive_sync.creds_conflict", "resolved": "GDRIVE_SA_KEY_JSON"}))  # noqa: T201


def _load_from_json_string(sa_key_json: str) -> service_account.Credentials:
    """Parse inline JSON string and build credentials. Exits on parse error."""
    try:
        info: dict[str, Any] = json.loads(sa_key_json)
    except json.JSONDecodeError as exc:
        print(f"GDRIVE_SA_KEY_JSON is not valid JSON: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    return _build_credentials(info)


def _load_from_file(gac_path: str) -> service_account.Credentials:
    """Read SA JSON from file path. Exits if file missing or not valid JSON."""
    try:
        with open(gac_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"GOOGLE_APPLICATION_CREDENTIALS file not readable: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    try:
        info: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"GOOGLE_APPLICATION_CREDENTIALS file is not valid JSON: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    return _build_credentials(info)


def _build_credentials(info: dict[str, Any]) -> service_account.Credentials:
    """Build Credentials from SA info dict. Exits on construction failure."""
    try:
        # google-auth has no stubs for from_service_account_info (upstream gap, not a workaround).
        creds = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            info, scopes=_SCOPES
        )
        return cast(service_account.Credentials, creds)
    except Exception as exc:
        print(f"Failed to build service account credentials: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
